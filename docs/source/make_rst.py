import os
import re


def title_from_module_name(module_name):
    """Convert module_name to a proper title format."""
    # Remove the package prefix if it exists (vision_architectures.xxx becomes xxx)
    if "." in module_name:
        module_name = module_name.split(".")[-1]

    # Replace underscores with spaces
    title = module_name.replace("_", " ")

    # Capitalize each word
    title = " ".join(word.capitalize() for word in title.split())

    # Special case handling for common abbreviations
    abbreviations = {
        "Cnn": "CNN",
        "Mlp": "MLP",
        "Fpn": "FPN",
        "Vit": "ViT",
        "Mim": "MIM",
        "Api": "API",
        "Io": "I/O",
        "3d": "3D",
        "2d": "2D",
    }

    for abbr_lower, abbr_upper in abbreviations.items():
        title = re.sub(r"\b" + abbr_lower + r"\b", abbr_upper, title)

    return title


def create_module_docs():
    """
    Create .rst files for all modules in the package with proper hierarchy.
    """
    package_name = "vision_architectures"
    package_dir = f"../../{package_name}"

    # Create directories to match the package structure
    os.makedirs("modules", exist_ok=True)

    # Create index.rst for the main page
    with open("index.rst", "w") as f:
        f.write(f"{package_name.replace('_', ' ').title()} Documentation\n")
        f.write("=" * len(f"{package_name.replace('_', ' ').title()} Documentation") + "\n\n")
        f.write(
            "Welcome to the documentation for the Vision Architectures library. This library provides implementations of various vision model architectures with a focus on vision models.\n\n"
        )
        f.write("Contents\n")
        f.write("--------\n\n")
        f.write(".. toctree::\n")
        f.write("   :maxdepth: 2\n\n")
        f.write("   modules/index\n")

    # Create modules/index.rst for the main modules page
    os.makedirs("modules", exist_ok=True)
    with open("modules/index.rst", "w") as f:
        f.write("API Reference\n")
        f.write("=============\n\n")
        f.write(
            "This section provides detailed API documentation for all modules in the Vision Architectures library.\n\n"
        )
        f.write(".. toctree::\n")
        f.write("   :maxdepth: 2\n\n")

    # Track all subdirectories
    module_categories = set()

    # Walk through the package directory to find all modules
    for root, dirs, files in os.walk(package_dir):
        for file in files:
            if file.endswith(".py") and not file.startswith("_"):
                # Get the module path
                rel_dir = os.path.relpath(root, package_dir)
                if rel_dir == ".":
                    module_path = f"{package_name}.{os.path.splitext(file)[0]}"
                    module_dir = ""
                else:
                    module_dir = rel_dir.replace("/", ".")
                    module_categories.add(module_dir)
                    module_path = f"{package_name}.{module_dir}.{os.path.splitext(file)[0]}"

                # Create directory for module category if it doesn't exist
                if module_dir:
                    os.makedirs(f"modules/{module_dir.replace('.', '/')}", exist_ok=True)

                    # Create index.rst for the module category if it doesn't exist
                    category_index_path = f"modules/{module_dir.replace('.', '/')}/index.rst"
                    if not os.path.exists(category_index_path):
                        with open(category_index_path, "w") as f:
                            category_title = title_from_module_name(module_dir)
                            f.write(f"{category_title}\n")
                            f.write("=" * len(category_title) + "\n\n")
                            f.write(f"This section documents the {category_title.lower()} components.\n\n")
                            f.write(".. toctree::\n")
                            f.write("   :maxdepth: 1\n\n")

                # Create .rst file for the module
                module_filename = os.path.splitext(file)[0]
                if module_dir:
                    rst_dir = f"modules/{module_dir.replace('.', '/')}"
                    rst_file = f"{rst_dir}/{module_filename}.rst"
                else:
                    rst_dir = "modules"
                    rst_file = f"{rst_dir}/{module_filename}.rst"

                with open(rst_file, "w") as f:
                    module_title = title_from_module_name(module_path)
                    f.write(f"{module_title}\n")
                    f.write("=" * len(module_title) + "\n\n")
                    f.write(".. automodule:: " + module_path + "\n")
                    f.write("   :members:\n")
                    f.write("   :undoc-members:\n")
                    f.write("   :show-inheritance:\n")

                # Add module to the appropriate index
                if module_dir:
                    with open(f"modules/{module_dir.replace('.', '/')}/index.rst", "a") as f:
                        f.write(f"   {module_filename}\n")

    # Update the modules/index.rst with the categories
    with open("modules/index.rst", "a") as f:
        for category in sorted(module_categories):
            category_path = category.replace(".", "/")
            f.write(f"   {category_path}/index\n")


if __name__ == "__main__":
    create_module_docs()
