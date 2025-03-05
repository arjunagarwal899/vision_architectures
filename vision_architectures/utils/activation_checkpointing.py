# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/utils/01_activation_checkpointing.ipynb.

# %% auto 0
__all__ = ['ActivationCheckpointing']

# %% ../../nbs/utils/01_activation_checkpointing.ipynb 2
from typing import Callable

from torch.utils.checkpoint import checkpoint

# %% ../../nbs/utils/01_activation_checkpointing.ipynb 4
class ActivationCheckpointing:
    """Activation checkpointing levels:
    Level 0: No checkpointing
    Level 1: Single layers are checkpointed e.g. linear / conv layers
    Level 2: Small blocks are checkpointed e.g. residual blocks, attention blocks
    Level 3: Medium-sized modules are checkpointed e.g. transformer layers, decoder blocks
    Level 4: Large modules are checkpointed e.g. groups of transformer layers, decoder stages
    Level 5: Very large modules are checkpointed e.g. entire encoders, decoders etc.
    """

    def __init__(self, fn_checkpoint_level: int, training_checkpoint_level: int):
        super().__init__()

        self.perform_checkpointing = fn_checkpoint_level <= training_checkpoint_level

    def __call__(self, fn: Callable, *args, **kwargs):
        if self.perform_checkpointing:
            return checkpoint(lambda: fn(*args, **kwargs), use_reentrant=False)
        return fn(*args, **kwargs)
