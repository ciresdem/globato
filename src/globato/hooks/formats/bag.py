#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.formats.bag
~~~~~~~~~~~~~~~~~~~

Dedicated BAG (Bathymetric Attributed Grid) Reader.
Handles VR-BAGs, standard BAGs, uncertainty bands, and corrupt XML metadata.

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import logging
import numpy as np

import rasterio
from rasterio.windows import Window, from_bounds
from rasterio.warp import transform_bounds
from rasterio.errors import WindowError

from fetchez.utils import float_or

from .rio import RasterioReader

logger = logging.getLogger(__name__)


class BAGReader(RasterioReader):
    """Specialized Reader for BAG files.

    - Automatically handles Variable Resolution (VR) via GDAL Open Options.
    - Reads Band 2 as Uncertainty ('u').
    - Calculates weight based on resolution.
    """

    def __init__(self, src_fn, mode="resampled", min_weight=0, **kwargs):
        super().__init__(src_fn, **kwargs)
        self.mode = mode
        self.min_weight = float_or(min_weight, 0)

    def _calculate_bag_weight(self, transform):
        """Weight = (3 * (10 if res <=3 else 1)) / res"""

        x_res = transform.a
        if x_res == 0:
            return 1.0

        base_mult = 10 if x_res <= 3.0 else 1
        calc_weight = (3 * base_mult) / x_res

        return max(calc_weight, self.min_weight)

    def _process_bag_dataset(self, src):
        """Internal generator that reads chunks from an open rasterio dataset."""

        bag_weight = self._calculate_bag_weight(src.transform)
        has_unc = src.count >= 2

        if self.region:
            w, e, s, n = self.region
            if src.crs and src.crs != "EPSG:4326":
                try:
                    w, s, e, n = transform_bounds("EPSG:4326", src.crs, w, s, e, n)
                except Exception as e:
                    logger.warning(f"Failed to transform bounds for BAG {self.src_fn}: {e}")

            req_window = from_bounds(w, s, e, n, transform=src.transform)

            try:
                master_window = req_window.intersection(Window(0, 0, src.width, src.height))
            except WindowError:
                logger.debug(f"BAG {self.src_fn} is entirely outside the requested region.")
                return
        else:
            master_window = Window(0, 0, src.width, src.height)

        block_h, block_w = src.block_shapes[0]
        y_start = int(master_window.row_off)
        y_end = int(master_window.row_off + master_window.height)
        x_start = int(master_window.col_off)
        x_end = int(master_window.col_off + master_window.width)

        for y in range(y_start, y_end, block_h):
            h_chunk = self.chunk_size or block_h
            rows = min(h_chunk, y_end - y)

            for x in range(x_start, x_end, block_w):
                w_chunk = self.chunk_size or block_w
                cols = min(w_chunk, x_end - x)
                window = Window(x, y, cols, rows)
                z = src.read(1, window=window)

                mask = ~np.isnan(z)
                if src.nodata is not None:
                    mask &= (z != src.nodata)

                if not np.any(mask):
                    continue

                if has_unc:
                    u = src.read(2, window=window)
                else:
                    u = np.zeros_like(z)

                z_valid = z[mask]
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
                chunk["w"] = np.full(count, bag_weight, dtype="float32")

                yield chunk

    def yield_chunks(self):
        env_opts = {
            "GDAL_IGNORE_BAG_XML_METADATA": "YES",
            "OGR_BAG_MIN_VERSION": "1.0",
            "CPL_MIN_LOG_LEVEL": rasterio.logging.ERROR,
        }

        is_vr = False

        #try:
        with rasterio.Env(**env_opts):
            with rasterio.open(self.src_fn) as src:
                tags = src.tags(ns="IMAGE_STRUCTURE")
                if tags.get("HAS_SUPERGRIDS") == "TRUE":
                    is_vr = True

                if not is_vr:
                    yield from self._process_bag_dataset(src)
                    return

        # except Exception as e:
        #     logger.error(f"Failed to probe BAG {self.src_fn}: {e}")
        #     return

        if is_vr:
            logger.debug(
                f"Detected VR-BAG, re-opening in resampled mode: {self.src_fn}"
            )
            vr_opts = {"MODE": "RESAMPLED_GRID", "RES_STRATEGY": "MIN"}

            try:
                with rasterio.Env(**env_opts):
                    with rasterio.open(self.src_fn, **vr_opts) as src:
                        yield from self._process_bag_dataset(src)
            except Exception as e:
                logger.error(f"Failed to read VR-BAG {self.src_fn}: {e}")
