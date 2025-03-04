# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/nets/01_swin_3d.ipynb.

# %% auto 0
__all__ = ['Swin3DPatchMergingConfig', 'Swin3DPatchSplittingConfig', 'Swin3DStageConfig', 'Swin3DConfig', 'Swin3DMIMConfig',
           'Swin3DLayer', 'Swin3DBlock', 'Swin3DPatchMerging', 'Swin3DStage', 'Swin3DEncoder', 'Swin3DModel',
           'Swin3DMIMDecoder', 'Swin3DMIM']

# %% ../../nbs/nets/01_swin_3d.ipynb 2
import numpy as np
import torch
from einops import rearrange, repeat
from huggingface_hub import PyTorchModelHubMixin
from torch import nn

from ..layers.attention import Attention3DWithMLP, Attention3DWithMLPConfig
from vision_architectures.layers.embeddings import (
    AbsolutePositionEmbeddings3D,
    PatchEmbeddings3D,
    RelativePositionEmbeddings3D,
    RelativePositionEmbeddings3DConfig,
)
from ..utils.custom_base_model import CustomBaseModel, Field, model_validator

# %% ../../nbs/nets/01_swin_3d.ipynb 4
class Swin3DPatchMergingConfig(CustomBaseModel):
    out_dim_ratio: int
    merge_window_size: tuple[int, int, int]

    @model_validator(mode='before')
    @classmethod
    def validate_before(cls, data):
        super().validate_before(data)
        merge_window_size = data.get("merge_window_size")
        if isinstance(merge_window_size, int):
            data["merge_window_size"] = (merge_window_size, merge_window_size, merge_window_size)
        return data


class Swin3DPatchSplittingConfig(CustomBaseModel):
    out_dim_ratio: int
    final_window_size: tuple[int, int, int]

    @model_validator(mode='before')
    @classmethod
    def validate_before(cls, data):
        super().validate_before(data)
        final_window_size = data.get("final_window_size")
        if isinstance(final_window_size, int):
            data["final_window_size"] = (final_window_size, final_window_size, final_window_size)
        return data


class Swin3DStageConfig(Attention3DWithMLPConfig):
    depth: int
    window_size: tuple[int, int, int]

    use_relative_position_bias: bool = False
    patch_merging: Swin3DPatchMergingConfig | None = None
    patch_splitting: Swin3DPatchSplittingConfig | None = None

    in_dim: int | None = None
    dim: int = Field(0, description="dim at which attention is performed")
    out_dim: int | None = None

    # Freeze other fields
    logit_scale_learnable: bool = Field(False, frozen=True)

    def get_out_patch_size(self, in_patch_size: tuple[int, int, int]):
        patch_size = in_patch_size
        if self.patch_merging is not None:
            patch_size = tuple(
                [patch * window for patch, window in zip(patch_size, self.patch_merging.merge_window_size)]
            )
        if self.patch_splitting is not None:
            patch_size = tuple(
                [patch // window for patch, window in zip(patch_size, self.patch_splitting.final_window_size)]
            )
        return patch_size

    def get_in_patch_size(self, out_patch_size: tuple[int, int, int]):
        patch_size = out_patch_size
        if self.patch_merging is not None:
            patch_size = tuple(
                [patch // window for patch, window in zip(patch_size, self.patch_merging.merge_window_size)]
            )
        if self.patch_splitting is not None:
            patch_size = tuple(
                [patch * window for patch, window in zip(patch_size, self.patch_splitting.final_window_size)]
            )
        return patch_size


class Swin3DConfig(CustomBaseModel):
    dim: int
    in_channels: int
    patch_size: tuple[int, int, int]
    image_size: tuple[int, int, int] | None = Field(
        None, description="required for learnable absolute position embeddings"
    )
    stages: list[Swin3DStageConfig]

    drop_prob: float = 0.0
    embed_spacing_info: bool = False
    use_absolute_position_embeddings: bool = True
    learnable_absolute_position_embeddings: bool = False

    def populate(self):
        dim = self.dim

        # Prepare config based on provided values
        for i in range(len(self.stages)):
            stage = self.stages[i]
            stage.in_dim = dim
            if stage.patch_merging is not None:
                dim *= stage.patch_merging.out_dim_ratio
                stage.dim = dim  # attention will happen after merging
            if stage.patch_splitting is not None:
                stage.dim = dim  # attention will happen before splitting
                dim //= stage.patch_splitting.out_dim_ratio
            if stage.dim == 0:
                stage.dim = dim  # In case it is not yet set
            stage.out_dim = dim

    @model_validator(mode="after")
    def validate(self):
        self.populate()
        super().validate()

        # test divisibility of dim with number of attention heads
        for stage in self.stages:
            assert (
                stage.dim % stage.num_heads == 0
            ), f"stage.dim {stage.dim} is not divisible by stage.num_heads {stage.num_heads}"

        # test population of image_size field iff the absolute position embeddings are relative
        if self.learnable_absolute_position_embeddings:
            assert (
                self.image_size is not None
            ), "Please provide image_size if absolute position embeddings are learnable"

        return self
    

class Swin3DMIMConfig(Swin3DConfig):
    mim: dict

# %% ../../nbs/nets/01_swin_3d.ipynb 8
class Swin3DLayer(nn.Module):
    def __init__(self, config: RelativePositionEmbeddings3DConfig | Attention3DWithMLPConfig = {}, **kwargs):
        super().__init__()

        all_inputs = config | kwargs
        self.window_size = all_inputs.get("window_size")
        use_relative_position_bias = all_inputs.get("use_relative_position_bias")

        self.embeddings_config = RelativePositionEmbeddings3DConfig.model_validate(
            all_inputs | {"grid_size": self.window_size}
        )
        self.transformer_config = Attention3DWithMLPConfig.model_validate(all_inputs)

        relative_position_bias = None
        if use_relative_position_bias:
            relative_position_bias = RelativePositionEmbeddings3D(self.embeddings_config)

        self.attn = Attention3DWithMLP(self.transformer_config, relative_position_bias=relative_position_bias)

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

        hidden_states = self.attn(hidden_states, hidden_states, hidden_states, channels_first=False)

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

# %% ../../nbs/nets/01_swin_3d.ipynb 11
class Swin3DBlock(nn.Module):
    def __init__(self, stage_config: Swin3DStageConfig):
        super().__init__()

        self.stage_config = Swin3DStageConfig.model_validate(stage_config)

        self.w_layer = Swin3DLayer(self.stage_config.model_dump())
        self.sw_layer = Swin3DLayer(self.stage_config.model_dump())

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

# %% ../../nbs/nets/01_swin_3d.ipynb 13
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

# %% ../../nbs/nets/01_swin_3d.ipynb 15
class Swin3DStage(nn.Module):
    def __init__(self, stage_config: Swin3DStageConfig):
        super().__init__()

        self.config = stage_config

        self.patch_merging = None
        if stage_config.patch_merging is not None:
            self.patch_merging = Swin3DPatchMerging(
                stage_config.patch_merging.merge_window_size,
                stage_config.in_dim,
                stage_config.out_dim,
            )

        self.blocks = nn.ModuleList(
            [Swin3DBlock(stage_config) for _ in range(stage_config.depth)],
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

# %% ../../nbs/nets/01_swin_3d.ipynb 18
class Swin3DEncoder(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: Swin3DConfig):
        super().__init__()

        self.stages = nn.ModuleList([Swin3DStage(stage_config) for stage_config in config.stages])

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)

        stage_outputs, layer_outputs = [], []
        for stage_module in self.stages:
            hidden_states, _layer_outputs = stage_module(hidden_states)
            # (b, new_num_patches_z, new_num_patches_y, new_num_patches_x, dim)

            stage_outputs.append(hidden_states)
            layer_outputs.extend(_layer_outputs)

        return hidden_states, stage_outputs, layer_outputs

# %% ../../nbs/nets/01_swin_3d.ipynb 21
class Swin3DModel(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: Swin3DConfig):
        super().__init__()

        self.config = config

        self.patchify = PatchEmbeddings3D(patch_size=config.patch_size, in_channels=config.in_channels, dim=config.dim)
        self.absolute_position_embeddings = AbsolutePositionEmbeddings3D(dim=config.dim, learnable=False)
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

        embeddings = self.patchify(pixel_values)
        # (b, dim, num_patches_z, num_patches_y, num_patches_x)

        if mask_patches is not None:
            # mask_patches (binary mask): (b, num_patches_z, num_patches_y, num_patches_x)
            # mask_token: (1, dim, 1, 1, 1)
            mask_patches = repeat(mask_patches, "b z y x -> b d z y x", d=embeddings.shape[1])
            embeddings = (embeddings * (1 - mask_patches)) + (mask_patches * mask_token)

        absolute_position_embeddings = self.absolute_position_embeddings(
            batch_size=embeddings.shape[0], grid_size=embeddings.shape[2:], spacings=spacings
        )
        # (b, dim, num_patches_z, num_patches_y, num_patches_x)
        embeddings = embeddings + absolute_position_embeddings
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

# %% ../../nbs/nets/01_swin_3d.ipynb 24
class Swin3DMIMDecoder(nn.Module):
    def __init__(self, config: Swin3DMIMConfig):
        super().__init__()

        self.image_size = config.image_size
        self.in_channels = config.in_channels

        dim = config.stages[-1].out_dim
        patch_size = config.stages[-1].out_patch_size

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

# %% ../../nbs/nets/01_swin_3d.ipynb 26
class Swin3DMIM(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: Swin3DMIMConfig):
        super().__init__()

        self.config = config

        self.swin = Swin3DModel(config)
        self.decoder = Swin3DMIMDecoder(config)

        self.mask_token = nn.Parameter(torch.randn(1, config.dim, 1, 1, 1))

    def forward(self, pixel_values: torch.Tensor, spacings: torch.Tensor):
        b = pixel_values.shape[0]

        mask_ratio = self.config.mim["mask_ratio"]
        mask_grid_size = self.config.mim["mask_grid_size"]
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

        grid_size = tuple([size // patch for size, patch in zip(self.config.image_size, self.config.patch_size)])
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
            pz=self.config.patch_size[0],
            py=self.config.patch_size[1],
            px=self.config.patch_size[2],
        )
        loss = (loss * mask).sum() / ((mask.sum() + 1e-5) * self.config.in_channels)

        return decoded, loss, mask
