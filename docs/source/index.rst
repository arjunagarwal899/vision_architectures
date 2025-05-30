.. vision_architectures documentation master file

Vision Architectures Documentation
==================================

Vision Architectures is a comprehensive library that implements various deep learning architectures for computer vision tasks, with a particular focus on 3D vision models. The library includes implementations of state-of-the-art architectures such as Vision Transformers (ViT), Swin Transformers, and their 3D variants.

Getting Started
---------------

To use this library, install it via pip:

.. code-block:: bash

   pip install vision-architectures

or, to develop it, clone it from github and run:

.. code-block:: bash

    pip install -e . 

Basic Usage Example:

.. code-block:: python

   import torch
   from vision_architectures.nets.vit_3d import ViT3D
   
   # Create a 3D Vision Transformer model
   model = ViT3D(
        dim=768,
        num_heads=12,
        mlp_ratio=4,
        patch_size=(16, 16, 16),
        in_channels=1,
        encoder_depth=12,
        num_class_tokens=1,
        layer_norm_eps=1e-6,
   )
   
   # Forward pass with 3D input
   x = torch.randn(1, 1, 128, 128, 128)  # Batch, Channels, Depth, Height, Width
   spacings = torch.tensor([[1.0, 1.0, 1.0]])  # Voxel spacing information
   class_tokens, encodings = model(x, spacings)

.. toctree::
   :maxdepth: 1
   :caption: Contents:
   
   modules/index