#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.filters.rq
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Reference Quality (RQ) Filter.
Fetches a reference raster (e.g. GEBCO) and filters points that deviate from it.

:copyright: (c) 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import logging
import numpy as np
import rasterio

from fetchez import utils
from fetchez.core import run_fetchez
from fetchez.hooks import FetchHook
from fetchez.registry import ModuleRegistry

from .base import GlobatoFilter

try:
    from osgeo import gdal
    HAS_GDAL = True
except ImportError:
    HAS_GDAL = False

try:
    from transformez.grid_engine import GridEngine, GridWriter
    HAS_GRID_ENGINE = True
except ImportError:
    HAS_GRID_ENGINE = False

from transformez.srs import SRSParser

logger = logging.getLogger(__name__)


class ReferenceQuality(GlobatoFilter):
    """Filters points by comparing Z values to a Reference Raster (RQ).

    Builder Modes:
      - 'vrt': Uses GDAL to build a Virtual Raster.
      - 'grid': Uses GridEngine (transformez) to mosaic/interpolate/fill a solid GeoTIFF.

    Modes:
      - 'diff': Absolute difference
      - 'percent': Relative difference (default)
      - 'iho_1' / 'iho': IHO S-44 Order 1 TVU (a=0.5, b=0.013)
      - 'iho_2': IHO S-44 Order 2 TVU (a=1.0, b=0.023)

    Args:
        reference (str): Fetchez Module Name (default: 'gebco_cog').
        threshold (float): Max allowed difference.
        mode (str): 'diff' (absolute) or 'percent' (relative).
        builder (str): 'vrt' or 'grid'.
        res (float): Resolution for 'grid' builder (default: 0.004 ~400m).
    """

    name = "rq"
    meta_desc = "Filter points by comparing z values to a reference raster."

    def __init__(self, reference="gmrt", threshold=50, mode="percent",
                 builder="grid", res=0.0008333333333333334, target_srs=None,
                 iho_order="1", **kwargs):
        super().__init__(**kwargs)
        self.ref_source = reference
        self.threshold = float(threshold)
        self.mode = mode.lower()
        self.builder = builder.lower()
        self.res = float(res)
        self.ref_fn = None

        self.target_srs = target_srs
        self._transformer = None

        self.total_points = 0
        self.dropped_points = 0

        # IHO S-44 Parameters (a, b)
        self.iho_order = str(iho_order).lower()
        if self.iho_order == "special":
            self.iho_a, self.iho_b = 0.25, 0.0075
        elif self.iho_order == "2":
            self.iho_a, self.iho_b = 1.0, 0.023
        else: # Default to Order 1
            self.iho_a, self.iho_b = 0.5, 0.013

    def setup(self, mod, entry):
        """Called once before stream processing starts."""

        if not getattr(mod, 'region', None):
            return False

        region = getattr(mod, 'region')
        if not self.ref_fn:
            files = self._fetch_reference_files(mod.region)
            if not files:
                logger.warning("[RQ] No reference data found. Skipping.")
                #return entries
                return False

            if self.builder == 'grid' and HAS_GRID_ENGINE:
                self.ref_fn = self._build_grid(files, region)
            else:
                self.ref_fn = self._build_vrt(files, region)

        self.src = rasterio.open(self.ref_fn)
        if self.target_srs:
            if self._transformer is None:
                self._transformer = srs.SpatialReference(
                    src_srs=self.target_srs,
                    dst_srs=self.src.crs
                )

        return True

    def _fetch_reference_files(self, region):
        """Downloads reference data and returns list of paths."""

        if os.path.exists(self.ref_source) and os.path.isfile(self.ref_source):
            return [self.ref_source]

        ModuleRegistry.load_all()
        logger.info(f"[RQ] Fetching reference data: {self.ref_source}...")
        mod_cls = ModuleRegistry.get_class(self.ref_source)

        if not mod_cls:
            return None

        buffered_region = region.copy().buffer(pct=5)
        fetcher = mod_cls(src_region=buffered_region)
        fetcher.run()
        run_fetchez([fetcher])

        files = []
        for entry in fetcher.results:
            if fetcher.fetch_entry(entry, check_size=True, verbose=False) == 0:
                files.append(entry['dst_fn'])
        return files

    def _build_vrt(self, files, region):
        """Builds a VRT using GDAL."""

        if not HAS_GDAL:
            logger.error("[RQ] GDAL required for 'vrt' builder.")
            return files[0] if files else None

        vrt_path = os.path.join(os.path.dirname(files[0]), f"rq_ref_{self.name}.vrt")
        try:
            vrt_options = gdal.BuildVRTOptions(resampleAlg='bilinear')
            gdal.BuildVRT(vrt_path, files, options=vrt_options)
            return vrt_path
        except Exception as e:
            logger.warning(f"[RQ] VRT Build failed: {e}. Using first file.")
            return files[0]

    def _build_grid(self, files, region):
        """Builds a mosaicked GeoTIFF using GridEngine."""

        if not HAS_GRID_ENGINE:
            logger.error("[RQ] transformez.grid_engine required for 'grid' builder.")
            return None

        out_path = os.path.join(os.path.dirname(files[0]), f"rq_ref_{self.name}.tif")

        target_region = region.copy().buffer(pct=5)
        nx = int(np.ceil((target_region[1] - target_region[0]) / self.res))
        ny = int(np.ceil((target_region[3] - target_region[2]) / self.res))

        logger.info(
            f"[RQ] Gridding reference surface ({nx}x{ny}) from {len(files)} files..."
        )

        grid_data = GridEngine.load_and_interpolate(files, target_region, nx, ny)
        #grid_data = GridEngine.fill_nans(grid_data, decay_pixels=50)

        GridWriter.write(out_path, grid_data, target_region)
        return out_path

    def filter_chunk(self, chunk):
        nodata = self.src.nodata if self.src.nodata is not None else -9999

        rx, ry, rz = chunk['x'], chunk['y'], chunk['z']

        if self.target_srs:
            rx, ry, rz = self._transformer.transform(rx, ry, rz)

        coords = list(zip(rx, ry))
        ref_vals = np.fromiter((val[0] for val in self.src.sample(coords)), dtype=np.float32)
        valid_ref = (ref_vals != nodata) & (~np.isnan(ref_vals))

        diff = np.abs(rz - ref_vals)
        is_outlier = np.zeros(len(chunk), dtype=bool)

        if self.mode == 'iho':
            # IHO S-44 Formula: TVU = sqrt(a^2 + (b * depth)^2)
            allowable_error = np.sqrt(self.iho_a**2 + (self.iho_b * ref_vals)**2)
            # allowable_error *= (self.threshold / 100.0) if self.threshold != 50 else 1.0

            is_outlier = (diff > allowable_error) & valid_ref

        elif self.mode == 'percent':
            with np.errstate(divide='ignore', invalid='ignore'):
                pct_diff = (diff / np.abs(ref_vals)) * 100
                is_outlier = (pct_diff > self.threshold) & valid_ref
        else:
            is_outlier = (diff > self.threshold) & valid_ref

        chunk_drops = np.sum(is_outlier)
        self.dropped_points += chunk_drops
        self.total_points += len(chunk)

        if self.total_points > 0 and self.total_points % 1000000 < len(chunk):
            logger.info(f"[RQ] Heartbeat: Filtered {self.dropped_points:,} outliers out of {self.total_points:,} points evaluated...")

        return is_outlier

    def teardown(self):
        if self.total_points > 0:
            pct_dropped = (self.dropped_points / self.total_points) * 100
            logger.info(
                f"[RQ] Complete: Removed {self.dropped_points:,} outliers ({pct_dropped:.2f}%) from {self.total_points:,} total points."
            )

        if hasattr(self, 'src'):
            self.src.close()

        if self.ref_fn and os.path.exists(self.ref_fn):
            if self.ref_fn.endswith('.vrt'):
                try:
                    os.remove(self.ref_fn)
                except Exception:
                    pass

        if hasattr(super(), 'teardown'):
            super().teardown()
