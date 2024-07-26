# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/07_maxvit_3d.ipynb.

# %% auto 0
__all__ = ['test_encoder_config', 'MaxViT3DMLP', 'MaxViTSqueezeExcitation', 'MaxViTCNNBlock3d', 'MaxViTStochasticDepth',
           'MaxViTMBConv3d', 'MaxViT3DMHSA', 'MaxViT3DStem0', 'MaxViT3DBlock', 'MaxViT3DStem', 'MaxViT3DEncoder']

# %% ../nbs/07_maxvit_3d.ipynb 1
import torch
from torch import nn
import torch.nn.functional as F
from einops import rearrange

# %% ../nbs/07_maxvit_3d.ipynb 2
class MaxViT3DMLP(nn.Module):
    def __init__(self, dim, mult=4, dropout=0.0, bias=False):
        super().__init__()
        inner_dim = int(dim * mult)
        self.net = nn.Sequential(
            nn.Linear(dim, inner_dim, bias=bias),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(inner_dim, dim, bias=bias),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        return self.net(x)

# %% ../nbs/07_maxvit_3d.ipynb 3
class MaxViTSqueezeExcitation(nn.Module):
    def __init__(self, in_channels, reduced_dim):
        super().__init__()
        self.se = nn.Sequential(
            nn.AdaptiveAvgPool3d(1),  # input C x H x W x D --> C x 1 X 1 x 1  ONE value of each channel
            nn.Conv3d(in_channels, reduced_dim, kernel_size=1),  # expansion
            nn.SiLU(),  # activation
            nn.Conv3d(reduced_dim, in_channels, kernel_size=1),  # brings it back
            nn.Sigmoid(),
        )

    def forward(self, x):
        return x * self.se(x)


class MaxViTCNNBlock3d(nn.Module):
    def __init__(
        self, in_channels, out_channels, kernel_size, stride=1, padding=0, groups=1, act=True, bn=True, bias=False
    ):
        super().__init__()
        self.cnn = nn.Conv3d(
            in_channels, out_channels, kernel_size, stride, padding, groups=groups, bias=bias
        )  # bias set to False as we are using BatchNorm

        self.bn = nn.BatchNorm3d(out_channels) if bn else nn.Identity()
        self.silu = nn.SiLU() if act else nn.Identity()  #  SiLU <--> Swish same Thing
        # 1 layer in MBConv doesn't have activation function

    def forward(self, x):
        out = self.cnn(x)
        out = self.bn(out)
        out = self.silu(out)
        return out


# dropout
class MaxViTStochasticDepth(nn.Module):
    def __init__(self, survival_prob=0.8):
        super().__init__()
        self.survival_prob = survival_prob

    def forward(self, x):  # form of dropout , randomly remove some layers not during testing
        if not self.training:
            return x
        binary_tensor = (
            torch.rand(x.shape[0], 1, 1, 1, 1, device=x.device) < self.survival_prob
        )  # maybe add 1 more here
        return torch.div(x, self.survival_prob) * binary_tensor


class MaxViTMBConv3d(nn.Module):
    def __init__(
        self,
        in_channels,
        out_channels,
        kernel_size,
        padding,
        is_first=False,
        expand_ratio=6,
        reduction=4,  # squeeze excitation 1/4 = 0.25
        survival_prob=0.8,  # for stocastic depth
    ):
        super().__init__()

        stride = 2 if is_first else 1
        survival_prob = 0.8
        self.use_residual = True if not is_first else False
        hidden_dim = int(out_channels * expand_ratio)
        reduced_dim = int(in_channels / reduction)
        padding = padding

        # expansion phase
        self.expand = nn.Identity() if (expand_ratio == 1) else MaxViTCNNBlock3d(in_channels, hidden_dim, kernel_size=1)

        # Depthwise convolution phase
        self.depthwise_conv = MaxViTCNNBlock3d(
            hidden_dim, hidden_dim, kernel_size=kernel_size, stride=stride, padding=padding, groups=hidden_dim
        )

        # Squeeze Excitation phase
        self.se = MaxViTSqueezeExcitation(hidden_dim, reduced_dim=reduced_dim)

        # output phase
        self.pointwise_conv = MaxViTCNNBlock3d(hidden_dim, out_channels, kernel_size=1, stride=1, act=False, padding=0)
        # add Sigmoid Activation as mentioned in the paper

        # drop connect
        self.drop_layers = MaxViTStochasticDepth(survival_prob=survival_prob)

    def forward(self, x):
        # Not 1st MBConv | 1st MBConv
        residual = x
        # (b, d, x, y, z) | (b, d, x, y, z)
        x = self.expand(x)
        # (b, 6d, x, y, z) | (b, 6d, x, y, z)
        x = self.depthwise_conv(x)
        # (b, 6d, x/2, y/2, z/2) | (b, 6d, x, y, z)
        x = self.se(x)
        # (b, 6d, x/2, y/2, z/2) | (b, 6d, x, y, z)
        x = self.pointwise_conv(x)
        # b, d,x,y,z | b,2d,x/2,y/2,z/2
        if self.use_residual:
            x = self.drop_layers(x)
            x += residual
        # b, d,x,y,z | b,2d,x/2,y/2,z/2
        return x

# %% ../nbs/07_maxvit_3d.ipynb 4
class MaxViT3DMHSA(nn.Module):
    def __init__(self, dim, dim_per_head=32, dropout=0.0, window_size=(7, 7, 7), bias=False):
        super().__init__()
        assert (dim % dim_per_head) == 0, "dimension should be divisible by dimension per head"

        self.heads = dim // dim_per_head
        self.scale = dim_per_head**-0.5

        self.to_qkv = nn.Linear(dim, dim * 3, bias=bias)

        self.to_out = nn.Sequential(nn.Linear(dim, dim, bias=bias), nn.Dropout(dropout))

        # relative positional bias
        w1, w2, w3 = window_size
        self.rel_pos_bias = nn.Embedding((2 * w1 - 1) * (2 * w2 - 1) * (2 * w3 - 1), self.heads)
        pos1 = torch.arange(w1, dtype=torch.int32)
        pos2 = torch.arange(w2, dtype=torch.int32)
        pos3 = torch.arange(w3, dtype=torch.int32)
        # First we use the torch.arange and torch.meshgrid functions to generate the corresponding coordinates, [3,H,W,D]
        # and then stack them up and expand them into a two-dimensional vector to get the absolute position index.
        grid = torch.stack(torch.meshgrid(pos1, pos2, pos3, indexing="ij"))
        grid = rearrange(grid, "c i j k -> (i j k) c")
        # insert a dimension in the first dimension and the second dimension respectively, perform broadcast subtraction, and obtain the tensor of 3, whd*ww, whd*ww
        rel_pos = rearrange(grid, "i ... -> i 1 ...") - rearrange(grid, "j ... -> 1 j ...")
        rel_pos[..., 0] += w1 - 1
        rel_pos[..., 1] += w2 - 1
        rel_pos[..., 2] += w3 - 1
        # Do a multiplication operation to distinguish, sum up the last dimension, and expand it into a one-dimensional coordinate   a*x1 + b*x2 + c*x3  (a= hd b=d c =1)
        rel_pos_indices = (rel_pos * torch.tensor([(2 * w2 - 1) * (2 * w3 - 1), (2 * w3 - 1), 1])).sum(dim=-1)

        # Register as a variable that does not participate in learning
        self.register_buffer("rel_pos_indices", rel_pos_indices, persistent=False)
        self.dropout_prob = dropout

    def forward(self, x):
        _, height, width, depth, window_height, window_width, window_depth, _ = x.shape
        h = self.heads

        # b, x/w1, y/w2, z/w3, w1, w2, w3, d
        x = rearrange(x, "b x y z w1 w2 w3 d -> (b x y z) (w1 w2 w3) d")
        # total_b, total_w, d
        q, k, v = self.to_qkv(x).chunk(3, dim=-1)
        q, k, v = map(lambda t: rearrange(t, "b n (h d ) -> b h n d", h=h), (q, k, v))  # split_heads

        # Calculate rel_pos_bias
        rel_pos_bias = rearrange(
            self.rel_pos_bias(self.rel_pos_indices),
            "i j h -> h i j",
        )

        # total_b, num_heads, total_w, d/num_heads
        context = F.scaled_dot_product_attention(
            q,
            k,
            v,
            attn_mask=rel_pos_bias,  # Use this as a way to introduce relative position bias
            dropout_p=self.dropout_prob,
            is_causal=False,
            scale=self.scale,  # Already scaled the vectors
        )

        # total_b, num_heads, total_w, d/num_heads
        out = rearrange(
            context, "b h (w1 w2 w3) d -> b w1 w2 w3 (h d)", w1=window_height, w2=window_width, w3=window_depth
        )  # merge heads

        # total_b, w1, w2 ,w3, d
        out = self.to_out(out)  # combine heads out
        out = rearrange(out, "(b x y z) ... -> b x y z ...", x=height, y=width, z=depth)
        # b, x/w1, y/w2, z/w3, w1, w2, w3, d
        return out

# %% ../nbs/07_maxvit_3d.ipynb 5
class MaxViT3DStem0(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.conv_stem = nn.Sequential(
            nn.Conv3d(
                self.config["in_channels"],
                self.config["hidden_dim"],
                kernel_size=3,
                stride=2,
                padding=1,
                bias=self.config["bias"],
            ),
            nn.Conv3d(self.config["hidden_dim"], self.config["out_channels"], 3, padding=1, bias=self.config["bias"]),
        )

    def forward(self, x):
        return self.conv_stem(x)

# %% ../nbs/07_maxvit_3d.ipynb 7
class MaxViT3DBlock(nn.Module):
    def __init__(self, block_config, is_first=False):
        super().__init__()

        self.w1, self.w2, self.w3 = block_config["window_size"]
        stem_dim_in = block_config["stem_dim_in"]
        dim = block_config["dim"]
        dim_per_head = block_config["dim_per_head"]
        dropout = block_config["dropout"]
        window_size = block_config["window_size"]
        expansion_rate = block_config["expansion_rate"]
        shrinkage_rate = block_config["shrinkage_rate"]
        bias = block_config["bias"]

        self.MBConv = MaxViTMBConv3d(
            in_channels=stem_dim_in if is_first else dim,
            out_channels=dim,
            kernel_size=3,
            padding=1,
            is_first=is_first,
            expand_ratio=expansion_rate if expansion_rate is not None else 4,
            reduction=shrinkage_rate if shrinkage_rate is not None else 4,  # squeeze excitation 1/4 = 0.25
            survival_prob=1 - dropout,  # for stocastic depth
        )

        self.layernorm1 = nn.LayerNorm(dim)
        self.blockAttn = MaxViT3DMHSA(
            dim=dim, dim_per_head=dim_per_head, dropout=dropout, window_size=window_size, bias=bias
        )

        self.layernorm2 = nn.LayerNorm(dim)
        self.FFN1 = MaxViT3DMLP(dim=dim, dropout=dropout, bias=bias)

        self.layernorm3 = nn.LayerNorm(dim)
        self.gridAttn = MaxViT3DMHSA(
            dim=dim, dim_per_head=dim_per_head, dropout=dropout, window_size=window_size, bias=bias
        )
        self.layernorm4 = nn.LayerNorm(dim)
        self.FFN2 = MaxViT3DMLP(dim=dim, dropout=dropout, bias=bias)

    def forward(self, x):
        # b,d,x,y,z | b,d/2,2x,2y,2z for first MBConv of stem
        x = self.MBConv(x)
        # b,d,x,y,z
        x = rearrange(
            x, "b d (x w1) (y w2) (z w3) -> b x y z w1 w2 w3 d", w1=self.w1, w2=self.w2, w3=self.w3
        )  # block-like attention
        # b,x/w1,y/w2,z/w3,w1,w2,w3,d
        x = self.layernorm1(x)
        x = x + self.blockAttn(x)
        x = self.layernorm2(x)
        x = x + self.FFN1(x)
        # b,x/w1,y/w2,z/w3,w1,w2,w3,d
        x = rearrange(x, "b x y z w1 w2 w3 d -> b d (x w1) (y w2) (z w3)")
        # b,d,x,y,z
        x = rearrange(
            x, "b d (w1 x) (w2 y) (w3 z) -> b x y z w1 w2 w3 d", w1=self.w1, w2=self.w2, w3=self.w3
        )  # grid-like attention
        # b,x/w1,y/w2,z/w3,w1,w2,w3,d
        x = self.layernorm3(x)
        x = x + self.gridAttn(x)
        x = self.layernorm4(x)
        x = x + self.FFN2(x)
        # b,x/w1,y/w2,z/w3,w1,w2,w3,d
        x = rearrange(x, "b x y z w1 w2 w3 d -> b d (w1 x) (w2 y) (w3 z)")
        # b,d,x,y,z

        return x

# %% ../nbs/07_maxvit_3d.ipynb 10
class MaxViT3DStem(nn.Module):
    def __init__(self, stem_config):
        super().__init__()
        self.stem = nn.ModuleList(
            [MaxViT3DBlock(stem_config, is_first=(i == 0)) for i in range(stem_config["num_maxvit_blocks"])]
        )

    def forward(self, hidden_states: torch.Tensor):
        for layer in self.stem:
            hidden_states = layer(hidden_states)
        return hidden_states

# %% ../nbs/07_maxvit_3d.ipynb 12
class MaxViT3DEncoder(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.stems = nn.ModuleList([])

        self.stems.append(MaxViT3DStem0(config["stem0"]))

        for stage_config in config["stems"]:
            self.stems.append(MaxViT3DStem(stage_config))

    def forward(self, hidden_states: torch.Tensor):

        for stem in self.stems:
            hidden_states = stem(hidden_states)

        return hidden_states

# %% ../nbs/07_maxvit_3d.ipynb 13
test_encoder_config = {
    "stem0": {"in_channels": 1, "hidden_dim": 32, "out_channels": 32, "bias": True},
    "stems": [
        {
            "num_maxvit_blocks": 2,
            "stem_dim_in": 32,
            "dim": 64,  # dimension of first layer, doubles every layer
            "dim_per_head": 8,  # dimension of attention heads, kept at 32 in paper`
            "window_size": (4, 4, 4),  # window size for block and grids
            "dropout": 0.1,  # dropout
            "expansion_rate": None,  # squeeze and (excitation)
            "shrinkage_rate": None,  # (squeeze) and excitation
            "bias": True,
        },
        {
            "num_maxvit_blocks": 2,
            "stem_dim_in": 64,
            "dim": 128,  # dimension of first layer, doubles every layer
            "dim_per_head": 8,  # dimension of attention heads, kept at 32 in paper`
            "window_size": (4, 4, 4),  # window size for block and grids
            "dropout": 0.1,  # dropout
            "expansion_rate": None,  # squeeze and (excitation)
            "shrinkage_rate": None,  # (squeeze) and excitation }
            "bias": True,
        },
        {
            "num_maxvit_blocks": 2,
            "stem_dim_in": 128,
            "dim": 256,  # dimension of first layer, doubles every layer
            "dim_per_head": 8,  # dimension of attention heads, kept at 32 in paper`
            "window_size": (2, 2, 2),  # window size for block and grids
            "dropout": 0.1,  # dropout
            "expansion_rate": None,  # squeeze and (excitation)
            "shrinkage_rate": None,  # (squeeze) and excitation }
            "bias": True,
        },
    ],
}