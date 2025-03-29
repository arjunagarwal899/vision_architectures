# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/blocks/04_cnn.ipynb.

# %% auto 0
__all__ = ['CNNBlockConfig', 'MultiResCNNBlockConfig', 'CNNBlock3D', 'MultiResCNNBlock3D']

# %% ../../nbs/blocks/04_cnn.ipynb 2
from typing import Any, Literal

import torch
from torch import nn

from ..utils.activation_checkpointing import ActivationCheckpointing
from ..utils.activations import get_act_layer
from ..utils.custom_base_model import CustomBaseModel, Field, field_validator, model_validator
from ..utils.normalizations import get_norm_layer
from ..utils.rearrange import rearrange_channels
from ..utils.residuals import Residual

# %% ../../nbs/blocks/04_cnn.ipynb 4
class CNNBlockConfig(CustomBaseModel):
    in_channels: int
    out_channels: int
    kernel_size: int | tuple[int, ...]
    padding: int | tuple[int, ...] | str = "same"
    stride: int = 1
    conv_kwargs: dict[str, Any] = {}
    transposed: bool = Field(False, description="Whether to perform ConvTranspose instead of Conv")

    normalization: str | None = "batchnorm3d"
    activation: str | None = "relu"

    sequence: Literal["ADN", "AND", "DAN", "DNA", "NAD", "NDA"] = "NDA"

    drop_prob: float = 0.0


class MultiResCNNBlockConfig(CustomBaseModel):
    in_channels: int
    out_channels: int
    kernel_sizes: tuple[int | tuple[int, ...], ...] = (3, 5, 7)
    filter_ratios: tuple[float, ...] = Field(
        (1, 2, 3), description="Ratio of filters to out_channels for each conv layer. Will be scaled to sum to 1."
    )
    padding: Literal["same"] = "same"
    stride: int = 1
    conv_kwargs: dict[str, Any] = {}
    transposed: bool = Field(False, description="Whether to perform ConvTranspose instead of Conv")

    normalization: str | None = "batchnorm3d"
    activation: str | None = "relu"

    sequence: Literal["ADN", "AND", "DAN", "DNA", "NAD", "NDA"] = "NDA"

    drop_prob: float = 0.0

    @field_validator("filter_ratios", mode="after")
    @classmethod
    def scale_filter_ratios(cls, filter_ratios):
        filter_ratios = tuple(ratio / sum(filter_ratios) for ratio in filter_ratios)
        return filter_ratios

    @model_validator(mode="after")
    def validate(self):
        super().validate()
        assert self.kernel_sizes == (3, 5, 7), "Only kernel sizes of (3, 5, 7) are supported for MultiResCNNBlock"
        assert len(self.kernel_sizes) == len(
            self.filter_ratios
        ), "kernel_sizes and filter_ratios must have the same length"
        return self

# %% ../../nbs/blocks/04_cnn.ipynb 7
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

# %% ../../nbs/blocks/04_cnn.ipynb 10
class MultiResCNNBlock3D(nn.Module):
    def __init__(self, config: MultiResCNNBlockConfig = {}, checkpointing_level: int = 0, **kwargs):
        super().__init__()

        self.config = MultiResCNNBlockConfig.model_validate(config | kwargs)

        assert self.config.kernel_sizes == (3, 5, 7), "Only kernel sizes of (3, 5, 7) are supported for now"

        all_out_channels = [max(1, int(self.config.out_channels * ratio)) for ratio in self.config.filter_ratios[:-1]]
        last_out_channels = self.config.out_channels - sum(all_out_channels)
        all_out_channels.append(last_out_channels)
        if last_out_channels <= 0:
            raise ValueError(
                f"These filter values ({self.config.filter_ratios}) won't work with the given out_channels. Please "
                f"adjust them. The out_channels of each conv layer is coming out to be {all_out_channels}."
            )
        all_in_channels = [self.config.in_channels] + all_out_channels[:-1]

        self.convs = nn.ModuleList(
            [
                CNNBlock3D(
                    self.config.model_dump(),
                    in_channels=in_channels,
                    out_channels=out_channels,
                    kernel_size=3,
                )
                for in_channels, out_channels in zip(all_in_channels, all_out_channels)
            ]
        )

        self.residual_conv = CNNBlock3D(
            self.config.model_dump(),
            in_channels=self.config.in_channels,
            out_channels=self.config.out_channels,
            kernel_size=1,
        )

        self.residual = Residual()

        self.checkpointing_level2 = ActivationCheckpointing(2, checkpointing_level)

    def _forward(self, x: torch.Tensor, channels_first: bool = True):
        # x: (b, [in_channels], z, y, x, [in_channels])

        x = rearrange_channels(x, channels_first, True)
        # (b, in_channels, z, y, x)

        residual = self.residual_conv(x)
        # (b, out_channels, z, y, x)

        conv_outputs = []
        for conv in self.convs:
            conv_input = conv_outputs[-1] if conv_outputs else x
            conv_output = conv(conv_input)
            conv_outputs.append(conv_output)
            # (b, one_of_all_out_channels, z, y, x)

        x = torch.cat(conv_outputs, dim=1)
        # (b, out_channels, z, y, x)

        x = self.residual(x, residual)
        # (b, out_channels, z, y, x)

        x = rearrange_channels(x, True, channels_first)
        # (b, [out_channels], z, y, x, [out_channels])

        return x

    def forward(self, *args, **kwargs):
        return self.checkpointing_level2(self._forward, *args, **kwargs)
