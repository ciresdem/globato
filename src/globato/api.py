#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.api
~~~~~~~~~~~

High-level Python API for Globato.
Provides interface for streaming, processing, and accessing geospatial data.

:copyright: (c) 2025 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import logging
from typing import Union, List, Optional


from fetchez.api import _compile_modules
from fetchez.spatial import parse_region

from globato.streams.base import GlobatoStream

logger = logging.getLogger(__name__)


def read(
    sources: Union[str, List[str]],
    region: Optional[Union[str, List[float]]] = None,
    shared_cache: Optional[str] = None,
    target_srs: Optional[str] = None,
    **kwargs,
) -> GlobatoStream:
    """The unified entry point for the Globato streaming API.

    Handles local file paths, directories, fetchez modules, and recipes.
    All reader options (data_type, classes, vertical_datum, etc.) are
    forwarded via kwargs.
    """

    modules = _compile_modules(
        sources, region=region, shared_cache=shared_cache, **kwargs
    )

    parsed_region = parse_region(region)[0] if region else None

    return GlobatoStream(modules=modules, region=parsed_region, target_srs=target_srs)
