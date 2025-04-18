# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/blocks/01_heads_3d.ipynb.

# %% auto 0
__all__ = ['ClassificationHead3D', 'SegmentationHead3D']

# %% ../../nbs/blocks/01_heads_3d.ipynb 2
from torch import nn

from ..utils.activations import get_act_layer

# %% ../../nbs/blocks/01_heads_3d.ipynb 5
class ClassificationHead3D(nn.Sequential):
    """A general purpose classification head for 3D inputs.

    # Inspiration:
    https://github.com/qubvel-org/segmentation_models.pytorch/blob/main/segmentation_models_pytorch/base/heads.py
    """

    def __init__(
        self, in_channels: int, classes: int, pooling: str = "avg", dropout: float = 0.2, activation: str | None = None
    ):
        """Initializes the head.

        Args:
            in_channels: Number of input channels.
            classes: Number of output classes
            pooling: Should be one of "avg" or "max". Defaults to "avg".
            dropout: Amount of dropout to apply. Defaults to 0.2.
            activation: Type of activation to perform. Defaults to None.

        Raises:
            ValueError: Incorrect pooling type.
        """
        if pooling not in ("max", "avg"):
            raise ValueError(f"Pooling should be one of ('max', 'avg'), got {pooling}.")
        pool = nn.AdaptiveAvgPool3d(1) if pooling == "avg" else nn.AdaptiveMaxPool3d(1)
        flatten = nn.Flatten()
        dropout = nn.Dropout(p=dropout, inplace=True) if dropout else nn.Identity()
        linear = nn.Linear(in_channels, classes, bias=True)
        activation = get_act_layer(activation)
        super().__init__(pool, flatten, dropout, linear, activation)

# %% ../../nbs/blocks/01_heads_3d.ipynb 7
class SegmentationHead3D(nn.Sequential):
    """A general purpose segmentation head for 3D inputs."

    Inspiration:
    https://github.com/qubvel-org/segmentation_models.pytorch/blob/main/segmentation_models_pytorch/base/heads.py
    """

    def __init__(
        self, in_channels: int, out_channels: int, kernel_size: float = 3, activation: str = None, upsampling: float = 1
    ):
        """Initializes the segmentation head

        Args:
            in_channels: Number of input channels.
            out_channels: Number of output channels.
            kernel_size: Size of the kernel. Defaults to 3.
            activation: Type of activation to perform. Defaults to None.
            upsampling: Scale factor. Defaults to 1.
        """
        conv3d = nn.Conv3d(in_channels, out_channels, kernel_size=kernel_size, padding=kernel_size // 2)
        upsampling = nn.Upsample(scale_factor=upsampling, mode="trilinear") if upsampling > 1 else nn.Identity()
        activation = get_act_layer(activation)
        super().__init__(conv3d, upsampling, activation)
