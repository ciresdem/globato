#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.filters.point_raster_mask
~~~~~~~~~~~~~

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import logging
import numpy as np
import rasterio

from fetchez.utils import str2bool
from .base import GlobatoFilter

logger = logging.getLogger(__name__)


class PointRasterMask(GlobatoFilter):
    """Filters or flags a point stream using a raster mask (e.g., Coastline).
    Dramatically faster than VectorCrop for complex shorelines and dense point clouds.
    """

    name = "point_raster_mask"
    desc = "Filter point streams using a boolean raster mask."
    meta_aliases = ["raster_mask", "point_mask", "coastline_crop"]

    def __init__(self, barrier=None, soft=False, invert=False, res="1s", **kwargs):
        super().__init__(**kwargs)
        self.barrier = barrier
        self.soft = str2bool(soft)
        self.invert = str2bool(invert)
        self.res = res

        # In-memory arrays
        self.mask_array = None
        self.mask_transform = None
        self.mask_width = None
        self.mask_height = None

    def setup(self, mod, entry):
        if not self.barrier:
            logger.warning(f"[{self.name}] No barrier provided. Skipping.")
            return False

        target_crs = entry.get("src_srs", "EPSG:4326")
        from globato.utils import resolve_barrier

        barrier_path = resolve_barrier(
            self.barrier,
            region=getattr(mod, "region", None),
            outdir=os.path.join(os.getcwd(), "auto_barriers"),
            res=self.res,
            include_rivers=True,
            output_type="raster",
            target_crs=target_crs,
        )

        if not barrier_path:
            logger.error(f"[{self.name}] Failed to resolve raster barrier.")
            return False

        with rasterio.open(barrier_path) as src:
            self.mask_array = src.read(1)
            self.mask_transform = src.transform
            self.mask_width = src.width
            self.mask_height = src.height

        return True

    def filter_chunk(self, chunk):
        """Map XYZ arrays to Image indices for sampling."""

        if len(chunk) == 0 or self.mask_array is None:
            return chunk if not self.soft else np.zeros(0, dtype=bool)

        inv_transform = ~self.mask_transform
        cols, rows = inv_transform * (chunk["x"], chunk["y"])
        cols = np.floor(cols).astype(int)
        rows = np.floor(rows).astype(int)

        inside_mask = np.zeros(len(chunk), dtype=bool)
        valid = (
            (cols >= 0)
            & (cols < self.mask_width)
            & (rows >= 0)
            & (rows < self.mask_height)
        )
        if np.any(valid):
            sampled_vals = self.mask_array[rows[valid], cols[valid]]
            inside_mask[valid] = sampled_vals == 1

        if self.invert:
            inside_mask = ~inside_mask

        if self.soft:
            return ~inside_mask
        else:
            return chunk[inside_mask]
