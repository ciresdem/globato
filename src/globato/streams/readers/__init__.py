#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.streams.readers.base
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Globato stream readers Base

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

from .base import BaseGlobatoReader
from ..schema import ensure_schema

__all__ = ["BaseGlobatoReader", "ensure_schema"]
