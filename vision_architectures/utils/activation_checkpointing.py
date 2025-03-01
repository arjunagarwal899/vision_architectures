# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/utils/01_activation_checkpointing.ipynb.

# %% auto 0
__all__ = ['ActivationCheckpointing']

# %% ../../nbs/utils/01_activation_checkpointing.ipynb 2
from torch.utils.checkpoint import checkpoint
from typing import Callable

# %% ../../nbs/utils/01_activation_checkpointing.ipynb 4
class ActivationCheckpointing():
    def __init__(self, fn_checkpoint_level: int, training_checkpoint_level: int):
        super().__init__()

        self.perform_checkpointing = (fn_checkpoint_level <= training_checkpoint_level)

    def __call__(self, fn: Callable, *args):
        if self.perform_checkpointing:
            return checkpoint(lambda: fn(*args), use_reentrant=False)
        return fn(*args)
