# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/blocks/04_cnn.ipynb.

# %% auto 0
__all__ = ['CNNBlockConfig', 'CNNBlock3D']

# %% ../../nbs/blocks/04_cnn.ipynb 2
from typing import Any, Literal

import torch
from torch import nn

from ..utils.activation_checkpointing import ActivationCheckpointing
from ..utils.activations import get_act_layer
from ..utils.custom_base_model import CustomBaseModel, Field
from ..utils.normalizations import get_norm_layer
from ..utils.rearrange import rearrange_channels

# %% ../../nbs/blocks/04_cnn.ipynb 4
class CNNBlockConfig(CustomBaseModel):
    in_channels: int
    out_channels: int
    kernel_size: int
    padding: int | tuple[int, ...] | str = "same"
    stride: int = 1
    conv_kwargs: dict[str, Any] = {}
    transposed: bool = Field(False, description="Whether to perform ConvTranspose instead of Conv")

    sequence: Literal["ADN", "AND", "DAN", "DNA", "NAD", "NDA"] = "NDA"

    normalization: str | None = None
    drop_prob: float = 0.0
    activation: str | None = None

# %% ../../nbs/blocks/04_cnn.ipynb 6
class CNNBlock3D(nn.Module):
    def __init__(self, config: CNNBlockConfig = {}, checkpointing_level: int = 0, **kwargs):
        super().__init__()

        self.config = CNNBlockConfig.model_validate(config | kwargs)

        normalization = self.config.normalization
        activation = self.config.activation
        drop_prob = self.config.drop_prob
        sequence = self.config.sequence

        bias = True
        if normalization is not None and normalization.startswith("batchnorm") and sequence.startswith("N"):
            bias = False

        cnn_module = nn.Conv3d
        if self.config.transposed:
            cnn_module = nn.ConvTranspose3d
        self.cnn = cnn_module(
            in_channels=self.config.in_channels,
            out_channels=self.config.out_channels,
            kernel_size=self.config.kernel_size,
            padding=self.config.padding,
            stride=self.config.stride,
            bias=bias,
            **self.config.conv_kwargs,
        )

        self.norm_layer = get_norm_layer(normalization, self.config.out_channels)
        self.act_layer = get_act_layer(activation)
        self.dropout = nn.Dropout(drop_prob)

        self.checkpointing_level1 = ActivationCheckpointing(1, checkpointing_level)

    def _forward(self, x: torch.Tensor, channels_first: bool = True):
        # x: (b, [in_channels], z, y, x, [in_channels])

        x = rearrange_channels(x, channels_first, True)
        # Now x is (b, in_channels, z, y, x)

        x = self.cnn(x)
        # Now x is (b, out_channels, z, y, x)

        for layer in self.config.sequence:
            if layer == "A":
                x = self.act_layer(x)
            elif layer == "D":
                x = self.dropout(x)
            elif layer == "N":
                x = self.norm_layer(x)
            # (b, out_channels, z, y, x)

        x = rearrange_channels(x, True, channels_first)
        # (b, [out_channels], z, y, x, [out_channels])

        return x

    def forward(self, *args, **kwargs):
        return self.checkpointing_level1(self._forward, *args, **kwargs)
