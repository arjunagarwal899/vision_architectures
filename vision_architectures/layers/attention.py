# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/layers/01_attention.ipynb.

# %% auto 0
__all__ = ['MultiHeadAttention3D', 'MultiQueryAttention3D', 'GroupedQueryAttention3D', 'Attention3DMLP', 'Attention3DLayer']

# %% ../../nbs/layers/01_attention.ipynb 2
from functools import partial
from typing import Literal

import torch
import torch.nn.functional as F
from einops import rearrange
from torch import nn

from .embeddings import RelativePositionEmbeddings
from ..utils.activations import get_act_layer

# %% ../../nbs/layers/01_attention.ipynb 4
class MultiHeadAttention3D(nn.Module):
    def __init__(
        self,
        dim: int,
        num_heads: int,
        relative_position_bias: RelativePositionEmbeddings | None = None,
        logit_scale=None,
        attn_drop_prob=0.0,
        proj_drop_prob=0.0,
    ):
        super().__init__()

        assert dim % num_heads == 0, "dimension must be divisible by number of heads"

        self.dim = dim
        self.num_heads = num_heads

        self.per_head_dim = int(dim // num_heads)

        self.W_q = nn.Linear(dim, dim)
        self.W_k = nn.Linear(dim, dim)
        self.W_v = nn.Linear(dim, dim)
        self.attn_drop_prob = attn_drop_prob
        self.proj = nn.Linear(dim, dim)
        self.proj_drop = nn.Dropout(proj_drop_prob)

        if logit_scale is None:
            self.logit_scale = nn.Parameter(torch.tensor([self.per_head_dim**-0.5]))
        else:
            self.logit_scale = logit_scale

        self.relative_position_bias = relative_position_bias

    def forward(self, query: torch.Tensor, key: torch.Tensor, value: torch.Tensor, tokens_as_3d: bool = True):
        # Each is (b, num_patches_z, num_patches_y, num_patches_x, dim) or (b, T, dim)

        query = self.W_q(query)
        key = self.W_k(key)
        value = self.W_v(value)

        if tokens_as_3d:
            _, num_patches_z, num_patches_y, num_patches_x, _ = query.shape
            rearrange_partial = partial(
                rearrange, pattern="b nz ny nx (num_heads d) -> b num_heads (nz ny nx) d", num_heads=self.num_heads
            )
            reverse_rearrange_partial = partial(
                rearrange,
                pattern="b num_heads (num_patches_z num_patches_y num_patches_x) d -> "
                "b num_patches_z num_patches_y num_patches_x (num_heads d)",
                num_patches_z=num_patches_z,
                num_patches_y=num_patches_y,
                num_patches_x=num_patches_x,
            )
        else:
            rearrange_partial = partial(
                rearrange, pattern="b T (num_heads d) -> b num_heads T d", num_heads=self.num_heads
            )
            reverse_rearrange_partial = partial(rearrange, pattern="b num_heads T d -> b T (num_heads d)")

        query = rearrange_partial(query)
        key = rearrange_partial(key)
        value = rearrange_partial(value)
        # T = num_patches_z * num_patches_y * num_patches_x
        # Each is (b, num_heads, T, per_head_dim)

        if isinstance(self.logit_scale, nn.Module):
            logit_scale = self.logit_scale()
        else:
            logit_scale = self.logit_scale

        query_normalized = F.normalize(query, dim=-1)
        key_normalized = F.normalize(key, dim=-1)

        query_normalized_and_scaled = query_normalized * logit_scale  # Scale the query beforehand

        relative_position_bias = None
        if self.relative_position_bias is not None:
            relative_position_bias = self.relative_position_bias()

        context = F.scaled_dot_product_attention(
            query_normalized_and_scaled,
            key_normalized,
            value,
            attn_mask=relative_position_bias,  # Use this as a way to introduce relative position bias
            dropout_p=self.attn_drop_prob,
            is_causal=False,
            scale=1.0,  # Already scaled the vectors
        )
        # (b, num_heads, T, per_head_dim)

        context = reverse_rearrange_partial(context)
        # (b, num_patches_z, num_patches_y, num_patches_x, dim) or (b, T, dim)

        context = self.proj(context)
        context = self.proj_drop(context)
        # (b, num_patches_z, num_patches_y, num_patches_x, dim) or (b, T, dim)

        return context

# %% ../../nbs/layers/01_attention.ipynb 6
class MultiQueryAttention3D(nn.Module):
    def __init__(self):
        raise NotImplementedError

# %% ../../nbs/layers/01_attention.ipynb 7
class GroupedQueryAttention3D(nn.Module):
    def __init__(self):
        raise NotImplementedError

# %% ../../nbs/layers/01_attention.ipynb 8
class Attention3DMLP(nn.Module):
    def __init__(self, dim, intermediate_ratio, activation, mlp_drop_prob=0.0):
        super().__init__()
        self.dense1 = nn.Linear(dim, dim * intermediate_ratio)

        if isinstance(activation, nn.Module):
            self.act = activation
        else:
            self.act = get_act_layer(activation)

        self.dense2 = nn.Linear(dim * intermediate_ratio, dim)
        self.dropout = nn.Dropout(mlp_drop_prob)

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, num_patches_z, num_patches_y, num_patches_x, dim)
        hidden_states = self.dense1(hidden_states)
        hidden_states = self.act(hidden_states)
        hidden_states = self.dense2(hidden_states)
        hidden_states = self.dropout(hidden_states)
        return hidden_states

# %% ../../nbs/layers/01_attention.ipynb 10
class Attention3DLayer(nn.Module):
    def __init__(
        self,
        dim: int,
        num_heads: int,
        mlp_ratio: int = 4,
        qkv_relative_position_bias=None,
        qk_scale: float = None,
        activation="gelu",
        norm_location: Literal["pre", "post"] = "post",
        layer_norm_eps: float = 1e-6,
        attn_drop_prob: float = 0.0,
        proj_drop_prob: float = 0.0,
        mlp_drop_prob: float = 0.0,
    ):
        super().__init__()

        self.norm_location = norm_location

        self.attn = MultiHeadAttention3D(
            dim=dim,
            num_heads=num_heads,
            relative_position_bias=qkv_relative_position_bias,
            logit_scale=qk_scale,
            attn_drop_prob=attn_drop_prob,
            proj_drop_prob=proj_drop_prob,
        )
        self.layernorm1 = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.mlp = Attention3DMLP(dim, intermediate_ratio=mlp_ratio, activation=activation, mlp_drop_prob=mlp_drop_prob)
        self.layernorm2 = nn.LayerNorm(dim, eps=layer_norm_eps)

    def forward(self, query: torch.Tensor, key: torch.Tensor, value: torch.Tensor, tokens_as_3d: bool = True):
        # Each is (b, num_patches_z, num_patches_y, num_patches_x, dim) or (b, T, dim)
        res_connection1 = query
        # (b, num_patches_z, num_patches_y, num_patches_x, dim) or (b, T, dim)

        if self.norm_location == "pre":
            query = self.layernorm1(query)
            key = self.layernorm1(key)
            value = self.layernorm1(value)
            # (b, num_patches_z, num_patches_y, num_patches_x, dim or (b, T, dim)

        hidden_states = self.attn(query, key, value, tokens_as_3d)
        # (b, num_patches_z, num_patches_y, num_patches_x, dim) or (b, T, dim)

        if self.norm_location == "post":
            hidden_states = self.layernorm1(hidden_states)
            # (b, num_patches_z, num_patches_y, num_patches_x, dim) or (b, T, dim)

        hidden_states = hidden_states + res_connection1
        res_connection2 = hidden_states
        # (b, num_patches_z, num_patches_y, num_patches_x, dim) or (b, T, dim)

        if self.norm_location == "pre":
            hidden_states = self.layernorm2(hidden_states)
            # (b, num_patches_z, num_patches_y, num_patches_x, dim) or (b, T, dim)

        hidden_states = self.mlp(hidden_states)
        # (b, num_patches_z, num_patches_y, num_patches_x, dim) or (b, T, dim)

        if self.norm_location == "post":
            hidden_states = self.layernorm2(hidden_states)
            # (b, num_patches_z, num_patches_y, num_patches_x, dim) or (b, T, dim)

        hidden_states = hidden_states + res_connection2
        # (b, num_patches_z, num_patches_y, num_patches_x, dim) or (b, T, dim)

        return hidden_states
