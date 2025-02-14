# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/nets/03_swinv2_3d.ipynb.

# %% auto 0
__all__ = ['SwinV23DStageConfig', 'SwinV23DConfig', 'get_coords_grid', 'SwinV23DMHSA', 'SwinV23DLayerMLP', 'SwinV23DLayer',
           'SwinV23DBlock', 'SwinV23DPatchMerging', 'SwinV23DPatchSplitting', 'SwinV23DStage', 'SwinV23DEncoder',
           'SwinV23DDecoder', 'SwinV23DPatchEmbeddings', 'get_3d_position_embeddings',
           'embed_spacings_in_position_embeddings', 'SwinV23DEmbeddings', 'SwinV23DModel',
           'SwinV23DReconstructionDecoder', 'SwinV23DMIM', 'SwinV23DSimMIM', 'SwinV23DVAEMIM']

# %% ../../nbs/nets/03_swinv2_3d.ipynb 2
import numpy as np
import torch
import torch.nn.functional as F
from einops import rearrange, repeat
from huggingface_hub import PyTorchModelHubMixin
from munch import munchify
from pydantic import BaseModel, model_validator
from torch import nn

# %% ../../nbs/nets/03_swinv2_3d.ipynb 4
class SwinV23DStageConfig(BaseModel):
    depth: int
    window_size: tuple[int, int, int]

    num_heads: int = 4
    intermediate_ratio: int = 4
    layer_norm_eps: float = 1e-6
    use_relative_position_bias: bool = False
    patch_merging: dict | None = None
    patch_splitting: dict | None = None

    attn_drop_prob: float = 0.0
    proj_drop_prob: float = 0.0
    mlp_drop_prob: float = 0.0

    _in_dim: int
    _in_patch_size: tuple[int, int, int]
    _attention_dim: int = None
    _out_dim: int
    _out_patch_size: tuple[int, int, int]

    @model_validator(mode="after")
    def validate_after(self):
        if isinstance(self.patch_merging, dict):
            assert {"merge_window_size", "out_dim_ratio"}.issubset(self.patch_merging), "Missing keys"
        if isinstance(self.patch_splitting, dict):
            assert {"final_window_size", "out_dim_ratio"}.issubset(self.patch_splitting), "Missing keys"
        return self


class SwinV23DConfig(BaseModel):
    in_channels: int
    dim: int
    patch_size: tuple[int, int, int]
    stages: list[SwinV23DStageConfig]

    image_size: tuple[int, int, int] | None = None  # required for learnable absolute position embeddings
    drop_prob: float = 0.0
    embed_spacing_info: bool = False
    use_absolute_position_embeddings: bool = True
    learnable_absolute_position_embeddings: bool = False

    def populate(self):
        dim = self.dim
        patch_size = self.patch_size

        # Prepare config based on provided values
        for i in range(len(self.stages)):
            stage = self.stages[i]
            stage._in_dim = dim
            stage._in_patch_size = patch_size
            if stage.patch_merging is not None:
                dim *= stage.patch_merging["out_dim_ratio"]
                stage._attention_dim = dim  # attention will happen after merging
                patch_size = tuple(
                    [patch * window for patch, window in zip(patch_size, stage.patch_merging["merge_window_size"])]
                )
            if stage.patch_splitting is not None:
                stage._attention_dim = dim  # attention will happen before splitting
                dim //= stage.patch_splitting["out_dim_ratio"]
                patch_size = tuple(
                    [patch * window for patch, window in zip(patch_size, stage.patch_splitting["final_window_size"])]
                )
            if stage._attention_dim is None:
                stage._attention_dim = dim  # In case it is not yet set
            stage._out_dim = dim
            stage._out_patch_size = patch_size

    @model_validator(mode="after")
    def validate_after(self):
        self.populate()

        # test divisibility of dim with number of attention heads
        for stage in self.stages:
            assert stage._out_dim % stage.num_heads == 0, f'{stage._out_dim} is not divisible by {stage.num_heads}'

        # test population of image_size field iff the absolute position embeddings are relative
        if self.learnable_absolute_position_embeddings:
            assert (
                self.image_size is not None
            ), "Please provide image_size if absolute position embeddings are learnable"

        return self

# %% ../../nbs/nets/03_swinv2_3d.ipynb 8
def get_coords_grid(grid_size):
    d, h, w = grid_size

    grid_d = torch.arange(d, dtype=torch.int32)
    grid_h = torch.arange(h, dtype=torch.int32)
    grid_w = torch.arange(w, dtype=torch.int32)

    grid = torch.meshgrid(grid_d, grid_h, grid_w, indexing="ij")
    grid = torch.stack(grid, axis=0)
    # (3, d, h, w)

    return grid

# %% ../../nbs/nets/03_swinv2_3d.ipynb 9
class SwinV23DMHSA(nn.Module):
    def __init__(
        self,
        dim,
        num_heads,
        window_size,
        use_relative_position_bias,
        attn_drop_prob=0.0,
        proj_drop_prob=0.0,
    ):
        super().__init__()

        assert dim % num_heads == 0, "dimension must be divisible by number of heads"

        self.dim = dim
        self.num_heads = num_heads
        self.window_size = window_size

        self.per_head_dim = int(dim // num_heads)

        self.W_qkv = nn.Linear(dim, 3 * dim)
        self.attn_drop_prob = attn_drop_prob
        self.proj = nn.Linear(dim, dim)
        self.proj_drop = nn.Dropout(proj_drop_prob)

        self.logit_scale = nn.Parameter(torch.log(10 * torch.ones((num_heads, 1, 1))))

        # TODO: Add embed_spacing_info functionality
        self.use_relative_position_bias = use_relative_position_bias
        if use_relative_position_bias:
            self.cpb_mlp = nn.Sequential(
                nn.Linear(3, 512, bias=True), nn.ReLU(inplace=True), nn.Linear(512, num_heads, bias=False)
            )

            relative_limits = (2 * window_size[0] - 1, 2 * window_size[1] - 1, 2 * window_size[2] - 1)

            # Relative coordinates table
            relative_coords_table = get_coords_grid(relative_limits).float()
            for i in range(3):
                relative_coords_table[i] = (relative_coords_table[i] - (window_size[0] - 1)) / (window_size[0] - 1)
            relative_coords_table = rearrange(
                relative_coords_table,
                "three window_size_z window_size_y window_size_x -> window_size_z window_size_y window_size_x three",
            ).contiguous()
            relative_coords_table *= 8  # Normalize to -8, 8
            relative_coords_table = (
                torch.sign(relative_coords_table) * torch.log2(torch.abs(relative_coords_table) + 1.0) / np.log2(8)
            )
            # (window_size_z, window_size_y, window_size_x, 3)
            # Allow moving this to and from cuda whenever required but don't save to state_dict
            self.register_buffer("relative_coords_table", relative_coords_table, persistent=False)

            # Pair-wise relative position index for each token inside the window
            coords = get_coords_grid(window_size)
            coords_flatten = rearrange(
                coords, "three_dimensional d h w -> three_dimensional (d h w)", three_dimensional=3
            )
            relative_coords = coords_flatten[:, :, None] - coords_flatten[:, None, :]
            relative_coords = relative_coords.permute(1, 2, 0).contiguous()
            relative_coords[:, :, 0] += window_size[0] - 1
            relative_coords[:, :, 1] += window_size[1] - 1
            relative_coords[:, :, 2] += window_size[2] - 1
            relative_position_index: torch.Tensor = (
                relative_coords[:, :, 0] * relative_limits[1] * relative_limits[2]
                + relative_coords[:, :, 1] * relative_limits[2]
                + relative_coords[:, :, 2]
            )
            self.relative_position_index = relative_position_index.flatten()
            # (num_patches, num_patches)

    def calculate_relative_position_bias(self):
        # (window_size_z, window_size_y, window_size_x, 3)
        relative_position_bias_table = self.cpb_mlp(self.relative_coords_table)
        # (window_size_z, window_size_y, window_size_x, num_heads)
        relative_position_bias_table = relative_position_bias_table.reshape(-1, self.num_heads)
        # (num_patches, num_heads)
        relative_position_bias = relative_position_bias_table[self.relative_position_index]
        # (num_patches * num_patches, num_heads)
        relative_position_bias = rearrange(
            relative_position_bias,
            "(num_patches1 num_patches2) num_heads -> num_heads num_patches1 num_patches2",
            num_patches1=np.prod(self.window_size),
            num_patches2=np.prod(self.window_size),
            num_heads=self.num_heads,
        ).contiguous()
        # (num_heads, num_patches, num_patches)
        relative_position_bias = 16 * torch.sigmoid(relative_position_bias)
        # (num_heads, num_patches, num_patches)
        return relative_position_bias

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (windowed_b, window_size_z window_size_y window_size_x, dim)
        _, num_patches_z, num_patches_y, num_patches_x, _ = hidden_states.shape

        query, key, value = rearrange(
            self.W_qkv(hidden_states),
            "b nz ny nx (n num_heads d) -> n b num_heads (nz ny nx) d",
            n=3,
            num_heads=self.num_heads,
        )
        # num_patches = window_size_z * window_size_y * window_size_x
        # Each is (windowed_b, num_heads, num_patches, per_head_dim)

        logit_scale = torch.clamp(self.logit_scale, max=np.log(1.0 / 0.01)).exp()

        query_normalized = F.normalize(query, dim=-1)
        key_normalized = F.normalize(key, dim=-1)

        query_normalized_and_scaled = query_normalized * logit_scale  # Scale the query beforehand

        relative_position_bias = None
        if self.use_relative_position_bias:
            relative_position_bias = self.calculate_relative_position_bias()

        context = F.scaled_dot_product_attention(
            query_normalized_and_scaled,
            key_normalized,
            value,
            attn_mask=relative_position_bias,  # Use this as a way to introduce relative position bias
            dropout_p=self.attn_drop_prob,
            is_causal=False,
            scale=1.0,  # Already scaled the vectors
        )
        # (windowed_b, num_heads, num_patches, per_head_dim)

        context = rearrange(
            context,
            "b num_heads (num_patches_z num_patches_y num_patches_x) d -> "
            "b num_patches_z num_patches_y num_patches_x (num_heads d)",
            num_patches_z=num_patches_z,
            num_patches_y=num_patches_y,
            num_patches_x=num_patches_x,
        )
        # (windowed_b, window_size_z window_size_y window_size_x, dim)

        context = self.proj(context)
        context = self.proj_drop(context)
        # (windowed_b, window_size_z window_size_y window_size_x, dim)

        return context

# %% ../../nbs/nets/03_swinv2_3d.ipynb 11
class SwinV23DLayerMLP(nn.Module):
    def __init__(self, dim, intermediate_ratio, dropout_prob=0.0):
        super().__init__()
        self.dense1 = nn.Linear(dim, dim * intermediate_ratio)
        self.act = nn.GELU()
        self.dense2 = nn.Linear(dim * intermediate_ratio, dim)
        self.dropout = nn.Dropout(dropout_prob)

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (windowed_b, window_size_z window_size_y window_size_x, dim)
        hidden_states = self.dense1(hidden_states)
        hidden_states = self.act(hidden_states)
        hidden_states = self.dense2(hidden_states)
        hidden_states = self.dropout(hidden_states)
        return hidden_states

# %% ../../nbs/nets/03_swinv2_3d.ipynb 13
class SwinV23DLayer(nn.Module):
    def __init__(
        self,
        dim,
        num_heads,
        intermediate_ratio,
        layer_norm_eps,
        window_size,
        use_relative_position_bias,
        attn_drop_prob=0.0,
        proj_drop_prob=0.0,
        mlp_drop_prob=0.0,
    ):
        super().__init__()

        self.window_size = window_size

        self.mhsa = SwinV23DMHSA(
            dim, num_heads, window_size, use_relative_position_bias, attn_drop_prob, proj_drop_prob
        )
        self.layernorm1 = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.mlp = SwinV23DLayerMLP(dim, intermediate_ratio, mlp_drop_prob)
        self.layernorm2 = nn.LayerNorm(dim, eps=layer_norm_eps)

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)
        _, num_patches_z, num_patches_y, num_patches_x, _ = hidden_states.shape

        # Perform windowing
        window_size_z, window_size_y, window_size_x = self.window_size
        num_windows_z, num_windows_y, num_windows_x = (
            num_patches_z // window_size_z,
            num_patches_y // window_size_y,
            num_patches_x // window_size_x,
        )
        hidden_states = rearrange(
            hidden_states,
            "b (num_windows_z window_size_z) (num_windows_y window_size_y) (num_windows_x window_size_x) dim -> "
            "(b num_windows_z num_windows_y num_windows_x) window_size_z window_size_y window_size_x dim ",
            num_windows_z=num_windows_z,
            num_windows_y=num_windows_y,
            num_windows_x=num_windows_x,
            window_size_z=window_size_z,
            window_size_y=window_size_y,
            window_size_x=window_size_x,
        )

        res_connection1 = hidden_states
        # (windowed_b, window_size_z window_size_y window_size_x, dim)

        hidden_states = self.mhsa(hidden_states)
        hidden_states = self.layernorm1(hidden_states)
        # (windowed_b, window_size_z window_size_y window_size_x, dim)

        res_connection2 = hidden_states + res_connection1
        # (windowed_b, window_size_z window_size_y window_size_x, dim)

        hidden_states = self.mlp(res_connection2)
        hidden_states = self.layernorm2(hidden_states)
        # (windowed_b, window_size_z window_size_y window_size_x, dim)

        hidden_states = hidden_states + res_connection2
        # (windowed_b, window_size_z window_size_y window_size_x, dim)

        # Undo windowing
        output = rearrange(
            hidden_states,
            "(b num_windows_z num_windows_y num_windows_x) window_size_z window_size_y window_size_x dim -> "
            "b (num_windows_z window_size_z) (num_windows_y window_size_y) (num_windows_x window_size_x) dim",
            num_windows_z=num_windows_z,
            num_windows_y=num_windows_y,
            num_windows_x=num_windows_x,
            window_size_z=window_size_z,
            window_size_y=window_size_y,
            window_size_x=window_size_x,
        )

        return output

# %% ../../nbs/nets/03_swinv2_3d.ipynb 16
class SwinV23DBlock(nn.Module):
    def __init__(self, stage_config):
        super().__init__()

        self.stage_config = stage_config
        self.w_layer = SwinV23DLayer(
            stage_config._attention_dim,
            stage_config.num_heads,
            stage_config.intermediate_ratio,
            stage_config.layer_norm_eps,
            stage_config.window_size,
            stage_config.use_relative_position_bias,
            stage_config.attn_drop_prob,
            stage_config.proj_drop_prob,
            stage_config.mlp_drop_prob,
        )
        self.sw_layer = SwinV23DLayer(
            stage_config._attention_dim,
            stage_config.num_heads,
            stage_config.intermediate_ratio,
            stage_config.layer_norm_eps,
            stage_config.window_size,
            stage_config.use_relative_position_bias,
            stage_config.attn_drop_prob,
            stage_config.proj_drop_prob,
            stage_config.mlp_drop_prob,
        )

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)

        layer_outputs = []

        # First layer
        hidden_states = self.w_layer(hidden_states)
        # (b, num_patches_z, num_patches_y, num_patches_x, dim)

        layer_outputs.append(hidden_states)

        # Shift windows
        window_size_z, window_size_y, window_size_x = self.stage_config.window_size
        shifts = (window_size_z // 2, window_size_y // 2, window_size_x // 2)
        hidden_states = torch.roll(hidden_states, shifts=shifts, dims=(1, 2, 3))
        # (b, num_patches_z, num_patches_y, num_patches_x, dim)

        # Second layer
        hidden_states = self.sw_layer(hidden_states)
        # (b, num_patches_z, num_patches_y, num_patches_x, dim)

        # Reverse window shift
        shifts = tuple(-shift for shift in shifts)
        hidden_states = torch.roll(hidden_states, shifts=shifts, dims=(1, 2, 3))
        # (b, num_patches_z, num_patches_y, num_patches_x, dim)

        layer_outputs.append(hidden_states)

        return hidden_states, layer_outputs

# %% ../../nbs/nets/03_swinv2_3d.ipynb 18
class SwinV23DPatchMerging(nn.Module):
    def __init__(self, merge_window_size, in_dim, out_dim):
        super().__init__()

        self.merge_window_size = merge_window_size

        in_dim = in_dim * np.prod(merge_window_size)
        self.layer_norm = nn.LayerNorm(in_dim)
        self.proj = nn.Linear(in_dim, out_dim)

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)

        window_size_z, window_size_y, window_size_x = self.merge_window_size

        hidden_states = rearrange(
            hidden_states,
            "b (new_num_patches_z window_size_z) (new_num_patches_y window_size_y) (new_num_patches_x window_size_x) dim -> "
            "b new_num_patches_z new_num_patches_y new_num_patches_x (window_size_z window_size_y window_size_x dim)",
            window_size_z=window_size_z,
            window_size_y=window_size_y,
            window_size_x=window_size_x,
        )

        hidden_states = self.layer_norm(hidden_states)
        hidden_states = self.proj(hidden_states)
        return hidden_states

# %% ../../nbs/nets/03_swinv2_3d.ipynb 20
class SwinV23DPatchSplitting(nn.Module):  # This is a self-implemented class and is not part of the paper.
    def __init__(self, final_window_size, in_dim, out_dim):
        super().__init__()

        self.final_window_size = final_window_size

        out_dim = out_dim * np.prod(final_window_size)
        self.layer_norm = nn.LayerNorm(in_dim)
        self.proj = nn.Linear(in_dim, out_dim)

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)

        hidden_states = self.layer_norm(hidden_states)
        hidden_states = self.proj(hidden_states)

        window_size_z, window_size_y, window_size_x = self.final_window_size

        hidden_states = rearrange(
            hidden_states,
            "b num_patches_z num_patches_y num_patches_x (window_size_z window_size_y window_size_x dim) -> "
            "b (num_patches_z window_size_z) (num_patches_y window_size_y) (num_patches_x window_size_x) dim",
            window_size_z=window_size_z,
            window_size_y=window_size_y,
            window_size_x=window_size_x,
        )

        return hidden_states

# %% ../../nbs/nets/03_swinv2_3d.ipynb 22
class SwinV23DStage(nn.Module):
    def __init__(self, stage_config):
        super().__init__()

        self.config = stage_config

        self.patch_merging = None
        if stage_config.patch_merging is not None:
            self.patch_merging = SwinV23DPatchMerging(
                stage_config.patch_merging["merge_window_size"],
                stage_config._in_dim,
                stage_config._attention_dim,
            )

        self.blocks = nn.ModuleList(
            [SwinV23DBlock(stage_config) for _ in range(stage_config.depth)],
        )

        self.patch_splitting = None
        if stage_config.patch_splitting is not None:  # This has been implemented to create a Swin-based decoder
            self.patch_splitting = SwinV23DPatchSplitting(
                stage_config.patch_splitting["final_window_size"],
                stage_config._attention_dim,
                stage_config._out_dim,
            )

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)

        if self.patch_merging:
            hidden_states = self.patch_merging(hidden_states)
            # (b, new_num_patches_z, new_num_patches_y, new_num_patches_x, new_dim)

        layer_outputs = []
        for layer_module in self.blocks:
            hidden_states, _layer_outputs = layer_module(hidden_states)
            # (b, new_num_patches_z, new_num_patches_y, new_num_patches_x, new_dim)
            layer_outputs.extend(_layer_outputs)

        if self.patch_splitting:
            hidden_states = self.patch_splitting(hidden_states)
            # (b, new_num_patches_z, new_num_patches_y, new_num_patches_x, new_dim)

        return hidden_states, layer_outputs

# %% ../../nbs/nets/03_swinv2_3d.ipynb 26
class SwinV23DEncoder(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config):
        super().__init__()

        for stage_config in config.stages:
            if stage_config.patch_splitting is not None:
                assert (
                    stage_config.patch_merging is not None
                ), "SwinV23DEncoder is not for decoding (mid blocks are ok)."

        self.stages = nn.ModuleList([SwinV23DStage(stage_config) for stage_config in config.stages])

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)

        stage_outputs, layer_outputs = [], []
        for stage_module in self.stages:
            hidden_states, _layer_outputs = stage_module(hidden_states)
            # (b, new_num_patches_z, new_num_patches_y, new_num_patches_x, dim)

            stage_outputs.append(hidden_states)
            layer_outputs.extend(_layer_outputs)

        return hidden_states, stage_outputs, layer_outputs

# %% ../../nbs/nets/03_swinv2_3d.ipynb 29
class SwinV23DDecoder(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config):
        super().__init__()

        for stage_config in config.stages:
            if stage_config.patch_merging is not None:
                assert (
                    stage_config.patch_splitting is not None
                ), "SwinV23DDecoder is not for encoding (mid blocks are ok)."

        self.stages = nn.ModuleList([SwinV23DStage(stage_config) for stage_config in config.stages])

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)

        stage_outputs, layer_outputs = [], []
        for stage_module in self.stages:
            hidden_states, _layer_outputs = stage_module(hidden_states)
            # (b, new_num_patches_z, new_num_patches_y, new_num_patches_x, dim)

            stage_outputs.append(hidden_states)
            layer_outputs.extend(_layer_outputs)

        return hidden_states, stage_outputs, layer_outputs

# %% ../../nbs/nets/03_swinv2_3d.ipynb 33
class SwinV23DPatchEmbeddings(nn.Module):
    def __init__(self, config):
        super().__init__()

        patch_size = config.patch_size
        num_channels = config.in_channels
        dim = config.dim

        self.patch_embeddings = nn.Conv3d(
            in_channels=num_channels,
            out_channels=dim,
            kernel_size=patch_size,
            stride=patch_size,
        )

    def forward(self, pixel_values: torch.Tensor):
        # pixel_values: (b, c, z, y, x)

        embeddings = self.patch_embeddings(pixel_values)
        # (b, dim, num_patches_z, num_patches_y, num_patches_x)

        return embeddings

# %% ../../nbs/nets/03_swinv2_3d.ipynb 36
def get_3d_position_embeddings(embedding_size, grid_size, patch_size=(1, 1, 1)):
    if embedding_size % 6 != 0:
        raise ValueError("embed_dim must be divisible by 6")

    grid = get_coords_grid(grid_size)
    # (3, d, h, w)

    grid = rearrange(grid, "x d h w -> x 1 d h w")
    # (3, 1, d, h, w)

    omega = torch.arange(embedding_size // 6, dtype=torch.float32)
    omega /= embedding_size / 6.0
    omega = 1.0 / 10000**omega
    # (d // 6)

    patch_multiplier = torch.Tensor(patch_size) / min(patch_size)

    position_embeddings = []
    for i, grid_subset in enumerate(grid):
        grid_subset = grid_subset.reshape(-1)
        out = torch.einsum("m,d->md", grid_subset, omega)

        emb_sin = torch.sin(out)
        emb_cos = torch.cos(out)

        emb = torch.cat([emb_sin, emb_cos], axis=1) * patch_multiplier[i]
        position_embeddings.append(emb)

    position_embeddings = torch.cat(position_embeddings, axis=1)
    # (embedding_size, d * h * w)
    d, h, w = grid_size
    position_embeddings = rearrange(position_embeddings, "(d h w) e -> 1 e d h w", d=d, h=h, w=w)
    # (1, embedding_size, d, h, w)

    return position_embeddings

# %% ../../nbs/nets/03_swinv2_3d.ipynb 37
def embed_spacings_in_position_embeddings(embeddings: torch.Tensor, spacings: torch.Tensor):
    assert spacings is not None, "spacing information cannot be None"
    assert spacings.ndim == 2, "Please provide spacing information for each batch element"
    _, embedding_size, _, _, _ = embeddings.shape
    assert embedding_size % 3 == 0, "To embed spacing info, the embedding size must be divisible by 3"
    embeddings = embeddings * repeat(spacings, f"B S -> B (S {int(embedding_size / 3)}) 1 1 1", S=3)

    return embeddings

# %% ../../nbs/nets/03_swinv2_3d.ipynb 38
class SwinV23DEmbeddings(nn.Module):
    def __init__(self, config):
        super().__init__()

        self.config = config

        dim = config.dim

        self.patch_embeddings = SwinV23DPatchEmbeddings(config)
        self.layer_norm = nn.LayerNorm(dim)

        self.absolute_position_embeddings = None
        if config.use_absolute_position_embeddings:
            self.absolute_position_embeddings = {}  # embeddings will be cached in this for every input image size
            if config.learnable_absolute_position_embeddings:
                grid_size = self._get_grid_size(config.image_size)
                self.absolute_position_embeddings[config.image_size] = nn.Parameter(
                    torch.randn(1, dim, grid_size[0], grid_size[1], grid_size[2])
                )

    def _get_grid_size(self, image_size):
        grid_size = (
            image_size[0] // self.config.patch_size[0],
            image_size[1] // self.config.patch_size[1],
            image_size[2] // self.config.patch_size[2],
        )
        return grid_size

    def forward(
        self,
        pixel_values: torch.Tensor,
        spacings: torch.Tensor = None,
        mask_patches: torch.Tensor = None,
        mask_token: torch.Tensor = None,
    ):
        # pixel_values: (b, c, z, y, x)

        embeddings = self.patch_embeddings(pixel_values)
        # (b, dim, num_patches_z, num_patches_y, num_patches_x)
        embeddings = rearrange(embeddings, "b d nz ny nx -> b nz ny nx d")
        embeddings = self.layer_norm(embeddings)
        embeddings = rearrange(embeddings, "b nz ny nx d -> b d nz ny nx")
        # (b, dim, num_patches_z, num_patches_y, num_patches_x)

        if mask_patches is not None:
            # mask_patches (binary mask): (b, num_patches_z, num_patches_y, num_patches_x)
            # mask_token: (1, dim, 1, 1, 1)
            mask_patches = repeat(mask_patches, "b z y x -> b d z y x", d=embeddings.shape[1])
            embeddings = (embeddings * (1 - mask_patches)) + (mask_patches * mask_token)

        if self.absolute_position_embeddings is not None:
            image_size = tuple(pixel_values.shape[-3:])

            if image_size not in self.absolute_position_embeddings:
                grid_size = self._get_grid_size(image_size)
                self.absolute_position_embeddings[image_size] = get_3d_position_embeddings(
                    self.config.dim, grid_size, self.config.patch_size
                )

            absolute_position_embeddings = self.absolute_position_embeddings[image_size].to(embeddings.device)
            # (1, dim, num_patches_z, num_patches_y, num_patches_x)
            if self.config.embed_spacing_info:
                absolute_position_embeddings = embed_spacings_in_position_embeddings(
                    absolute_position_embeddings, spacings
                )
                # (b, dim, num_patches_z, num_patches_y, num_patches_x)

            embeddings = embeddings + absolute_position_embeddings
            # (b, dim, num_patches_z, num_patches_y, num_patches_x)

        return embeddings

# %% ../../nbs/nets/03_swinv2_3d.ipynb 42
class SwinV23DModel(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config):
        super().__init__()

        self.embeddings = SwinV23DEmbeddings(config)
        self.pos_drop = nn.Dropout(config.drop_prob)
        self.encoder = SwinV23DEncoder(config)

    def forward(
        self,
        pixel_values: torch.Tensor,
        spacings: torch.Tensor = None,
        mask_patches: torch.Tensor = None,
        mask_token: torch.Tensor = None,
    ):
        # pixel_values: (b, c, z, y, x)
        # spacings: (b, 3)
        # mask_patches: (num_patches_z, num_patches_y, num_patches_x)

        embeddings = self.embeddings(pixel_values, spacings, mask_patches, mask_token)
        embeddings = self.pos_drop(embeddings)
        # (b, dim, num_patches_z, num_patches_y, num_patches_x)

        embeddings = rearrange(embeddings, "b e nz ny nx -> b nz ny nx e")
        # (b, num_patches_z, num_patches_y, num_patches_x, dim)

        encoded, stage_outputs, layer_outputs = self.encoder(embeddings)
        # encoded: (b, new_num_patches_z, new_num_patches_y, new_num_patches_x, dim)
        # stage_outputs, layer_outputs: list of (b, some_num_patches_z, some_num_patches_y, some_num_patches_x, dim)

        encoded = rearrange(encoded, "b nz ny nx d -> b d nz ny nx")
        # (b, dim, new_num_patches_z, new_num_patches_y, new_num_patches_x)

        for i in range(len(stage_outputs)):
            stage_outputs[i] = rearrange(stage_outputs[i], "b nz ny nx d -> b d nz ny nx")
            # (b, dim, some_num_patches_z, some_num_patches_y, some_num_patches_x)

        for i in range(len(layer_outputs)):
            layer_outputs[i] = rearrange(layer_outputs[i], "b nz ny nx d -> b d nz ny nx")
            # (b, dim, some_num_patches_z, some_num_patches_y, some_num_patches_x)

        return encoded, stage_outputs, layer_outputs

# %% ../../nbs/nets/03_swinv2_3d.ipynb 45
class SwinV23DReconstructionDecoder(nn.Module):
    def __init__(self, config):
        super().__init__()

        self.in_channels = config.in_channels

        dim = config.dim
        patch_size = config.patch_size

        out_dim = np.prod(patch_size) * self.in_channels
        self.final_patch_size = patch_size

        self.decoder = nn.Conv3d(dim, out_dim, kernel_size=1)

    def forward(self, encodings: torch.Tensor):
        # encodings: (b, dim, num_patches_z, num_patches_y, num_patches_x)

        decoded = self.decoder(encodings)
        # (b, new_dim, num_patches_z, num_patches_y, num_patches_x)

        decoded = rearrange(
            decoded,
            "b (c pz py px) nz ny nx -> b c (nz pz) (ny py) (nx px)",
            c=self.in_channels,
            pz=self.final_patch_size[0],
            py=self.final_patch_size[1],
            px=self.final_patch_size[2],
        )
        # (b, c, z, y, x)

        return decoded

# %% ../../nbs/nets/03_swinv2_3d.ipynb 47
class SwinV23DMIM(nn.Module):
    def __init__(self, swin_config, decoder_config, mim_config):
        super().__init__()

        self.swin_config = swin_config
        self.decoder_config = decoder_config
        self.mim_config = mim_config

        self.swin = SwinV23DModel(swin_config)
        self.decoder = SwinV23DReconstructionDecoder(decoder_config)

        self.mask_token = nn.Parameter(torch.randn(1, swin_config.dim, 1, 1, 1))

    def _get_grid_size(self, image_size):
        grid_size = (
            image_size[0] // self.swin_config.patch_size[0],
            image_size[1] // self.swin_config.patch_size[1],
            image_size[2] // self.swin_config.patch_size[2],
        )
        return grid_size

    def mask_image(self, pixel_values: torch.Tensor):
        b = pixel_values.shape[0]

        mask_ratio = self.mim_config["mask_ratio"]
        mask_grid_size = self.mim_config["mask_grid_size"]
        num_patches = np.prod(mask_grid_size)
        mask_patches = []
        for _ in range(b):
            _mask_patches = torch.zeros(num_patches, dtype=torch.int8, device=pixel_values.device)
            _mask_patches[: int(mask_ratio * num_patches)] = 1
            _mask_patches = _mask_patches[torch.randperm(num_patches)]
            _mask_patches = rearrange(
                _mask_patches, "(z y x) -> z y x", z=mask_grid_size[0], y=mask_grid_size[1], x=mask_grid_size[2]
            )
            mask_patches.append(_mask_patches)
        mask_patches: torch.Tensor = torch.stack(mask_patches, dim=0)

        grid_size = self._get_grid_size(self.swin_config.image_size)
        assert all(
            [x % y == 0 for x, y in zip(grid_size, mask_grid_size)]
        ), "Mask grid size must divide image grid size"
        mask_patches = repeat(
            mask_patches,
            "b z y x -> b (z gz) (y gy) (x gx)",
            gz=grid_size[0] // mask_grid_size[0],
            gy=grid_size[1] // mask_grid_size[1],
            gx=grid_size[2] // mask_grid_size[2],
        )

        return mask_patches

# %% ../../nbs/nets/03_swinv2_3d.ipynb 48
class SwinV23DSimMIM(SwinV23DMIM, PyTorchModelHubMixin):
    def __init__(self, swin_config, decoder_config, mim_config):
        super().__init__(swin_config, decoder_config, mim_config)

    @staticmethod
    def loss_fn(pred: torch.Tensor, target: torch.Tensor, reduction="mean"):
        return nn.functional.l1_loss(pred, target, reduction=reduction)

    def forward(self, pixel_values: torch.Tensor, spacings: torch.Tensor = None):
        mask_patches = self.mask_image(pixel_values)

        encodings, _, _ = self.swin(pixel_values, spacings, mask_patches, self.mask_token)

        decoded = self.decoder(encodings)

        loss = self.loss_fn(decoded, pixel_values, reduction="none")
        mask = repeat(
            mask_patches,
            "b z y x -> b (z pz) (y py) (x px)",
            pz=self.swin_config.patch_size[0],
            py=self.swin_config.patch_size[1],
            px=self.swin_config.patch_size[2],
        )
        loss = (loss * mask).sum() / ((mask.sum() + 1e-5) * self.swin_config.in_channels)

        return decoded, loss, mask

# %% ../../nbs/nets/03_swinv2_3d.ipynb 50
class SwinV23DVAEMIM(SwinV23DMIM, PyTorchModelHubMixin):
    def __init__(self, swin_config, decoder_config, mim_config):
        super().__init__(swin_config, decoder_config, mim_config)

        assert (decoder_config["beta"] is None) is not (
            decoder_config["beta_schedule"] is None
        ), "Only one of beta or beta_schedule should be provided"

        if decoder_config["beta_schedule"] is not None:
            self.beta_schedule = decoder_config["beta_schedule"]
            self.beta_increment = (self.beta_schedule[2] - self.beta_schedule[1]) / self.beta_schedule[0]
            self.beta = None
        else:
            self.beta = decoder_config["beta"]
            self.beta_schedule = None
            self.beta_increment = None

        self.mu_layer = nn.Conv3d(swin_config.stages[-1]._out_dim, decoder_config.dim, kernel_size=1)
        self.logvar_layer = nn.Conv3d(swin_config.stages[-1]._out_dim, decoder_config.dim, kernel_size=1)

    def get_beta(self):
        # If fixed beta
        if self.beta_schedule is None:
            return self.beta

        # Else there is a beta schedule
        if self.beta is None:
            # If first iteration
            self.beta = self.beta_schedule[1]
        else:
            # Calculate new beta and return
            self.beta = min(self.beta + self.beta_increment, self.beta_schedule[2])
        return self.beta

    def reparameterize(self, mu, logvar):
        return mu + torch.randn_like(logvar) * torch.exp(0.5 * logvar)

    @staticmethod
    def reconstruction_loss_fn(pred: torch.Tensor, target: torch.Tensor, loss_type: str = "l2", reduction="mean"):
        loss = ...
        if loss_type == "l2":
            loss = nn.functional.mse_loss(pred, target, reduction=reduction)
        elif loss_type == "l1":
            loss = nn.functional.l1_loss(pred, target, reduction=reduction)
        else:
            raise NotImplementedError(f"Loss type {loss_type} not implemented")
        return loss

    @staticmethod
    def kl_divergence_loss_fn(mu: torch.Tensor, logvar: torch.Tensor):
        return torch.mean(-0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp(), dim=1))

    def forward(
        self,
        pixel_values: torch.Tensor,
        spacings: torch.Tensor = None,
        reconstruction_loss_type: str = "l2",
    ):
        mask_patches = self.mask_image(pixel_values)

        encodings, _, _ = self.swin(pixel_values, spacings, mask_patches, self.mask_token)

        mu = self.mu_layer(encodings)
        logvar = self.logvar_layer(encodings)
        kl_loss = self.kl_divergence_loss_fn(mu, logvar)

        sampled = self.reparameterize(mu, logvar)
        decoded = self.decoder(sampled)

        reconstruction_loss = self.reconstruction_loss_fn(decoded, pixel_values, reconstruction_loss_type)

        mask = repeat(
            mask_patches,
            "b z y x -> b (z pz) (y py) (x px)",
            pz=self.swin_config.patch_size[0],
            py=self.swin_config.patch_size[1],
            px=self.swin_config.patch_size[2],
        )

        beta = self.get_beta()
        loss = reconstruction_loss + beta * kl_loss

        return decoded, loss, mask, [reconstruction_loss, kl_loss, beta]
