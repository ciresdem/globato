#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.streams.base
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Base fetchez Reader class to create 'streams'

:copyright: (c) 2016 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

from fetchez.streams import BaseReader

class BaseGlobatoReader(BaseReader):
    """The base class for fetchez data Readers"""

    def __init__(self, path, **kwargs):
        self.path = path
        self.kwargs = kwargs

    # maybe have all globato readers define yield_points and yield_chunks here
    # can modify the chunks before going on to fetchez...
    def yield_chunks(self):
        """Stream read the source and yield standard output."""

        raise NotImplementedError
