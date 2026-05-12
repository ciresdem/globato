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
        **kwargs,
    ):
        """
        Args:
            source_field (str): The column to read from (e.g., 'confidence', 'u', 'z').
            method (str): 'inverse', 'inverse_squared', 'linear_invert', or 'linear'.
            offset (float): A mathematical offset to prevent division by zero or set a ceiling.
            scale (float): A multiplier for the resulting weight.
        """
        super().__init__(**kwargs)
        self.source_field = source_field
        self.method = method
        self.offset = float(offset)
        self.scale = float(scale)

    def _process_stream(self, stream):
        for chunk in stream:
            if chunk is None or len(chunk) == 0:
                continue

            # If the source field isn't present, yield untouched
            if self.source_field not in chunk.dtype.names:
                yield chunk
                continue

            # Extract the source array
            src_arr = chunk[self.source_field].astype(np.float32)

            if self.method == "inverse":
                # w = scale / (src + offset)
                new_w = self.scale / (src_arr + self.offset)

            elif self.method == "inverse_squared":
                # Common for uncertainty: w = 1 / (U^2)
                new_w = self.scale / ((src_arr**2) + self.offset)

            elif self.method == "linear_invert":
                # w = offset - (src * scale)
                new_w = self.offset - (src_arr * self.scale)

            else:  # linear
                new_w = src_arr * self.scale

            new_w = np.clip(new_w, 0.0001, None)
            chunk["w"] = new_w
            yield chunk

    def run(self, entries):
        for mod, entry in entries:
            if self.is_point_stream(entry):
                stream = entry.get("stream")
                entry["stream"] = self._process_stream(stream)
        return entries
