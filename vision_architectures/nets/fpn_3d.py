# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/nets/08_fpn_3d.ipynb.

# %% auto 0
__all__ = ['FPN3DBlock', 'FPN3D']

# %% ../../nbs/nets/08_fpn_3d.ipynb 2
import torch
from huggingface_hub import PyTorchModelHubMixin
from torch import nn
from torch.nn import functional as F

from ..utils.activation_checkpointing import ActivationCheckpointing

# %% ../../nbs/nets/08_fpn_3d.ipynb 5
class FPN3DBlock(nn.Module):
    def __init__(self, shallow_dim, fpn_dim, is_deepest=False, checkpointing_level=0):
        super().__init__()

        self.is_deepest = is_deepest
        self.checkpointing_level = checkpointing_level

        self.checkpointing_level1 = ActivationCheckpointing(1, checkpointing_level)

        self.in_conv = nn.Sequential(
            nn.Conv3d(shallow_dim, fpn_dim, kernel_size=1, bias=False),
            nn.BatchNorm3d(fpn_dim),
            nn.ReLU(inplace=True),
        )

        if not is_deepest:
            self.out_conv = nn.Sequential(
                nn.Conv3d(fpn_dim, fpn_dim, kernel_size=3, stride=1, padding=1, bias=False),
                nn.BatchNorm3d(fpn_dim),
                nn.ReLU(inplace=True),
            )

    def merge_features(self, shallow_features, deep_features):
        deep_features = F.interpolate(
            deep_features, size=shallow_features.shape[2:], mode="trilinear", align_corners=False
        )
        # (b, fpn_dim, d1, h1, w1)

        merged_features = shallow_features + deep_features
        # (b, fpn_dim, d1, h1, w1)

        merged_features = self.out_conv(merged_features)
        # (b, fpn_dim, d1, h1, w1)

        return merged_features

    def forward(self, shallow_features: torch.Tensor, deep_features: torch.Tensor):
        # shallow_features: (b, in_dim, d1, h1, w1)
        # deep_features: (b, fpn_dim, d2, h2, w2)

        shallow_features = self.in_conv(shallow_features)
        # (b, fpn_dim, d1, h1, w1)

        if self.is_deepest:
            merged_features = shallow_features
        else:
            merged_features = self.checkpointing_level1(self.merge_features, shallow_features, deep_features)
            # (b, fpn_dim, d1, h1, w1)

        return merged_features

# %% ../../nbs/nets/08_fpn_3d.ipynb 9
class FPN3D(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config):
        super().__init__()

        fpn_dim = config["fpn_dim"]
        in_dims = config["in_dims"]

        self.blocks = nn.ModuleList()
        for i in range(len(in_dims)):
            is_deepest = False
            if i == len(in_dims) - 1:
                is_deepest = True

            self.blocks.append(
                FPN3DBlock(
                    in_dims[i],
                    fpn_dim,
                    is_deepest=is_deepest,
                    checkpointing_level=config["checkpointing_level"],
                )
            )

    def forward(self, features: list[torch.Tensor]):
        # features: [
        #   (b, in_dim1, d1, h1, w1),
        #   (b, in_dim2, d2, h2, w2),
        #   ...
        # ]

        features_None = features + [None]
        for i in range(len(features), 0, -1):
            features_None[i - 1] = self.blocks[i - 1](features_None[i - 1], features_None[i])
        features = features_None[:-1]

        return features