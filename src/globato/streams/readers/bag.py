#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.streams.readers.bag
~~~~~~~~~~~~~~~~~~~

Dedicated BAG (Bathymetric Attributed Grid) Reader.
Handles VR-BAGs, standard BAGs, uncertainty bands, and corrupt XML metadata.

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import logging

import rasterio

from fetchez.utils import float_or

from .rio import RasterioReader

logger = logging.getLogger(__name__)


class BAGReader(RasterioReader):
    """Specialized Reader for BAG files.

    - Automatically handles Variable Resolution (VR) via GDAL Open Options.
    - Reads Band 2 as Uncertainty ('u').
    - Calculates weight based on resolution.
    - MODE=[LOW_RES_GRID​/​LIST_SUPERGRIDS​/​RESAMPLED_GRID​/​INTERPOLATED​/​AUTO]: Defaults to AUTO.
    """

    name = "bag-point-reader"
    meta_category = "point-stream"
    meta_dtype = "bag-raster"
    meta_desc = "Read BAG data through rasterio into a point stream"
    meta_extensions = ["bag"]

    def __init__(self, path, mode="RESAMPLED_GRID", min_weight=0, **kwargs):
        super().__init__(path, **kwargs)
        self.modes = [
            "LOW_RES_GRID​",
            "​LIST_SUPERGRIDS​",
            "​RESAMPLED_GRID​",
            "​INTERPOLATED​",
            "​AUTO",
        ]
        self.mode = mode if mode.upper() in self.modes else "AUTO"
        self.min_weight = float_or(min_weight, 0)

    def _calculate_bag_weight(self, transform):
        """Weight = (3 * (10 if res <=3 else 1)) / res"""

        x_res = transform.a
        if x_res == 0:
            return 1.0

        base_mult = 10 if x_res <= 3.0 else 1
        calc_weight = (3 * base_mult) / x_res

        return max(calc_weight, self.min_weight)

    def _yield_raw_chunks(self):
        env_opts = {
            "GDAL_IGNORE_BAG_XML_METADATA": "YES",
            "OGR_BAG_MIN_VERSION": "1.0",
            "CPL_MIN_LOG_LEVEL": rasterio.logging.ERROR,
        }

        is_vr = False

        try:
            with rasterio.Env(**env_opts):
                with rasterio.open(self.src_fn) as src:
                    tags = src.tags(ns="IMAGE_STRUCTURE")
                    if tags.get("HAS_SUPERGRIDS") == "TRUE":
                        is_vr = True

                    self.weight = self._calculate_bag_weight(src.transform)

            if not is_vr:
                self.u_band = 2
                yield from self._process_rio_dataset()
                return

            elif is_vr:
                logger.debug(
                    f"Detected VR-BAG, re-opening in resampled mode: {self.src_fn}"
                )
                vr_opts = {"MODE": self.mode, "RES_STRATEGY": "MIN"}

                try:
                    with rasterio.Env(**env_opts):
                        self.u_band = 2
                        with rasterio.open(self.src_fn, **vr_opts) as src:
                            self.weight = self._calculate_bag_weight(src.transform)
                        yield from self._process_rio_dataset()
                except Exception as e:
                    logger.error(f"Failed to read VR-BAG {self.src_fn}: {e}")
        except Exception as e:
            logger.error(f"Failed to probe BAG {self.src_fn}: {e}")
            return
