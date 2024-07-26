# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/10_activation_checkpointing.ipynb.

# %% auto 0
__all__ = ['ActivationCheckpointing']

# %% ../nbs/10_activation_checkpointing.ipynb 2
from torch import nn
from torch.utils.checkpoint import checkpoint
from typing import Callable

# %% ../nbs/10_activation_checkpointing.ipynb 4
class ActivationCheckpointing(nn.Module):
    def __init__(self, fn_checkpoint_level: int, training_checkpoint_level: int):
        super().__init__()

        self.perform_checkpointing = (fn_checkpoint_level <= training_checkpoint_level)

    def forward(self, fn: Callable, *args):
        if self.perform_checkpointing:
            return checkpoint(lambda: fn(*args), use_reentrant=False)
        return fn(*args)
