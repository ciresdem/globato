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
from rasterio.warp import calculate_default_transform, reproject, Resampling

from fetchez.utils import str2bool
from fetchez.core import run_fetchez
from fetchez.registry import ModuleRegistry
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

        barrier_lower = str(os.path.basename(self.barrier)).lower()
        target_crs = entry.get("src_srs", "EPSG:4326")  # The stream's current CRS

        barrier_path = self.barrier
        if barrier_lower in ["coastline", "landmask", "osm", "glob_coast"]:
            target_mod = (
                "osm_landmask" if barrier_lower in ["osm", "landmask"] else "glob_coast"
            )

            if not getattr(mod, "region", None):
                logger.error(f"[{self.name}] Region required to auto-generate barrier.")
                return False

            logger.info(
                f"[{self.name}] Auto-generating raster barrier using {target_mod} at {self.res}..."
            )

            generator_mod = ModuleRegistry.get_class(target_mod)
            gen_instance = generator_mod(
                src_region=mod.region,
                outdir=os.path.join(os.getcwd(), "auto_barriers"),
                res=self.res,
                include_water=True,
            )

            gen_instance.run()
            run_fetchez([gen_instance])

            # if gen_instance.results:
            #     for r in gen_instance.results:
            #         artifacts = r.get("artifacts", {})

            barrier_path = None
            for r in gen_instance.results:
                if (
                    r.get("data_type") == "coastline_mask"
                    or r.get("data_type") == "osm_landmask"
                ):
                    barrier_path = r.get("src_fn")
                    break

            if not barrier_path or not os.path.exists(barrier_path):
                logger.error(f"[{self.name}] Failed to generate raster barrier.")
                return False

        with rasterio.open(barrier_path) as src:
            if src.crs and src.crs.to_string() != target_crs:
                logger.debug(
                    f"[{self.name}] Warping mask to {target_crs} for stream sampling..."
                )

                transform, width, height = calculate_default_transform(
                    src.crs, target_crs, src.width, src.height, *src.bounds
                )

                # Create an empty numpy array to hold the warped mask
                self.mask_array = np.zeros((height, width), dtype=np.uint8)

                reproject(
                    source=rasterio.band(src, 1),
                    destination=self.mask_array,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=target_crs,
                    resampling=Resampling.nearest,  # Crucial: Keep the 0/1 boolean nature!
                )

                self.mask_transform = transform
                self.mask_width = width
                self.mask_height = height
            else:
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
