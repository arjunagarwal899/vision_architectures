# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/nets/12_upernet_2d.ipynb.

# %% auto 0
__all__ = ['UPerNet2DFusion', 'UPerNet2D']

# %% ../../nbs/nets/12_upernet_2d.ipynb 2
import torch
from huggingface_hub import PyTorchModelHubMixin
from torch import nn
from torch.nn import functional as F

from .fpn_2d import FPN2D
from ..utils.activation_checkpointing import ActivationCheckpointing

# %% ../../nbs/nets/12_upernet_2d.ipynb 5
class UPerNet2DFusion(nn.Module):
    def __init__(self, dim, num_layers, fusion_shape=None, checkpointing_level=0):
        super().__init__()

        self.fusion_shape = fusion_shape
        # (h, w) | None

        self.conv = nn.Sequential(
            nn.Conv2d(dim * num_layers, dim, kernel_size=3, stride=1, padding=1, bias=False),
            nn.BatchNorm2d(dim),
            nn.ReLU(inplace=True),
        )

        self.checkpointing_level1 = ActivationCheckpointing(1, checkpointing_level)
        self.checkpointing_level2 = ActivationCheckpointing(2, checkpointing_level)

    def concat_features(self, features: list[torch.Tensor]):
        # features: List of [(b, dim, h1, w1), (b, dim, h2, ...]

        if self.fusion_shape is None:
            self.fusion_shape = features[0].shape[-2:]
            # (h, w)

        for i in range(len(features)):
            features[i] = F.interpolate(
                features[i],
                size=self.fusion_shape,
                mode="bilinear",
                align_corners=False,
            )
            # Each is (b, dim, h, w)

        concatenated_features = torch.cat(features, dim=1)
        # (b, dim * num_layers, h, w)

        return concatenated_features

    def fuse_features(self, concatenated_features: torch.Tensor):
        # (b, dim * num_layers, h, w)
        fused_features = self.conv(concatenated_features)
        # (b, dim, h, w)

        return fused_features

    def forward(self, features: list[torch.Tensor]):
        # features: List of [(b, dim, h1, w1), (b, dim, h2, w2), ...]
        concatenated_features = self.checkpointing_level1(self.concat_features, features)
        # (b, dim * num_layers, h, w)
        fused_features = self.checkpointing_level2(self.fuse_features, concatenated_features)
        # (b, dim, h, w)

        return fused_features

# %% ../../nbs/nets/12_upernet_2d.ipynb 8
class UPerNet2D(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config):
        super().__init__()

        self.fpn = FPN2D(config)

        dim = config["fpn_dim"]
        num_layers = len(config["in_dims"])
        num_objects = config["num_objects"]
        checkpointing_level = config["checkpointing_level"]
        enabled_outputs = config["enabled_outputs"]
        fusion_shape = config.get("fusion_shape", None)

        self.output_shape = config["output_shape"]
        # (d, h, w)

        self.fusion = None
        self.object_head = None
        self.scene_head = None
        self.part_head = None
        self.material_head = None
        self.texture_head = None

        # TODO: Implement scene, part, material, texture
        if {"object", "part"} & set(enabled_outputs):
            self.fusion = UPerNet2DFusion(dim, num_layers, fusion_shape, checkpointing_level=checkpointing_level)

            if "object" in enabled_outputs:
                self.object_head = nn.Sequential(
                    nn.Conv2d(dim, dim, kernel_size=3, stride=1, padding=1, bias=False),
                    nn.BatchNorm2d(dim),
                    nn.ReLU(inplace=True),
                    nn.Conv2d(dim, num_objects, kernel_size=1, stride=1),
                )

            if "part" in enabled_outputs:
                raise NotImplementedError("Part output not implemented yet")

        if "scene" in enabled_outputs:
            raise NotImplementedError("Scene output not implemented yet")

        if "material" in enabled_outputs:
            raise NotImplementedError("Material output not implemented yet")

        if "texture" in enabled_outputs:
            raise NotImplementedError("Texture output not implemented yet")

    def forward(self, features: list[torch.Tensor]):
        # features: [
        #   (b, in_dim1, h1, w1),
        #   (b, in_dim2, h2, w2),
        #   ...
        # ]

        features = self.fpn(features)
        # features: [
        #   (b, fpn_dim, h1, w1),
        #   (b, fpn_dim, h2, w2),
        #   ...
        # ]

        output = {}

        if self.fusion is not None:
            fused_features = self.fusion(features)
            # (b, fpn_dim, h1, w1)

            object_logits = self.object_head(fused_features)
            # (b, num_objects, h1, w1)

            object_logits = F.interpolate(
                object_logits,
                size=self.output_shape,
                mode="bilinear",
                align_corners=False,
            )
            # (b, num_objects, h, w)

            output["object"] = object_logits

        return output
