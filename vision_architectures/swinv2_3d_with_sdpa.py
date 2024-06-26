# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/04_swinv2_3d_with_sdpa.ipynb.

# %% auto 0
__all__ = ['SwinV23DMHSA', 'SwinV23DLayer', 'SwinV23DBlock', 'SwinV23DStage', 'SwinV23DEncoder', 'SwinV23DModel', 'SwinV23DMIM',
           'SwinV23DSimMIM', 'SwinV23DVAEMIM']

# %% ../nbs/04_swinv2_3d_with_sdpa.ipynb 2
import numpy as np
import torch
import torch.nn.functional as F

from einops import rearrange
from torch import nn
from vision_architectures.swinv2_3d import (
    populate_and_validate_config,
    get_coords_grid,
    SwinV23DMHSA as SwinV23DMHSAWithoutSDPA,
    SwinV23DLayerMLP,
    SwinV23DLayer as SwinV23DLayerWithoutSDPA,
    SwinV23DBlock as SwinV23DBlockWithoutSDPA,
    SwinV23DPatchMerging,
    SwinV23DStage as SwinV23DStageWithoutSDPA,
    SwinV23DEncoder as SwinV23DEncoderWithoutSDPA,
    SwinV23DPatchEmbeddings,
    get_3d_position_embeddings,
    embed_spacings_in_position_embeddings,
    SwinV23DEmbeddings,
    SwinV23DModel as SwinV23DModelWithoutSDPA,
    SwinV23DMIMDecoder,
    SwinV23DMIM as SwinV23DMIMWithoutSDPA,
    SwinV23DSimMIM as SwinV23DSimMIMWithoutSDPA,
    SwinV23DVAEMIM as SwinV23DVAEMIMWithoutSDPA,
)

# %% ../nbs/04_swinv2_3d_with_sdpa.ipynb 4
class SwinV23DMHSA(SwinV23DMHSAWithoutSDPA):
    def __init__(
        self,
        dim,
        num_heads,
        window_size,
        use_relative_position_bias,
        attn_drop_prob=0.0,
        proj_drop_prob=0.0,
    ):
        super().__init__(dim, num_heads, window_size, use_relative_position_bias, attn_drop_prob, proj_drop_prob)

        # Remove attention dropout layer as that is handled automatically, but store the dropout for later
        del self.attn_drop
        self.attn_drop_prob = attn_drop_prob

    def forward(self, hidden_states: torch.Tensor):
        # hidden_states: (windowed_b, window_size_z window_size_y window_size_x, dim)
        _, num_patches_z, num_patches_y, num_patches_x, _ = hidden_states.shape

        query, key, value = rearrange(
            self.W_qkv(hidden_states),
            "b nz ny nx (n num_heads d) -> n b num_heads (nz ny nx) d",
            n=3,
            num_heads=self.num_heads,
        )
        # num_patches = window_size_z * window_size_y * window_size_x
        # Each is (windowed_b, num_heads, num_patches, per_head_dim)

        logit_scale = torch.clamp(self.logit_scale, max=np.log(1.0 / 0.01)).exp()

        query_normalized = F.normalize(query, dim=-1)
        key_normalized = F.normalize(key, dim=-1)

        query_normalized_and_scaled = query_normalized * logit_scale  # Scale the query beforehand

        relative_position_bias = None
        if self.use_relative_position_bias:
            relative_position_bias = self.calculate_relative_position_bias()

        context = F.scaled_dot_product_attention(
            query_normalized_and_scaled,
            key_normalized,
            value,
            attn_mask=relative_position_bias,  # Use this as a way to introduce relative position bias
            dropout_p=self.attn_drop_prob,
            is_causal=False,
            scale=1.0,  # Already scaled the vectors
        )
        # (windowed_b, num_heads, num_patches, per_head_dim)
        
        context = rearrange(
            context,
            "b num_heads (num_patches_z num_patches_y num_patches_x) d -> "
            "b num_patches_z num_patches_y num_patches_x (num_heads d)",
            num_patches_z=num_patches_z,
            num_patches_y=num_patches_y,
            num_patches_x=num_patches_x,
        )
        # (windowed_b, window_size_z window_size_y window_size_x, dim)

        context = self.proj(context)
        context = self.proj_drop(context)
        # (windowed_b, window_size_z window_size_y window_size_x, dim)

        return context

# %% ../nbs/04_swinv2_3d_with_sdpa.ipynb 8
class SwinV23DLayer(SwinV23DLayerWithoutSDPA):
    def __init__(
        self,
        dim,
        num_heads,
        intermediate_ratio,
        layer_norm_eps,
        window_size,
        use_relative_position_bias,
        attn_drop_prob=0.0,
        proj_drop_prob=0.0,
        mlp_drop_prob=0.0,
    ):
        super().__init__(
            dim,
            num_heads,
            intermediate_ratio,
            layer_norm_eps,
            window_size,
            use_relative_position_bias,
            attn_drop_prob,
            proj_drop_prob,
            mlp_drop_prob,
        )

        self.mhsa = SwinV23DMHSA(
            dim, num_heads, window_size, use_relative_position_bias, attn_drop_prob, proj_drop_prob
        )
        

# %% ../nbs/04_swinv2_3d_with_sdpa.ipynb 9
class SwinV23DBlock(SwinV23DBlockWithoutSDPA):
    def __init__(self, stage_config):
        super().__init__(stage_config)

        self.stage_config = stage_config
        self.w_layer = SwinV23DLayer(
            stage_config["_out_dim"],
            stage_config["num_heads"],
            stage_config["intermediate_ratio"],
            stage_config["layer_norm_eps"],
            stage_config["window_size"],
            stage_config["use_relative_position_bias"],
            stage_config.get("attn_drop_prob", 0.0),
            stage_config.get("proj_drop_prob", 0.0),
            stage_config.get("mlp_drop_prob", 0.0),
        )
        self.sw_layer = SwinV23DLayer(
            stage_config["_out_dim"],
            stage_config["num_heads"],
            stage_config["intermediate_ratio"],
            stage_config["layer_norm_eps"],
            stage_config["window_size"],
            stage_config["use_relative_position_bias"],
            stage_config.get("attn_drop_prob", 0.0),
            stage_config.get("proj_drop_prob", 0.0),
            stage_config.get("mlp_drop_prob", 0.0),
        )

# %% ../nbs/04_swinv2_3d_with_sdpa.ipynb 10
class SwinV23DStage(SwinV23DStageWithoutSDPA):
    def __init__(self, stage_config):
        super().__init__(stage_config)

        self.blocks = nn.ModuleList(
            [SwinV23DBlock(stage_config) for _ in range(stage_config["depth"])],
        )

# %% ../nbs/04_swinv2_3d_with_sdpa.ipynb 11
class SwinV23DEncoder(SwinV23DEncoderWithoutSDPA):
    def __init__(self, config):
        super().__init__(config)

        self.stages = nn.ModuleList([SwinV23DStage(stage_config) for stage_config in config["stages"]])

# %% ../nbs/04_swinv2_3d_with_sdpa.ipynb 12
class SwinV23DModel(SwinV23DModelWithoutSDPA):
    def __init__(self, config):
        super().__init__(config)

        self.encoder = SwinV23DEncoder(config)

# %% ../nbs/04_swinv2_3d_with_sdpa.ipynb 13
class SwinV23DMIM(SwinV23DMIMWithoutSDPA):
    def __init__(self, config):
        super().__init__(config)

        self.swin = SwinV23DModel(config)

# %% ../nbs/04_swinv2_3d_with_sdpa.ipynb 14
class SwinV23DSimMIM(SwinV23DSimMIMWithoutSDPA, SwinV23DMIM):
    def __init__(self, config):
        super().__init__(config)  # This calls all inits in order written above

# %% ../nbs/04_swinv2_3d_with_sdpa.ipynb 15
class SwinV23DVAEMIM(SwinV23DVAEMIMWithoutSDPA, SwinV23DMIM):
    def __init__(self, config):
        super().__init__(config)  # This calls all inits in order written above
