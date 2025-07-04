# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/blocks/02_transformer.ipynb.

# %% auto 0
__all__ = ['Attention1DMLPConfig', 'Attention3DMLPConfig', 'Attention1DWithMLPConfig', 'Attention3DWithMLPConfig',
           'Attention1DMLP', 'Attention3DMLP', 'Attention1DWithMLP', 'Attention3DWithMLP', 'TransformerEncoderBlock1D',
           'TransformerEncoderBlock3D', 'TransformerDecoderBlock1D', 'TransformerDecoderBlock3D']

# %% ../../nbs/blocks/02_transformer.ipynb 2
from functools import wraps
from typing import Literal

import torch
from torch import nn

from ..docstrings import populate_docstring
from ..layers.attention import Attention1D, Attention1DConfig, Attention3D, Attention3DConfig
from ..layers.embeddings import RelativePositionEmbeddings
from ..utils.activation_checkpointing import ActivationCheckpointing
from ..utils.activations import get_act_layer
from ..utils.custom_base_model import CustomBaseModel, Field
from ..utils.rearrange import rearrange_channels
from ..utils.residuals import Residual

# %% ../../nbs/blocks/02_transformer.ipynb 4
class Attention1DMLPConfig(CustomBaseModel):
    dim: int = Field(..., description="Dimension of the input and output features.")
    mlp_ratio: int = Field(4, description="Ratio of the hidden dimension in the MLP to the input dimension.")
    activation: str = Field("gelu", description="Activation function for the MLP.")
    mlp_drop_prob: float = Field(0.0, description="Dropout probability for the MLP.")


class Attention3DMLPConfig(Attention1DMLPConfig):
    pass


class Attention1DWithMLPConfig(Attention1DMLPConfig, Attention1DConfig):
    norm_location: Literal["pre", "post"] = Field(
        "post",
        description="Location of the normalization layer in the attention block. Pre-normalization implies "
        "normalization before the attention operation, while post-normalization applies it after.",
    )
    layer_norm_eps: float = Field(1e-6, description="Epsilon value for the layer normalization.")


class Attention3DWithMLPConfig(Attention3DMLPConfig, Attention3DConfig):
    norm_location: Literal["pre", "post"] = Field(
        "post",
        description="Location of the normalization layer in the attention block. Pre-normalization implies "
        "normalization before the attention operation, while post-normalization applies it after.",
    )
    layer_norm_eps: float = Field(1e-6, description="Epsilon value for the layer normalization.")

# %% ../../nbs/blocks/02_transformer.ipynb 6
@populate_docstring
class Attention1DMLP(nn.Module):
    """The MLP that is usually used after performing attention. {CLASS_DESCRIPTION_1D_DOC}"""

    @populate_docstring
    def __init__(self, config: Attention1DMLPConfig = {}, checkpointing_level: int = 0, **kwargs):
        """Initialize an Attention1DMLP block. Activation checkpointing level 2.

        Args:
            config: {CONFIG_INSTANCE_DOC}
            checkpointing_level: {CHECKPOINTING_LEVEL_DOC}
            **kwargs: {CONFIG_KWARGS_DOC}
        """
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

    def _forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        """Forward pass of the Attention1DMLP block.

        Args:
            hidden_states: {INPUT_1D_DOC}

        Returns:
            {OUTPUT_1D_DOC}
        """

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

    @wraps(_forward)
    def forward(self, *args, **kwargs):
        return self.checkpointing_level2(self._forward, *args, **kwargs)

# %% ../../nbs/blocks/02_transformer.ipynb 8
@populate_docstring
class Attention3DMLP(Attention1DMLP):
    """The MLP that is usually used after performing attention. {CLASS_DESCRIPTION_3D_DOC}"""

    @populate_docstring
    def __init__(self, config: Attention3DMLPConfig = {}, checkpointing_level: int = 0, **kwargs):
        """Initialize an Attention3DMLP block. Activation checkpointing level 2.

        Args:
            config: {CONFIG_INSTANCE_DOC}
            checkpointing_level: {CHECKPOINTING_LEVEL_DOC}
            **kwargs: {CONFIG_KWARGS_DOC}
        """
        super().__init__(config, checkpointing_level, **kwargs)

    @populate_docstring
    def _forward(self, hidden_states: torch.Tensor, channels_first: bool = True) -> torch.Tensor:
        """Forward pass of the Attention3DMLP block.

        Args:
            hidden_states: {INPUT_3D_DOC}
            channels_first: {CHANNELS_FIRST_DOC}

        Returns:
            {OUTPUT_3D_DOC}
        """
        # hidden_states: (b, dim, z, y, x) or (b, z, y, x, dim)
        hidden_states = rearrange_channels(hidden_states, channels_first, False)
        hidden_states = super()._forward(hidden_states)
        hidden_states = rearrange_channels(hidden_states, False, channels_first)
        return hidden_states

    @wraps(_forward)
    def forward(self, *args, **kwargs):
        return self.checkpointing_level1(self._forward, *args, **kwargs)

# %% ../../nbs/blocks/02_transformer.ipynb 10
@populate_docstring
class Attention1DWithMLP(nn.Module):
    """An attention block with an MLP. {CLASS_DESCRIPTION_1D_DOC}"""

    @populate_docstring
    def __init__(
        self,
        config: Attention1DWithMLPConfig = {},
        relative_position_bias: RelativePositionEmbeddings | None = None,
        logit_scale: float | None = None,
        checkpointing_level: int = 0,
        **kwargs
    ):
        """Initialize an Attention1DWithMLP block. Activation checkpointing level 3.

        Args:
            config: {CONFIG_INSTANCE_DOC}
            relative_position_bias: {RELATIVE_POSITION_BIAS_DOC}
            logit_scale: {LOGIT_SCALE_DOC}
            checkpointing_level: {CHECKPOINTING_LEVEL_DOC}
            **kwargs: {CONFIG_KWARGS_DOC}
        """
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

        self.residual = Residual()

        self.checkpointing_level3 = ActivationCheckpointing(3, checkpointing_level)

    @populate_docstring
    def _forward(self, query: torch.Tensor, key: torch.Tensor, value: torch.Tensor) -> torch.Tensor:
        """Forward pass of the Attention1DWithMLP block.

        Args:
            query: {INPUT_1D_DOC}
            key: {INPUT_1D_DOC}
            value: {INPUT_1D_DOC}

        Returns:
            {OUTPUT_1D_DOC}
        """
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

        hidden_states = self.residual(hidden_states, res_connection1)
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

        hidden_states = self.residual(hidden_states, res_connection2)
        # (b, T, dim)

        return hidden_states

    @wraps(_forward)
    def forward(self, *args, **kwargs):
        return self.checkpointing_level3(self._forward, *args, **kwargs)

# %% ../../nbs/blocks/02_transformer.ipynb 12
@populate_docstring
class Attention3DWithMLP(nn.Module):
    """An attention block with an MLP. {CLASS_DESCRIPTION_3D_DOC}"""

    @populate_docstring
    def __init__(
        self,
        config: Attention3DWithMLPConfig = {},
        relative_position_bias: RelativePositionEmbeddings | None = None,
        logit_scale: float | None = None,
        checkpointing_level: int = 0,
        **kwargs
    ):
        """Initialize an Attention3DWithMLP block. Activation checkpointing level 3.

        Args:
            config: {CONFIG_INSTANCE_DOC}
            relative_position_bias: {RELATIVE_POSITION_BIAS_DOC}
            logit_scale: {LOGIT_SCALE_DOC}
            checkpointing_level: {CHECKPOINTING_LEVEL_DOC}
            **kwargs: {CONFIG_KWARGS_DOC}
        """
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

        self.residual = Residual()

        self.checkpointing_level3 = ActivationCheckpointing(3, checkpointing_level)

    @populate_docstring
    def _forward(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
        channels_first: bool = True,
    ) -> torch.Tensor:
        """Forward pass of the Attention3DWithMLP block.

        Args:
            query: {INPUT_3D_DOC}
            key: {INPUT_3D_DOC}
            value: {INPUT_3D_DOC}
            channels_first: {CHANNELS_FIRST_DOC}

        Returns:
            {OUTPUT_3D_DOC}
        """
        # Each is (b, [dim], tokens_z, tokens_y, tokens_x, [dim])

        query = rearrange_channels(query, channels_first, False)
        key = rearrange_channels(key, channels_first, False)
        value = rearrange_channels(value, channels_first, False)
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

        hidden_states = self.residual(hidden_states, res_connection1)
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

        hidden_states = self.residual(hidden_states, res_connection2)
        # (b, tokens_z, tokens_y, tokens_x, dim)

        hidden_states = rearrange_channels(hidden_states, False, channels_first)
        # (b, [dim], tokens_z, tokens_y, tokens_x, [dim])

        return hidden_states

    @wraps(_forward)
    def forward(self, *args, **kwargs):
        return self.checkpointing_level3(self._forward, *args, **kwargs)

# %% ../../nbs/blocks/02_transformer.ipynb 14
@populate_docstring
class TransformerEncoderBlock1D(Attention1DWithMLP):
    """A self attention transformer block. {CLASS_DESCRIPTION_1D_DOC}"""

    @populate_docstring
    def forward(self, qkv: torch.Tensor, *args, **kwargs) -> torch.Tensor:
        """Forward pass of the TransformerEncoderBlock1D block. Activation checkpointing level 3.

        Args:
            qkv: {INPUT_1D_DOC} The same tensor is used for query, key, and value.

        Returns:
            {OUTPUT_1D_DOC}
        """

        # qkv: (b, num_tokens, dim)
        return super().forward(qkv, qkv, qkv, *args, **kwargs)

# %% ../../nbs/blocks/02_transformer.ipynb 16
@populate_docstring
class TransformerEncoderBlock3D(Attention3DWithMLP):
    """A self attention transformer block. {CLASS_DESCRIPTION_3D_DOC}"""

    @populate_docstring
    def forward(self, qkv: torch.Tensor, *args, **kwargs) -> torch.Tensor:
        """Forward pass of the TransformerEncoderBlock3D block. Activation checkpointing level 3.

        Args:
            qkv: {INPUT_3D_DOC} The same tensor is used for query, key, and value.

        Returns:
            {OUTPUT_3D_DOC}
        """
        # qkv: (b, [dim], tokens_z, tokens_y, tokens_x, [dim])
        return super().forward(qkv, qkv, qkv, *args, **kwargs)

# %% ../../nbs/blocks/02_transformer.ipynb 18
@populate_docstring
class TransformerDecoderBlock1D(nn.Module):
    """A cross attention transformer block. {CLASS_DESCRIPTION_1D_DOC}"""

    @populate_docstring
    def __init__(self, config: Attention1DWithMLPConfig = {}, checkpointing_level: int = 0, **kwargs):
        """Initialize a TransformerDecoderBlock1D block. Activation checkpointing level 3.

        Args:
            config: {CONFIG_INSTANCE_DOC}
            checkpointing_level: {CHECKPOINTING_LEVEL_DOC}
            **kwargs: {CONFIG_KWARGS_DOC}
        """
        super().__init__()

        self.config = Attention1DWithMLPConfig.model_validate(config | kwargs)

        dim = self.config.dim
        num_heads = self.config.num_heads
        mlp_ratio = self.config.mlp_ratio
        layer_norm_eps = self.config.layer_norm_eps
        attn_drop_prob = self.config.attn_drop_prob
        proj_drop_prob = self.config.proj_drop_prob
        mlp_drop_prob = self.config.mlp_drop_prob

        self.attn1 = Attention1D(
            dim=dim,
            num_heads=num_heads,
            attn_drop_prob=attn_drop_prob,
            proj_drop_prob=proj_drop_prob,
        )
        self.layernorm1 = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.attn2 = Attention1D(
            dim=dim,
            num_heads=num_heads,
            attn_drop_prob=attn_drop_prob,
            proj_drop_prob=proj_drop_prob,
        )
        self.layernorm2 = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.mlp = Attention1DMLP(dim=dim, mlp_ratio=mlp_ratio, mlp_drop_prob=mlp_drop_prob)
        self.layernorm3 = nn.LayerNorm(dim, eps=layer_norm_eps)

        self.residual = Residual()

        self.checkpointing_level3 = ActivationCheckpointing(3, checkpointing_level)

    @populate_docstring
    def _forward(self, q: torch.Tensor, kv: torch.Tensor) -> torch.Tensor:
        """Forward pass of the TransformerDecoderBlock1D block.

        Args:
            q: The query tensor. {INPUT_1D_DOC}
            kv: The key and value tensors. {INPUT_1D_DOC}

        Returns:
            {OUTPUT_1D_DOC}
        """
        # q: (b, num_tokens_in_q, dim)
        # kv: (b, num_tokens_in_kv, dim)

        res_connection1 = q
        # (b, num_tokens_in_q, dim)

        if self.config.norm_location == "pre":
            q = self.layernorm1(q)
            # (b, num_tokens_in_q, dim)
            kv = self.layernorm1(kv)
            # (b, num_tokens_in_kv, dim)

        hidden_states = self.attn1(q, q, q)
        # (b, num_tokens_in_q, dim)

        if self.config.norm_location == "post":
            hidden_states = self.layernorm1(hidden_states)
            # (b, num_tokens_in_q, dim)

        hidden_states = self.residual(hidden_states, res_connection1)
        res_connection2 = hidden_states
        # (b, num_tokens_in_q, dim)

        if self.config.norm_location == "pre":
            hidden_states = self.layernorm2(hidden_states)
            # (b, num_tokens_in_q, dim)

        hidden_states = self.attn2(hidden_states, kv, kv)
        # (b, num_tokens_in_q, dim)

        if self.config.norm_location == "post":
            hidden_states = self.layernorm2(hidden_states)
            # (b, num_tokens_in_q, dim)

        hidden_states = self.residual(hidden_states, res_connection2)
        res_connection3 = hidden_states
        # (b, num_tokens_in_q, dim)

        if self.config.norm_location == "pre":
            hidden_states = self.layernorm3(hidden_states)
            # (b, num_tokens_in_q, dim)

        hidden_states = self.mlp(hidden_states)
        # (b, num_tokens_in_q, dim)

        if self.config.norm_location == "post":
            hidden_states = self.layernorm3(hidden_states)
            # (b, num_tokens_in_q, dim)

        hidden_states = self.residual(hidden_states, res_connection3)
        # (b, num_tokens_in_q, dim)

        return hidden_states

    @wraps(_forward)
    def forward(self, *args, **kwargs):
        return self.checkpointing_level3(self._forward, *args, **kwargs)

# %% ../../nbs/blocks/02_transformer.ipynb 20
@populate_docstring
class TransformerDecoderBlock3D(nn.Module):
    """A cross attention transformer block. {CLASS_DESCRIPTION_3D_DOC}"""

    @populate_docstring
    def __init__(self, config: Attention3DWithMLPConfig = {}, checkpointing_level: int = 0, **kwargs):
        """Initialize a TransformerDecoderBlock3D block. Activation checkpointing level 3.

        Args:
            config: {CONFIG_INSTANCE_DOC}
            checkpointing_level: {CHECKPOINTING_LEVEL_DOC}
            **kwargs: {CONFIG_KWARGS_DOC}
        """
        super().__init__()

        self.config = Attention3DWithMLPConfig.model_validate(config | kwargs)

        dim = self.config.dim
        num_heads = self.config.num_heads
        mlp_ratio = self.config.mlp_ratio
        layer_norm_eps = self.config.layer_norm_eps
        attn_drop_prob = self.config.attn_drop_prob
        proj_drop_prob = self.config.proj_drop_prob
        mlp_drop_prob = self.config.mlp_drop_prob

        self.attn1 = Attention3D(
            dim=dim,
            num_heads=num_heads,
            attn_drop_prob=attn_drop_prob,
            proj_drop_prob=proj_drop_prob,
        )
        self.layernorm1 = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.attn2 = Attention3D(
            dim=dim,
            num_heads=num_heads,
            attn_drop_prob=attn_drop_prob,
            proj_drop_prob=proj_drop_prob,
        )
        self.layernorm2 = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.mlp = Attention3DMLP(dim=dim, mlp_ratio=mlp_ratio, mlp_drop_prob=mlp_drop_prob)
        self.layernorm3 = nn.LayerNorm(dim, eps=layer_norm_eps)

        self.residual = Residual()

        self.checkpointing_level3 = ActivationCheckpointing(3, checkpointing_level)

    @populate_docstring
    def _forward(self, q: torch.Tensor, kv: torch.Tensor, channels_first: bool = True) -> torch.Tensor:
        """Forward pass of the TransformerDecoderBlock3D block.

        Args:
            q: The query tensor. {INPUT_3D_DOC}
            kv: The key and value tensors. {INPUT_3D_DOC}
            channels_first: {CHANNELS_FIRST_DOC}

        Returns:
            {OUTPUT_3D_DOC}
        """
        # Each is (b, [dim], tokens_z, tokens_y, tokens_x, [dim])

        q = rearrange_channels(q, channels_first, False)
        kv = rearrange_channels(kv, channels_first, False)
        # (b, tokens_z, tokens_y, tokens_x, dim)

        res_connection1 = q
        # (b, tokens_z, tokens_y, tokens_x, dim)

        if self.config.norm_location == "pre":
            q = self.layernorm1(q)
            kv = self.layernorm1(kv)
            # (b, tokens_z, tokens_y, tokens_x, dim)

        hidden_states: torch.Tensor = self.attn1(q, q, q, channels_first=False)
        # (b, tokens_z, tokens_y, tokens_x, dim)

        if self.config.norm_location == "post":
            hidden_states = self.layernorm1(hidden_states)
            # (b, tokens_z, tokens_y, tokens_x, dim)

        hidden_states = self.residual(hidden_states, res_connection1)
        res_connection2 = hidden_states
        # (b, tokens_z, tokens_y, tokens_x, dim)

        if self.config.norm_location == "pre":
            hidden_states = self.layernorm2(hidden_states)
            # (b, tokens_z, tokens_y, tokens_x, dim)

        hidden_states = self.attn2(hidden_states, kv, kv, channels_first=False)
        # (b, tokens_z, tokens_y, tokens_x, dim)

        if self.config.norm_location == "post":
            hidden_states = self.layernorm2(hidden_states)
            # (b, tokens_z, tokens_y, tokens_x, dim)

        hidden_states = self.residual(hidden_states, res_connection2)
        res_connection3 = hidden_states
        # (b, tokens_z, tokens_y, tokens_x, dim)

        if self.config.norm_location == "pre":
            hidden_states = self.layernorm3(hidden_states)
            # (b, tokens_z, tokens_y, tokens_x, dim)

        hidden_states = self.mlp(hidden_states, channels_first=False)
        # (b, tokens_z, tokens_y, tokens_x, dim)

        if self.config.norm_location == "post":
            hidden_states = self.layernorm3(hidden_states)
            # (b, tokens_z, tokens_y, tokens_x, dim)

        hidden_states = self.residual(hidden_states, res_connection3)
        # (b, tokens_z, tokens_y, tokens_x, dim)

        hidden_states = rearrange_channels(hidden_states, False, channels_first)
        # (b, [dim], tokens_z, tokens_y, tokens_x, [dim])

        return hidden_states

    @wraps(_forward)
    def forward(self, *args, **kwargs):
        return self.checkpointing_level3(self._forward, *args, **kwargs)
