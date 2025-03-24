# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/utils/01_activation_checkpointing.ipynb.

# %% auto 0
__all__ = ['ActivationCheckpointing']

# %% ../../nbs/utils/01_activation_checkpointing.ipynb 2
from collections.abc import Callable

from torch.utils.checkpoint import checkpoint

# %% ../../nbs/utils/01_activation_checkpointing.ipynb 4
class ActivationCheckpointing:
    """This class is used to perform activation checkpointing during training. Users can set a level of checkpointing
    for each module / function in their architecture. While training, the module / function will be checkpointed if the
    training checkpoint level is greater than or equal to the checkpoint level set for the module / function.

    A general guide of the Activation checkpointing levels in this repository:

    - **Level 0**: No checkpointing
    - **Level 1**: Single layers are checkpointed e.g. linear layer + activation, conv layer + dropout
    - **Level 2**: Small blocks are checkpointed e.g. residual blocks, attention blocks, MLP blocks
    - **Level 3**: Medium-sized modules are checkpointed e.g. transformer layers, decoder blocks
    - **Level 4**: Large modules are checkpointed e.g. groups of transformer layers, decoder stages
    - **Level 5**: Very large modules are checkpointed e.g. entire encoders, decoders etc.
    """

    def __init__(self, fn_checkpoint_level: int, training_checkpoint_level: int):
        """Initialize the ActivationCheckpointing class.

        Args:
            fn_checkpoint_level: Level at which the module / function should be checkpointed
            training_checkpoint_level: Checkpointing level at which the model is being trained

        Example:
            .. code-block:: python

                class MyModel(nn.Module):
                    def __init__(self, training_checkpointing_level: int = 0):
                        super().__init__()
                        my_network = nn.Sequential(
                            nn.Linear(784, 256),
                            nn.ReLU(),
                            nn.Linear(256, 10)
                        )

                        self.activation_checkpointing_level2 = ActivationCheckpointing(2, training_checkpointing_level)

                    def forward(self, x):
                        y = self.activation_checkpointing_level2(self.my_network, x)
                        return y

            In this example, a ``training_checkpointing_level`` of greater than or equal to 2 will checkpoint ``my_network``
            during training. If it's less than 2, the network will not be checkpointed.
        """
        super().__init__()

        self.perform_checkpointing = fn_checkpoint_level <= training_checkpoint_level

    def __call__(self, fn: Callable, *fn_args, use_reentrant: bool = False, **fn_kwargs):
        """Checkpoint the module / function if the checkpointing level is greater than or equal to the training
        checkpoint level.

        Args:
            fn: The module / function to checkpoint
            use_reentrant: Passed on to torch.utils.checkpoint.checkpoint. Defaults to False.
            *fn_args: Arguments to pass to the module / function
            **fn_kwargs: Keyword arguments to pass to the module / function

        Returns:
            The checkpointed module / function if checkpointing is performed, else the module / function itself.
        """
        if self.perform_checkpointing:
            return checkpoint(lambda: fn(*fn_args, **fn_kwargs), use_reentrant=use_reentrant)
        return fn(*fn_args, **fn_kwargs)
