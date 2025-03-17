# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/nets/13_perceiver_3d.ipynb.

# %% auto 0
__all__ = ['Perceiver3DChannelMappingConfig', 'Perceiver3DEncoderEncodeConfig', 'Perceiver3DEncoderProcessConfig',
           'Perceiver3DEncoderConfig', 'Perceiver3DDecoderConfig', 'Perceiver3DConfig', 'unfold_with_roll_3d',
           'fold_back_3d', 'Perceiver3DChannelMapping', 'Perceiver3DEncoderEncode', 'Perceiver3DEncoderProcess',
           'Perceiver3DEncoder', 'Perceiver3DDecoder']

# %% ../../nbs/nets/13_perceiver_3d.ipynb 2
import math

import torch
from einops import rearrange, repeat
from huggingface_hub import PyTorchModelHubMixin
from torch import nn

from ..layers.attention import Attention3DWithMLP, Attention3DWithMLPConfig
from vision_architectures.layers.embeddings import (
    AbsolutePositionEmbeddings3D,
    AbsolutePositionEmbeddings3DConfig,
    RelativePositionEmbeddings3D,
    RelativePositionEmbeddings3DConfig,
)
from ..utils.activation_checkpointing import ActivationCheckpointing
from ..utils.custom_base_model import CustomBaseModel, model_validator

# %% ../../nbs/nets/13_perceiver_3d.ipynb 4
class Perceiver3DChannelMappingConfig(CustomBaseModel):
    in_channels: int | set[int]
    out_channels: int


class Perceiver3DEncoderEncodeConfig(Attention3DWithMLPConfig):
    dim: int
    num_layers: int
    latent_grid_size: tuple[int, int, int]


class Perceiver3DEncoderProcessConfig(Attention3DWithMLPConfig):
    dim: int
    num_layers: int
    latent_grid_size: tuple[int, int, int] | None
    use_relative_position_embeddings: bool = True  # can help with self attention

    @model_validator(mode="after")
    def validate(self):
        super().validate()
        if self.use_relative_position_embeddings:
            assert (
                self.latent_grid_size is not None
            ), "latent_grid_size must be provided if using relative position embeddings"
        return self


class Perceiver3DEncoderConfig(CustomBaseModel):
    encode: Perceiver3DEncoderEncodeConfig
    process: Perceiver3DEncoderProcessConfig

    @property
    def dim(self):
        return self.encode.dim

    @property
    def latent_grid_size(self):
        return self.encode.latent_grid_size

    @model_validator(mode="before")
    @classmethod
    def validate_before(cls, data):
        super().validate_before(data)
        if isinstance(data, dict):
            data.setdefault("encode", {})
            data.setdefault("process", {})
            for key, value in data.items():
                if key in {"encode", "process", "decode"}:
                    continue
                data["encode"].setdefault(key, value)
                data["process"].setdefault(key, value)
        return data

    @model_validator(mode="after")
    def validate(self):
        super().validate()
        assert self.encode.dim == self.process.dim, "encode and process dims must be equal"
        assert (
            self.encode.latent_grid_size == self.process.latent_grid_size
        ), "encode and process latent_grid_size must be equal"
        return self


class Perceiver3DDecoderConfig(Attention3DWithMLPConfig):
    dim: int
    num_layers: int
    out_channels: int
    use_absolute_position_embeddings: bool = True


class Perceiver3DConfig(Perceiver3DEncoderConfig):
    decode: Perceiver3DDecoderConfig

    @model_validator(mode="before")
    @classmethod
    def validate_before(cls, data):
        super().validate_before(data)
        if isinstance(data, dict):
            data.setdefault("decode", {})
            for key, value in data.items():
                if key in {"encode", "process", "decode"}:
                    continue
                data["decode"].setdefault(key, value)
        return data

    @model_validator(mode="after")
    def validate(self):
        super().validate()
        assert self.encode.dim == self.decode.dim, "encode and decode dims must be equal"
        return self

# %% ../../nbs/nets/13_perceiver_3d.ipynb 7
def unfold_with_roll_3d(
    ten: torch.Tensor,
    window_size: tuple[int, int, int] | None,
    stride: tuple[int, int, int] | None,
    raise_large_window_error: bool = False,
    raise_large_stride_error: bool = True,
):
    if window_size is None or stride is None:
        return ten.unsqueeze(0), torch.tensor([[0, 0, 0]], device=ten.device)

    window_size = list(window_size)
    for i in range(3):
        if window_size[i] > ten.shape[i + 2]:
            if raise_large_window_error:
                raise ValueError(f"window_size[{i}] must be less than or equal to {ten.shape[i+2]}")
            window_size[i] = ten.shape[i + 2]
        if stride[i] > window_size[i] and raise_large_stride_error:
            raise ValueError(f"stride[{i}] must be less than or equal to window_size[{i}]")
    window_size = tuple(window_size)

    _, _, z, y, x = ten.shape
    wz, wy, wx = window_size
    sz, sy, sx = stride

    positions_z = torch.arange(0, z, sz, device=ten.device)
    positions_y = torch.arange(0, y, sy, device=ten.device)
    positions_x = torch.arange(0, x, sx, device=ten.device)
    positions = torch.stack(torch.meshgrid(positions_z, positions_y, positions_x, indexing="ij"), dim=-1)
    positions = rearrange(positions, "z y x three -> (z y x) three")

    # If required, number of patches along each dimension is calculated here:
    # nz = positions_z.shape[0]
    # ny = positions_y.shape[0]
    # nx = positions_x.shape[0]
    # n = nz * ny * nx

    pad_z = positions_z[-1] + wz - z
    pad_y = positions_y[-1] + wy - y
    pad_x = positions_x[-1] + wx - x
    if pad_z > 0:
        ten = torch.cat([ten, ten[:, :, :pad_z]], dim=2)
    if pad_y > 0:
        ten = torch.cat([ten, ten[:, :, :, :pad_y]], dim=3)
    if pad_x > 0:
        ten = torch.cat([ten, ten[:, :, :, :, :pad_x]], dim=4)

    windows = []
    for i, j, k in positions:
        window = ten[:, :, i : i + wz, j : j + wy, k : k + wx]
        windows.append(window)
    windows = torch.stack(windows, dim=0)

    return windows, positions

# %% ../../nbs/nets/13_perceiver_3d.ipynb 9
def fold_back_3d(
    windows: torch.Tensor,
    positions: torch.Tensor,
    output_shape: tuple[int, int, int],
    reduction="mean",
):
    z, y, x = output_shape
    b, d, wz, wy, wx = windows.shape[1:]

    ez, ey, ex = positions[-1] + torch.tensor([wz, wy, wx], device=windows.device)

    output = torch.zeros(b, d, ez, ey, ex, dtype=windows.dtype, device=windows.device)
    count = torch.zeros(b, d, ez, ey, ex, dtype=windows.dtype, device=windows.device)

    for (pz, py, px), window in zip(positions, windows):
        output[:, :, pz : pz + wz, py : py + wy, px : px + wx] += window
        count[:, :, pz : pz + wz, py : py + wy, px : px + wx] += 1

    if ez > z:
        pad_z = ez - z
        output[:, :, :pad_z] += output[:, :, -pad_z:]
        count[:, :, :pad_z] += count[:, :, -pad_z:]
        output = output[:, :, :z]
        count = count[:, :, :z]
    if ey > y:
        pad_y = ey - y
        output[:, :, :, :pad_y] += output[:, :, :, -pad_y:]
        count[:, :, :, :pad_y] += count[:, :, :, -pad_y:]
        output = output[:, :, :, :y]
        count = count[:, :, :, :y]
    if ex > x:
        pad_x = ex - x
        output[:, :, :, :, :pad_x] += output[:, :, :, :, -pad_x:]
        count[:, :, :, :, :pad_x] += count[:, :, :, :, -pad_x:]
        output = output[:, :, :, :, :x]
        count = count[:, :, :, :, :x]

    if reduction == "sum":
        pass
    elif reduction == "mean":
        output = output / count
    else:
        raise NotImplementedError(f"reduction={reduction} is not implemented")

    output = output.type_as(windows)

    return output

# %% ../../nbs/nets/13_perceiver_3d.ipynb 13
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

# %% ../../nbs/nets/13_perceiver_3d.ipynb 16
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
        latent_grid_size = self.config.latent_grid_size
        num_layers = self.config.num_layers

        self.latent_tokens = nn.Parameter(torch.empty(dim, *latent_grid_size), requires_grad=True)
        nn.init.xavier_uniform_(self.latent_tokens)

        self.position_embeddings = AbsolutePositionEmbeddings3D(dim=dim, grid_size=latent_grid_size, learnable=False)

        self.channel_mapping = channel_mapping

        self.cross_attention = nn.ModuleList(
            [
                Attention3DWithMLP(self.config.model_dump(), checkpointing_level=checkpointing_level)
                for _ in range(num_layers)
            ]
        )

        self.checkpointing_level1 = ActivationCheckpointing(1, checkpointing_level)
        self.checkpointing_level4 = ActivationCheckpointing(4, checkpointing_level)

    def _forward(
        self,
        x: torch.Tensor | list[torch.Tensor],
        sliding_window: tuple[int, int, int] | None = None,
        sliding_stride: tuple[int, int, int] | None = None,
        return_all: bool = False,
    ) -> torch.Tensor | dict[str, torch.Tensor]:
        # x: [(b, in_channels, z, y, x), ...]

        # Prepare keys and values
        def prepare_keys_values(x: torch.Tensor | list[torch.Tensor]):
            if not isinstance(x, list):
                x = [x]
            # x is now a list of tensors
            if self.channel_mapping is None:
                kvs = x
            else:
                kvs = []
                for i in range(len(x)):
                    mapped = self.channel_mapping(x[i])  # modifying in-place leads to errors when checkpointing
                    # (b, dim, z, y, x)
                    mapped_windows, _ = unfold_with_roll_3d(mapped, sliding_window, sliding_stride)
                    # (num_windows, b, dim, *sliding_window])
                    kvs.append(mapped_windows)
            return kvs

        kvs = self.checkpointing_level1(prepare_keys_values, x)
        # list of (num_windows, b, dim, *sliding_window)

        # Prepare queries
        b = kvs[0].shape[1]
        q = repeat(self.latent_tokens, "d zl yl xl -> b d zl yl xl", b=b)
        if self.position_embeddings is not None:
            q = q + self.position_embeddings(batch_size=b, device=q.device)
        # (b, dim, zl, yl, xl)

        # Perform attention
        embeddings = []
        for cross_attention_layer in self.cross_attention:
            embedding = torch.zeros_like(q)
            for kv_windows in kvs:
                for kv_window in kv_windows:
                    embedding_window = cross_attention_layer(q, kv_window, kv_window)
                    embedding = embedding + embedding_window
            q = embedding  # To pass to the next layer
            embeddings.append(embedding)
        # (b, latent_grid_size, dim)

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

# %% ../../nbs/nets/13_perceiver_3d.ipynb 19
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

        self.self_attention = nn.ModuleList()
        for _ in range(num_layers):
            relative_position_embeddings = None
            if self.config.use_relative_position_embeddings:
                relative_position_embeddings_config = RelativePositionEmbeddings3DConfig(
                    num_heads=self.config.num_heads, grid_size=self.config.latent_grid_size
                )
                relative_position_embeddings = RelativePositionEmbeddings3D(relative_position_embeddings_config)

            self.self_attention.append(
                Attention3DWithMLP(
                    self.config.model_dump(),
                    relative_position_bias=relative_position_embeddings,
                    checkpointing_level=checkpointing_level,
                )
            )

        self.checkpointing_level4 = ActivationCheckpointing(4, checkpointing_level)

    def _forward(self, qkv, return_all: bool = False) -> torch.Tensor | dict[str, torch.Tensor]:
        # qkv: (b, dim, zl, yl, xl)

        embeddings = []
        embedding = qkv
        for self_attention_layer in self.self_attention:
            embedding = self_attention_layer(embedding, embedding, embedding)
            embeddings.append(embedding)
        # (b, dim, zl, yl, xl)

        return_value = embeddings[-1]
        if return_all:
            return_value = {
                "embeddings": return_value,
                "all_embeddings": embeddings,
            }

        return return_value

    def forward(self, q: torch.Tensor, return_all: bool = False):
        return self.checkpointing_level4(self._forward, q, return_all)

# %% ../../nbs/nets/13_perceiver_3d.ipynb 21
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
        # (b, dim, zl, yl, xl)

        process_embeddings = self.process(embeddings, return_all=True)["all_embeddings"]
        return_value["process_embeddings"] = process_embeddings
        embeddings = process_embeddings[-1]
        # (b, dim, zl, yl, xl)

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

# %% ../../nbs/nets/13_perceiver_3d.ipynb 24
class Perceiver3DDecoder(nn.Module, PyTorchModelHubMixin):
    def __init__(
        self,
        config: Perceiver3DDecoderConfig | Perceiver3DConfig = {},
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

        self.position_embeddings = None
        if self.config.use_absolute_position_embeddings:
            self.position_embeddings = AbsolutePositionEmbeddings3D()

        self.cross_attention = nn.ModuleList(
            [
                Attention3DWithMLP(config.model_dump(), checkpointing_level=checkpointing_level)
                for _ in range(num_layers)
            ]
        )

        self.channel_mapping = Perceiver3DChannelMapping(in_channels=dim, out_channels=self.config.out_channels)

        self.checkpointing_level4 = ActivationCheckpointing(4, checkpointing_level)

    def _forward(
        self,
        kv: torch.Tensor,
        out_shape: tuple[int, int, int],
        sliding_window: tuple[int, int, int] | None = None,
        sliding_stride: tuple[int, int, int] | None = None,
        crop_offsets: torch.Tensor = None,
        return_all: bool = False,
    ) -> torch.Tensor | dict[str, torch.Tensor]:
        # kv: (b, dim, zl, yl, xl)

        # Prepare queries
        b = kv.shape[0]
        z, y, x = out_shape
        q = repeat(self.empty_token, "d 1 -> b d z y x", b=b, z=z, y=y, x=x)
        # (b, dim, z, y, x)
        if self.position_embeddings is not None:
            q = q + self.position_embeddings(
                batch_size=b, dim=q.shape[1], grid_size=out_shape, device=q.device, crop_offsets=crop_offsets
            )
        # (b, dim, z, y, x)

        # Perform attention
        outputs = []
        for cross_attention_layer in self.cross_attention:
            q_windows, q_positions = unfold_with_roll_3d(q, sliding_window, sliding_stride)
            # (num_windows, b, dim, *sliding_window)
            new_q_windows = []
            for q_window in q_windows:
                output_window = cross_attention_layer(q_window, kv, kv)
                new_q_windows.append(output_window)
            new_q_windows = torch.stack(new_q_windows, dim=0)
            # (num_windows, b, dim, *sliding_window)
            q = fold_back_3d(new_q_windows, q_positions, q.shape[2:])
            outputs.append(q)
        # list of (b, dim, z, y, x)

        output = outputs[-1]
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
