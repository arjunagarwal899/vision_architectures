# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information
import os
from configparser import ConfigParser

config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "settings.ini"))
config = ConfigParser(delimiters=["="])
config.read(config_path, encoding="utf-8")
cfg = config["DEFAULT"]

project = cfg["repo"]
copyright = cfg["copyright"]
author = cfg["author"]
release = cfg["version"]

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "sphinx_autodoc_typehints",
    "sphinx.ext.intersphinx",
    "sphinx.ext.mathjax",
    "sphinx.ext.todo",
]

# Comprehensive autodoc configuration
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",  # Used for preserving source order
    "special-members": "__init__",
    "undoc-members": True,
    "exclude-members": "__weakref__",
}

# Add any paths that contain templates
templates_path = ["_templates"]

# The suffix of source filenames
source_suffix = ".rst"

# The master toctree document
master_doc = "index"

# Configure Napoleon (for Google/NumPy style docstrings)
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = False
napoleon_use_admonition_for_notes = False
napoleon_use_admonition_for_references = False
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_preprocess_types = False
napoleon_type_aliases = None
napoleon_attr_annotations = True

# Add the path to your Python code
import os
import sys

sys.path.insert(0, os.path.abspath("../../"))

exclude_patterns = []


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

project_display_name = project.replace("_", " ").capitalize()

html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]
html_title = f"{project_display_name} (main)"


def setup(app):
    app.add_css_file("custom.css")  # Optional custom CSS
    import os

    if os.path.exists("_static/custom.css"):
        app.connect("build-finished", lambda app, exc: os.system("cp .nojekyll _build/html/"))
