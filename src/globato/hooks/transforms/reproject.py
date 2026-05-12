#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.transforms.reproject
~~~~~~~~~~~~~

Reproject the data stream. Hook for fetchez.

:copyright: (c) 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import logging

from fetchez.hooks import FetchHook

from transformez.srs import SRSParser
from transformez.utils import RasterQuery

logger = logging.getLogger(__name__)


class StreamReproject(FetchHook):
    name = "stream-reproject"
    meta_stage = "stream"
    meta_desc = "Reproject the stream to the desired SRS using Transformez."
    meta_category = "point-stream"
    meta_requires = "point-stream"
    meta_aliases = ["stream_reproject"]

    def __init__(
        self, dst_srs=None, src_srs=None, vert_grid=None, cache_dir=".", **kwargs
    ):
        super().__init__(**kwargs)
        self.dst_srs = dst_srs
        self.forced_src_srs = src_srs
        self.vert_grid = vert_grid
        self.cache_dir = cache_dir
        self._cache = {}

        # print(dst_srs, src_srs)

    def _get_pipeline(self, entry_src_srs, region=None):
        if not SRSParser:
            return None

        actual_src = self.forced_src_srs or entry_src_srs or "EPSG:4326"
        if not actual_src:
            return None

        if actual_src in self._cache:
            return self._cache[actual_src]

        # todo: reproject region if nec.
        parser = SRSParser(
            actual_src,
            self.dst_srs,
            region=region,
            vert_grid=self.vert_grid,
            cache_dir=self.cache_dir,
        )
        t_in, t_out, grid_fn = parser.get_components()

        grid_query = RasterQuery(grid_fn) if grid_fn else None

        self._cache[actual_src] = (t_in, t_out, grid_query)
        return self._cache[actual_src]

    def run(self, entries):
        for mod, entry in entries:
            if not self.dst_srs:
                continue

            if self.is_point_stream(entry):
                src_srs = entry.get("src_srs", "EPSG:4326")

                safe_region = None
                mod_region = getattr(mod, "region", None)
                if mod_region:
                    try:
                        buffered = mod_region.copy().buffer(pct=5)
                        safe_region = [
                            buffered.xmin,
                            buffered.xmax,
                            buffered.ymin,
                            buffered.ymax,
                        ]
                    except Exception:
                        safe_region = list(mod_region)

                pipeline = self._get_pipeline(src_srs, region=safe_region)
                stream = entry.get("stream")

                if pipeline:
                    entry["stream"] = self._apply_transform(stream, pipeline)
                    entry["src_srs"] = self.dst_srs

        return entries

    def _apply_transform(self, stream, pipeline):
        t_to_hub, t_from_hub, grid_query = pipeline

        for chunk in stream:
            h_x, h_y = t_to_hub.transform(chunk["x"], chunk["y"])

            if grid_query and chunk["z"] is not None:
                shifts = grid_query.query(h_x, h_y)
                chunk["z"] += shifts

            d_x, d_y = t_from_hub.transform(h_x, h_y)
            chunk["x"] = d_x
            chunk["y"] = d_y

            yield chunk
