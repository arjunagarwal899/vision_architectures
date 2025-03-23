# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/image_readers/01_safetensors_reader.ipynb.

# %% auto 0
__all__ = ['SafetensorsReader']

# %% ../../nbs/image_readers/01_safetensors_reader.ipynb 2
from typing import Any

import torch
from monai.data import ImageReader, MetaTensor, is_supported_format
from monai.utils import require_pkg
from safetensors import safe_open

# %% ../../nbs/image_readers/01_safetensors_reader.ipynb 4
@require_pkg("safetensors")
class SafetensorsReader(ImageReader):
    def __init__(
        self,
        image_key: str = "images",
        spacing_key: str | None = "spacing",
        other_keys: set[str] | None = None,
        add_channel_dim: bool = True,
        dtype=torch.float32,
    ):
        self.image_key = image_key
        self.spacing_key = spacing_key
        self.other_keys = other_keys
        self.add_channel_dim = add_channel_dim
        self.dtype = dtype

    def verify_suffix(self, filename):
        return is_supported_format(filename, ["safetensors"])

    def read(self, filepath) -> dict[str, torch.Tensor | Any]:
        if isinstance(filepath, (list, tuple)):
            return [self.read(fp) for fp in filepath]

        with safe_open(filepath, "pt") as f:
            image = f.get_tensor(self.image_key)
            spacing = f.get_tensor(self.spacing_key) if self.spacing_key else None
            others = {key: f.get_tensor(key) for key in self.other_keys} if self.other_keys else {}

        return {"image": image, "spacing": spacing, "others": others}

    def get_data(self, datapoint):
        datapoint = datapoint[0]

        image = datapoint["image"].to(self.dtype)
        spacing = datapoint["spacing"]
        others = datapoint["others"]

        if self.add_channel_dim:
            image = image.unsqueeze(0)

        image = MetaTensor(image.type(torch.float32), affine=self._spacing_to_affine(spacing))

        return image, others

    @staticmethod
    def _spacing_to_affine(spacing):
        if spacing is None:
            spacing = torch.ones(3)
        return torch.diag(torch.cat([spacing, torch.zeros(1)]))
