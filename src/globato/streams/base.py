#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
from fetchez.streams import BaseReader
from .schema import ensure_schema

logger = logging.getLogger(__name__)


class BaseGlobatoReader(BaseReader):
    """The base class for Globato data readers.
    Ensures all output streams conform to the strict xyz_recarray schema.
    """

    def __init__(self, path, weight=1.0, uncertainty=0.0, **kwargs):
        super().__init__(path, **kwargs)
        self.module_weight = float(weight)
        self.module_unc = float(uncertainty)

    def _yield_raw_chunks(self):
        """Child globato classes MUST implement this to yield their raw format data."""

        raise NotImplementedError

    def yield_chunks(self):
        """Automatically intercepts the raw chunks and enforces the Globato schema."""

        raw_stream = self._yield_raw_chunks()

        yield from ensure_schema(
            raw_stream,
            module_weight=self.module_weight,
            module_unc=self.module_unc
        )
