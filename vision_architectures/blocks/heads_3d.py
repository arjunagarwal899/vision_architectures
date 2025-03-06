# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/blocks/01_heads_3d.ipynb.

# %% auto 0
__all__ = ['ClassificationHead3D', 'SegmentationHead3D']

# %% ../../nbs/blocks/01_heads_3d.ipynb 2
from segmentation_models_pytorch.base.modules import Activation
from torch import nn

# %% ../../nbs/blocks/01_heads_3d.ipynb 5
# Inspiration:
# https://github.com/qubvel-org/segmentation_models.pytorch/blob/main/segmentation_models_pytorch/base/heads.py
class ClassificationHead3D(nn.Sequential):
    def __init__(self, in_channels, classes, pooling="avg", dropout=0.2, activation=None):
        if pooling not in ("max", "avg"):
            raise ValueError(f"Pooling should be one of ('max', 'avg'), got {pooling}.")
        pool = nn.AdaptiveAvgPool3d(1) if pooling == "avg" else nn.AdaptiveMaxPool3d(1)
        flatten = nn.Flatten()
        dropout = nn.Dropout(p=dropout, inplace=True) if dropout else nn.Identity()
        linear = nn.Linear(in_channels, classes, bias=True)
        activation = Activation(activation)
        super().__init__(pool, flatten, dropout, linear, activation)

# %% ../../nbs/blocks/01_heads_3d.ipynb 7
# Inspiration: https://github.com/qubvel-org/segmentation_models.pytorch/blob/main/segmentation_models_pytorch/base/heads.py
class SegmentationHead3D(nn.Sequential):
    def __init__(self, in_channels, out_channels, kernel_size=3, activation=None, upsampling=1):
        conv3d = nn.Conv3d(in_channels, out_channels, kernel_size=kernel_size, padding=kernel_size // 2)
        upsampling = nn.Upsample(scale_factor=upsampling, mode="trilinear") if upsampling > 1 else nn.Identity()
        activation = Activation(activation)
        super().__init__(conv3d, upsampling, activation)
