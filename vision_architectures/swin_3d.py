# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_swin_3d.ipynb.

# %% auto 0
__all__ = ['populate_and_validate_config', 'get_coords_grid', 'Swin3DMHSA', 'Swin3DLayerMLP', 'Swin3DLayer', 'Swin3DBlock',
           'Swin3DPatchMerging', 'Swin3DStage', 'Swin3DEncoder', 'Swin3DPatchEmbeddings', 'get_3d_position_embeddings',
           'embed_spacings_in_position_embeddings', 'Swin3DEmbeddings', 'Swin3DModel', 'Swin3DMIMDecoder', 'Swin3DMIM']

# %% ../nbs/01_swin_3d.ipynb 2
import torch
import numpy as np
from torch import nn
from einops import rearrange, repeat
from huggingface_hub import PyTorchModelHubMixin

# %% ../nbs/01_swin_3d.ipynb 4
def populate_and_validate_config(config: dict) -> dict:
    assert config["stages"][0]["patch_merging"] is None

    # Prepare config based on provided values
    dim = config["dim"]
    patch_size = config["patch_size"]
    # image_size = config["image_size"]  # This may not be fixed while fine-tuning.
    for i in range(len(config["stages"])):
        stage = config["stages"][i]
        stage["_in_dim"] = dim
        stage["_in_patch_size"] = patch_size
        # stage['_in"grid_size'] = tuple([image // patch for image, patch in zip(image_size, patch_size)])
        if stage["patch_merging"] is not None:
            dim *= stage["patch_merging"]["out_dim_ratio"]
            patch_size = tuple(
                [patch * window for patch, window in zip(patch_size, stage["patch_merging"]["merge_window_size"])]
            )
        stage["_out_dim"] = dim
        stage["_out_patch_size"] = patch_size
        # stage["_out_grid_size"] = tuple([image // patch for image, patch in zip(image_size, patch_size)])

    for stage in config["stages"]:
        assert stage["_out_dim"] % stage["num_heads"] == 0, stage

    return config

# %% ../nbs/01_swin_3d.ipynb 8
def get_coords_grid(grid_size):
    d, h, w = grid_size

    grid_d = torch.arange(d, dtype=torch.int32)
    grid_h = torch.arange(h, dtype=torch.int32)
    grid_w = torch.arange(w, dtype=torch.int32)

    grid = torch.meshgrid(grid_w, grid_h, grid_d, indexing="ij")
    grid = torch.stack(grid, axis=0)
    # (3, d, h, w)

    return grid

# %% ../nbs/01_swin_3d.ipynb 9
class Swin3DMHSA(nn.Module):
    def __init__(self, dim, num_heads, window_size, use_relative_position_bias):
        super().__init__()

        assert dim % num_heads == 0, "dimension must be divisible by number of heads"

        self.dim = dim
        self.num_heads = num_heads
        self.window_size = window_size

        self.per_head_dim = int(dim // num_heads)

        self.W_qkv = nn.Linear(dim, 3 * dim)
        self.proj = nn.Linear(dim, dim)

        # TODO: Add embed_spacing_info functionality
        # TODO: Add dropout everywhere
        self.use_relative_position_bias = use_relative_position_bias
        if use_relative_position_bias:
            relative_limits = (2 * window_size[0] - 1, 2 * window_size[1] - 1, 2 * window_size[2] - 1)

            self.relative_position_bias_table = nn.Parameter(torch.randn(num_heads, np.prod(relative_limits)))

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

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (windowed_b, window_size_z window_size_y window_size_x, dim)
        _, num_patches_z, num_patches_y, num_patches_x, _ = hidden_states.shape

        query, key, value = rearrange(
            self.W_qkv(hidden_states),
            "b nz ny nx (n num_heads d) -> n b num_heads (nz ny nx) d",
            n=3,
            num_heads=self.num_heads,
        )
        # num_patches = window_size_z window_size_y window_size_x
        # Each is (windowed_b, num_heads, num_patches, per_head_dim)

        attention_scores = query @ rearrange(key, "b num_heads n d -> b num_heads d n")
        attention_scores = attention_scores / (self.per_head_dim**0.5)
        # (windowed_b, num_heads, num_patches, num_patches)

        if self.use_relative_position_bias:
            relative_position_bias = self.relative_position_bias_table[:, self.relative_position_index]
            relative_position_bias = relative_position_bias.reshape(
                1, np.prod(self.window_size), np.prod(self.window_size), -1
            )
            relative_position_bias = relative_position_bias.permute(0, 3, 1, 2).contiguous()
            attention_scores = attention_scores + relative_position_bias

        attention_probs = nn.functional.softmax(attention_scores, dim=-1)
        # (windowed_b, num_heads, num_patches, num_patches)

        context = attention_probs @ value
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
        # (windowed_b, window_size_z window_size_y window_size_x, dim)

        return context

# %% ../nbs/01_swin_3d.ipynb 11
class Swin3DLayerMLP(nn.Module):
    def __init__(self, dim, intermediate_ratio):
        super().__init__()
        self.dense1 = nn.Linear(dim, dim * intermediate_ratio)
        self.act = nn.GELU()
        self.dense2 = nn.Linear(dim * intermediate_ratio, dim)

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (windowed_b, window_size_z window_size_y window_size_x, dim)
        hidden_states = self.dense1(hidden_states)
        hidden_states = self.act(hidden_states)
        hidden_states = self.dense2(hidden_states)
        return hidden_states

# %% ../nbs/01_swin_3d.ipynb 13
class Swin3DLayer(nn.Module):
    def __init__(self, dim, num_heads, intermediate_ratio, layer_norm_eps, window_size, use_relative_position_bias):
        super().__init__()

        self.window_size = window_size

        self.layernorm_before = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.mhsa = Swin3DMHSA(dim, num_heads, window_size, use_relative_position_bias)
        self.layernorm_after = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.mlp = Swin3DLayerMLP(dim, intermediate_ratio)

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

        hidden_states = self.layernorm_before(hidden_states)
        hidden_states = self.mhsa(hidden_states)
        # (windowed_b, window_size_z window_size_y window_size_x, dim)

        res_connection2 = hidden_states + res_connection1
        # (windowed_b, window_size_z window_size_y window_size_x, dim)

        hidden_states = self.layernorm_after(res_connection2)
        hidden_states = self.mlp(hidden_states)
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

# %% ../nbs/01_swin_3d.ipynb 16
class Swin3DBlock(nn.Module):
    def __init__(self, stage_config):
        super().__init__()

        self.stage_config = stage_config
        self.w_layer = Swin3DLayer(
            stage_config["_out_dim"],
            stage_config["num_heads"],
            stage_config["intermediate_ratio"],
            stage_config["layer_norm_eps"],
            stage_config["window_size"],
            stage_config["use_relative_position_bias"],
        )
        self.sw_layer = Swin3DLayer(
            stage_config["_out_dim"],
            stage_config["num_heads"],
            stage_config["intermediate_ratio"],
            stage_config["layer_norm_eps"],
            stage_config["window_size"],
            stage_config["use_relative_position_bias"],
        )

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)

        layer_outputs = []

        # First layer
        hidden_states = self.w_layer(hidden_states)
        # (b, num_patches_z, num_patches_y, num_patches_x, dim)

        layer_outputs.append(hidden_states)

        # Shift windows
        window_size_z, window_size_y, window_size_x = self.stage_config["window_size"]
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

# %% ../nbs/01_swin_3d.ipynb 18
class Swin3DPatchMerging(nn.Module):
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

# %% ../nbs/01_swin_3d.ipynb 20
class Swin3DStage(nn.Module):
    def __init__(self, stage_config):
        super().__init__()

        self.config = stage_config

        self.patch_merging = None
        if stage_config["patch_merging"] is not None:
            self.patch_merging = Swin3DPatchMerging(
                stage_config["patch_merging"]["merge_window_size"],
                stage_config["_in_dim"],
                stage_config["_out_dim"],
            )

        self.blocks = nn.ModuleList(
            [Swin3DBlock(stage_config) for _ in range(stage_config["depth"])],
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

        return hidden_states, layer_outputs

# %% ../nbs/01_swin_3d.ipynb 23
class Swin3DEncoder(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config, default_layer_norm_eps=1e-6):
        super().__init__()

        self.stages = nn.ModuleList([Swin3DStage(stage_config) for stage_config in config["stages"]])

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)

        stage_outputs, layer_outputs = [], []
        for stage_module in self.stages:
            hidden_states, _layer_outputs = stage_module(hidden_states)
            # (b, new_num_patches_z, new_num_patches_y, new_num_patches_x, dim)

            stage_outputs.append(hidden_states)
            layer_outputs.extend(_layer_outputs)

        return hidden_states, stage_outputs, layer_outputs

# %% ../nbs/01_swin_3d.ipynb 27
class Swin3DPatchEmbeddings(nn.Module):
    def __init__(self, config):
        super().__init__()

        patch_size = config["patch_size"]
        num_channels = config["in_channels"]
        dim = config["dim"]

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

# %% ../nbs/01_swin_3d.ipynb 30
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

# %% ../nbs/01_swin_3d.ipynb 31
def embed_spacings_in_position_embeddings(embeddings: torch.Tensor, spacings: torch.Tensor):
    assert spacings.ndim == 2, "Please provide spacing information for each batch element"
    _, embedding_size, _, _, _ = embeddings.shape
    assert embedding_size % 3 == 0, "To embed spacing info, the embedding size must be divisible by 3"
    embeddings = embeddings * repeat(spacings, f"B S -> B (S {int(embedding_size / 3)}) 1 1 1", S=3)

    return embeddings

# %% ../nbs/01_swin_3d.ipynb 32
class Swin3DEmbeddings(nn.Module):
    def __init__(self, config):
        super().__init__()

        self.config = config

        dim = config["dim"]

        self.patch_embeddings = Swin3DPatchEmbeddings(config)
        self.layer_norm = nn.LayerNorm(dim)

        self.absolute_position_embeddings = None
        if config["use_absolute_position_embeddings"]:
            grid_size = (
                config["image_size"][0] // config["patch_size"][0],
                config["image_size"][1] // config["patch_size"][1],
                config["image_size"][2] // config["patch_size"][2],
            )
            if config["learnable_absolute_position_embeddings"]:
                self.absolute_position_embeddings = nn.Parameter(
                    torch.randn(1, dim, grid_size[0], grid_size[1], grid_size[2])
                )
            else:
                self.absolute_position_embeddings = get_3d_position_embeddings(dim, grid_size, config["patch_size"])

    def forward(
        self,
        pixel_values: torch.Tensor,
        spacings: torch.Tensor,
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
            absolute_position_embeddings = self.absolute_position_embeddings.to(embeddings.device)
            # (1, dim, num_patches_z, num_patches_y, num_patches_x)
            if self.config["embed_spacing_info"]:
                absolute_position_embeddings = embed_spacings_in_position_embeddings(
                    absolute_position_embeddings, spacings
                )
                # (b, dim, num_patches_z, num_patches_y, num_patches_x)

            embeddings = embeddings + absolute_position_embeddings
            # (b, dim, num_patches_z, num_patches_y, num_patches_x)

        return embeddings

# %% ../nbs/01_swin_3d.ipynb 36
class Swin3DModel(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config):
        super().__init__()

        self.embeddings = Swin3DEmbeddings(config)
        self.encoder = Swin3DEncoder(config)

    def forward(
        self,
        pixel_values: torch.Tensor,
        spacings: torch.Tensor,
        mask_patches: torch.Tensor = None,
        mask_token: torch.Tensor = None,
    ):
        # pixel_values: (b, c, z, y, x)
        # spacings: (b, 3)
        # mask_patches: (num_patches_z, num_patches_y, num_patches_x)

        embeddings = self.embeddings(pixel_values, spacings, mask_patches, mask_token)
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

# %% ../nbs/01_swin_3d.ipynb 39
class Swin3DMIMDecoder(nn.Module):
    def __init__(self, config):
        super().__init__()

        self.image_size = config["image_size"]
        self.in_channels = config["in_channels"]

        dim = config["stages"][-1]["_out_dim"]
        patch_size = config["stages"][-1]["_out_patch_size"]

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

# %% ../nbs/01_swin_3d.ipynb 41
class Swin3DMIM(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config):
        super().__init__()

        self.config = config

        self.swin = Swin3DModel(config)
        self.decoder = Swin3DMIMDecoder(config)

        self.mask_token = nn.Parameter(torch.randn(1, config["dim"], 1, 1, 1))

    def forward(self, pixel_values: torch.Tensor, spacings: torch.Tensor):
        b = pixel_values.shape[0]

        mask_ratio = self.config["mim"]["mask_ratio"]
        mask_grid_size = self.config["mim"]["mask_grid_size"]
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

        grid_size = tuple([size // patch for size, patch in zip(self.config["image_size"], self.config["patch_size"])])
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

        encodings, _, _ = self.swin(pixel_values, spacings, mask_patches, self.mask_token)

        decoded = self.decoder(encodings)

        loss = nn.functional.l1_loss(decoded, pixel_values, reduction="none")
        mask = repeat(
            mask_patches,
            "b z y x -> b (z pz) (y py) (x px)",
            pz=self.config["patch_size"][0],
            py=self.config["patch_size"][1],
            px=self.config["patch_size"][2],
        )
        loss = (loss * mask).sum() / ((mask.sum() + 1e-5) * self.config["in_channels"])

        return decoded, loss, mask
