#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato
~~~~~~~~~~~~~

Initialize API and version

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

try:
    from globato._version import __version__
except ImportError:
    # Fallback when using the package from source without installing
    # in editable mode with pip (nobody should do this):
    # <https://pip.pypa.io/en/stable/topics/local-project-installs/#editable-installs>
    import warnings

    warnings.warn(
        "Importing 'globato' outside a proper installation."
        " It's highly recommended to install the package from a stable release or"
        " in editable mode.",
        stacklevel=2,
    )
    __version__ = "dev"

# import os
# import inspect
# import importlib
# import logging

# from fetchez.modules.registry import FetchezRegistry
# from fetchez.hooks import FetchHook
# from fetchez.hooks.registry import HookRegistry

# # --- Custom fetchez modules ---
# from .modules.local_fs import LocalFS
# from .modules.gebco import GEBCO_COG
# from .modules.glob_dem import GlobDEM
# from .modules.glob_coast import GlobCoast
# from .modules.sources import GlobCopernicus, GlobFabDEM, GlobMultibeam, GlobBAG, GlobNOSXYZ

# --- Schemas ---
# from . import schemas

# --- API ----
from .api import read

# logger = logging.getLogger(__name__)


# def _auto_register_hooks():
#     """Recursively scan the 'processors' directory and auto-register all FetchHooks."""

#     current_dir = os.path.dirname(os.path.abspath(__file__))
#     processors_dir = os.path.join(current_dir, "processors")

#     if not os.path.exists(processors_dir):
#         return

#     for root, dirs, files in os.walk(processors_dir):
#         dirs[:] = [d for d in dirs if not d.startswith('_')]

#         for f in files:
#             if f.endswith(".py") and not f.startswith("_"):
#                 rel_dir = os.path.relpath(root, current_dir)
#                 mod_path = rel_dir.replace(os.sep, '.')
#                 mod_name = f[:-3]

#                 full_mod_name = f"globato.{mod_path}.{mod_name}"

#                 try:
#                     mod = importlib.import_module(full_mod_name)
#                     for name, obj in inspect.getmembers(mod):
#                         if (inspect.isclass(obj) and
#                             issubclass(obj, FetchHook) and
#                             obj is not FetchHook):
#                             HookRegistry.register_hook(obj)
#                 except Exception as e:
#                     logger.warning(f"Failed to auto-load globato hook {full_mod_name}: {e}")


# def setup_fetchez(registry_cls):
#     """Register All globato capabilities with Fetchez."""

#     _auto_register_hooks()

# setup_fetchez(FetchezRegistry)

__all__ = ["read"]
