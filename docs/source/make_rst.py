import os


def create_module_docs():
    """
    Create .rst files for all modules in the package.

    Args:
        package_name: Name of the package
        package_dir: Directory containing the package
    """
    package_name = "vision_architectures"
    package_dir = f"../../{package_name}"

    # Create package .rst file
    with open(f"{package_name}.rst", "w") as f:
        f.write(f"{package_name} package\n")
        f.write("=" * (len(package_name) + 8) + "\n\n")
        f.write(".. automodule:: " + package_name + "\n")
        f.write("   :members:\n")
        f.write("   :undoc-members:\n")
        f.write("   :show-inheritance:\n\n")
        f.write("Submodules\n")
        f.write("---------\n\n")
        f.write(".. toctree::\n")
        f.write("   :maxdepth: 4\n\n")

    # Walk through the package directory
    for root, dirs, files in os.walk(package_dir):
        for file in files:
            if file.endswith(".py") and not file.startswith("_"):
                # Get the module path
                rel_dir = os.path.relpath(root, package_dir)
                if rel_dir == ".":
                    module_path = f"{package_name}.{os.path.splitext(file)[0]}"
                else:
                    module_path = f"{package_name}.{rel_dir.replace('/', '.')}.{os.path.splitext(file)[0]}"

                # Create .rst file for the module
                rst_file = module_path + ".rst"
                with open(rst_file, "w") as f:
                    f.write(f"{module_path} module\n")
                    f.write("=" * (len(module_path) + 8) + "\n\n")
                    f.write(".. automodule:: " + module_path + "\n")
                    f.write("   :members:\n")
                    f.write("   :undoc-members:\n")
                    f.write("   :show-inheritance:\n")

                # Add to package toctree
                with open(f"{package_name}.rst", "a") as f:
                    f.write(f"   {module_path}\n")


if __name__ == "__main__":
    create_module_docs()
    create_module_docs()
