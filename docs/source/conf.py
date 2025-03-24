# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

import importlib
import inspect
import os
from configparser import ConfigParser

repo_root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
config_path = os.path.join(repo_root_path, "settings.ini")
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
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx_autodoc_typehints",
    "sphinx.ext.intersphinx",
    "sphinx.ext.mathjax",
    "sphinx.ext.todo",
    "sphinx.ext.linkcode",
    "sphinxcontrib.autodoc_pydantic",
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

project_display_name = project.replace("_", " ").title()

html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]
html_title = f"{project_display_name} (main)"
html_context = {
    "github_user": "arjunagarwal899",
    "github_repo": "vision_architectures",
    "github_version": "main",
    "doc_path": "docs/source",
}


# Function to generate GitHub links
def linkcode_resolve(domain, info):
    if domain != "py":
        raise ValueError(
            f"expected domain to be 'py', got {domain}."
            "Please adjust linkcode_resolve to either handle this domain or ignore it."
        )

    mod = importlib.import_module(info["module"])
    if "." in info["fullname"]:
        objname, attrname = info["fullname"].split(".")
        obj = getattr(mod, objname)
        try:
            # object is a method of a class
            obj = getattr(obj, attrname)
        except AttributeError:
            # object is an attribute of a class
            return None
    else:
        obj = getattr(mod, info["fullname"])

    try:
        file = inspect.getsourcefile(obj)
        source, lineno = inspect.getsourcelines(obj)
    except TypeError:
        # e.g. object is a typing.Union
        return None
    file = os.path.relpath(file, repo_root_path)
    if not file.startswith("vision_architectures"):
        # e.g. object is a typing.NewType
        return None
    start, end = lineno, lineno + len(source) - 1
    url = (
        f"https://github.com/arjunagarwal899/vision_architectures/blob/{os.environ.get('GITHUB_SHA', 'main')}"
        f"/{file}#L{start}-L{end}"
    )
    return url


# Add custom CSS and nojekyll file
def setup(app):
    app.add_css_file("custom.css")
    import os

    if os.path.exists("_static/custom.css"):
        app.connect("build-finished", lambda app, exc: os.system("cp .nojekyll _build/html/"))
