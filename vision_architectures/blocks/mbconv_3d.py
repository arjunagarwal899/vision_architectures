# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/blocks/05_mbconv_3d.ipynb.

# %% auto 0
__all__ = ['MBConv3DConfig', 'MBConv3D']

# %% ../../nbs/blocks/05_mbconv_3d.ipynb 2
import torch
from torch import nn

from .cnn import CNNBlock3D, CNNBlockConfig
from .se import SEBlock3D
from ..utils.activation_checkpointing import ActivationCheckpointing
from ..utils.custom_base_model import model_validator
from ..utils.rearrange import rearrange_channels
from ..utils.residuals import Residual

# %% ../../nbs/blocks/05_mbconv_3d.ipynb 4
class MBConv3DConfig(CNNBlockConfig):
    dim: int
    out_dim: int | None = None
    expansion_ratio: float = 6.0
    se_reduction_ratio: float = 4.0

    kernel_size: int = 3
    activation: str = "relu"
    normalization: str = "batchnorm3d"

    in_channels: None = None  # use dim instead
    out_channels: None = None  # use expansion_ratio instead

    @property
    def hidden_dim(self):
        return int(self.expansion_ratio * self.dim)

    @model_validator(mode="before")
    @classmethod
    def validate_before(cls, data: dict):
        data.setdefault("dim", data.pop("in_channels", None))
        data.setdefault("out_dim", data.pop("out_channels", None))
        return data

    @model_validator(mode="after")
    def validate(self):
        super().validate()
        min_expansion_ratio = (self.dim + 1) / self.dim
        assert self.expansion_ratio > min_expansion_ratio, f"expansion_ratio must be greater than {min_expansion_ratio}"
        if self.out_dim is None:
            self.out_dim = self.dim
        return self

# %% ../../nbs/blocks/05_mbconv_3d.ipynb 7
class MBConv3D(nn.Module):
    def __init__(self, config: MBConv3DConfig = {}, checkpointing_level: int = 0, **kwargs):
        super().__init__()

        self.config = MBConv3DConfig.model_validate(config | kwargs)

        dim = self.config.dim
        hidden_dim = self.config.hidden_dim
        out_dim = self.config.out_dim
        se_reduction_ratio = self.config.se_reduction_ratio

        se_config = self.config.model_dump() | {"dim": hidden_dim, "r": se_reduction_ratio}

        self.expand = CNNBlock3D(
            self.config,
            checkpointing_level,
            in_channels=dim,
            out_channels=hidden_dim,
            kernel_size=1,
            stride=1,
            padding=0,
        )
        self.depthwise_conv = CNNBlock3D(
            self.config,
            checkpointing_level,
            in_channels=hidden_dim,
            out_channels=hidden_dim,
            conv_kwargs=self.config.conv_kwargs | dict(groups=hidden_dim),
        )
        self.se = SEBlock3D(se_config, checkpointing_level)
        self.pointwise_conv = CNNBlock3D(
            self.config,
            checkpointing_level,
            in_channels=hidden_dim,
            out_channels=out_dim,
            kernel_size=1,
            stride=1,
            padding=0,
            activation=None,
        )

        self.residual = Residual()

        self.checkpointing_level2 = ActivationCheckpointing(2, checkpointing_level)

    def _forward(self, x: torch.Tensor, channels_first: bool = True):
        # x: (b, [dim], z, y, x, [dim])

        x = rearrange_channels(x, channels_first, True)
        # Now x is (b, dim, z, y, x)

        res_connection = x

        # Expand
        x = self.expand(x, channels_first=True)
        # (b, hidden_dim, z, y, x)

        # Depthwise Conv
        x = self.depthwise_conv(x, channels_first=True)
        # (b, hidden_dim, z, y, x)

        # SE
        x = self.se(x, channels_first=True)
        # (b, hidden_dim, z, y, x)

        # Pointwise Conv
        x = self.pointwise_conv(x, channels_first=True)
        # (b, dim, z, y, x)

        # Residual
        if x.shape == res_connection.shape:
            x = self.residual(x, res_connection)

        x = rearrange_channels(x, True, channels_first)
        # (b, [dim], z, y, x, [dim])

        return x

    def forward(self, *args, **kwargs):
        return self.checkpointing_level2(self._forward, *args, **kwargs)
