# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/layers/03_codebook.ipynb.

# %% auto 0
__all__ = ['CodebookConfig', 'Codebook']

# %% ../../nbs/layers/03_codebook.ipynb 2
import torch
import torch.distributed as dist
from einops import rearrange
from huggingface_hub import PyTorchModelHubMixin
from torch import nn

from ..utils.custom_base_model import CustomBaseModel

# %% ../../nbs/layers/03_codebook.ipynb 4
class CodebookConfig(CustomBaseModel):
    num_vectors: int
    dim: int

    revive_dead_vectors_after_n_steps: int = 100  # 0 means never revive

    ema_decay: float | None = 0.99

# %% ../../nbs/layers/03_codebook.ipynb 6
class Codebook(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: CodebookConfig = {}, use_ema: bool = True, **kwargs):
        super().__init__()

        self.config = CodebookConfig.model_validate(config | kwargs)

        self.use_ema = use_ema
        if self.use_ema:
            assert self.config.ema_decay is not None, "ema_decay must be provided if use_ema is True"

        num_vectors = self.config.num_vectors
        dim = self.config.dim

        self.vectors = nn.Embedding(num_vectors, dim)

        # Usage counter tracks the number of times a vector has been used since it was last revived
        usage_counter = torch.zeros(num_vectors, dtype=torch.long)
        self.register_buffer("usage_counter", usage_counter, persistent=False)
        self.usage_counter: torch.Tensor  # For hinting

        # stale_counter tracks the number of batches a vector has been unused since the last time it was used
        stale_counter = torch.zeros(num_vectors, dtype=torch.long)
        self.register_buffer("stale_counter", stale_counter, persistent=False)
        self.stale_counter: torch.Tensor

        # Create a generator object so that randomness is consistent across all devices
        self.generator = torch.Generator()
        self.generator_initalized = False

        if self.use_ema:
            self.decay = self.config.ema_decay

            # EMA cluster size tracking
            cluster_size = torch.zeros(self.config.num_vectors)
            self.register_buffer("cluster_size", cluster_size, persistent=False)
            self.cluster_size: torch.Tensor

            # EMA for embedding vectors
            ema_vectors = torch.zeros_like(self.vectors.weight)
            self.register_buffer("ema_vectors", ema_vectors, persistent=False)
            self.ema_vectors: torch.Tensor

    @torch.no_grad()
    def calculate_perplexity(self, indices: torch.Tensor):
        encodings = torch.zeros(indices.shape[0], self.config.num_vectors, device=indices.device)
        encodings.scatter_(1, indices.unsqueeze(1), 1)
        avg_probs = encodings.float().mean(0)
        perplexity = torch.exp(-torch.sum(avg_probs * torch.log(avg_probs + 1e-10)))
        return perplexity

    def calculate_losses(self, x: torch.Tensor, z: torch.Tensor):
        commitment_loss = torch.mean((z - x.detach()) ** 2)
        if self.use_ema:
            codebook_loss = torch.zeros_like(commitment_loss)
        else:
            codebook_loss = torch.mean((z.detach() - x) ** 2)
        return codebook_loss, commitment_loss

    def quantize(self, x: torch.Tensor):
        # x: (BS, C)  where BS is a combination of batch and spatial/temporal dimensions

        # Compute distances
        distances = torch.cdist(x, self.vectors.weight)
        # (BS, num_vectors)

        # Find nearest vectors
        indices = torch.argmin(distances, dim=1)
        # (BS,)

        # Quantize
        z: torch.Tensor = self.vectors(indices)
        # (BS, C)

        # Perform EMA
        if self.training and self.use_ema:
            self._perform_ema(x, indices)

        # Loss calculations
        codebook_loss, commitment_loss = self.calculate_losses(x, z)

        # Allow gradients to propagate using straight-through estimator
        z = x + (z - x).detach()
        # (BS, C)

        # Calculate perplexity
        perplexity = self.calculate_perplexity(indices)

        # Update counters
        if self.training:
            self._update_counters(indices)

        return z, codebook_loss, commitment_loss, perplexity

    def revive_dead_vectors(self):
        assert self.training, "revive_dead_vectors should only be called during training"
        revive_vector_mask = self.stale_counter >= self.config.revive_dead_vectors_after_n_steps
        if not revive_vector_mask.any():
            return

        revive_vectors_shape = self.vectors.weight[revive_vector_mask].shape
        num_revive_vectors = revive_vectors_shape[0]

        # Sample commonly used vectors from the codebook
        sampling_probabilities = self.usage_counter.clone().to(torch.float32)
        sampling_probabilities.clamp_(min=1e-9)  # Don't allow all zero probabilities
        selected_vectors_mask = torch.multinomial(
            sampling_probabilities, num_revive_vectors, replacement=True, generator=self.generator
        )
        selected_vectors = self.vectors(selected_vectors_mask).detach()

        # Add noise to the selected vectors
        noise = torch.empty_like(selected_vectors).normal_(
            generator=self.generator
        )  # This is because current randn_like does not support generator input
        with torch.no_grad():
            std = self._estimate_codebook_distance() * 0.1  # https://openreview.net/pdf?id=HkGGfhC5Y7
        noised_selected_vectors = selected_vectors + noise * std

        # Replace dead vectors with noised selected vectors
        self.vectors.weight.data[revive_vector_mask] = noised_selected_vectors

        self.usage_counter[revive_vector_mask] = 0
        self.stale_counter[revive_vector_mask] = 0

        if self.use_ema:
            # Also update the EMA buffers for these vectors
            self.ema_vectors.data[revive_vector_mask] = noised_selected_vectors
            self.cluster_size.data[revive_vector_mask] = 0

    def forward(self, x: torch.Tensor, channels_first: bool = None):
        #  If channels_first is None: x: (B, T, C) if ndim == 3, else (B, C, ...)
        #  If channels_first is True: x: (B, C, ...)
        #  If channels_first is False: x: (B, ..., C)

        if not self.generator_initalized:
            self._initialize_generator()

        shape = x.shape
        ndim = x.ndim
        B = shape[0]

        if channels_first is None:
            if ndim == 3:
                channels_first = False
            else:
                channels_first = True

        if channels_first:
            forward_pattern = "b c ... -> (b ...) c"
            backward_pattern = "(b s) c -> b c s"  # s stands for flattened spatial dimensions
        else:
            forward_pattern = "b ... c -> (b ...) c"
            backward_pattern = "(b s) c -> b s c"

        # Flatten input
        x = rearrange(x, forward_pattern)
        # (BS, C)

        z, codebook_loss, commitment_loss, perplexity = self.quantize(x)

        # Return back to original shape
        z = rearrange(z, backward_pattern, b=B).reshape(shape)
        # (x.shape)

        if self.training and self.config.revive_dead_vectors_after_n_steps > 0:
            self.revive_dead_vectors()

        return z, codebook_loss, commitment_loss, perplexity

    def _perform_ema(self, x: torch.Tensor, indices: torch.Tensor):
        # Create one-hot encodings for the selected indices
        encodings = torch.zeros(x.shape[0], self.config.num_vectors, device=x.device)
        encodings.scatter_(1, indices.unsqueeze(1), 1)

        # Calculate new cluster sizes with EMA
        batch_cluster_size = encodings.sum(0)  # Sum over batch dimension

        # Synchronize across devices if using distributed training
        if dist.is_initialized():
            dist.all_reduce(batch_cluster_size, op=dist.ReduceOp.SUM)

        # Update cluster size using EMA
        self.cluster_size.data = self.cluster_size * self.decay + (1 - self.decay) * batch_cluster_size

        # Calculate sum of embeddings assigned to each cluster
        batch_ema_vectors = torch.matmul(encodings.t(), x)

        # Synchronize across devices if using distributed training
        if dist.is_initialized():
            dist.all_reduce(batch_ema_vectors, op=dist.ReduceOp.SUM)

        # Update EMA for vectors
        self.ema_vectors.data = self.ema_vectors * self.decay + (1 - self.decay) * batch_ema_vectors

        # Normalize EMA vectors by cluster size
        n = self.cluster_size.sum()
        cluster_size = (self.cluster_size + 1e-6) / (n + self.config.num_vectors * 1e-6) * n

        # Normalize codebook vectors using Laplace smoothing
        normalized_vectors = self.ema_vectors / cluster_size.unsqueeze(1)
        self.vectors.weight.data = normalized_vectors

    def _initialize_generator(self):
        assert not self.generator_initalized, "Generator has already been initialized"
        seed = torch.randint(0, 2**32, (1,))
        if dist.is_initialized():
            dist.all_reduce(seed, op=dist.ReduceOp.MIN)
        self.generator.manual_seed(seed.item())
        self.generator_initalized = True

    def _update_counters(self, indices):
        # Create a tensor which counts the number of times a vector has been used
        used_vector_indices, counts = torch.unique(indices, return_counts=True)
        usage_counter_increment = torch.zeros_like(self.usage_counter)
        usage_counter_increment[used_vector_indices] = counts

        # Synchronise the usage counts across all devices
        if dist.is_initialized():
            dist.all_reduce(usage_counter_increment, op=dist.ReduceOp.SUM)

        # Don't allow counters to exceed maximum possible values
        approximate_max_value = int(torch.iinfo(torch.long).max * 0.9)
        self.usage_counter.clamp_(max=approximate_max_value)
        self.stale_counter.clamp_(max=approximate_max_value)

        # Update usage counter
        self.usage_counter += usage_counter_increment

        # Identify vectors that were not used across all devices
        stale_counter_increment = torch.zeros_like(self.stale_counter)
        stale_counter_increment[usage_counter_increment == 0] = 1

        # Incrememnt counts of stale vectors and reset counts of used vectors
        self.stale_counter += stale_counter_increment
        self.stale_counter[usage_counter_increment > 0] = 0

    def _estimate_codebook_distance(self, max_sample=500):
        """Estimate mean distance between codebook vectors"""
        with torch.no_grad():
            if self.vectors.weight.shape[0] > max_sample:
                # Sample a subset for efficiency
                idx = torch.randperm(self.vectors.weight.shape[0], generator=self.generator)[:max_sample]
                vectors_weight = self.vectors.weight[idx]
            else:
                vectors_weight = self.vectors.weight

            distances = torch.cdist(vectors_weight, vectors_weight)
            mask = ~torch.eye(distances.shape[0], dtype=torch.bool, device=distances.device)  # Exclude self-distances
            codebook_distance = distances[mask].mean()

        return codebook_distance
