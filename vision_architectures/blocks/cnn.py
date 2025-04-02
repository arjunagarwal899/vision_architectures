# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/blocks/04_cnn.ipynb.

# %% auto 0
__all__ = ['possible_sequences', 'CNNBlockConfig', 'MultiResCNNBlockConfig', 'CNNBlock3D', 'MultiResCNNBlock3D']

# %% ../../nbs/blocks/04_cnn.ipynb 2
from itertools import chain, permutations
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
possible_sequences = ["".join(p) for p in chain.from_iterable(permutations("ACDN", r) for r in range(5)) if "C" in p]


class CNNBlockConfig(CustomBaseModel):
    in_channels: int
    out_channels: int
    kernel_size: int | tuple[int, ...]
    padding: int | tuple[int, ...] | str = "same"
    stride: int = 1
    conv_kwargs: dict[str, Any] = {}
    transposed: bool = Field(False, description="Whether to perform ConvTranspose instead of Conv")

    normalization: str | None = "batchnorm3d"
    normalization_pre_args: list = []
    normalization_post_args: list = []
    normalization_kwargs: dict = {}
    activation: str | None = "relu"
    activation_kwargs: dict = {}

    sequence: Literal[tuple(possible_sequences)] = "CNDA"

    drop_prob: float = 0.0

    @model_validator(mode="after")
    def validate(self):
        super().validate()
        if self.normalization is None and "N" in self.sequence:
            self.sequence = self.sequence.replace("N", "")
        if self.activation is None and "A" in self.sequence:
            self.sequence = self.sequence.replace("A", "")
        if self.drop_prob == 0 and "D" in self.sequence:
            self.sequence = self.sequence.replace("D", "")
        return self


class MultiResCNNBlockConfig(CNNBlockConfig):
    kernel_sizes: tuple[int | tuple[int, ...], ...] = (3, 5, 7)
    filter_ratios: tuple[float, ...] = Field(
        (1, 2, 3), description="Ratio of filters to out_channels for each conv layer. Will be scaled to sum to 1."
    )
    padding: Literal["same"] = "same"

    kernel_size: int = 3

    @field_validator("filter_ratios", mode="after")
    @classmethod
    def scale_filter_ratios(cls, filter_ratios):
        filter_ratios = tuple(ratio / sum(filter_ratios) for ratio in filter_ratios)
        return filter_ratios

    @model_validator(mode="after")
    def validate(self):
        super().validate()
        assert self.kernel_sizes == (3, 5, 7), "Only kernel sizes of (3, 5, 7) are supported for MultiResCNNBlock"
        assert self.kernel_size == 3, "only kernel_size = 3 is supported for MultiResCNNBlock"
        assert len(self.kernel_sizes) == len(
            self.filter_ratios
        ), "kernel_sizes and filter_ratios must have the same length"
        return self

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
        if normalization is not None and normalization.startswith("batchnorm") and "CN" in sequence:
            bias = False

        conv_module = nn.Conv3d
        if self.config.transposed:
            conv_module = nn.ConvTranspose3d
        self.conv = conv_module(
            in_channels=self.config.in_channels,
            out_channels=self.config.out_channels,
            kernel_size=self.config.kernel_size,
            padding=self.config.padding,
            stride=self.config.stride,
            bias=bias,
            **self.config.conv_kwargs,
        )

        self.norm = None
        self.act = None
        self.dropout = None

        norm_channels = self.config.out_channels
        if "N" in sequence.split("C")[0]:
            norm_channels = self.config.in_channels

        if "N" in sequence:
            self.norm = get_norm_layer(
                normalization,
                *self.config.normalization_pre_args,
                norm_channels,
                *self.config.normalization_post_args,
                **self.config.normalization_kwargs,
            )
        if "A" in sequence:
            self.act = get_act_layer(activation, **self.config.activation_kwargs)
        if "D" in sequence:
            self.dropout = nn.Dropout(drop_prob)

        self.checkpointing_level1 = ActivationCheckpointing(1, checkpointing_level)

    def _forward(self, x: torch.Tensor, channels_first: bool = True):
        # x: (b, [in_channels], z, y, x, [in_channels])

        x = rearrange_channels(x, channels_first, True)
        # Now x is (b, in_channels, z, y, x)

        for layer in self.config.sequence:
            if layer == "C":
                x = self.conv(x)
            if layer == "A":
                x = self.act(x)
            elif layer == "D":
                x = self.dropout(x)
            elif layer == "N":
                x = self.norm(x)
        # (b, out_channels, z, y, x)

        x = rearrange_channels(x, True, channels_first)
        # (b, [out_channels], z, y, x, [out_channels])

        return x

    def forward(self, *args, **kwargs):
        return self.checkpointing_level1(self._forward, *args, **kwargs)

# %% ../../nbs/blocks/04_cnn.ipynb 9
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
                    checkpointing_level,
                    in_channels=in_channels,
                    out_channels=out_channels,
                    kernel_size=3,
                )
                for in_channels, out_channels in zip(all_in_channels, all_out_channels)
            ]
        )

        self.residual_conv = CNNBlock3D(
            self.config.model_dump(),
            checkpointing_level,
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
