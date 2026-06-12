#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.rasters.rio_warp
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Warp/reproject a raster dataset

:copyright: (c) 2016 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import shutil
import logging
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from fetchez.hooks import FetchHook

logger = logging.getLogger(__name__)

class RioWarpHook(FetchHook):
    """Reprojects physical raster files to a target CRS before streaming.
    Safely ignores non-raster data types.
    """

    name = "raster_warp"
    meta_stage = "file"
    meta_category = "raster-file"

    def __init__(self, dst_crs, resampling="bilinear", **kwargs):
        super().__init__(**kwargs)
        self.dst_crs = dst_crs
        self.resampling = getattr(Resampling, resampling.lower(), Resampling.bilinear)

    def run(self, entries):
        for mod, entry in entries:
            src_fn = entry.get("dst_fn")

            # Only attempt to warp if it's a known raster file
            if not src_fn or not src_fn.lower().endswith(('.tif', '.tiff', '.nc', '.vrt')):
                continue

            try:
                with rasterio.open(src_fn) as src:
                    if src.crs and src.crs.to_string() == self.dst_crs:
                        continue

                    logger.info(f"[{self.name}] Warping {os.path.basename(src_fn)} to {self.dst_crs}...")

                    transform, width, height = calculate_default_transform(
                        src.crs, self.dst_crs, src.width, src.height, *src.bounds
                    )

                    kwargs = src.profile.copy()
                    kwargs.update({
                        'crs': self.dst_crs,
                        'transform': transform,
                        'width': width,
                        'height': height
                    })

                    temp_fn = src_fn + ".warp.tif"
                    with rasterio.open(temp_fn, 'w', **kwargs) as dst:
                        for i in range(1, src.count + 1):
                            reproject(
                                source=rasterio.band(src, i),
                                destination=rasterio.band(dst, i),
                                src_transform=src.transform,
                                src_crs=src.crs,
                                dst_transform=transform,
                                dst_crs=self.dst_crs,
                                resampling=self.resampling
                            )

                shutil.move(temp_fn, src_fn)

            except Exception as e:
                logger.error(f"[{self.name}] Failed to warp {src_fn}: {e}")

        return entries
