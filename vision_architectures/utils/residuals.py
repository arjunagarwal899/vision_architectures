# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/utils/05_residuals.ipynb.

# %% auto 0
__all__ = ['Residual', 'StochasticDepthResidual']

# %% ../../nbs/utils/05_residuals.ipynb 2
from typing import Literal

import torch
from torch import nn

# %% ../../nbs/utils/05_residuals.ipynb 4
class Residual(nn.Module):
    """A simple residual connection.

    This has been saved as an nn.Module so that it can always be converted to a stochastic version if required.
    """

    def forward(self, old_value: torch.Tensor, new_value: torch.Tensor):
        """Simply adds the new value to the old value.

        Args:
            old_value: Old value to be added to.
            new_value: New value to be added.

        Returns:
            Value after adding the new value to the old value.
        """
        return old_value + new_value

# %% ../../nbs/utils/05_residuals.ipynb 5
class StochasticDepthResidual(Residual):
    """This class can be wrapped around a list of modules to randomly drop some of them during training."""

    def __init__(self, survival_prob: float = 1.0, dropout_type: Literal["layer", "neuron"] = "layer"):
        """Initializes the StochasticDepthResidual module.

        Use ``dropout_type="layer"`` for most occasions as that implements true stochastic depth dropout as per SOTA
        papers. Use `dropout_type="neuron"` only if you want to drop individual neurons.

        Args:
            survival_prob: Prbability that every layer / neuron will survive the residual connection. Defaults to 1.0.
            dropout_type: Defaults to "layer".
        """

        super().__init__()
        assert 1.0 >= survival_prob > 0.0, "Survival probability must be between (0, 1]"
        self.survival_prob = survival_prob
        self.dropout_type = dropout_type

    def forward(self, old_value: torch.Tensor, new_value: torch.Tensor):
        """Drops the new value with a certain probability and scales the remaining value before adding it to the old
        value. See :py:class:`Residual` for more details.

        Args:
            old_value: Old value to be added to.
            new_value: New value to be added.

        Returns:
            Value after performing stochastic depth and adding the new value to the old value.
        """

        if not self.training:
            return super().forward(old_value, new_value)

        if self.dropout_type == "layer":
            ndim = new_value.ndim
            mask_shape = tuple([new_value.shape[0]] + [1] * (ndim - 1))
        elif self.dropout_type == "neuron":
            mask_shape = new_value.shape

        survival_mask = torch.rand(mask_shape, device=new_value.device) < self.survival_prob
        new_value = (new_value * survival_mask) / self.survival_prob
        return super().forward(old_value, new_value)

    def extra_repr(self) -> str:
        return f"survival_prob={self.survival_prob}, dropout_type='{self.dropout_type}'"
