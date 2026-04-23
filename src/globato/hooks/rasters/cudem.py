#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.rasters.cudem
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Orchestrates multi-resolution step-down gridding in the raster domain.
Based on cudem.waffles.cudem

:copyright: (c) 2016 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import shutil
import logging
import numpy as np
import rasterio
import scipy.ndimage
from fetchez.utils import remove_glob2, str2inc
from rasterio.warp import calculate_default_transform, reproject, Resampling

from .fill import RasterFill
from .base import RasterGlobalHook

logger = logging.getLogger(__name__)


class CudemStepDown(RasterGlobalHook):
    """The CUDEM interpolation strategy.
    Decimates the master stack, interpolates, and uses it to fill finer resolutions.
    """

    name = "ms_cudem"
    default_suffix = "_cudem"
    meta_category = "multi-stack"

    def __init__(
        self,
        steps=None,
        weights=[1.0, 0.5],
        resolutions=["1s", "3s"],
        algo="raster_fill",
        barrier=None,
        blend_dist=5,
        **kwargs,
    ):
        super().__init__(barrier=barrier, strip_bands=True, **kwargs)
        self.algo = algo

        # --- Helper to parse Lists OR Legacy Strings ---
        def _parse_arg(val, cast_type):
            if isinstance(val, list):
                return [cast_type(v) for v in val]
            if isinstance(val, str) and "/" in val:
                return [cast_type(v) for v in val.split("/")]
            return [cast_type(val)]

        self.resolutions = _parse_arg(resolutions, str)
        self.weights = _parse_arg(weights, float)
        self.blend_dists = _parse_arg(blend_dist, int)

        self.steps = (
            steps
            if steps is not None
            else max(len(self.resolutions), len(self.weights), len(self.blend_dists))
            - 1
        )

        def _pad_list(lst, target_len):
            while len(lst) < target_len:
                lst.append(lst[-1])
            return lst

        def _crop_list(lst, target_len):
            while len(lst) > target_len:
                lst = lst[:-1]
            return lst

        self.resolutions = _crop_list(
            _pad_list(self.resolutions, self.steps + 1), self.steps + 1
        )
        self.weights = _crop_list(
            _pad_list(self.weights, self.steps + 1), self.steps + 1
        )
        self.blend_dists = _crop_list(
            _pad_list(self.blend_dists, self.steps + 1), self.steps + 1
        )

    def _validate_deps(self):
        if self.algo == "interp_gmt":
            try:
                import pygmt
            except ImportError:
                return False, "PyGMT is required when using algo='interp_gmt'."

        elif self.algo == "interp_verde":
            try:
                import verde
            except ImportError:
                return False, "Verde is required when using algo='interp_verde'."

        elif self.algo == "interp_scipy":
            try:
                import scipy
            except ImportError:
                return False, "SciPy is required when using algo='interp_scipy'."

        return True, ""

    def _decimate_raster(self, src_path, dst_path, target_res):
        """Downsamples the main stack using average pooling."""

        target_res = float(target_res)
        with rasterio.open(src_path) as src:
            if abs(src.res[0] - target_res) < 1e-9:
                shutil.copy(src_path, dst_path)
                return

            transform, width, height = calculate_default_transform(
                src.crs,
                src.crs,
                src.width,
                src.height,
                *src.bounds,
                resolution=(target_res, target_res),
            )
            kwargs = src.meta.copy()
            kwargs.update({"transform": transform, "width": width, "height": height})

            with rasterio.open(dst_path, "w", **kwargs) as dst:
                for i in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, i),
                        destination=rasterio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=src.crs,
                        resampling=Resampling.average,
                    )

    def _blend_background(
        self, foreground_path, background_path, current_weight, current_blend_dist
    ):
        """Fills gaps in the foreground stack using the interpolated background."""

        temp_path = foreground_path + ".blend.tif"
        with (
            rasterio.open(foreground_path) as fg_src,
            rasterio.open(background_path) as bg_src,
        ):
            profile = fg_src.profile.copy()
            fg_ndv = fg_src.nodata
            if fg_ndv is None:
                fg_ndv = -9999

            with rasterio.open(temp_path, "w", **profile) as dst:
                #  Align background to foreground grid
                bg_aligned = np.full(
                    (fg_src.height, fg_src.width), fg_ndv, dtype=fg_src.dtypes[0]
                )

                reproject(
                    source=rasterio.band(bg_src, 1),
                    destination=bg_aligned,
                    src_transform=bg_src.transform,
                    src_crs=bg_src.crs,
                    dst_transform=fg_src.transform,
                    dst_crs=fg_src.crs,
                    src_nodata=bg_src.nodata,
                    dst_nodata=fg_ndv,
                    resampling=Resampling.cubic,
                )

                for window, _ in self.yield_buffered_windows(fg_src, buffer_size=0):
                    # Read original Foreground bands
                    fg_z = fg_src.read(1, window=window)
                    fg_count = fg_src.read(2, window=window)
                    fg_weight = fg_src.read(3, window=window)

                    # Scrub data that doesn't meet the weight threshold.
                    # This turns low-quality bathy into NoData so the background can overwrite it.
                    invalid_weight_mask = (fg_count == 0) | (fg_weight < current_weight)
                    fg_z[invalid_weight_mask] = fg_ndv

                    fg_valid_mask = (fg_z != fg_ndv) & (~np.isnan(fg_z))

                    if current_blend_dist > 0:
                        # Create a circular/square structural element for the buffer
                        moat_mask = scipy.ndimage.binary_dilation(
                            fg_valid_mask, iterations=current_blend_dist
                        )
                    else:
                        moat_mask = fg_valid_mask

                    # Get Background chunk
                    bg_chunk = bg_aligned[
                        window.row_off : window.row_off + window.height,
                        window.col_off : window.col_off + window.width,
                    ]

                    bg_chunk[moat_mask] = fg_ndv
                    # gaps + scrubbed low-weight pixels are both marked as invalid
                    fg_invalid = (fg_z == fg_ndv) | np.isnan(fg_z)
                    bg_valid = (bg_chunk != fg_ndv) & ~np.isnan(bg_chunk)

                    fill_mask = fg_invalid & bg_valid

                    if np.any(fill_mask):
                        fg_z[fill_mask] = bg_chunk[fill_mask]
                        fg_count[fill_mask] = 1
                        fg_weight[fill_mask] = current_weight

                    dst.write(fg_z, 1, window=window)
                    dst.write(fg_count, 2, window=window)
                    dst.write(fg_weight, 3, window=window)

                    for b in range(4, fg_src.count + 1):
                        data = fg_src.read(b, window=window)
                        dst.write(data, b, window=window)

        shutil.move(temp_path, foreground_path)

    def process_raster(self, src_path, dst_path, entry):
        """src_path is the high-resolution master stack."""

        previous_surface = None

        for i in range(self.steps, -1, -1):
            res = str2inc(self.resolutions[i])
            weight = self.weights[i]

            logger.info(f"--- CUDEM STEP {i} | Res: {res} | Min Weight: {weight} ---")

            step_stack = f"temp_stack_step{i}.tif"
            step_interp = f"temp_interp_step{i}.tif"

            self._decimate_raster(src_path, step_stack, target_res=res)
            current_blend_dist = self.blend_dists[i]
            if previous_surface and os.path.exists(previous_surface):
                self._blend_background(
                    step_stack,
                    previous_surface,
                    current_weight=weight,
                    current_blend_dist=current_blend_dist,
                )

            step_barrier = self.barrier if i > 0 else None
            interp = None

            if self.algo == "interp_gmt":
                from .gmt_surface import GmtSurface, HAS_PYGMT

                if HAS_PYGMT:
                    interp = GmtSurface(
                        tension=0.95,
                        barrier=step_barrier,
                        gmt_upper=-0.01 if i > 0 else None,
                        min_weight=weight,
                        verbose=True,
                    )
                else:
                    logger.warning(
                        "PyGMT is missing or failed to load. Falling back to Scipy interpolation."
                    )
                    self.algo = "raster_fill"

            elif self.algo == "interp_verde":
                from .verde_surface import VerdeSurface, HAS_VERDE

                if HAS_VERDE:
                    interp = VerdeSurface(
                        damping=1e-4,
                        barrier=step_barrier,
                        min_weight=weight,
                        upper=-0.01 if i > 0 else None,
                    )
                else:
                    logger.warning(
                        "Verde is missing. Falling back to Scipy interpolation."
                    )
                    self.algo = "raster_fill"

            elif self.algo == "interp_scipy" or interp is None:
                from .scipy_griddata import ScipyInterp

                interp = ScipyInterp(
                    method="cubic",
                    barrier=step_barrier,
                    min_weight=weight,
                    upper=-0.01 if i > 0 else None,
                )
            else:
                self.algo = "raster_fill"

            if self.algo == "raster_fill" or interp is None:
                interp = RasterFill(
                    max_dist=1000,
                    smoothing=3,
                    barrier=step_barrier,
                    min_weight=weight,
                    upper=-0.01 if i > 0 else None,
                )
            interp.current_mod = getattr(self, "current_mod", None)

            success = interp.process_raster(step_stack, step_interp, entry)

            if success:
                interp._clamp_raster(step_interp)
                previous_surface = step_interp

        if previous_surface and os.path.exists(previous_surface):
            shutil.move(previous_surface, dst_path)
            remove_glob2("temp_stack_step*.tif", "temp_interp_step*.tif", "*.blend.tif")
            return True

        return False
