# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/nets/03_swinv2_3d.ipynb.

# %% auto 0
__all__ = ['SwinV23DPatchMergingConfig', 'SwinV23DPatchSplittingConfig', 'SwinV23DStageConfig', 'SwinV23DDecoderConfig',
           'SwinV23DConfig', 'Swin3DMIMConfig', 'SwinV23DLayerLogitScale', 'SwinV23DLayer', 'SwinV23DBlock',
           'SwinV23DPatchMerging', 'SwinV23DPatchSplitting', 'SwinV23DStage', 'SwinV23DEncoder', 'SwinV23DDecoder',
           'SwinV23DModel', 'SwinV23DReconstructionDecoder', 'SwinV23DMIM', 'SwinV23DSimMIM', 'SwinV23DVAEMIM']

# %% ../../nbs/nets/03_swinv2_3d.ipynb 2
import numpy as np
import torch
from einops import rearrange, repeat
from huggingface_hub import PyTorchModelHubMixin
from torch import nn

from ..blocks.transformer import Attention3DWithMLP, Attention3DWithMLPConfig
from vision_architectures.layers.embeddings import (
    AbsolutePositionEmbeddings3D,
    PatchEmbeddings3D,
    RelativePositionEmbeddings3DConfig,
    RelativePositionEmbeddings3DMetaNetwork,
)
from ..utils.activation_checkpointing import ActivationCheckpointing
from ..utils.custom_base_model import CustomBaseModel, Field, model_validator

# %% ../../nbs/nets/03_swinv2_3d.ipynb 5
class SwinV23DPatchMergingConfig(CustomBaseModel):
    out_dim_ratio: int
    merge_window_size: tuple[int, int, int]

    @model_validator(mode="before")
    @classmethod
    def validate_before(cls, data):
        super().validate_before(data)
        merge_window_size = data.get("merge_window_size")
        if isinstance(merge_window_size, int):
            data["merge_window_size"] = (
                merge_window_size,
                merge_window_size,
                merge_window_size,
            )
        return data


class SwinV23DPatchSplittingConfig(CustomBaseModel):
    out_dim_ratio: int
    final_window_size: tuple[int, int, int]

    @model_validator(mode="before")
    @classmethod
    def validate_before(cls, data):
        super().validate_before(data)
        final_window_size = data.get("final_window_size")
        if isinstance(final_window_size, int):
            data["final_window_size"] = (
                final_window_size,
                final_window_size,
                final_window_size,
            )
        return data


class SwinV23DStageConfig(Attention3DWithMLPConfig):
    depth: int
    window_size: tuple[int, int, int]

    use_relative_position_bias: bool = True
    patch_merging: SwinV23DPatchMergingConfig | None = None
    patch_splitting: SwinV23DPatchSplittingConfig | None = None

    in_dim: int | None = None
    dim: int = Field(0, description="dim at which attention is performed")
    out_dim: int | None = None

    # Freeze other fields
    logit_scale_learnable: bool = Field(False, frozen=True)

    @property
    def spatial_compression_ratio(self):
        compression_ratio = (1.0, 1.0, 1.0)
        if self.patch_merging is not None:
            compression_ratio = tuple(compression_ratio[i] * self.patch_merging.merge_window_size[i] for i in range(3))
        if self.patch_splitting is not None:
            compression_ratio = tuple(
                compression_ratio[i] / self.patch_splitting.final_window_size[i] for i in range(3)
            )
        return compression_ratio

    def get_out_patch_size(self, in_patch_size: tuple[int, int, int]):
        patch_size = tuple(int(in_patch_size[i] * self.spatial_compression_ratio[i]) for i in range(3))
        return patch_size

    def get_in_patch_size(self, out_patch_size: tuple[int, int, int]):
        patch_size = tuple(int(out_patch_size[i] / self.spatial_compression_ratio[i]) for i in range(3))
        return patch_size


class SwinV23DDecoderConfig(CustomBaseModel):
    dim: int
    stages: list[SwinV23DStageConfig]

    drop_prob: float = 0.0
    embed_spacing_info: bool = False

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

        # test divisibility of dim with number of attention heads
        for stage in self.stages:
            assert (
                stage.dim % stage.num_heads == 0
            ), f"stage._dim {stage.dim} is not divisible by stage.num_heads {stage.num_heads}"

        return self


class SwinV23DConfig(SwinV23DDecoderConfig):
    in_channels: int
    patch_size: tuple[int, int, int]
    image_size: tuple[int, int, int] | None = Field(
        None, description="required for learnable absolute position embeddings"
    )
    use_absolute_position_embeddings: bool = True  # TODO: Use it in the code
    learnable_absolute_position_embeddings: bool = False  # TODO: Use it in the code

    @model_validator(mode="after")
    def validate(self):
        super().validate()
        # test population of image_size field iff the absolute position embeddings are relative
        if self.learnable_absolute_position_embeddings:
            assert (
                self.image_size is not None
            ), "Please provide image_size if absolute position embeddings are learnable"
        return self


class Swin3DMIMConfig(SwinV23DConfig):  # TODO: Implement and fix
    mim: dict

# %% ../../nbs/nets/03_swinv2_3d.ipynb 9
class SwinV23DLayerLogitScale(nn.Module):
    def __init__(self, num_heads):
        super().__init__()

        self.logit_scale = nn.Parameter(torch.log(10 * torch.ones((num_heads, 1, 1))), requires_grad=True)

    def forward(self):
        logit_scale = torch.clamp(self.logit_scale, max=np.log(1.0 / 0.01)).exp()
        return logit_scale

# %% ../../nbs/nets/03_swinv2_3d.ipynb 10
class SwinV23DLayer(nn.Module):
    def __init__(
        self,
        config: RelativePositionEmbeddings3DConfig | Attention3DWithMLPConfig = {},
        checkpointing_level: int = 0,
        **kwargs
    ):
        super().__init__()

        all_inputs = config | kwargs
        self.window_size = all_inputs.get("window_size")
        use_relative_position_bias = all_inputs.get("use_relative_position_bias")

        self.embeddings_config = RelativePositionEmbeddings3DConfig.model_validate(
            config | kwargs | {"grid_size": self.window_size}
        )
        self.transformer_config = Attention3DWithMLPConfig.model_validate(config | kwargs)

        relative_position_bias = None
        if use_relative_position_bias:
            relative_position_bias = RelativePositionEmbeddings3DMetaNetwork(
                self.embeddings_config, checkpointing_level=checkpointing_level
            )

        logit_scale = SwinV23DLayerLogitScale(self.transformer_config.num_heads)

        self.transformer = Attention3DWithMLP(
            self.transformer_config,
            relative_position_bias=relative_position_bias,
            logit_scale=logit_scale,
            checkpointing_level=checkpointing_level,
        )

        self.checkpointing_level3 = ActivationCheckpointing(3, checkpointing_level)

    def _forward(self, hidden_states: torch.Tensor):
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
        ).contiguous()

        hidden_states = self.transformer(hidden_states, hidden_states, hidden_states, channels_first=False)

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
        ).contiguous()

        return output

    def forward(self, hidden_states: torch.Tensor):
        return self.checkpointing_level3(self._forward, hidden_states)

# %% ../../nbs/nets/03_swinv2_3d.ipynb 13
class SwinV23DBlock(nn.Module):
    def __init__(self, stage_config, checkpointing_level: int = 0):
        super().__init__()

        self.stage_config = SwinV23DStageConfig.model_validate(stage_config)

        self.w_layer = SwinV23DLayer(self.stage_config.model_dump(), checkpointing_level=checkpointing_level)
        self.sw_layer = SwinV23DLayer(self.stage_config.model_dump(), checkpointing_level=checkpointing_level)

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

# %% ../../nbs/nets/03_swinv2_3d.ipynb 15
class SwinV23DPatchMerging(nn.Module):
    def __init__(self, merge_window_size, in_dim, out_dim, checkpointing_level: int = 0):
        super().__init__()

        self.merge_window_size = merge_window_size

        in_dim = in_dim * np.prod(merge_window_size)
        self.layer_norm = nn.LayerNorm(in_dim)
        self.proj = nn.Linear(in_dim, out_dim)

        self.checkpointing_level1 = ActivationCheckpointing(1, checkpointing_level)

    def _forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)

        window_size_z, window_size_y, window_size_x = self.merge_window_size

        hidden_states = rearrange(
            hidden_states,
            "b (new_num_patches_z window_size_z) (new_num_patches_y window_size_y) (new_num_patches_x window_size_x) dim -> "
            "b new_num_patches_z new_num_patches_y new_num_patches_x (window_size_z window_size_y window_size_x dim)",
            window_size_z=window_size_z,
            window_size_y=window_size_y,
            window_size_x=window_size_x,
        ).contiguous()

        hidden_states = self.layer_norm(hidden_states)
        hidden_states = self.proj(hidden_states)
        return hidden_states

    def forward(self, hidden_states: torch.Tensor):
        return self.checkpointing_level1(self._forward, hidden_states)

# %% ../../nbs/nets/03_swinv2_3d.ipynb 17
class SwinV23DPatchSplitting(nn.Module):  # This is a self-implemented class and is not part of the paper.
    def __init__(self, final_window_size, in_dim, out_dim, checkpointing_level: int = 0):
        super().__init__()

        self.final_window_size = final_window_size

        out_dim = out_dim * np.prod(final_window_size)
        self.layer_norm = nn.LayerNorm(in_dim)
        self.proj = nn.Linear(in_dim, out_dim)

        self.checkpointing_level1 = ActivationCheckpointing(1, checkpointing_level)

    def _forward(self, hidden_states: torch.Tensor):
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
        ).contiguous()

        return hidden_states

    def forward(self, hidden_states: torch.Tensor):
        return self.checkpointing_level1(self._forward, hidden_states)

# %% ../../nbs/nets/03_swinv2_3d.ipynb 19
class SwinV23DStage(nn.Module):
    def __init__(self, stage_config, checkpointing_level: int = 0):
        super().__init__()

        stage_config = SwinV23DStageConfig.model_validate(stage_config)

        self.config = stage_config

        self.patch_merging = None
        if stage_config.patch_merging is not None:
            self.patch_merging = SwinV23DPatchMerging(
                stage_config.patch_merging.merge_window_size,
                stage_config.in_dim,
                stage_config.dim,
                checkpointing_level,
            )

        self.blocks = nn.ModuleList(
            [SwinV23DBlock(stage_config, checkpointing_level) for _ in range(stage_config.depth)],
        )

        self.patch_splitting = None
        if stage_config.patch_splitting is not None:  # This has been implemented to create a Swin-based decoder
            self.patch_splitting = SwinV23DPatchSplitting(
                stage_config.patch_splitting.final_window_size,
                stage_config.dim,
                stage_config.out_dim,
                checkpointing_level,
            )

        self.checkpointing_level4 = ActivationCheckpointing(4, checkpointing_level)

    def _forward(self, hidden_states: torch.Tensor):
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

    def forward(self, hidden_states: torch.Tensor):
        return self.checkpointing_level4(self._forward, hidden_states)

# %% ../../nbs/nets/03_swinv2_3d.ipynb 23
class SwinV23DEncoder(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config, checkpointing_level: int = 0):
        super().__init__()

        for stage_config in config.stages:
            if stage_config.patch_splitting is not None:
                assert (
                    stage_config.patch_merging is not None
                ), "SwinV23DEncoder is not for decoding (mid blocks are ok)."

        self.stages = nn.ModuleList(
            [SwinV23DStage(stage_config, checkpointing_level) for stage_config in config.stages]
        )

        self.checkpointing_level5 = ActivationCheckpointing(5, checkpointing_level)

    def _forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)

        stage_outputs, layer_outputs = [], []
        for stage_module in self.stages:
            hidden_states, _layer_outputs = stage_module(hidden_states)
            # (b, new_num_patches_z, new_num_patches_y, new_num_patches_x, dim)

            stage_outputs.append(hidden_states)
            layer_outputs.extend(_layer_outputs)

        return hidden_states, stage_outputs, layer_outputs

    def forward(self, hidden_states: torch.Tensor):
        return self.checkpointing_level5(self._forward, hidden_states)

# %% ../../nbs/nets/03_swinv2_3d.ipynb 26
class SwinV23DDecoder(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: SwinV23DDecoderConfig, checkpointing_level: int = 0):
        super().__init__()

        self.config = SwinV23DDecoderConfig.model_validate(config)

        for stage_config in config.stages:
            if stage_config.patch_merging is not None:
                assert (
                    stage_config.patch_splitting is not None
                ), "SwinV23DDecoder is not for encoding (mid blocks are ok)."

        self.stages = nn.ModuleList(
            [SwinV23DStage(stage_config, checkpointing_level) for stage_config in config.stages]
        )

        self.checkpointing_level5 = ActivationCheckpointing(5, checkpointing_level)

    def _forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)

        stage_outputs, layer_outputs = [], []
        for stage_module in self.stages:
            hidden_states, _layer_outputs = stage_module(hidden_states)
            # (b, new_num_patches_z, new_num_patches_y, new_num_patches_x, dim)

            stage_outputs.append(hidden_states)
            layer_outputs.extend(_layer_outputs)

        return hidden_states, stage_outputs, layer_outputs

    def forward(self, hidden_states: torch.Tensor):
        return self.checkpointing_level5(self._forward, hidden_states)

# %% ../../nbs/nets/03_swinv2_3d.ipynb 29
class SwinV23DModel(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: SwinV23DConfig, checkpointing_level: int = 0):
        super().__init__()

        self.config = config

        self.patchify = PatchEmbeddings3D(
            patch_size=config.patch_size,
            in_channels=config.in_channels,
            dim=config.dim,
            checkpointing_level=checkpointing_level,
        )
        self.absolute_position_embeddings = AbsolutePositionEmbeddings3D(
            dim=config.dim, learnable=False, checkpointing_level=checkpointing_level
        )
        self.encoder = SwinV23DEncoder(config, checkpointing_level)

    def forward(
        self,
        pixel_values: torch.Tensor,
        spacings: torch.Tensor = None,
        crop_offsets: torch.Tensor = None,
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

        embeddings = self.absolute_position_embeddings(
            embeddings, spacings=spacings, device=embeddings.device, crop_offsets=crop_offsets
        )
        # (b, dim, num_patches_z, num_patches_y, num_patches_x)

        embeddings = rearrange(embeddings, "b e nz ny nx -> b nz ny nx e").contiguous()
        # (b, num_patches_z, num_patches_y, num_patches_x, dim)

        encoded, stage_outputs, layer_outputs = self.encoder(embeddings)
        # encoded: (b, new_num_patches_z, new_num_patches_y, new_num_patches_x, dim)
        # stage_outputs, layer_outputs: list of (b, some_num_patches_z, some_num_patches_y, some_num_patches_x, dim)

        encoded = rearrange(encoded, "b nz ny nx d -> b d nz ny nx").contiguous()
        # (b, dim, new_num_patches_z, new_num_patches_y, new_num_patches_x)

        for i in range(len(stage_outputs)):
            stage_outputs[i] = rearrange(stage_outputs[i], "b nz ny nx d -> b d nz ny nx").contiguous()
            # (b, dim, some_num_patches_z, some_num_patches_y, some_num_patches_x)

        for i in range(len(layer_outputs)):
            layer_outputs[i] = rearrange(layer_outputs[i], "b nz ny nx d -> b d nz ny nx").contiguous()
            # (b, dim, some_num_patches_z, some_num_patches_y, some_num_patches_x)

        return encoded, stage_outputs, layer_outputs

# %% ../../nbs/nets/03_swinv2_3d.ipynb 32
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
        ).contiguous()
        # (b, c, z, y, x)

        return decoded

# %% ../../nbs/nets/03_swinv2_3d.ipynb 34
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
                _mask_patches,
                "(z y x) -> z y x",
                z=mask_grid_size[0],
                y=mask_grid_size[1],
                x=mask_grid_size[2],
            ).contiguous()
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

# %% ../../nbs/nets/03_swinv2_3d.ipynb 35
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

# %% ../../nbs/nets/03_swinv2_3d.ipynb 37
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

        self.mu_layer = nn.Conv3d(swin_config.stages[-1].out_dim, decoder_config.dim, kernel_size=1)
        self.logvar_layer = nn.Conv3d(swin_config.stages[-1].out_dim, decoder_config.dim, kernel_size=1)

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
    def reconstruction_loss_fn(
        pred: torch.Tensor,
        target: torch.Tensor,
        loss_type: str = "l2",
        reduction="mean",
    ):
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
