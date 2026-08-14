#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.transforms.dynamic_weights
~~~~~~~~~~~~~

Adjust weight values based on point-stream array fields

:copyright: (c) 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import numpy as np
import logging
from fetchez.hooks import FetchHook
from fetchez.utils import str2bool, float_or

logger = logging.getLogger(__name__)


class DynamicWeight(FetchHook):
    """Dynamically calculates point weights based on confidence or uncertainty fields."""

    name = "dynamic-weight"
    meta_stage = "stream"
    meta_category = "stream-transform"
    meta_desc = "Calculate weights dynamically from other stream columns."

    def __init__(
        self,
        source_field="confidence",
        method="inverse",
        offset=1.0,
        scale=1.0,
        seed=0,
        on_entry=False,
        **kwargs,
    ):
        """
        Args:
            source_field: The column to read from (e.g., 'confidence', 'u', 'z').
            method: 'inverse', 'inverse_squared', 'linear_invert', or 'linear'. 'chronological'
            offset: An offset to prevent division by zero or set a ceiling or floor.
            scale: A multiplier for the resulting weight.
            seed: Seed value for the chronological method.
            on_entry: Perform dynamic weighting on an entry instead of a stream.
                      When set, source_field is the entry key.
        """
        super().__init__(**kwargs)
        self.source_field = source_field
        self.method = method
        self.offset = float(offset)
        self.scale = float(scale)
        self.seed = float(seed)
        self.on_entry = str2bool(on_entry)

        if self.on_entry:
            self.stage = "manifest"

    def _process_arr_or_val(self, arr_or_val):
        if self.method == "inverse":
            # w = scale / (src + offset)
            new_w = self.scale / (arr_or_val + self.offset)

        elif self.method == "inverse_squared":
            # Common for uncertainty: w = 1 / (U^2)
            new_w = self.scale / ((arr_or_val**2) + self.offset)

        elif self.method == "linear_invert":
            # w = offset - (src * scale)
            new_w = self.offset - (arr_or_val * self.scale)

        elif self.method == "chronological":
            new_w = max(self.offset, (arr_or_val - self.seed) * self.scale)

        else:  # linear
            new_w = arr_or_val * self.scale

        return new_w

    def _process_stream(self, stream):
        for chunk in stream:
            if chunk is None or len(chunk) == 0:
                continue

            if self.source_field not in chunk.dtype.names:
                yield chunk
                continue

            src_arr = chunk[self.source_field].astype(np.float32)
            new_w = self._process_arr_or_val(src_arr)
            new_w = np.clip(new_w, 0.0001, None)

            chunk["w"] *= new_w

            yield chunk

    def _process_entry(self, entry):
        entry_val = entry.get(self.source_field)
        if not float_or(entry_val):
            return None
        return self._process_arr_or_val(float(entry_val))

    def run(self, entries):
        for mod, entry in entries:
            if self.on_entry:
                entry["weight"] = self._process_entry(entry) or entry.get("weight")

            elif self.is_point_stream(entry):
                stream = entry.get("stream")
                entry["stream"] = self._process_stream(stream)
        return entries
