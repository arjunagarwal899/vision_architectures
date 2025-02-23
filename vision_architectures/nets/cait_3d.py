# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/nets/05_cait_3d.ipynb.

# %% auto 0
__all__ = ['CaiT3DStage1Config', 'CaiT3DStage2Config', 'CaiT3DConfig', 'CaiT3DAttentionLayer', 'CaiT3DStage1Layer',
           'CaiT3DStage2Layer', 'CaiT3DStage1', 'CaiT3DStage2', 'CaiT3DStage2OnlyModel', 'CaiT3DModel']

# %% ../../nbs/nets/05_cait_3d.ipynb 2
import torch
from einops import repeat
from huggingface_hub import PyTorchModelHubMixin
from pydantic import BaseModel
from torch import nn

from ..layers.attention import Attention3DMLP, MultiHeadAttention3D

# %% ../../nbs/nets/05_cait_3d.ipynb 4
class CaiT3DStage1Config(BaseModel):
    dim: int
    num_heads: int
    mlp_ratio: int
    layer_norm_eps: float
    attn_drop_prob: float = 0.0
    proj_drop_prob: float = 0.0
    mlp_drop_prob: float = 0.0

    stage1_depth: int


class CaiT3DStage2Config(BaseModel):
    dim: int
    num_heads: int
    mlp_ratio: int
    layer_norm_eps: float
    attn_drop_prob: float = 0.0
    proj_drop_prob: float = 0.0
    mlp_drop_prob: float = 0.0

    stage2_depth: int

    num_class_tokens: int | None = None


class CaiT3DConfig(CaiT3DStage1Config, CaiT3DStage2Config):
    pass

# %% ../../nbs/nets/05_cait_3d.ipynb 7
class CaiT3DAttentionLayer(nn.Module):
    def __init__(
        self,
        dim,
        num_heads,
        mlp_ratio,
        layer_norm_eps,
        attn_drop_prob=0.0,
        proj_drop_prob=0.0,
        mlp_drop_prob=0.0,
    ):
        super().__init__()

        self.mhsa = MultiHeadAttention3D(dim, num_heads, attn_drop_prob=attn_drop_prob, proj_drop_prob=proj_drop_prob)
        self.gamma1 = nn.Parameter(torch.empty(1, 1, dim))
        self.layernorm1 = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.mlp = Attention3DMLP(dim, mlp_ratio, "gelu", mlp_drop_prob)
        self.gamma2 = nn.Parameter(torch.empty(1, 1, dim))
        self.layernorm2 = nn.LayerNorm(dim, eps=layer_norm_eps)

        nn.init.uniform_(self.gamma1, a=-1e-4, b=1e-4)
        nn.init.uniform_(self.gamma2, a=-1e-4, b=1e-4)

    def forward(self, q: torch.Tensor, kv: torch.Tensor):
        # q: (b, num_tokens_in_q, dim)
        # kv: (b, num_tokens_in_kv, dim)

        res_connection1 = q
        # (b, num_tokens, dim)

        hidden_states = self.layernorm1(q)
        hidden_states = self.mhsa(hidden_states, kv, kv, tokens_as_3d=False)
        hidden_states = self.gamma1 * hidden_states
        # (b, num_tokens, dim)

        res_connection2 = hidden_states + res_connection1
        # (b, num_tokens, dim)

        hidden_states = self.layernorm2(hidden_states)
        hidden_states = self.mlp(res_connection2)
        hidden_states = self.gamma2 * hidden_states
        # (b, num_tokens, dim)

        hidden_states = hidden_states + res_connection2
        # (b, num_tokens, dim)

        return hidden_states

# %% ../../nbs/nets/05_cait_3d.ipynb 9
class CaiT3DStage1Layer(CaiT3DAttentionLayer):  # Self attention without class tokens
    def __init__(
        self,
        dim,
        num_heads,
        mlp_ratio,
        layer_norm_eps,
        attn_drop_prob=0.0,
        proj_drop_prob=0.0,
        mlp_drop_prob=0.0,
    ):
        super().__init__(
            dim,
            num_heads,
            mlp_ratio,
            layer_norm_eps,
            attn_drop_prob=attn_drop_prob,
            proj_drop_prob=proj_drop_prob,
            mlp_drop_prob=mlp_drop_prob,
        )

    def forward(self, embeddings: torch.Tensor):
        # embeddings: (b, num_tokens, dim)
        return super().forward(embeddings, embeddings)

# %% ../../nbs/nets/05_cait_3d.ipynb 11
class CaiT3DStage2Layer(CaiT3DAttentionLayer):  # Attention with class tokens
    def __init__(
        self,
        dim,
        num_heads,
        mlp_ratio,
        layer_norm_eps,
        attn_drop_prob=0.0,
        proj_drop_prob=0.0,
        mlp_drop_prob=0.0,
    ):
        super().__init__(
            dim,
            num_heads,
            mlp_ratio,
            layer_norm_eps,
            attn_drop_prob=attn_drop_prob,
            proj_drop_prob=proj_drop_prob,
            mlp_drop_prob=mlp_drop_prob,
        )

    def forward(self, class_tokens: torch.Tensor, embeddings: torch.Tensor):
        # class_tokens: (b, num_class_tokens, dim)
        # embeddings: (b, num_embedding_tokens, dim)
        return super().forward(class_tokens, embeddings)

# %% ../../nbs/nets/05_cait_3d.ipynb 14
class CaiT3DStage1(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: CaiT3DStage1Config):
        super().__init__()

        self.layers = nn.ModuleList(
            [
                CaiT3DStage1Layer(
                    config.dim,
                    config.num_heads,
                    config.mlp_ratio,
                    config.layer_norm_eps,
                    config.attn_drop_prob,
                    config.proj_drop_prob,
                    config.mlp_drop_prob,
                )
                for _ in range(config.stage1_depth)
            ]
        )

    def forward(self, embeddings: torch.Tensor):
        # embeddings: (b, num_tokens, dim)

        layer_outputs = []
        for layer in self.layers:
            embeddings = layer(embeddings)
            # (b, num_tokens, dim)

            layer_outputs.append(embeddings)

        return embeddings, layer_outputs

# %% ../../nbs/nets/05_cait_3d.ipynb 16
class CaiT3DStage2(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: CaiT3DStage2Config):
        super().__init__()

        self.layers = nn.ModuleList(
            [
                CaiT3DStage2Layer(
                    config.dim,
                    config.num_heads,
                    config.mlp_ratio,
                    config.layer_norm_eps,
                    config.attn_drop_prob,
                    config.proj_drop_prob,
                    config.mlp_drop_prob,
                )
                for _ in range(config.stage2_depth)
            ]
        )

    def forward(self, class_tokens: torch.Tensor, embeddings: torch.Tensor):
        # class_tokens: (b, num_class_tokens, dim)
        # embeddings: (b, num_embed_tokens, dim)

        class_embeddings = class_tokens

        layer_outputs = []
        for layer in self.layers:
            class_embeddings = layer(class_embeddings, embeddings)
            # (b, num_class_tokens, dim)

            layer_outputs.append(class_embeddings)

        return class_embeddings, layer_outputs

# %% ../../nbs/nets/05_cait_3d.ipynb 19
class CaiT3DStage2OnlyModel(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: CaiT3DStage2Config):
        super().__init__()

        self.num_class_tokens = config.num_class_tokens
        self.class_tokens = nn.Parameter(torch.randn(1, config.num_class_tokens, config.dim))

        self.class_attention = CaiT3DStage2(config)
        self.classifiers = nn.ModuleList([nn.Linear(config.dim, 1) for i in range(self.num_class_tokens)])

    def forward(self, embeddings: torch.Tensor):
        # embeddings: (b, num_embedding_tokens, dim)

        class_tokens = repeat(self.class_tokens, "1 n d -> b n d", b=embeddings.shape[0])
        # (b, num_class_tokens, dim)

        class_embeddings, layer_outputs = self.class_attention(class_tokens, embeddings)
        # class_embeddings: (b, num_class_tokens, dim)
        # layer_outputs: list of (b, num_embedding_tokens, dim)

        class_logits = torch.cat(
            [self.classifiers[i](class_embeddings[:, i]) for i in range(len(self.classifiers))], dim=1
        )
        # list of (b, num_classes) for each class token

        return class_logits, class_embeddings, layer_outputs

# %% ../../nbs/nets/05_cait_3d.ipynb 21
class CaiT3DModel(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config: CaiT3DConfig):
        super().__init__()

        self.num_class_tokens = config.num_class_tokens
        self.class_tokens = nn.Parameter(torch.randn(1, config.num_class_tokens, config.dim))

        self.self_attention = CaiT3DStage1(config)
        self.class_attention = CaiT3DStage2(config)
        self.classifiers = nn.ModuleList([nn.Linear(config.dim, 1) for i in range(self.num_class_tokens)])

    def forward(self, tokens: torch.Tensor):
        # tokens: (b, num_embedding_tokens, dim)

        embeddings, layer_outputs1 = self.self_attention(tokens)

        class_tokens = repeat(self.class_tokens, "1 n d -> b n d", b=embeddings.shape[0])
        # (b, num_class_tokens, dim)

        class_embeddings, layer_outputs2 = self.class_attention(class_tokens, embeddings)
        # class_embeddings: (b, num_class_tokens, dim)
        # layer_outputs: list of (b, num_embedding_tokens, dim)

        class_logits = torch.cat(
            [self.classifiers[i](class_embeddings[:, i]) for i in range(len(self.classifiers))], dim=1
        )
        # list of (b, num_classes) for each class token

        return class_logits, class_embeddings, [layer_outputs1, layer_outputs2]
