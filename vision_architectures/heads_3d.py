# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/11_heads_3d.ipynb.

# %% auto 0
__all__ = ['ClassificationHead3D']

# %% ../nbs/11_heads_3d.ipynb 2
from torch import nn
from segmentation_models_pytorch.base.modules import Activation

# %% ../nbs/11_heads_3d.ipynb 5
# Inspiration: https://github.com/qubvel-org/segmentation_models.pytorch/blob/main/segmentation_models_pytorch/base/heads.py
class ClassificationHead3D(nn.Sequential):
    def __init__(self, in_channels, classes, pooling="avg", dropout=0.2, activation=None):
        if pooling not in ("max", "avg"):
            raise ValueError("Pooling should be one of ('max', 'avg'), got {}.".format(pooling))
        pool = nn.AdaptiveAvgPool3d(1) if pooling == "avg" else nn.AdaptiveMaxPool3d(1)
        flatten = nn.Flatten()
        dropout = nn.Dropout(p=dropout, inplace=True) if dropout else nn.Identity()
        linear = nn.Linear(in_channels, classes, bias=True)
        activation = Activation(activation)
        super().__init__(pool, flatten, dropout, linear, activation)
