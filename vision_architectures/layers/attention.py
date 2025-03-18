# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/layers/01_attention.ipynb.

# %% auto 0
__all__ = ['Attention1DConfig', 'Attention3DConfig', 'Attention1DMLPConfig', 'Attention3DMLPConfig', 'Attention1DWithMLPConfig',
           'Attention3DWithMLPConfig', 'Attention1D', 'Attention3D', 'Attention1DMLP', 'Attention3DMLP',
           'Attention1DWithMLP', 'Attention3DWithMLP']

# %% ../../nbs/layers/01_attention.ipynb 2
from functools import partial
from typing import Literal

import torch
import torch.nn.functional as F
from einops import rearrange
from torch import nn

from .embeddings import RelativePositionEmbeddings
from ..utils.activation_checkpointing import ActivationCheckpointing
from ..utils.activations import get_act_layer
from ..utils.custom_base_model import CustomBaseModel, Field, model_validator

# %% ../../nbs/layers/01_attention.ipynb 4
class Attention1DConfig(CustomBaseModel):
    dim: int | tuple[int, int]
    num_heads: int = Field(..., description="Number of query heads")
    ratio_q_to_kv_heads: int = 1
    logit_scale_learnable: bool = False
    attn_drop_prob: float = 0.0
    proj_drop_prob: float = 0.0
    max_attention_batch_size: int = Field(
        -1,
        description=(
            "Runs attention by splitting the inputs into chunks of this size. 0 means no chunking. "
            "Useful for large inputs during inference."
        ),
    )

    @property
    def num_q_heads(self) -> int:
        return self.num_heads

    @property
    def num_kv_heads(self) -> int:
        return self.num_heads // self.ratio_q_to_kv_heads

    @property
    def gqa_mqa_enabled(self) -> bool:
        return self.ratio_q_to_kv_heads != 1

    @property
    def dim_qk(self) -> int:
        if isinstance(self.dim, tuple):
            return self.dim[0]
        return self.dim

    @property
    def dim_v(self) -> int:
        if isinstance(self.dim, tuple):
            return self.dim[1]
        return self.dim

    @property
    def per_head_dim_qk(self) -> int:
        return self.dim_qk // self.num_heads

    @model_validator(mode="after")
    def validate(self):
        super().validate()
        if self.gqa_mqa_enabled:
            assert torch.__version__ >= "2.5", "Need PyTorch version >= 2.5 for GQA and MQA"

        assert self.dim_qk % self.num_heads == 0, "dimension must be divisible by number of heads"
        assert (
            self.num_heads % self.num_kv_heads == 0
        ), "number of query heads must be divisible by number of key and value heads"

        return self


class Attention3DConfig(Attention1DConfig):
    pass


class Attention1DMLPConfig(CustomBaseModel):
    dim: int
    mlp_ratio: int = 4
    activation: str = "gelu"
    mlp_drop_prob: float = 0.0


class Attention3DMLPConfig(Attention1DMLPConfig):
    pass


class Attention1DWithMLPConfig(Attention1DConfig, Attention1DMLPConfig):
    dim: int | tuple[int, int]
    norm_location: Literal["pre", "post"] = "post"
    layer_norm_eps: float = 1e-6


class Attention3DWithMLPConfig(Attention3DConfig, Attention3DMLPConfig):
    dim: int | tuple[int, int]
    norm_location: Literal["pre", "post"] = "post"
    layer_norm_eps: float = 1e-6

# %% ../../nbs/layers/01_attention.ipynb 6
class Attention1D(nn.Module):
    """Performs attention (MHA, GQA, and MQA) on 1D sequences"""

    _warn_relative_position_bias: bool = True

    def __init__(
        self,
        config: Attention1DConfig = {},
        relative_position_bias: RelativePositionEmbeddings | None = None,
        logit_scale: float | None = None,
        checkpointing_level: int = 0,
        **kwargs
    ):
        super().__init__()

        self.config = Attention1DConfig.model_validate(config | kwargs)

        dim_qk = self.config.dim_qk
        dim_v = self.config.dim_v
        ratio_q_to_kv_heads = self.config.ratio_q_to_kv_heads
        per_head_dim = self.config.per_head_dim_qk
        logit_scale_learnable = self.config.logit_scale_learnable
        attn_drop_prob = self.config.attn_drop_prob
        proj_drop_prob = self.config.proj_drop_prob

        self.W_q = nn.Linear(dim_qk, dim_qk)
        self.W_k = nn.Linear(dim_qk, dim_qk // ratio_q_to_kv_heads)
        self.W_v = nn.Linear(dim_v, dim_qk // ratio_q_to_kv_heads)
        self.attn_drop_prob = attn_drop_prob
        self.proj = nn.Linear(dim_qk, dim_qk)
        self.proj_drop = nn.Dropout(proj_drop_prob)

        if logit_scale is None:
            self.logit_scale = nn.Parameter(
                torch.tensor([per_head_dim**-0.5]),
                requires_grad=logit_scale_learnable,
            )
        else:
            self.logit_scale = logit_scale

        if self._warn_relative_position_bias and relative_position_bias is not None:
            print(
                "Warning: Relative position bias is not used in Attention1D. "
                "Use Attention3D for relative position bias."
            )
        self.relative_position_bias = relative_position_bias

        self.checkpointing_level1 = ActivationCheckpointing(1, checkpointing_level)
        self.checkpointing_level2 = ActivationCheckpointing(2, checkpointing_level)

    def _forward(self, query: torch.Tensor, key: torch.Tensor, value: torch.Tensor):
        """
        Parameters: T => number of tokens, b => batch size
            - query: (b, T_q, dim_qk)
            - key: (b, T_kv, dim_qk)
            - value: (b, T_kv, dim_v)
        """

        def get_final_query_key_value(query, key, value):
            query = self.W_q(query)
            key = self.W_k(key)
            value = self.W_v(value)

            rearrange_partial = partial(rearrange, pattern="b T (num_heads d) -> b num_heads T d")
            query = rearrange_partial(query, num_heads=self.config.num_heads).contiguous()
            key = rearrange_partial(key, num_heads=self.config.num_kv_heads).contiguous()
            value = rearrange_partial(value, num_heads=self.config.num_kv_heads).contiguous()
            # query: (b, num_heads, T, per_head_dim)
            # key: (b, num_kv_heads, T, per_head_dim)
            # value: (b, num_kv_heads, T, per_head_dim)

            if isinstance(self.logit_scale, nn.Module):
                logit_scale = self.logit_scale()
            else:
                logit_scale = self.logit_scale

            query_normalized = F.normalize(query, dim=-1)
            key_normalized = F.normalize(key, dim=-1)

            query_normalized_and_scaled = query_normalized * logit_scale  # Scale the query beforehand

            return query_normalized_and_scaled, key_normalized, value

        query_normalized_and_scaled, key_normalized, value = self.checkpointing_level1(
            get_final_query_key_value, query, key, value
        )

        relative_position_bias = None
        if self.relative_position_bias is not None:
            relative_position_bias = self.relative_position_bias()

        # Split tensors into batches and perform attention
        output = []
        chunk_size = self.config.max_attention_batch_size
        if chunk_size == -1:
            chunk_size = query_normalized_and_scaled.size(0)
        for query_normalized_and_scaled_chunk, key_normalized_chunk, value_chunk in zip(
            torch.split(query_normalized_and_scaled, chunk_size, dim=0),
            torch.split(key_normalized, chunk_size, dim=0),
            torch.split(value, chunk_size, dim=0),
        ):
            torch250plus_kwargs = {}
            if torch.__version__ >= "2.5":
                torch250plus_kwargs["enable_gqa"] = self.config.gqa_mqa_enabled

            output_chunk = F.scaled_dot_product_attention(
                query_normalized_and_scaled_chunk,
                key_normalized_chunk,
                value_chunk,
                attn_mask=relative_position_bias,  # Use this as a way to introduce relative position bias
                dropout_p=self.attn_drop_prob,
                is_causal=False,
                scale=1.0,  # Already scaled the vectors
                **torch250plus_kwargs,
            )
            output.append(output_chunk)
            # (chunk_size, num_heads, T, per_head_dim)
        output = torch.cat(output, dim=0)
        # (b, num_heads, T, per_head_dim)

        output = rearrange(output, "b num_heads T d -> b T (num_heads d)").contiguous()
        # (b, T, dim_qk)

        def get_final_output(output):
            output = self.proj(output)
            output = self.proj_drop(output)
            return output

        output = self.checkpointing_level1(get_final_output, output)
        # (b, T, dim_qk)

        return output

    def forward(self, query: torch.Tensor, key: torch.Tensor, value: torch.Tensor):
        return self.checkpointing_level2(self._forward, query, key, value)

# %% ../../nbs/layers/01_attention.ipynb 8
class Attention3D(Attention1D):
    _warn_relative_position_bias: bool = False

    def __init__(
        self,
        config: Attention3DConfig = {},
        relative_position_bias: RelativePositionEmbeddings | None = None,
        logit_scale: float | None = None,
        checkpointing_level: int = 0,
        **kwargs
    ):
        super().__init__(config, relative_position_bias, logit_scale, checkpointing_level, **kwargs)

    def _forward(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
        channels_first: bool = True,
    ):
        """
        Parameters: z => depth, y => height, x => width, b => batch size
            - query: (b, [dim_qk], z_q, y_q, x_q, [dim_qk])
            - key: (b, [dim_qk], z_k, y_k, x_k, [dim_qk])
            - value: (b, [dim_v], z_k, y_k, x_k, [dim_v])
            - channels_first: if True, BCDHW expected, else BDHWC

        Constraints:
            - d_q * h_q * w_q = d_k * h_k * w_k
        """

        if channels_first:
            z_q, y_q, x_q = query.shape[2:5]
            forward_pattern = "b d z y x -> b (z y x) d"
            reverse_pattern = "b (z y x) d -> b d z y x"
        else:
            z_q, y_q, x_q = query.shape[1:4]
            forward_pattern = "b z y x d -> b (z y x) d"
            reverse_pattern = "b (z y x) d -> b z y x d"

        query = rearrange(query, forward_pattern).contiguous()
        key = rearrange(key, forward_pattern).contiguous()
        value = rearrange(value, forward_pattern).contiguous()

        output = super()._forward(query, key, value)

        output = rearrange(output, reverse_pattern, z=z_q, y=y_q, x=x_q).contiguous()

        return output

    def forward(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
        channels_first: bool = True,
    ):
        return self.checkpointing_level2(self._forward, query, key, value, channels_first)

# %% ../../nbs/layers/01_attention.ipynb 10
class Attention1DMLP(nn.Module):
    def __init__(self, config: Attention1DMLPConfig = {}, checkpointing_level: int = 0, **kwargs):
        super().__init__()

        self.config = Attention1DMLPConfig.model_validate(config | kwargs)

        dim = self.config.dim
        mlp_ratio = self.config.mlp_ratio
        activation = self.config.activation
        mlp_drop_prob = self.config.mlp_drop_prob

        self.dense1 = nn.Linear(dim, dim * mlp_ratio)

        if isinstance(activation, nn.Module):
            self.act = activation
        else:
            self.act = get_act_layer(activation)

        self.dense2 = nn.Linear(dim * mlp_ratio, dim)
        self.dropout = nn.Dropout(mlp_drop_prob)

        self.checkpointing_level1 = ActivationCheckpointing(1, checkpointing_level)
        self.checkpointing_level2 = ActivationCheckpointing(2, checkpointing_level)

    def _forward(self, hidden_states: torch.Tensor):
        # hidden_states: (b, T, dim)
        def first_half(hidden_states):
            hidden_states = self.dense1(hidden_states)
            hidden_states = self.act(hidden_states)
            return hidden_states

        def second_half(hidden_states):
            hidden_states = self.dense2(hidden_states)
            hidden_states = self.dropout(hidden_states)
            return hidden_states

        hidden_states = self.checkpointing_level1(first_half, hidden_states)
        hidden_states = self.checkpointing_level1(second_half, hidden_states)
        return hidden_states

    def forward(self, hidden_states: torch.Tensor):
        return self.checkpointing_level2(self._forward, hidden_states)

# %% ../../nbs/layers/01_attention.ipynb 12
class Attention3DMLP(Attention1DMLP):
    def __init__(self, config: Attention3DMLPConfig = {}, checkpointing_level: int = 0, **kwargs):
        super().__init__(config, checkpointing_level, **kwargs)

    def _forward(self, hidden_states: torch.Tensor, channels_first: bool = True):
        # hidden_states: (b, dim, z, y, x) or (b, z, y, x, dim)

        if channels_first:
            hidden_states = rearrange(hidden_states, "b d z y x -> b z y x d").contiguous()

        hidden_states = super()._forward(hidden_states)

        if channels_first:
            hidden_states = rearrange(hidden_states, "b z y x d -> b d z y x").contiguous()

        return hidden_states

    def forward(self, hidden_states: torch.Tensor, channels_first: bool = True):
        return self.checkpointing_level1(self._forward, hidden_states, channels_first)

# %% ../../nbs/layers/01_attention.ipynb 14
class Attention1DWithMLP(nn.Module):
    def __init__(
        self,
        config: Attention1DWithMLPConfig = {},
        relative_position_bias: RelativePositionEmbeddings | None = None,
        logit_scale: float | None = None,
        checkpointing_level: int = 0,
        **kwargs
    ):
        super().__init__()

        self.config = Attention1DWithMLPConfig.model_validate(config | kwargs)

        dim_qk = self.config.dim_qk
        layer_norm_eps = self.config.layer_norm_eps

        self.attn = Attention1D(
            self.config,
            relative_position_bias=relative_position_bias,
            logit_scale=logit_scale,
            checkpointing_level=checkpointing_level,
        )
        self.layernorm1 = nn.LayerNorm(dim_qk, eps=layer_norm_eps)
        self.mlp = Attention1DMLP(self.config, checkpointing_level=checkpointing_level)
        self.layernorm2 = nn.LayerNorm(dim_qk, eps=layer_norm_eps)

        self.checkpointing_level3 = ActivationCheckpointing(3, checkpointing_level)

    def _forward(self, query: torch.Tensor, key: torch.Tensor, value: torch.Tensor):
        # Each is (b, T, dim)
        res_connection1 = query
        # (b, T, dim)

        if self.config.norm_location == "pre":
            query = self.layernorm1(query)
            key = self.layernorm1(key)
            value = self.layernorm1(value)
            # (b, T, dim)

        hidden_states = self.attn(query, key, value)
        # (b, T, dim)

        if self.config.norm_location == "post":
            hidden_states = self.layernorm1(hidden_states)
            # (b, T, dim)

        hidden_states = hidden_states + res_connection1
        res_connection2 = hidden_states
        # (b, T, dim)

        if self.config.norm_location == "pre":
            hidden_states = self.layernorm2(hidden_states)
            # (b, T, dim)

        hidden_states = self.mlp(hidden_states)
        # (b, T, dim)

        if self.config.norm_location == "post":
            hidden_states = self.layernorm2(hidden_states)
            # (b, T, dim)

        hidden_states = hidden_states + res_connection2
        # (b, T, dim)

        return hidden_states

    def forward(self, query: torch.Tensor, key: torch.Tensor, value: torch.Tensor):
        return self.checkpointing_level3(self._forward, query, key, value)

# %% ../../nbs/layers/01_attention.ipynb 16
class Attention3DWithMLP(nn.Module):
    def __init__(
        self,
        config: Attention3DWithMLPConfig = {},
        relative_position_bias: RelativePositionEmbeddings | None = None,
        logit_scale: float | None = None,
        checkpointing_level: int = 0,
        **kwargs
    ):
        super().__init__()

        self.config = Attention3DWithMLPConfig.model_validate(config | kwargs)

        dim_qk = self.config.dim_qk
        layer_norm_eps = self.config.layer_norm_eps

        self.attn = Attention3D(
            self.config,
            relative_position_bias=relative_position_bias,
            logit_scale=logit_scale,
            checkpointing_level=checkpointing_level,
        )
        self.layernorm1 = nn.LayerNorm(dim_qk, eps=layer_norm_eps)
        self.mlp = Attention3DMLP(self.config, checkpointing_level=checkpointing_level)
        self.layernorm2 = nn.LayerNorm(dim_qk, eps=layer_norm_eps)

        self.checkpointing_level3 = ActivationCheckpointing(3, checkpointing_level)

    def _forward(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
        channels_first: bool = True,
    ):
        # Each is (b, [dim], tokens_z, tokens_y, tokens_x, [dim])

        if channels_first:
            query = rearrange(query, "b d z y x -> b z y x d").contiguous()
            key = rearrange(key, "b d z y x -> b z y x d").contiguous()
            value = rearrange(value, "b d z y x -> b z y x d").contiguous()
            # (b, tokens_z, tokens_y, tokens_x, dim)

        res_connection1 = query
        # (b, tokens_z, tokens_y, tokens_x, dim)

        if self.config.norm_location == "pre":
            query = self.layernorm1(query)
            key = self.layernorm1(key)
            value = self.layernorm1(value)
            # (b, tokens_z, tokens_y, tokens_x, dim)

        hidden_states = self.attn(query, key, value, channels_first=False)
        # (b, tokens_z, tokens_y, tokens_x, dim)

        if self.config.norm_location == "post":
            hidden_states = self.layernorm1(hidden_states)
            # (b, tokens_z, tokens_y, tokens_x, dim)

        hidden_states = hidden_states + res_connection1
        res_connection2 = hidden_states
        # (b, tokens_z, tokens_y, tokens_x, dim)

        if self.config.norm_location == "pre":
            hidden_states = self.layernorm2(hidden_states)
            # (b, tokens_z, tokens_y, tokens_x, dim)

        hidden_states = self.mlp(hidden_states, channels_first=False)
        # (b, tokens_z, tokens_y, tokens_x, dim)

        if self.config.norm_location == "post":
            hidden_states = self.layernorm2(hidden_states)
            # (b, tokens_z, tokens_y, tokens_x, dim)

        hidden_states = hidden_states + res_connection2
        # (b, tokens_z, tokens_y, tokens_x, dim)

        if channels_first:
            hidden_states = rearrange(hidden_states, "b z y x d -> b d z y x").contiguous()
            # (b, dim, tokens_z, tokens_y, tokens_x)

        return hidden_states

    def forward(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
        channels_first: bool = True,
    ):
        return self.checkpointing_level3(self._forward, query, key, value, channels_first)
