# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/utils/02_activations.ipynb.

# %% auto 0
__all__ = ['get_act_layer']

# %% ../../nbs/utils/02_activations.ipynb 2
from torch import nn

# %% ../../nbs/utils/02_activations.ipynb 4
def get_act_layer(activation_name: str, *args, **kwargs):
    if activation_name == "relu":
        act_layer = nn.ReLU(*args, **kwargs)
    elif activation_name == "relu6":
        act_layer = nn.ReLU6(*args, **kwargs)
    elif activation_name == "leaky_relu":
        act_layer = nn.LeakyReLU(*args, **kwargs)
    elif activation_name == "sigmoid":
        act_layer = nn.Sigmoid(*args, **kwargs)
    elif activation_name == "tanh":
        act_layer = nn.Tanh(*args, **kwargs)
    elif activation_name == "softmax":
        act_layer = nn.Softmax(*args, **kwargs)
    elif activation_name == "gelu":
        act_layer = nn.GELU(*args, **kwargs)
    elif activation_name == "silu":
        act_layer = nn.SiLU(*args, **kwargs)
    else:
        raise ValueError(f"Activation {activation_name} not implemented")

    return act_layer
