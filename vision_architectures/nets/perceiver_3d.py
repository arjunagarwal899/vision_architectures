# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/nets/13_perceiver_3d.ipynb.

# %% auto 0
__all__ = ['Perceiver3DChannelMappingConfig', 'Perceiver3DEncoderEncodeConfig', 'Perceiver3DEncoderProcessConfig',
           'Perceiver3DEncoderConfig', 'Perceiver3DDecoderConfig', 'Perceiver3DConfig', 'Perceiver3DChannelMapping',
           'Perceiver3DEncoderEncode', 'Perceiver3DEncoderProcess', 'Perceiver3DEncoder', 'Perceiver3DDecoder']

# %% ../../nbs/nets/13_perceiver_3d.ipynb 2
import torch
from einops import rearrange, repeat
from huggingface_hub import PyTorchModelHubMixin
from torch import nn

from ..layers.attention import Attention1DWithMLP, Attention1DWithMLPConfig
from ..layers.embeddings import AbsolutePositionEmbeddings3D
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

# %% ../../nbs/nets/13_perceiver_3d.ipynb 8
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

# %% ../../nbs/nets/13_perceiver_3d.ipynb 11
class Perceiver3DEncoderEncode(nn.Module):
    def __init__(
        self,
        config: Perceiver3DEncoderEncodeConfig | Perceiver3DEncoderConfig = {},
        channel_mapping: Perceiver3DChannelMapping | None = None,
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

        self.cross_attention = nn.ModuleList([Attention1DWithMLP(self.config.model_dump()) for _ in range(num_layers)])

    def forward(self, x, return_all: bool = False) -> torch.Tensor | dict[str, torch.Tensor]:
        # x: (b, dim_or_channels, z, y, x)

        if self.channel_mapping is not None:
            x = self.channel_mapping(x)
        # (b, dim, z, y, x)

        b = x.shape[0]

        q = repeat(self.latent_tokens, "t d -> b t d", b=b)
        # (b, num_latent_tokens, dim)
        kv = rearrange(x, "b d z y x -> b (z y x) d")
        # (b, z*y*x, dim)
        embeddings = [q]
        for cross_attention_layer in self.cross_attention:
            q = embeddings[-1]
            embeddings.append(cross_attention_layer(q, kv, kv))
        # (b, num_latent_tokens, dim)

        return_value = embeddings[-1]
        if return_all:
            return_value = {
                "embeddings": return_value,
                "all_embeddings": embeddings,
            }

        return return_value

# %% ../../nbs/nets/13_perceiver_3d.ipynb 13
class Perceiver3DEncoderProcess(nn.Module):
    def __init__(self, config: Perceiver3DEncoderProcessConfig | Perceiver3DEncoderConfig = {}, **kwargs):
        super().__init__()

        if isinstance(config, Perceiver3DEncoderConfig):
            config = config.process
        if "process" in config:
            config = config["process"]

        self.config = Perceiver3DEncoderProcessConfig.model_validate(config | kwargs)

        num_layers = self.config.num_layers

        self.self_attention = nn.ModuleList([Attention1DWithMLP(self.config.model_dump()) for _ in range(num_layers)])

    def forward(self, q, return_all: bool = False) -> torch.Tensor | dict[str, torch.Tensor]:
        # q: (b, num_tokens, dim)

        embeddings = [q]
        for self_attention_layer in self.self_attention:
            qkv = embeddings[-1]
            embeddings.append(self_attention_layer(qkv, qkv, qkv))
        # (b, num_tokens, dim)

        return_value = embeddings[-1]
        if return_all:
            return_value = {
                "embeddings": return_value,
                "all_embeddings": embeddings,
            }

        return return_value

# %% ../../nbs/nets/13_perceiver_3d.ipynb 15
class Perceiver3DEncoder(nn.Module, PyTorchModelHubMixin):
    def __init__(
        self,
        config: Perceiver3DEncoderConfig = {},
        channel_mapping: Perceiver3DChannelMapping | None = None,
        **kwargs,
    ):
        super().__init__()

        self.config = Perceiver3DEncoderConfig.model_validate(config | kwargs)

        self.encode = Perceiver3DEncoderEncode(config.encode, channel_mapping)
        self.process = Perceiver3DEncoderProcess(config.process)

    def forward(self, x, return_all: bool = False) -> torch.Tensor | dict[str, torch.Tensor]:
        # x: (b, in_channels, z, y, x)

        return_value = {}

        encode_embeddings = self.encode(x, return_all=True)["all_embeddings"]
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

# %% ../../nbs/nets/13_perceiver_3d.ipynb 18
class Perceiver3DDecoder(nn.Module, PyTorchModelHubMixin):
    def __init__(
        self,
        config: Perceiver3DDecoderConfig | Perceiver3DConfig = {},
        position_embeddings: AbsolutePositionEmbeddings3D = None,
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

        self.empty_token = nn.Parameter(torch.empty(dim, 1), requires_grad=True)
        nn.init.xavier_uniform_(self.empty_token)

        self.position_embeddings = position_embeddings

        self.cross_attention = nn.ModuleList([Attention1DWithMLP(config.model_dump()) for _ in range(num_layers)])

        self.channel_mapping = Perceiver3DChannelMapping(in_channels=dim, out_channels=self.config.out_channels)

    def forward(
        self, kv, out_shape: tuple[int, int, int], return_all: bool = False
    ) -> torch.Tensor | dict[str, torch.Tensor]:
        # kv: (b, num_tokens, dim)

        b = kv.shape[0]

        q = repeat(self.empty_token, "d 1 -> b d z y x", b=b, z=out_shape[0], y=out_shape[1], x=out_shape[2])
        # (b, dim, z, y, x)

        if self.position_embeddings is not None:
            q = q + self.position_embeddings(batch_size=b, grid_size=out_shape)

        q = rearrange(q, "b d z y x -> b (z y x) d")
        # (b, num_output_tokens, dim)
        outputs = [q]
        for cross_attention_layer in self.cross_attention:
            q = outputs[-1]
            outputs.append(cross_attention_layer(q, kv, kv))
        # (b, num_output_tokens, dim)

        output = outputs[-1]
        output = rearrange(output, "b (z y x) d -> b d z y x", z=out_shape[0], y=out_shape[1], x=out_shape[2])
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
