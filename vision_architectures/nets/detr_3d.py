# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/nets/06_detr_3d.ipynb.

# %% auto 0
__all__ = ['DETR3DDecoderLayer', 'DETR3DDecoder', 'DETR3DBBoxMLP', 'get_coords_grid', 'get_3d_position_embeddings',
           'embed_spacings_in_position_embeddings', 'DETR3DPositionEmbeddings', 'DETR3DModel']

# %% ../../nbs/nets/06_detr_3d.ipynb 2
import torch
from einops import rearrange, repeat
from huggingface_hub import PyTorchModelHubMixin
from torch import nn

from ..layers.attention import Attention1D, Attention1DMLP

# %% ../../nbs/nets/06_detr_3d.ipynb 4
class DETR3DDecoderLayer(nn.Module):
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

        self.mhsa = Attention1D(dim, num_heads, attn_drop_prob=attn_drop_prob, proj_drop_prob=proj_drop_prob)
        self.layernorm1 = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.mhca = Attention1D(dim, num_heads, attn_drop_prob=attn_drop_prob, proj_drop_prob=proj_drop_prob)
        self.layernorm2 = nn.LayerNorm(dim, eps=layer_norm_eps)
        self.mlp = Attention1DMLP(dim, mlp_ratio, mlp_drop_prob=mlp_drop_prob)
        self.layernorm3 = nn.LayerNorm(dim, eps=layer_norm_eps)

    def forward(self, object_queries: torch.Tensor, embeddings: torch.Tensor):  # This uses post-normalization
        # object_queries: (b, num_possible_objects, dim)
        # embeddings: (b, num_embed_tokens, dim)

        res_connection1 = object_queries
        # (b, num_tokens_in_q, dim)

        hidden_states_q = self.mhsa(object_queries, object_queries, object_queries)
        hidden_states_q = self.layernorm1(hidden_states_q)
        # (b, num_tokens_in_q, dim)

        res_connection2 = hidden_states_q + res_connection1
        # (b, num_tokens_in_q, dim)

        hidden_states = self.mhca(res_connection2, embeddings, embeddings)
        hidden_states = self.layernorm2(hidden_states)
        # (b, num_tokens_in_q, dim)

        res_connection3 = hidden_states + res_connection2
        # (b, num_tokens_in_q, dim)

        hidden_states = self.mlp(res_connection3)
        hidden_states = self.layernorm3(hidden_states)
        # (b, num_tokens_in_q, dim)

        hidden_states = hidden_states + res_connection3
        # (b, num_tokens_in_q, dim)

        return hidden_states

# %% ../../nbs/nets/06_detr_3d.ipynb 7
class DETR3DDecoder(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config):
        super().__init__()

        self.layers = nn.ModuleList(
            [
                DETR3DDecoderLayer(
                    config["dim"],
                    config["num_heads"],
                    config["mlp_ratio"],
                    config["layer_norm_eps"],
                    config["attn_drop_prob"],
                    config["proj_drop_prob"],
                    config["mlp_drop_prob"],
                )
                for _ in range(config["decoder_depth"])
            ]
        )

    def forward(self, object_queries: torch.Tensor, embeddings: torch.Tensor):
        # object_queries: (b, num_possible_objects, dim)
        # embeddings: (b, num_embed_tokens, dim)

        object_embeddings = object_queries

        layer_outputs = []
        for layer in self.layers:
            object_embeddings = layer(object_embeddings, embeddings)
            layer_outputs.append(object_embeddings)

        return object_embeddings, layer_outputs

# %% ../../nbs/nets/06_detr_3d.ipynb 9
class DETR3DBBoxMLP(nn.Module):
    def __init__(self, config):
        super().__init__()

        dim = config["dim"]
        num_classes = config["num_classes"]

        self.linear = nn.Linear(dim, 1 + 4 + num_classes)

    def forward(self, object_embeddings: torch.Tensor):
        # object_embeddings: (b, num_possible_objects, dim)

        bboxes = self.linear(object_embeddings)
        # (b, num_possible_objects, 1 + 4 + num_classes)

        bboxes[:, :, :5] = bboxes[:, :, :5].sigmoid()
        bboxes[:, :, 5:] = bboxes[:, :, 5:].softmax(-1)

        return bboxes

# %% ../../nbs/nets/06_detr_3d.ipynb 13
def get_coords_grid(grid_size):
    d, h, w = grid_size

    grid_d = torch.arange(d, dtype=torch.int32)
    grid_h = torch.arange(h, dtype=torch.int32)
    grid_w = torch.arange(w, dtype=torch.int32)

    grid = torch.meshgrid(grid_w, grid_h, grid_d, indexing="ij")
    grid = torch.stack(grid, axis=0)
    # (3, d, h, w)

    return grid

# %% ../../nbs/nets/06_detr_3d.ipynb 14
def get_3d_position_embeddings(embedding_size, grid_size, patch_size=(1, 1, 1)):
    if embedding_size % 6 != 0:
        raise ValueError("embed_dim must be divisible by 6")

    grid = get_coords_grid(grid_size)
    # (3, d, h, w)

    grid = rearrange(grid, "x d h w -> x 1 d h w").contiguous()
    # (3, 1, d, h, w)

    omega = torch.arange(embedding_size // 6, dtype=torch.float32)
    omega /= embedding_size / 6.0
    omega = 1.0 / 10000**omega
    # (d // 6)

    patch_multiplier = torch.Tensor(patch_size) / min(patch_size)

    position_embeddings = []
    for i, grid_subset in enumerate(grid):
        grid_subset = grid_subset.reshape(-1)
        out = torch.einsum("m,d->md", grid_subset, omega)

        emb_sin = torch.sin(out)
        emb_cos = torch.cos(out)

        emb = torch.cat([emb_sin, emb_cos], axis=1) * patch_multiplier[i]
        position_embeddings.append(emb)

    position_embeddings = torch.cat(position_embeddings, axis=1)
    # (embedding_size, d * h * w)
    d, h, w = grid_size
    position_embeddings = rearrange(position_embeddings, "(d h w) e -> 1 e d h w", d=d, h=h, w=w).contiguous()
    # (1, embedding_size, d, h, w)

    return position_embeddings

# %% ../../nbs/nets/06_detr_3d.ipynb 15
def embed_spacings_in_position_embeddings(embeddings: torch.Tensor, spacings: torch.Tensor):
    assert spacings.ndim == 2, "Please provide spacing information for each batch element"
    _, embedding_size, _, _, _ = embeddings.shape
    assert embedding_size % 3 == 0, "To embed spacing info, the embedding size must be divisible by 3"
    embeddings = embeddings * repeat(spacings, f"B S -> B (S {int(embedding_size / 3)}) 1 1 1", S=3)

    return embeddings

# %% ../../nbs/nets/06_detr_3d.ipynb 16
class DETR3DPositionEmbeddings(nn.Module):
    def __init__(self, config):
        super().__init__()

        self.config = config

        dim = config["dim"]
        grid_size = (
            config["image_size"][0] // config["patch_size"][0],
            config["image_size"][1] // config["patch_size"][1],
            config["image_size"][2] // config["patch_size"][2],
        )
        if config["learnable_absolute_position_embeddings"]:
            absolute_position_embeddings = nn.Parameter(
                torch.randn(1, dim, grid_size[0], grid_size[1], grid_size[2]),
                requires_grad=True,
            )
        else:
            absolute_position_embeddings = get_3d_position_embeddings(dim, grid_size, config["patch_size"])
        self.register_buffer("absolute_position_embeddings", absolute_position_embeddings)

    def forward(
        self,
        embeddings: torch.Tensor,
        spacings: torch.Tensor,
    ):
        # embeddings: (b, dim, num_tokens_z, num_tokens_y, num_tokens_x)

        absolute_position_embeddings = self.absolute_position_embeddings
        # (1, dim, num_tokens_z, num_tokens_y, num_tokens_x)
        if self.config["embed_spacing_info"]:
            absolute_position_embeddings = embed_spacings_in_position_embeddings(absolute_position_embeddings, spacings)
            # (b, dim, num_tokens_z, num_tokens_y, num_tokens_x)

        embeddings = embeddings + absolute_position_embeddings
        # (b, dim, num_tokens_z, num_tokens_y, num_tokens_x)

        embeddings = rearrange(embeddings, "b d nz ny nx -> b (nz ny nx) d").contiguous()

        return embeddings

# %% ../../nbs/nets/06_detr_3d.ipynb 19
class DETR3DModel(nn.Module, PyTorchModelHubMixin):
    def __init__(self, config):
        super().__init__()

        self.embeddings = DETR3DPositionEmbeddings(config)
        self.pos_drop = nn.Dropout(config.get("drop_prob", 0.0))
        self.num_possible_objects = config["num_possible_objects"]
        object_queries = nn.Parameter(torch.randn(1, self.num_possible_objects, config["dim"]))
        self.register_buffer("object_queries", object_queries)
        self.decoder = DETR3DDecoder(config)
        self.bbox_mlp = DETR3DBBoxMLP(config)

    def forward(
        self,
        embeddings: torch.Tensor,
        spacings: torch.Tensor,
    ):
        # embeddings: (b, dim, num_tokens_z, num_tokens_y, num_tokens_x)
        # spacings: (b, 3)

        embeddings = self.embeddings(embeddings, spacings)
        embeddings = self.pos_drop(embeddings)
        # (b, num_embed_tokens, dim)

        object_queries = repeat(self.object_queries, "1 n d -> b n d", b=embeddings.shape[0])
        # (b, num_possible_objects, dim)

        object_embeddings, layer_outputs = self.decoder(object_queries, embeddings)
        # object_embeddings: (b, num_possible_objects, dim)
        # layer_outputs: list of (b, num_possible_objects, dim)

        bboxes = self.bbox_mlp(object_embeddings)
        # (b, num_possible_objects, 1 + 4 + num_classes)

        return bboxes, object_embeddings, layer_outputs
