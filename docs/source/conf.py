# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

import os
import sys

sys.path.insert(0, os.path.abspath("../.."))
sys.path.insert(0, os.path.abspath("../_ext"))

project = "GloBaTo"
copyright = "2026, The Continuous-DEMs Development Team."
author = "Matthew Love"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

nitpicky = True
nitpick_ignore = [
    ("py:data", "typing.Union"),
    ("py:class", "wsgiref.types.WSGIEnvironment"),
    ("py:class", "numpy.rec.recarray"),
    ("py:class", "numpy.ndarray"),
    ("py:class", "tkinter.getint"),
    ("py:class", "tkinter.getdouble"),
]

extensions = [
    "sphinx.ext.autodoc",  # Generate docs from docstrings
    "sphinx.ext.napoleon",  # Support Google-style docstrings
    "sphinx_autodoc_typehints",  # Generate docs from typehints
    "sphinx.ext.intersphinx",  # Link to other projects' docs
    "sphinx.ext.viewcode",  # Add links to source code
    "sphinx.ext.githubpages",  # Auto-generate .nojekyll for GH Pages
    "sphinx.ext.mathjax",
    "sphinx_argparse_cli",  # argparse
    "myst_parser",  # Parse Markdown files
    "sphinxcontrib.mermaid",  # mermaid support
]

myst_fence_as_directive = ["mermaid"]

myst_enable_extensions = [
    "dollarmath",
]

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = False

# # MyST Parser configuration
# source_suffix = {
#     '.rst': 'restructuredtext',
#     '.txt': 'markdown',
#     '.md': 'markdown',
# }

templates_path = ["_templates"]
exclude_patterns = []

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "pydata_sphinx_theme"
# html_static_path = ["_static"]

html_sidebars = {
    "index": [],
    "modules/*": [],
}

html_theme_options = {
    "github_url": "https://github.com/continuous-dems/globato",
    "show_prev_next": False,
    "navbar_end": ["theme-switcher", "navbar-icon-links"],
    # "secondary_sidebar_items": ["page-toc", "edit-this-page", "sourcelink"],
    "icon_links": [
        {
            "name": "PyPI",
            "url": "https://pypi.org/project/globato/",
            "icon": "fa-solid fa-box",
        },
    ],
    "logo": {
        "text": "Globato",
    },
    "secondary_sidebar_items": [],
}

# html_context = {
#     "github_user": "continuous-dems",
#     "github_repo": "fetchez",
#     "github_version": "main",
#     "doc_path": "docs/source",
# }

# Optional: Add a logo
# html_logo = "_static/logo.png"
html_title = "Globato Documentation"
# #html_logo = "_static/fetchez_logo_micro.svg"
html_logo = "_static/continuous_dems_logo_mini.svg"

# -- Autodoc Options ---------------------------------------------------------
# Ensure methods are documented
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__",
    "undoc-members": True,
    "exclude-members": "__weakref__",
}

# Combine return description with return type
napoleon_use_rtype = False
typehints_use_rtype = False

# Show types of undocumented parameters
always_document_param_types = True

# Display the parameter's default value alongside the parameter's type
typehints_defaults = "comma"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    "requests": ("https://requests.readthedocs.io/en/latest/", None),
    "fetchez": ("https://fetchez.readthedocs.io/en/latest/", None),
    "transformez": ("https://transformez.readthedocs.io/en/latest/", None),
    "pandas": ("https://pandas.pydata.org/pandas-docs/stable/", None),
}
