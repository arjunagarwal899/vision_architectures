# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/nets/04_vit_3d.ipynb.

# %% auto 0
__all__ = ['ViT3DEncoderConfig', 'ViT3DConfig', 'ViT3DDecoderConfig', 'ViT3DEncoderLayer', 'ViT3DEncoder', 'ViT3DDecoderLayer',
           'ViT3DDecoder', 'ViT3DModel', 'ViT3DMIMDecoder', 'ViT3DMIM', 'ViT3DSimMIM']

# %% ../../nbs/nets/04_vit_3d.ipynb 2
from typing import Literal

import numpy as np
import torch
from einops import rearrange, repeat
from huggingface_hub import PyTorchModelHubMixin
from torch import nn

from ..layers.attention import Attention1D, Attention1DMLP, Attention1DWithMLP
from ..layers.embeddings import AbsolutePositionEmbeddings3D, PatchEmbeddings3D
from ..utils.custom_base_model import CustomBaseModel

# %% ../../nbs/nets/04_vit_3d.ipynb 4
class ViT3DEncoderConfig(CustomBaseModel):
    dim: int
    num_heads: int
    mlp_ratio: int
    layer_norm_eps: float
    attn_drop_prob: float = 0.0
    proj_drop_prob: float = 0.0
    mlp_drop_prob: float = 0.0
    proj_drop_prob: float = 0.0
    norm_location: Literal["pre", "post"] = "pre"

    encoder_depth: int


class ViT3DConfig(ViT3DEncoderConfig):
    patch_size: tuple[int, int, int]
    in_channels: int
    num_class_tokens: int

    drop_prob: float = 0.0

    # For MIM
    image_size: tuple[int, int, int] | None = None
    mask_ratio: float | None = None


class ViT3DDecoderConfig(CustomBaseModel):
    dim: int
    num_heads: int
    mlp_ratio: int
    layer_norm_eps: float
    attn_drop_prob: float = 0.0
    proj_drop_prob: float = 0.0
    mlp_drop_prob: float = 0.0
    proj_drop_prob: float = 0.0
    norm_location: Literal["pre", "post"] = "pre"

    decoder_depth: int

# %% ../../nbs/nets/04_vit_3d.ipynb 8
class ViT3DEncoderLayer(Attention1DWithMLP):
    def __init__(
        self,
        dim,
        num_heads,
        *args,
        **kwargs,
    ):
        super().__init__(
            dim=dim,
            num_heads=num_heads,
            *args,
            **kwargs,
        )

    def forward(self, qkv: torch.Tensor):
        # qkv: (b, num_tokens, dim)
        return super().forward(qkv, qkv, qkv)

# %% ../../nbs/nets/04_vit_3d.ipynb 10
class ViT3DEncoder(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: ViT3DEncoderConfig):
        super().__init__()

        self.layers = nn.ModuleList(
            [
                ViT3DEncoderLayer(
                    config.dim,
                    config.num_heads,
                    mlp_ratio=config.mlp_ratio,
                    layer_norm_eps=config.layer_norm_eps,
                    attn_drop_prob=config.attn_drop_prob,
                    proj_drop_prob=config.proj_drop_prob,
                    mlp_drop_prob=config.mlp_drop_prob,
                    norm_location=config.norm_location,
                )
                for _ in range(config.encoder_depth)
            ]
        )

    def forward(self, embeddings: torch.Tensor, return_all: bool = False):
        # hidden_states: (b, num_tokens, dim)

        layer_outputs = []
        for encoder_layer in self.layers:
            embeddings = encoder_layer(embeddings)
            # (b, num_tokens, dim)

            layer_outputs.append(embeddings)

        return_value = embeddings
        if return_all:
            return_value = {
                "embeddings": embeddings,
                "layer_outputs": layer_outputs,
            }

        return return_value

# %% ../../nbs/nets/04_vit_3d.ipynb 13
class ViT3DDecoderLayer(nn.Module):
    def __init__(
        self,
        dim,
        num_heads,
        mlp_ratio,
        layer_norm_eps,
        attn_drop_prob=0.0,
        proj_drop_prob=0.0,
        mlp_drop_prob=0.0,
        use_post_norm=False,
    ):
        super().__init__()

        self.use_post_norm = use_post_norm

        self.mhsa = Attention1D(
            dim=dim,
            num_heads=num_heads,
            attn_drop_prob=attn_drop_prob,
            proj_drop_prob=proj_drop_prob,
        )
        self.layernorm1 = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.mhca = Attention1D(
            dim=dim,
            num_heads=num_heads,
            attn_drop_prob=attn_drop_prob,
            proj_drop_prob=proj_drop_prob,
        )
        self.layernorm2 = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.mlp = Attention1DMLP(dim, mlp_ratio=mlp_ratio, activation="gelu", mlp_drop_prob=mlp_drop_prob)
        self.layernorm3 = nn.LayerNorm(dim, eps=layer_norm_eps)

    def forward(self, q: torch.Tensor, kv: torch.Tensor):
        # q: (b, num_tokens_in_q, dim)
        # kv: (b, num_tokens_in_kv, dim)

        res_connection1 = q
        # (b, num_tokens_in_q, dim)

        if not self.use_post_norm:
            q = self.layernorm1(q)
            # (b, num_tokens_in_q, dim)
            kv = self.layernorm1(kv)
            # (b, num_tokens_in_kv, dim)

        hidden_states = self.mhsa(q, q, q)
        # (b, num_tokens_in_q, dim)

        if self.use_post_norm:
            hidden_states = self.layernorm1(hidden_states)
            # (b, num_tokens_in_q, dim)

        hidden_states = hidden_states + res_connection1
        res_connection2 = hidden_states
        # (b, num_tokens_in_q, dim)

        if not self.use_post_norm:
            hidden_states = self.layernorm1(hidden_states)
            # (b, num_tokens_in_q, dim)

        hidden_states = self.mhca(hidden_states, kv, kv)
        # (b, num_tokens_in_q, dim)

        if self.use_post_norm:
            hidden_states = self.layernorm2(hidden_states)
            # (b, num_tokens_in_q, dim)

        hidden_states = hidden_states + res_connection2
        res_connection3 = hidden_states
        # (b, num_tokens_in_q, dim)

        if not self.use_post_norm:
            hidden_states = self.layernorm3(hidden_states)
            # (b, num_tokens_in_q, dim)

        hidden_states = self.mlp(hidden_states)
        # (b, num_tokens_in_q, dim)

        if self.use_post_norm:
            hidden_states = self.layernorm3(hidden_states)
            # (b, num_tokens_in_q, dim)

        hidden_states = hidden_states + res_connection3
        # (b, num_tokens_in_q, dim)

        return hidden_states

# %% ../../nbs/nets/04_vit_3d.ipynb 16
class ViT3DDecoder(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: ViT3DDecoderConfig):
        super().__init__()

        self.layers = nn.ModuleList(
            [
                ViT3DDecoderLayer(
                    config.dim,
                    config.num_heads,
                    config.mlp_ratio,
                    config.layer_norm_eps,
                    config.attn_drop_prob,
                    config.proj_drop_prob,
                    config.mlp_drop_prob,
                )
                for _ in range(config.decoder_depth)
            ]
        )

    def forward(self, q: torch.Tensor, kv: torch.Tensor, return_all: bool = False):
        # q: (b, num_q_tokens, dim)
        # kv: (b, num_kv_tokens, dim)

        embeddings = q

        layer_outputs = []
        for decoder_layer in self.layers:
            embeddings = decoder_layer(embeddings, kv)
            # (b, num_q_tokens, dim)

            layer_outputs.append(embeddings)

        return_value = embeddings
        if return_all:
            return_value = {
                "embeddings": embeddings,
                "layer_outputs": layer_outputs,
            }

        return return_value

# %% ../../nbs/nets/04_vit_3d.ipynb 19
class ViT3DModel(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: ViT3DConfig):
        super().__init__()

        self.patchify = PatchEmbeddings3D(config.patch_size, config.in_channels, config.dim)
        self.absolute_position_embeddings = AbsolutePositionEmbeddings3D(config.dim, learnable=False)
        self.pos_drop = nn.Dropout(config.drop_prob)
        self.num_class_tokens = config.num_class_tokens
        if self.num_class_tokens > 0:
            self.class_tokens = nn.Parameter(torch.randn(1, config.num_class_tokens, config.dim))
        self.encoder = ViT3DEncoder(config)

    def forward(
        self,
        pixel_values: torch.Tensor,
        spacings: torch.Tensor,
        mask_patches: torch.Tensor = None,
        mask_token: torch.Tensor = None,
        return_all: bool = False,
    ):
        # pixel_values: (b, c, z, y, x)
        # spacings: (b, 3)
        # mask_patches: (b, num_patches_z, num_patches_y, num_patches_x)
        # mask_token: (1, dim, 1, 1, 1)

        embeddings = self.patchify(pixel_values)
        # (b, dim, num_patches_z, num_patches_y, num_patches_x)

        if mask_patches is not None:
            # mask_patches (binary mask): (b, num_patches_z, num_patches_y, num_patches_x)
            # mask_token: (1, dim, 1, 1, 1)
            mask_patches = repeat(mask_patches, "b z y x -> b d z y x", d=embeddings.shape[1])
            embeddings = (embeddings * (1 - mask_patches)) + (mask_patches * mask_token)

        absolute_position_embeddings = self.absolute_position_embeddings(
            batch_size=embeddings.shape[0],
            grid_size=embeddings.shape[2:],
            spacings=spacings,
            device=pixel_values.device,
        )
        # (b, dim, num_patches_z, num_patches_y, num_patches_x)
        embeddings = embeddings + absolute_position_embeddings
        # (b, dim, num_patches_z, num_patches_y, num_patches_x)

        embeddings = rearrange(embeddings, "b e nz ny nx -> b (nz ny nx) e")
        # (b, num_tokens, dim)

        embeddings = self.pos_drop(embeddings)
        # (b, num_tokens, dim)

        class_tokens = None
        if self.num_class_tokens > 0:
            class_tokens = repeat(self.class_tokens, "1 n d -> b n d", b=embeddings.shape[0])
            embeddings = torch.cat([class_tokens, embeddings], dim=1)
            # (b, num_tokens + num_class_tokens, dim)

        encoder_output = self.encoder(embeddings, return_all=True)
        encoded, layer_outputs = (
            encoder_output["embeddings"],
            encoder_output["layer_outputs"],
        )
        # encoded: (b, num_tokens (+ num_class_tokens), dim)
        # layer_outputs: list of (b, num_tokens (+ 1), dim)

        if self.num_class_tokens > 0:
            class_tokens = encoded[:, : self.num_class_tokens]
            encoded = encoded[:, self.num_class_tokens :]

        return_value = class_tokens, encoded
        if return_all:
            return_value = {
                "class_tokens": class_tokens,
                "encoded": encoded,
                "layer_outputs": layer_outputs,
            }

        return return_value

# %% ../../nbs/nets/04_vit_3d.ipynb 22
class ViT3DMIMDecoder(nn.Module):
    def __init__(self, dim, image_size, in_channels, patch_size):
        super().__init__()

        self.image_size = image_size
        self.in_channels = in_channels
        self.patch_size = patch_size

        out_dim = np.prod(self.patch_size) * self.in_channels

        self.decoder = nn.Linear(dim, out_dim)

    def forward(self, encodings: torch.Tensor):
        # encodings: (b, num_tokens, dim)

        decoded = self.decoder(encodings)
        # (b, num_tokens, new_dim)

        decoded = rearrange(
            decoded,
            "b (nz ny nx) (c pz py px) -> b c (nz pz) (ny py) (nx px)",
            c=self.in_channels,
            pz=self.patch_size[0],
            py=self.patch_size[1],
            px=self.patch_size[2],
            nz=self.image_size[0] // self.patch_size[0],
            ny=self.image_size[1] // self.patch_size[1],
            nx=self.image_size[2] // self.patch_size[2],
        )
        # (b, c, z, y, x)

        return decoded

# %% ../../nbs/nets/04_vit_3d.ipynb 24
class ViT3DMIM(nn.Module):
    def __init__(self, config: ViT3DConfig):
        super().__init__()

        assert config.num_class_tokens == 0, "MIM does not support class tokens"

        self.image_size = config.image_size
        self.patch_size = config.patch_size
        self.in_channels = config.in_channels
        self.mask_ratio = config.mask_ratio

        self.vit = ViT3DModel(config)
        self.decoder = ViT3DMIMDecoder(config.dim, config.image_size, config.in_channels, config.patch_size)

        self.mask_token = nn.Parameter(torch.randn(1, config.dim, 1, 1, 1))

    def mask_image(self, pixel_values: torch.Tensor):
        b = pixel_values.shape[0]

        mask_ratio = self.mask_ratio
        grid_size = tuple([size // patch for size, patch in zip(self.image_size, self.patch_size)])
        num_tokens = np.prod(grid_size)
        mask_patches = []
        for _ in range(b):
            _mask_patches = torch.zeros(num_tokens, dtype=torch.int8, device=pixel_values.device)
            _mask_patches[: int(mask_ratio * num_tokens)] = 1
            _mask_patches = _mask_patches[torch.randperm(num_tokens)]
            _mask_patches = rearrange(
                _mask_patches,
                "(z y x) -> z y x",
                z=grid_size[0],
                y=grid_size[1],
                x=grid_size[2],
            )
            mask_patches.append(_mask_patches)
        mask_patches: torch.Tensor = torch.stack(mask_patches, dim=0)

        return mask_patches

# %% ../../nbs/nets/04_vit_3d.ipynb 25
class ViT3DSimMIM(ViT3DMIM, PyTorchModelHubMixin):
    def __init__(self, config):
        super().__init__(config)

    @staticmethod
    def loss_fn(pred: torch.Tensor, target: torch.Tensor, reduction="mean"):
        return nn.functional.l1_loss(pred, target, reduction=reduction)

    def forward(self, pixel_values: torch.Tensor, spacings: torch.Tensor):
        mask_patches = self.mask_image(pixel_values)

        _, encodings = self.vit(pixel_values, spacings, mask_patches, self.mask_token)
        decoded = self.decoder(encodings)

        loss = self.loss_fn(decoded, pixel_values, reduction="none")
        mask = repeat(
            mask_patches,
            "b z y x -> b (z pz) (y py) (x px)",
            pz=self.patch_size[0],
            py=self.patch_size[1],
            px=self.patch_size[2],
        )
        loss = (loss * mask).sum() / ((mask.sum() + 1e-5) * self.in_channels)

        return decoded, loss, mask
