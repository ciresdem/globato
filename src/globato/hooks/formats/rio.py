#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.formats.rio
~~~~~~~~~~~~~

Rasterio data parsing

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import logging
import numpy as np
import rasterio
from rasterio.windows import Window, from_bounds
from rasterio.warp import transform_bounds
from rasterio.errors import WindowError

from fetchez.hooks import FetchHook
from fetchez.utils import float_or

logger = logging.getLogger(__name__)
logging.getLogger("rasterio").setLevel(logging.ERROR)


class RasterioReader:
    """Streaming Raster Parser using Rasterio."""

    def __init__(self, src_fn, band_no=1, chunk_size=None, region=None, **kwargs):
        self.src_fn = src_fn
        self.band_no = band_no
        self.chunk_size = chunk_size
        self.region = region
        self.kwargs = kwargs

    def get_srs(self):
        """Get SRS as WKT."""

        try:
            with rasterio.Env(CPL_MIN_LOG_LEVEL=rasterio.logging.ERROR):
                with rasterio.open(self.src_fn) as src:
                    return src.crs.to_wkt() if src.crs else "EPSG:4326"
        except Exception:
            return "EPSG:4326"

    def yield_chunks(self):
        """Yield chunks using Rasterio Windows."""

        try:
            with rasterio.Env(CPL_MIN_LOG_LEVEL=rasterio.logging.ERROR):
                with rasterio.open(self.src_fn) as src:
                    ndv = float_or(src.nodata, -9999)

                    if self.region:
                        w, e, s, n = self.region
                        if src.crs and src.crs != "EPSG:4326":
                            try:
                                w, s, e, n = transform_bounds("EPSG:4326", src.crs, w, s, e, n)
                            except Exception as e:
                                logger.warning(f"Failed to transform bounds for {self.src_fn}: {e}")

                        req_window = from_bounds(w, s, e, n, transform=src.transform)

                        try:
                            master_window = req_window.intersection(Window(0, 0, src.width, src.height))
                        except WindowError:
                            logger.debug(f"Raster {self.src_fn} is entirely outside the requested region.")
                            return

                        if master_window.width <= 0 or master_window.height <= 0:
                            logger.debug(f"Raster {self.src_fn} is entirely outside the requested region.")
                            return
                    else:
                        master_window = Window(0, 0, src.width, src.height)

                    block_h, block_w = src.block_shapes[0]
                    h_chunk = self.chunk_size or block_h
                    w_chunk = self.chunk_size or block_w

                    y_start = int(master_window.row_off)
                    y_end = int(master_window.row_off + master_window.height)
                    x_start = int(master_window.col_off)
                    x_end = int(master_window.col_off + master_window.width)

                    for y in range(y_start, y_end, h_chunk):
                        rows = min(h_chunk, y_end - y)

                        for x in range(x_start, x_end, w_chunk):
                            cols = min(w_chunk, x_end - x)
                            window = Window(x, y, cols, rows)
                            z = src.read(self.band_no, window=window)
                            u = np.zeros_like(z)
                            w = np.zeros_like(z)

                            if not np.issubdtype(z.dtype, np.floating):
                                z = z.astype(np.float32)

                            mask = ~np.isnan(z)
                            if src.nodata is not None:
                                mask &= (z != src.nodata)

                            if not np.any(mask):
                                continue

                            z_valid = z[mask]
                            w_valid = w[mask]
                            u_valid = u[mask]

                            local_rows, local_cols = np.where(mask)

                            global_rows = local_rows + window.row_off
                            global_cols = local_cols + window.col_off

                            xs, ys = rasterio.transform.xy(
                                src.transform, global_rows, global_cols, offset="center"
                            )

                            count = len(z_valid)
                            chunk = np.zeros(
                                count,
                                dtype=[("x", "f8"), ("y", "f8"), ("z", "f8"), ("w", "f4"), ("u", "f4")],
                            )

                            chunk["x"] = xs
                            chunk["y"] = ys
                            chunk["z"] = z_valid
                            chunk["u"] = u_valid
                            chunk["w"] = w_valid

                            yield chunk

        except Exception as e:
            logger.error(f"Rasterio read failed: {e}")

class RasterioStream(FetchHook):
    name = "rasterio_stream"
    meta_stage = "file"
    meta_category = "format-stream"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.params = kwargs

    def run(self, entries):
        for mod, entry in entries:
            src = entry.get("dst_fn")
            if not src or not os.path.exists(src):
                continue

            region = getattr(mod, "region", None)

            try:
                reader = RasterioReader(src, region=region, **self.params)
                entry["stream"] = reader.yield_chunks()
                entry["stream_type"] = "xyz_recarray"
            except Exception as e:
                logger.warning(f"RasterioStream failed for {src}: {e}")
        return entries
