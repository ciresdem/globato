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
import threading

import fetchez

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

T_LOCK = threading.Lock()


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
        res (float): Resolution for 'grid' builder (default: 0.000833333 ~3 arc-seconds).
    """

    name = "rq"
    meta_stage = "stream"
    meta_desc = "Filter points by comparing z values to a reference raster."

    def __init__(
        self,
        reference="gmrt",
        threshold=50,
        mode="percent",
        builder="grid",
        res=0.0008333333333333334,
        target_srs=None,
        iho_order="1",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.ref_source = reference
        self.ref_sources = [s.strip().lower() for s in str(reference).split("/")]
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
        else:  # Default to Order 1
            self.iho_a, self.iho_b = 0.5, 0.013

    def setup(self, mod, entry):
        """Called once before stream processing starts."""

        if not getattr(mod, "region", None):
            return False

        region = getattr(mod, "region")
        outdir = getattr(mod, "_outdir")
        if not self.ref_fn:
            files = self._fetch_reference_files(region, outdir)

            if not files:
                logger.error(
                    f"[RQ] No valid reference data found for {region}. Disabling RQ filter to prevent crash!"
                )
                return False

            if self.builder == "grid" and HAS_GRID_ENGINE:
                self.ref_fn = self._build_grid(files, region)
            else:
                self.ref_fn = self._build_vrt(files, region)

            if not self.ref_fn or not os.path.exists(self.ref_fn):
                logger.error(
                    "[RQ] Builder failed to generate a reference surface. Disabling RQ filter."
                )
                return False

        try:
            self.src = rasterio.open(self.ref_fn)
            self.ref_data = self.src.read(1)
        except Exception as e:
            logger.error(
                f"[RQ] Failed to open generated reference surface: {e}. Disabling RQ filter."
            )
            return False

        if self.target_srs:
            if self._transformer is None:
                self._transformer = SRSParser(self.target_srs, self.src_crs).tc

        return True

    def _fetch_reference_files(self, region, outdir):
        """Downloads multiple reference datasets and stacks them by resolution."""

        valid_files = []

        for source in self.ref_sources:
            if os.path.exists(source) and os.path.isfile(source):
                valid_files.append(source)
                continue

            logger.debug(f"[RQ] Fetching reference tier: {source}...")
            try:
                files = fetchez.get(
                    source, region=region.copy().buffer(pct=5).to_list(), outdir=outdir
                )
                if files:
                    for f in files:
                        if os.path.exists(f) and os.path.getsize(f) > 2000:
                            valid_files.append(f)
            except Exception as e:
                logger.warning(f"[RQ] Fetch failed for {source}: {e}")

        if not valid_files and "gebco" not in self.ref_sources:
            logger.warning("[RQ] Primary references failed. Falling back to GEBCO...")
            try:
                fallback = fetchez.get(
                    "gebco", region=region.copy().buffer(pct=5).to_list()
                )
                if fallback:
                    valid_files.extend(
                        [
                            f
                            for f in fallback
                            if os.path.exists(f) and os.path.getsize(f) > 2000
                        ]
                    )
            except Exception as e:
                logger.error(f"[RQ] Fallback to GEBCO failed: {e}")

        if not valid_files:
            return []

        file_resolutions = []
        for f in valid_files:
            try:
                with rasterio.open(f) as src:
                    res = src.res[0]
                    file_resolutions.append((res, f))
            except Exception:
                pass

        file_resolutions.sort(key=lambda x: x[0], reverse=True)
        sorted_files = [f[1] for f in file_resolutions]
        logger.debug(
            f"[RQ] Stacked {len(sorted_files)} reference files for VRT/Grid engine."
        )
        return sorted_files

    def _build_vrt(self, files, region):
        """Builds a VRT using GDAL."""

        if not HAS_GDAL:
            logger.error("[RQ] GDAL required for 'vrt' builder.")
            return files[0] if files else None

        vrt_path = os.path.join(os.path.dirname(files[0]), f"rq_ref_{self.name}.vrt")
        try:
            vrt_options = gdal.BuildVRTOptions(resampleAlg="bilinear")
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

        logger.debug(
            f"[RQ] Gridding reference surface ({nx}x{ny}) from {len(files)} files..."
        )

        grid_data = GridEngine.load_and_interpolate(files, target_region, nx, ny)
        # grid_data = GridEngine.fill_nans(grid_data, decay_pixels=50)

        GridWriter.write(out_path, grid_data, target_region)
        return out_path

    def filter_chunk(self, chunk):
        nodata = self.src.nodata if self.src.nodata is not None else -9999

        rx, ry, rz = chunk["x"], chunk["y"], chunk["z"]

        if self.target_srs:
            rx, ry, rz = self._transformer.transform(rx, ry, rz)

        rows, cols = rasterio.transform.rowcol(self.src.transform, rx, ry)
        rows = np.clip(rows, 0, self.src.height - 1)
        cols = np.clip(cols, 0, self.src.width - 1)

        ref_vals = self.ref_data[rows, cols]
        valid_ref = (ref_vals != nodata) & (~np.isnan(ref_vals))

        diff = np.abs(rz - ref_vals)
        is_outlier = np.zeros(len(chunk), dtype=bool)

        if self.mode == "iho":
            # IHO S-44 Formula: TVU = sqrt(a^2 + (b * depth)^2)
            allowable_error = np.sqrt(self.iho_a**2 + (self.iho_b * ref_vals) ** 2)
            # allowable_error *= (self.threshold / 100.0) if self.threshold != 50 else 1.0

            is_outlier = (diff > allowable_error) & valid_ref

        elif self.mode == "percent":
            with np.errstate(divide="ignore", invalid="ignore"):
                pct_diff = (diff / np.abs(ref_vals)) * 100
                is_outlier = (pct_diff > self.threshold) & valid_ref
        else:
            is_outlier = (diff > self.threshold) & valid_ref

        chunk_drops = np.sum(is_outlier)
        self.dropped_points += chunk_drops
        self.total_points += len(chunk)

        if self.total_points > 0 and self.total_points % 1000000 < len(chunk):
            logger.debug(
                f"[RQ] Heartbeat: Filtered {self.dropped_points:,} outliers out of {self.total_points:,} points evaluated..."
            )

        return is_outlier

    def teardown(self):
        if self.total_points > 0:
            pct_dropped = (self.dropped_points / self.total_points) * 100
            logger.debug(
                f"[RQ] Complete: Removed {self.dropped_points:,} outliers ({pct_dropped:.2f}%) from {self.total_points:,} total points."
            )

        if hasattr(self, "src"):
            self.src.close()

        if self.ref_fn and os.path.exists(self.ref_fn):
            if self.ref_fn.endswith(".vrt"):
                try:
                    os.remove(self.ref_fn)
                except Exception:
                    pass

        if hasattr(super(), "teardown"):
            super().teardown()
