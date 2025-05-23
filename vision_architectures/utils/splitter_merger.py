# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/utils/09_splitter_merger.ipynb.

# %% auto 0
__all__ = ['SplitterConfig', 'Splitter', 'Merger']

# %% ../../nbs/utils/09_splitter_merger.ipynb 1
from collections.abc import Generator
from functools import wraps
from typing import Literal

import torch
from einops import rearrange
from torch.nn import functional as F

from .custom_base_model import CustomBaseModel, Field, model_validator

# %% ../../nbs/utils/09_splitter_merger.ipynb 3
class SplitterConfig(CustomBaseModel):
    split_dims: int = Field(3, description="Number of spatial dimensions.")
    split_size: int | tuple[int, ...]
    stride: int | tuple[int, ...]
    extend_mode: Literal["pad", "wrap"] | None = Field(
        "pad",
        description=(
            "Whether to pad or wrap the input tensor to get correct windows. If None, exact divisibility is expected"
        ),
    )

    raise_large_stride_error: bool = True

    @model_validator(mode="after")
    def validate(self):
        super().validate()
        if isinstance(self.split_size, int):
            self.split_size = (self.split_size,) * self.split_dims
        if isinstance(self.stride, int):
            self.stride = (self.stride,) * self.split_dims

        assert (
            len(self.stride) == len(self.split_size) == self.split_dims
        ), "Length of stride and split size must be equal to the number of spatial dimensions."

        if self.raise_large_stride_error:
            assert all(
                st <= s for st, s in zip(self.stride, self.split_size)
            ), "Stride must be smaller than split size."

        return self

# %% ../../nbs/utils/09_splitter_merger.ipynb 6
class Splitter:
    def __init__(self, config: SplitterConfig = {}, **kwargs):
        self.config = SplitterConfig.model_validate(config | kwargs)

    def get_expanded_shape(self, input_shape: tuple[int, ...] | torch.Size | torch.Tensor) -> tuple[int, ...]:
        """Get the shape of the input tensor after padding / wrapping.

        Args:
            input_shape: The shape of the input tensor. Only the last "split_dims" dimensions are considered. If a
                tensor is passed, its shape will be used.

        Returns:
            A tuple containing the shape of the input tensor after padding / wrapping.
        """
        if isinstance(input_shape, torch.Tensor):
            input_shape = input_shape.shape
        self._check_input_shape(input_shape)
        input_shape = list(input_shape[-self.config.split_dims :])

        # Get shape after padding / wrapping
        expanded_shape = []
        for i in range(self.config.split_dims):
            actual_length = input_shape[i]
            split_length = self.config.split_size[i]
            stride = self.config.stride[i]

            # Minimum length is split size
            total_length = max(actual_length, split_length)

            # If a particular dimension can be split into n different splits, then the length of that dimensions is
            # equal to split_size + (n - 1) * stride
            # So the total length minus the split size must be divisible by the stride
            total_length = total_length + (stride - (total_length - split_length)) % stride

            input_shape[i] = total_length

            expanded_shape.append(total_length)

        return expanded_shape

    def get_positions(self, input_shape: tuple[int, ...] | torch.Size | torch.Tensor) -> torch.Tensor:
        """Get the top-left coordinates of all the splits that will be generated using the config.

        Args:
            input_shape: The shape of the input tensor. Only the last "split_dims" dimensions are considered. If a
                tensor is passed, its shape will be used.

        Returns:
            A tensor of shape (num_splits, split_dims) containing the top-left coordinates of each split.
        """
        if isinstance(input_shape, torch.Tensor):
            input_shape = input_shape.shape
        self._check_input_shape(input_shape)
        input_shape = list(input_shape[-self.config.split_dims :])

        expanded_shape = self.get_expanded_shape(input_shape)

        positions = []
        for i in range(self.config.split_dims):
            total_length = expanded_shape[i]
            split_length = self.config.split_size[i]
            stride = self.config.stride[i]

            positions.append(torch.arange(0, total_length - split_length + 1, stride))
        positions = torch.stack(torch.meshgrid(*positions, indexing="ij"), dim=0)
        positions = rearrange(positions, "split_dims ... -> (...) split_dims").contiguous()

        return positions

    def get_num_splits(self, input_shape: tuple[int, ...] | torch.Size | torch.Tensor) -> int:
        """Get the number of splits that will be generated using the config.

        Args:
            input_shape: The shape of the input tensor. Only the last "split_dims" dimensions are considered. If a
                tensor is passed, its shape will be used.

        Returns:
            The number of splits that will be generated.
        """
        if isinstance(input_shape, torch.Tensor):
            input_shape = input_shape.shape
        self._check_input_shape(input_shape)
        input_shape = list(input_shape[-self.config.split_dims :])

        positions = self.get_positions(input_shape)
        num_splits = positions.shape[0]

        return num_splits

    def expand(self, x: torch.Tensor) -> torch.Tensor:
        """Expand the input tensor to the shape after padding / wrapping.

        Args:
            x: The input tensor.

        Returns:
            The expanded tensor.
        """
        self._check_input_shape(x.shape)
        expanded_shape = self.get_expanded_shape(x.shape)

        for i in range(self.config.split_dims):
            dim = x.ndim - self.config.split_dims + i
            expansion = expanded_shape[i] - x.shape[dim]
            if expansion > 0:
                if self.config.extend_mode == "pad":
                    padding = (0, 0) * (self.config.split_dims - i - 1) + (expansion // 2, expansion - expansion // 2)
                    x = F.pad(x, padding)
                elif self.config.extend_mode == "wrap":
                    x = torch.cat([x, x.narrow(dim, 0, expansion)], dim=dim)
                elif self.config.extend_mode is None:
                    assert expansion == 0, "Exact divisibility is expected when extend_mode is None."

        return x

    def split(self, x: torch.Tensor) -> Generator[torch.Tensor, None, None]:
        """Split the input tensor into smaller tensors using the config.

        Args:
            x: The input tensor.

        Yields:
            A tensor of shape (*split_size) for each split.
        """
        input_shape = x.shape
        x = self.expand(x)
        positions = self.get_positions(input_shape)

        splits = []
        for position in positions:
            starts = position.tolist()
            ends = [start + size for start, size in zip(starts, self.config.split_size)]

            slices = [slice(None)] * (x.ndim - self.config.split_dims)
            slices += [slice(starts[d], ends[d]) for d in range(self.config.split_dims)]
            yield x[tuple(slices)]

    @wraps(split)
    def __call__(self, *args, **kwargs):
        return self.split(*args, **kwargs)

    def _check_input_shape(self, input_shape: tuple[int, ...]):
        # Some checks
        assert (
            len(input_shape) >= self.config.split_dims
        ), f"Input shape {input_shape} must have at least {self.config.split_dims} length"

# %% ../../nbs/utils/09_splitter_merger.ipynb 8
class Merger:
    def __init__(self):
        raise NotImplementedError("Merger is yet to be implemented.")
