# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/nets/13_perceiver_3d.ipynb.

# %% auto 0
__all__ = ['Perceiver3DChannelMappingConfig', 'Perceiver3DEncoderEncodeConfig', 'Perceiver3DEncoderProcessConfig',
           'Perceiver3DEncoderConfig', 'Perceiver3DDecoderConfig', 'Perceiver3DConfig', 'unfold_with_rollover_1d',
           'unfold_with_rollover_3d_with_mask', 'fold_back_3d', 'Perceiver3DChannelMapping', 'Perceiver3DEncoderEncode',
           'Perceiver3DEncoderProcess', 'Perceiver3DEncoder', 'Perceiver3DDecoder']

# %% ../../nbs/nets/13_perceiver_3d.ipynb 2
import torch
from einops import rearrange, repeat
from huggingface_hub import PyTorchModelHubMixin
from torch import nn

from ..layers.attention import Attention1DWithMLP, Attention1DWithMLPConfig
from ..layers.embeddings import AbsolutePositionEmbeddings3D
from ..utils.activation_checkpointing import ActivationCheckpointing
from ..utils.custom_base_model import CustomBaseModel, model_validator

# %% ../../nbs/nets/13_perceiver_3d.ipynb 4
class Perceiver3DChannelMappingConfig(CustomBaseModel):
    in_channels: int | set[int]
    out_channels: int


class Perceiver3DEncoderEncodeConfig(Attention1DWithMLPConfig):
    dim: int
    num_latent_tokens: int
    num_layers: int


class Perceiver3DEncoderProcessConfig(Attention1DWithMLPConfig):
    dim: int
    num_layers: int


class Perceiver3DEncoderConfig(CustomBaseModel):
    encode: Perceiver3DEncoderEncodeConfig
    process: Perceiver3DEncoderProcessConfig

    @property
    def dim(self):
        return self.encode.dim

    @model_validator(mode="after")
    def validate(self):
        super().validate()
        assert self.encode.dim == self.process.dim, "encode and process dims must be equal"
        return self


class Perceiver3DDecoderConfig(Attention1DWithMLPConfig):
    dim: int
    num_layers: int
    out_channels: int


class Perceiver3DConfig(Perceiver3DEncoderConfig):
    decode: Perceiver3DDecoderConfig

    @model_validator(mode="after")
    def validate(self):
        super().validate()
        assert self.encode.dim == self.decode.dim, "encode and decode dims must be equal"
        return self

# %% ../../nbs/nets/13_perceiver_3d.ipynb 7
def unfold_with_rollover_1d(x: torch.Tensor, window_size: int | None, stride: int | None):
    # x: (b, T, dim)
    if window_size is None or stride is None:
        return x.unsqueeze(0)
    total_len = x.shape[1]
    num_windows = (total_len + stride - 1) // stride  # Number of windows needed
    pad_len = max(0, window_size + (num_windows - 1) * stride - total_len)  # How many elements are missing
    if pad_len > 0:
        x = torch.cat([x, x[:, :pad_len]], dim=1)  # Rollover padding
    x = x.unfold(1, window_size, stride)
    x = rearrange(x, "b num_windows dim window_size -> num_windows b window_size dim")
    # (num_windows, b, window_size, dim)
    return x

# %% ../../nbs/nets/13_perceiver_3d.ipynb 9
def unfold_with_rollover_3d_with_mask(
    x: torch.Tensor, window_size: tuple[int, int, int] | None, stride: tuple[int, int, int] | None
):
    if window_size is None or stride is None:
        return x.unsqueeze(0)

    b, c, d, h, w = x.shape
    w_d, w_h, w_w = window_size
    s_d, s_h, s_w = stride

    # Calculate number of windows and required padding
    n_d = (d + s_d - 1) // s_d
    n_h = (h + s_h - 1) // s_h
    n_w = (w + s_w - 1) // s_w

    # Calculate padding with rollover
    pad_d = max(0, w_d + (n_d - 1) * s_d - d)
    pad_h = max(0, w_h + (n_h - 1) * s_h - h)
    pad_w = max(0, w_w + (n_w - 1) * s_w - w)

    # Apply rollover padding efficiently
    if pad_d > 0:
        x = torch.cat([x, x[:, :, :pad_d]], dim=2)
    if pad_h > 0:
        x = torch.cat([x, x[:, :, :, :pad_h]], dim=3)
    if pad_w > 0:
        x = torch.cat([x, x[:, :, :, :, :pad_w]], dim=4)

    # Extract patches using stride tricks
    # This approach avoids creating intermediate tensors
    patches = x.unfold(2, w_d, s_d).unfold(3, w_h, s_h).unfold(4, w_w, s_w)

    # Rearrange dimensions using einops for clarity and efficiency
    patches = rearrange(
        patches, "b c d_windows h_windows w_windows w_d w_h w_w -> (d_windows h_windows w_windows) b c w_d w_h w_w"
    )

    # Padded shape
    padded_d, padded_h, padded_w = x.shape[2], x.shape[3], x.shape[4]

    # Generate positional information for each fold
    # Create meshgrid for all window positions
    d_indices = torch.arange(n_d, device=x.device)
    h_indices = torch.arange(n_h, device=x.device)
    w_indices = torch.arange(n_w, device=x.device)

    # Calculate starting indices for each window
    d_starts = d_indices * s_d
    h_starts = h_indices * s_h
    w_starts = w_indices * s_w

    # Generate all possible window starting positions
    d_pos, h_pos, w_pos = torch.meshgrid(d_starts, h_starts, w_starts, indexing="ij")
    positions = torch.stack([d_pos, h_pos, w_pos], dim=-1).reshape(-1, 3)

    # Generate usage count mask
    usage_mask = torch.zeros(padded_d, padded_h, padded_w, device=x.device)

    # For each position, increment the count for all pixels in that window
    for pos in positions:
        pos_d, pos_h, pos_w = pos
        # Handle potential out-of-bounds for the last window
        end_d = min(pos_d + w_d, padded_d)
        end_h = min(pos_h + w_h, padded_h)
        end_w = min(pos_w + w_w, padded_w)

        usage_mask[pos_d:end_d, pos_h:end_h, pos_w:end_w] += 1

    # Crop usage_mask back to original dimensions (without padding)
    usage_mask = usage_mask[:d, :h, :w]

    # Repeat usage mask for batch and channel dimensions
    usage_mask = usage_mask.expand(b, c, d, h, w)

    return patches, positions, usage_mask


def fold_back_3d(
    patches: torch.Tensor,
    positions: torch.Tensor,
    usage_mask: torch.Tensor,
    output_shape: tuple,
    window_size: tuple[int, int, int],
    aggregation_mode: str = "mean",
):
    b, c, d, h, w = output_shape
    w_d, w_h, w_w = window_size

    # Initialize output tensor
    output = torch.zeros(output_shape, dtype=patches.dtype, device=patches.device)

    # Handle case where we want sum instead of mean
    if aggregation_mode == "sum":
        usage_mask = torch.ones_like(usage_mask)

    # Fold each patch back to its position
    for i, pos in enumerate(positions):
        pos_d, pos_h, pos_w = pos
        # Get the patch
        patch = patches[i]

        # Handle window dimensions (might be smaller at edges)
        valid_w_d = min(w_d, d - pos_d) if pos_d < d else 0
        valid_w_h = min(w_h, h - pos_h) if pos_h < h else 0
        valid_w_w = min(w_w, w - pos_w) if pos_w < w else 0

        if valid_w_d <= 0 or valid_w_h <= 0 or valid_w_w <= 0:
            continue

        # Only add the valid portion of the patch
        output[:, :, pos_d : pos_d + valid_w_d, pos_h : pos_h + valid_w_h, pos_w : pos_w + valid_w_w] += patch[
            :, :, :valid_w_d, :valid_w_h, :valid_w_w
        ]

    # Average according to usage count
    if aggregation_mode == "mean":
        # Avoid division by zero
        mask = (usage_mask > 0).float()
        output = output / (usage_mask + (1 - mask))

    return output

# %% ../../nbs/nets/13_perceiver_3d.ipynb 12
class Perceiver3DChannelMapping(nn.Module):
    def __init__(self, config: Perceiver3DChannelMappingConfig = {}, **kwargs):
        super().__init__()

        self.config = Perceiver3DChannelMappingConfig.model_validate(config | kwargs)

        self.in_channels = self.config.in_channels
        self.out_channels = self.config.out_channels

        if isinstance(self.in_channels, int):
            self.in_channels = {self.in_channels}

        self.mappers = nn.ModuleDict()
        for in_channels in self.in_channels:
            self.mappers[str(in_channels)] = nn.Conv3d(in_channels, self.out_channels, kernel_size=1)

    def forward(self, x: torch.Tensor):
        # x: (b, in_channels, z, y, x)

        in_channels = x.shape[1]
        if in_channels not in self.in_channels:
            raise ValueError(f"Input channels {in_channels} not in {self.in_channels}")

        mapper = self.mappers[str(in_channels)]
        x = mapper(x)

        return x

# %% ../../nbs/nets/13_perceiver_3d.ipynb 15
class Perceiver3DEncoderEncode(nn.Module):
    def __init__(
        self,
        config: Perceiver3DEncoderEncodeConfig | Perceiver3DEncoderConfig = {},
        channel_mapping: Perceiver3DChannelMapping | None = None,
        checkpointing_level: int = 0,
        **kwargs
    ):
        super().__init__()

        if isinstance(config, Perceiver3DEncoderConfig):
            config = config.encode
        if "encode" in config:
            config = config["encode"]

        self.config = Perceiver3DEncoderEncodeConfig.model_validate(config | kwargs)

        dim = self.config.dim
        num_latent_tokens = self.config.num_latent_tokens
        num_layers = self.config.num_layers

        self.latent_tokens = nn.Parameter(torch.empty(num_latent_tokens, dim), requires_grad=True)
        nn.init.xavier_uniform_(self.latent_tokens)

        self.channel_mapping = channel_mapping

        self.cross_attention = nn.ModuleList(
            [
                Attention1DWithMLP(self.config.model_dump(), checkpointing_level=checkpointing_level)
                for _ in range(num_layers)
            ]
        )

        self.checkpointing_level1 = ActivationCheckpointing(1, checkpointing_level)
        self.checkpointing_level4 = ActivationCheckpointing(4, checkpointing_level)

    def _forward(
        self,
        x: torch.Tensor | list[torch.Tensor],
        sliding_window: int | None = None,
        sliding_stride: int | None = None,
        return_all: bool = False,
    ) -> torch.Tensor | dict[str, torch.Tensor]:
        # x: [(b, in_channels, z, y, x), ...]

        # Prepare keys and values
        def prepare_keys_values(x: torch.Tensor | list[torch.Tensor]):
            if not isinstance(x, list):
                x = [x]
            # x is now a list of tensors
            if self.channel_mapping is None:
                kv = x
            else:
                kv = []
                for i in range(len(x)):
                    mapped = self.channel_mapping(x[i])  # modifying in-place leads to errors when checkpointing
                    mapped = rearrange(mapped, "b d z y x -> b (z y x) d")
                    kv.append(mapped)
            kv = torch.cat(kv, dim=1)
            return kv

        kv = self.checkpointing_level1(prepare_keys_values, x)
        # (b, num_kv_tokens, dim)

        # Prepare queries
        b = kv.shape[0]
        q = repeat(self.latent_tokens, "t d -> b t d", b=b)
        # (b, num_latent_tokens, dim)

        # Prepare sliding window
        kv_windows = unfold_with_rollover_1d(kv, sliding_window, sliding_stride)
        # (num_windows, b, window_size, dim)

        # Perform attention
        embeddings = []
        for cross_attention_layer in self.cross_attention:
            embedding = torch.zeros_like(q)
            for kv_window in kv_windows:
                embedding_window = cross_attention_layer(q, kv_window, kv_window)
                embedding = embedding + embedding_window
            q = embedding  # To pass to the next layer
            embeddings.append(embedding)
        # (b, num_latent_tokens, dim)

        return_value = embeddings[-1]
        if return_all:
            return_value = {
                "embeddings": return_value,
                "all_embeddings": embeddings,
            }
        return return_value

    def forward(
        self,
        x: torch.Tensor | list[torch.Tensor],
        sliding_window: int | None = None,  # Sliding window may be beneficial during inference time
        sliding_stride: int | None = None,
        return_all: bool = False,
    ):
        return self.checkpointing_level4(self._forward, x, sliding_window, sliding_stride, return_all)

# %% ../../nbs/nets/13_perceiver_3d.ipynb 18
class Perceiver3DEncoderProcess(nn.Module):
    def __init__(
        self,
        config: Perceiver3DEncoderProcessConfig | Perceiver3DEncoderConfig = {},
        checkpointing_level: int = 0,
        **kwargs
    ):
        super().__init__()

        if isinstance(config, Perceiver3DEncoderConfig):
            config = config.process
        if "process" in config:
            config = config["process"]

        self.config = Perceiver3DEncoderProcessConfig.model_validate(config | kwargs)

        num_layers = self.config.num_layers

        self.self_attention = nn.ModuleList(
            [
                Attention1DWithMLP(self.config.model_dump(), checkpointing_level=checkpointing_level)
                for _ in range(num_layers)
            ]
        )

        self.checkpointing_level4 = ActivationCheckpointing(4, checkpointing_level)

    def _forward(self, qkv, return_all: bool = False) -> torch.Tensor | dict[str, torch.Tensor]:
        # qkv: (b, num_tokens, dim)

        embeddings = []
        embedding = qkv
        for self_attention_layer in self.self_attention:
            embedding = self_attention_layer(embedding, embedding, embedding)
            embeddings.append(embedding)
        # (b, num_tokens, dim)

        return_value = embeddings[-1]
        if return_all:
            return_value = {
                "embeddings": return_value,
                "all_embeddings": embeddings,
            }

        return return_value

    def forward(self, q: torch.Tensor, return_all: bool = False):
        return self.checkpointing_level4(self._forward, q, return_all)

# %% ../../nbs/nets/13_perceiver_3d.ipynb 20
class Perceiver3DEncoder(nn.Module, PyTorchModelHubMixin):
    def __init__(
        self,
        config: Perceiver3DEncoderConfig = {},
        channel_mapping: Perceiver3DChannelMapping | None = None,
        checkpointing_level: int = 0,
        **kwargs,
    ):
        super().__init__()

        self.config = Perceiver3DEncoderConfig.model_validate(config | kwargs)

        self.encode = Perceiver3DEncoderEncode(config.encode, channel_mapping, checkpointing_level)
        self.process = Perceiver3DEncoderProcess(config.process, checkpointing_level)

        self.checkpointing_level5 = ActivationCheckpointing(5, checkpointing_level)

    def _forward(
        self,
        x,
        sliding_window: int | None = None,
        sliding_stride: int | None = None,
        return_all: bool = False,
    ) -> torch.Tensor | dict[str, torch.Tensor]:
        # x: (b, in_channels, z, y, x)

        return_value = {}

        encode_embeddings = self.encode(x, sliding_window, sliding_stride, return_all=True)["all_embeddings"]
        return_value["encode_embeddings"] = encode_embeddings
        embeddings = encode_embeddings[-1]
        # (b, num_tokens, dim)

        process_embeddings = self.process(embeddings, return_all=True)["all_embeddings"]
        return_value["process_embeddings"] = process_embeddings
        embeddings = process_embeddings[-1]

        return_value["embeddings"] = embeddings

        if not return_all:
            return_value = embeddings

        return return_value

    def forward(
        self,
        x: torch.Tensor,
        sliding_window: int | None = None,
        sliding_stride: int | None = None,
        return_all: bool = False,
    ):
        return self.checkpointing_level5(self._forward, x, sliding_window, sliding_stride, return_all)

# %% ../../nbs/nets/13_perceiver_3d.ipynb 23
class Perceiver3DDecoder(nn.Module, PyTorchModelHubMixin):
    def __init__(
        self,
        config: Perceiver3DDecoderConfig | Perceiver3DConfig = {},
        position_embeddings: AbsolutePositionEmbeddings3D = None,
        checkpointing_level: int = 0,
        **kwargs,
    ):
        super().__init__()

        if isinstance(config, Perceiver3DConfig):
            config = config.decode
        if "decode" in config:
            config = config["decode"]

        self.config = Perceiver3DDecoderConfig.model_validate(config | kwargs)

        dim = self.config.dim
        num_layers = self.config.num_layers

        self.empty_token = nn.Parameter(torch.randn(dim, 1) * 0.02, requires_grad=True)
        # Initialized with gaussian for robust training stability

        self.position_embeddings = position_embeddings

        self.cross_attention = nn.ModuleList(
            [
                Attention1DWithMLP(config.model_dump(), checkpointing_level=checkpointing_level)
                for _ in range(num_layers)
            ]
        )

        self.channel_mapping = Perceiver3DChannelMapping(in_channels=dim, out_channels=self.config.out_channels)

        self.checkpointing_level4 = ActivationCheckpointing(4, checkpointing_level)

    def _forward(
        self,
        kv,
        out_shape: tuple[int, int, int],
        sliding_window: tuple[int, int, int] | None = None,
        sliding_stride: tuple[int, int, int] | None = None,
        crop_offsets: torch.Tensor = None,
        return_all: bool = False,
    ) -> torch.Tensor | dict[str, torch.Tensor]:
        # kv: (b, num_tokens, dim)

        b = kv.shape[0]

        q = repeat(
            self.empty_token,
            "d 1 -> b d z y x",
            b=b,
            z=out_shape[0],
            y=out_shape[1],
            x=out_shape[2],
        )
        # (b, dim, z, y, x)

        if self.position_embeddings is not None:
            q = q + self.position_embeddings(
                batch_size=b, dim=q.shape[1], grid_size=out_shape, device=q.device, crop_offsets=crop_offsets
            )

        # Prepare sliding windows  # TODO
        # q_windows, _, _ = unfold_with_rollover_3d_with_mask(q, sliding_window, sliding_stride)
        # (num_windows, b, dim, window_size_z, window_size_y, window_size_x)

        # Perform attention
        # outputs = []
        # for cross_attention_layer in self.cross_attention:
        #     output = torch.zeros_like(q)
        #     for q_window in q_windows:
        #         output_window = cross_attention_layer(q_window, kv, kv)
        #         ...
        q = rearrange(q, "b d z y x -> b (z y x) d")
        # (b, num_output_tokens, dim)
        outputs = [q]
        for cross_attention_layer in self.cross_attention:
            q = outputs[-1]
            outputs.append(cross_attention_layer(q, kv, kv))
        # (b, num_output_tokens, dim)

        output = outputs[-1]
        output = rearrange(
            output,
            "b (z y x) d -> b d z y x",
            z=out_shape[0],
            y=out_shape[1],
            x=out_shape[2],
        )
        # (b, dim, z, y, x)

        output = self.channel_mapping(output)
        # (b, out_channels, z, y, x)

        return_value = output
        if return_all:
            return_value = {
                "output": output,
                "all_outputs": outputs,
            }

        return return_value

    # def _forward(
    #     self,
    #     kv,
    #     out_shape: tuple[int, int, int],
    #     sliding_window: tuple[int, int, int] | None = None,
    #     sliding_stride: tuple[int, int, int] | None = None,
    #     crop_offset: torch.Tensor = None,
    #     return_all: bool = False,
    # ) -> torch.Tensor | dict[str, torch.Tensor]:
    #     # kv: (b, num_tokens, dim)

    #     b = kv.shape[0]

    #     q = repeat(
    #         self.empty_token,
    #         "d 1 -> b d z y x",
    #         b=b,
    #         z=out_shape[0],
    #         y=out_shape[1],
    #         x=out_shape[2],
    #     )
    #     # (b, dim, z, y, x)

    #     if self.position_embeddings is not None:
    #         q = q + self.position_embeddings(
    #             batch_size=b, grid_size=out_shape, device=q.device, crop_offset=crop_offset
    #         )

    #     # Conditional execution based on sliding window parameters
    #     if sliding_window is not None and sliding_stride is not None:
    #         # Apply sliding window processing
    #         q_windows, positions, usage_mask = unfold_with_rollover_3d_with_mask(q, sliding_window, sliding_stride)
    #         # q_windows: (num_windows, b, dim, window_size_z, window_size_y, window_size_x)

    #         # Process each window with attention
    #         num_windows = q_windows.shape[0]
    #         window_shape = q_windows.shape[-3:]  # (window_size_z, window_size_y, window_size_x)

    #         # Initialize output tensor for each layer's output
    #         outputs = []
    #         current_windows = q_windows

    #         # Apply cross-attention to each window sequentially through all layers
    #         for layer_idx, cross_attention_layer in enumerate(self.cross_attention):
    #             processed_windows = torch.zeros_like(current_windows)

    #             for window_idx in range(num_windows):
    #                 # Extract current window
    #                 window = current_windows[window_idx]  # (b, dim, w_z, w_y, w_x)

    #                 # Reshape for attention operation
    #                 flat_window = rearrange(window, "b d z y x -> b (z y x) d")

    #                 # Apply cross-attention
    #                 attended_window = cross_attention_layer(flat_window, kv, kv)

    #                 # Reshape back to 3D
    #                 processed_window = rearrange(
    #                     attended_window,
    #                     "b (z y x) d -> b d z y x",
    #                     z=window_shape[0],
    #                     y=window_shape[1],
    #                     x=window_shape[2],
    #                 )

    #                 # Store processed window
    #                 processed_windows[window_idx] = processed_window

    #             # Update current windows for next layer
    #             current_windows = processed_windows

    #             # Fold windows back to full volume for this layer's output
    #             folded_output = fold_back_3d(
    #                 processed_windows, positions, usage_mask, (b, q.shape[1], out_shape[0], out_shape[1], out_shape[2])
    #             )

    #             # Store layer output for return_all
    #             flat_output = rearrange(
    #                 folded_output, "b d z y x -> b (z y x) d", z=out_shape[0], y=out_shape[1], x=out_shape[2]
    #             )
    #             outputs.append(flat_output)

    #         # Final output is from the last layer
    #         output = folded_output

    #     else:
    #         # Original processing without sliding windows
    #         q = rearrange(q, "b d z y x -> b (z y x) d")
    #         # (b, num_output_tokens, dim)
    #         outputs = [q]
    #         for cross_attention_layer in self.cross_attention:
    #             q = outputs[-1]
    #             outputs.append(cross_attention_layer(q, kv, kv))
    #         # (b, num_output_tokens, dim)

    #         output = outputs[-1]
    #         output = rearrange(
    #             output,
    #             "b (z y x) d -> b d z y x",
    #             z=out_shape[0],
    #             y=out_shape[1],
    #             x=out_shape[2],
    #         )
    #         # (b, dim, z, y, x)

    #     # Apply channel mapping to get final output
    #     output = self.channel_mapping(output)
    #     # (b, out_channels, z, y, x)

    #     return_value = output
    #     if return_all:
    #         return_value = {
    #             "output": output,
    #             "all_outputs": outputs,
    #         }

    #     return return_value

    def forward(
        self,
        kv: torch.Tensor,
        out_shape: tuple[int, int, int],
        sliding_window: tuple[int, int, int] | None = None,
        sliding_stride: tuple[int, int, int] | None = None,
        crop_offsets: torch.Tensor = None,
        return_all: bool = False,
    ):
        return self.checkpointing_level4(
            self._forward, kv, out_shape, sliding_window, sliding_stride, crop_offsets, return_all
        )
