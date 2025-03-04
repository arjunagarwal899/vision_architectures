# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/nets/02_unetr_3d_decoder.ipynb.

# %% auto 0
__all__ = ['KernelSizeType', 'UNetR3DDecoderConfig', 'UNetR3DStageConfig', 'UNetR3DConfig', 'UNetR3DConvBlock',
           'UNetR3DDeConvBlock', 'UNetR3DBlock', 'UNetR3DDecoder']

# %% ../../nbs/nets/02_unetr_3d_decoder.ipynb 2
import torch
from einops import rearrange
from huggingface_hub import PyTorchModelHubMixin
from torch import nn

from ..utils.custom_base_model import CustomBaseModel, model_validator

# %% ../../nbs/nets/02_unetr_3d_decoder.ipynb 4
KernelSizeType = int | tuple[int, int, int]


class UNetR3DDecoderConfig(CustomBaseModel):
    num_outputs: int
    conv_kernel_size: KernelSizeType
    final_layer_kernel_size: KernelSizeType


class UNetR3DStageConfig(CustomBaseModel):
    in_dim: int
    out_dim: int
    in_patch_size: tuple[int, int, int]
    out_patch_size: tuple[int, int, int]


class UNetR3DConfig(CustomBaseModel):
    in_channels: int

    decoder: UNetR3DDecoderConfig
    stages: list[UNetR3DStageConfig]

    @model_validator(mode="after")
    def validate(self):
        out_dim = None
        out_patch_size = None
        for stage in self.stages:
            if out_dim is not None:
                assert stage.in_dim == out_dim, "in_dim of each stage should match the out_stage of the previous stage"
                assert (
                    stage.in_patch_size == out_patch_size
                ), "in_patch_size of each stage should match the out_patch_size of the previous stage"
            out_dim = stage.out_dim
            out_patch_size = stage.out_patch_size
        return self

# %% ../../nbs/nets/02_unetr_3d_decoder.ipynb 8
class UNetR3DConvBlock(nn.Module):
    def __init__(self, dim, kernel_size):
        super().__init__()

        self.conv = nn.Conv3d(
            dim,
            dim,
            kernel_size=kernel_size,
            padding=tuple([k // 2 for k in kernel_size]),
            bias=False,
        )
        self.batch_norm = nn.BatchNorm3d(dim)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        x = self.conv(x)
        x = self.batch_norm(x)
        x = self.relu(x)
        return x

# %% ../../nbs/nets/02_unetr_3d_decoder.ipynb 9
class UNetR3DDeConvBlock(nn.Module):
    def __init__(self, in_dim, out_dim, kernel_size):
        super().__init__()

        self.deconv = nn.ConvTranspose3d(
            in_dim,
            out_dim,
            kernel_size=kernel_size,
            stride=kernel_size,
            padding=0,
            bias=False,
        )
        self.batch_norm = nn.BatchNorm3d(out_dim)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        x = self.deconv(x)
        x = self.batch_norm(x)
        x = self.relu(x)
        return x

# %% ../../nbs/nets/02_unetr_3d_decoder.ipynb 11
class UNetR3DBlock(nn.Module):
    def __init__(self, in_dim, out_dim, conv_kernel_size, deconv_kernel_size, is_first_layer):
        super().__init__()

        self.conv = UNetR3DConvBlock(in_dim, conv_kernel_size)
        if not is_first_layer:
            in_dim = in_dim * 2
        self.deconv = UNetR3DDeConvBlock(in_dim, out_dim, deconv_kernel_size)

    def forward(self, current_layer, previous_layer=None):
        x = self.conv(current_layer)
        if previous_layer is not None:
            x = torch.cat([x, previous_layer], dim=1)
        x = self.deconv(x)
        return x

# %% ../../nbs/nets/02_unetr_3d_decoder.ipynb 15
class UNetR3DDecoder(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: UNetR3DConfig):
        super().__init__()

        conv_kernel_size = config.decoder.conv_kernel_size
        final_layer_kernel_size = config.decoder.final_layer_kernel_size

        self.blocks = nn.ModuleList()
        for i in range(len(config.stages)):
            stage = config.stages[-i - 1]

            in_dim = stage.out_dim
            is_first_layer = i == 0

            if i == len(config.stages) - 1:
                out_dim = config.in_channels
                deconv_kernel_size = stage.out_patch_size
            else:
                out_dim = stage.in_dim
                deconv_kernel_size = tuple([o // i for o, i in zip(stage.out_patch_size, stage.in_patch_size)])

            self.blocks.append(
                UNetR3DBlock(
                    in_dim=in_dim,
                    out_dim=out_dim,
                    conv_kernel_size=conv_kernel_size,
                    deconv_kernel_size=deconv_kernel_size,
                    is_first_layer=is_first_layer,
                )
            )
        self.scan_conv = nn.Conv3d(
            config.in_channels,
            config.in_channels,
            kernel_size=final_layer_kernel_size,
            padding=tuple([k // 2 for k in final_layer_kernel_size]),
        )
        self.final_conv = nn.Conv3d(
            config.in_channels * 2,
            config.decoder.num_outputs,
            kernel_size=final_layer_kernel_size,
            padding=tuple([k // 2 for k in final_layer_kernel_size]),
        )

    def forward(self, embeddings, scan):
        # embeddings is a list of (B, C_layer, D_layer, W_layer, H_layer)
        embeddings = embeddings[::-1]

        decoded = None
        for i in range(len(embeddings)):
            embedding = embeddings[i]
            if i == 0:
                decoded = self.blocks[i](embedding)
            else:
                decoded = self.blocks[i](embedding, decoded)

        high_resolution_embeddings = self.scan_conv(scan)
        final_embeddings = torch.cat([high_resolution_embeddings, decoded], dim=1)
        decoded = self.final_conv(final_embeddings)

        return decoded

    @staticmethod
    def _reduce(loss, reduction):
        if reduction is None:
            return loss
        elif reduction == "mean":
            return loss.mean()
        elif reduction == "sum":
            return loss.sum()
        else:
            raise NotImplementedError("Please implement the reduction type")

    @staticmethod
    def soft_dice_loss_fn(
        prediction: torch.Tensor, target: torch.Tensor, reduction="mean", ignore_index: int = -100, smooth: float = 1e-8
    ):
        """
        Both prediction and target should be of the form (batch_size, num_classes, depth, width, height).

        prediction: probability scores for each class
        target: should be binary masks.
        """

        num_classes = prediction.shape[1]

        prediction = rearrange(prediction, "b n d h w -> b n (d h w)")
        target = rearrange(target, "b n d h w -> b n (d h w)")

        if ignore_index is not None:
            # Remove gradients of the predictions based on the target
            mask = target != ignore_index
            prediction = prediction * mask
            target = target * mask

        loss = 1 - (1 / num_classes) * (
            (2 * (prediction * target).sum(dim=2) + smooth)
            / ((prediction**2).sum(dim=2) + (target**2).sum(dim=2) + smooth)
        ).sum(dim=1)
        loss = UNetR3DDecoder._reduce(loss, reduction)

        return loss

    @staticmethod
    def cross_entropy_loss_fn(
        prediction: torch.Tensor, target: torch.Tensor, reduction="mean", ignore_index: int = -100, smooth: float = 1e-8
    ):
        """
        Both prediction and target should be of the form (batch_size, num_classes, depth, width, height).

        prediction: probability scores for each class
        target: should be binary masks.
        """

        num_voxels = torch.prod(torch.tensor(prediction.shape[2:]))

        prediction = rearrange(prediction, "b n d h w -> b n (d h w)")
        target = rearrange(target, "b n d h w -> b n (d h w)")

        if ignore_index is not None:
            # Remove gradients of the predictions based on the target
            mask = target != ignore_index
            prediction = prediction * mask
            target = target * mask

        loss = -(1 / num_voxels) * (target * torch.log(prediction + smooth)).sum(dim=(1, 2))
        loss = UNetR3DDecoder._reduce(loss, reduction)

        return loss

    @staticmethod
    def loss_fn(
        prediction: torch.Tensor,
        target: torch.Tensor,
        reduction="mean",
        weight_dsc=1.0,
        weight_ce=1.0,
        ignore_index=-100,
        smooth: float = 1e-8,
        return_components=False,
    ):
        """
        Both prediction and target should be of the form (batch_size, num_classes, depth, width, height).

        prediction: probability scores for each class
        target: should be binary masks.
        """

        loss1 = UNetR3DDecoder.soft_dice_loss_fn(
            prediction, target, reduction=None, ignore_index=ignore_index, smooth=smooth
        )
        loss2 = UNetR3DDecoder.cross_entropy_loss_fn(
            prediction, target, reduction=None, ignore_index=ignore_index, smooth=smooth
        )
        loss = weight_dsc * loss1 + weight_ce * loss2

        loss = UNetR3DDecoder._reduce(loss, reduction)

        if return_components:
            loss1 = UNetR3DDecoder._reduce(loss1, reduction)
            loss2 = UNetR3DDecoder._reduce(loss2, reduction)
            return loss, [loss1, loss2]
        return loss
