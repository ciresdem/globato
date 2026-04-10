#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.sinks.raster_writer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Writes the stream to a raster

:copyright: (c) 2016 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import logging
import rasterio
from fetchez.hooks import FetchHook

logger = logging.getLogger(__name__)


class RasterWrite(FetchHook):
    """Universal sink that writes a TIFF. Can act as a terminal sink or an inline tap."""

    name = "raster_write"
    meta_stage = "collection"
    meta_category = "sink"

    def __init__(self, suffix="_final", artifact_id=None, inline=False, **kwargs):
        super().__init__(**kwargs)
        self.suffix = suffix
        self.artifact_id = artifact_id or self.name
        self.inline = str(inline).lower() in ["true", "1", "t", "yes"]

    def _write_stream(self, stream, dst_fn):
        """Generator that intercepts the stream, writes to disk, and yields onward."""

        profile = next(stream)
        yield profile

        with rasterio.open(dst_fn, "w", **profile) as dst:
            for window, buff_win, data, ndv, transform in stream:
                y_off = window.row_off - buff_win.row_off
                x_off = window.col_off - buff_win.col_off

                if data.ndim == 3:
                    final_chunk = data[
                        :, y_off : y_off + window.height, x_off : x_off + window.width
                    ]
                    dst.write(final_chunk, window=window)
                else:
                    final_chunk = data[
                        y_off : y_off + window.height, x_off : x_off + window.width
                    ]
                    dst.write(final_chunk, 1, window=window)

                # YIELD THE CHUNK ONWARD to keep the stream alive!
                yield window, buff_win, data, ndv, transform

    def run(self, entries):
        # auto-grid point streams
        for mod, entry in entries:
            if entry.get("stream_type") == "xyz_recarray":
                from globato.hooks.transforms.point_pixels import Point2PixelStream

                gridder = Point2PixelStream()
                gridder.run([(mod, entry)])

        # Wrap the raster streams
        for mod, entry in entries:
            stream = entry.get("raster_stream")
            if not stream:
                continue

            src_fn = entry.get("dst_fn")
            base = os.path.splitext(src_fn)[0]
            dst_fn = f"{base}{self.suffix}.tif"

            writer_generator = self._write_stream(stream, dst_fn)

            if self.inline:
                entry["raster_stream"] = writer_generator
            else:
                logger.info(f"Draining raster stream to disk: {dst_fn}")
                for _ in writer_generator:
                    pass
                del entry["raster_stream"]

            entry.setdefault("artifacts", {})[self.artifact_id] = dst_fn

        return entries
