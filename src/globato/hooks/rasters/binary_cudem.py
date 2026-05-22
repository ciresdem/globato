#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.rasters.binary_cudem
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Binary CUDEM: Morphological Multi-Resolution Step-Down.
Uses weight-based bitmasks to route specific datasets to specific resolutions
and interpolator settings, bridging gaps in sparse data without
degrading the high-frequency fidelity of dense coastal data.
"""

import os
import shutil
import logging
import numpy as np
import rasterio
import scipy.ndimage
from scipy.interpolate import RBFInterpolator
from rasterio.warp import calculate_default_transform, reproject, Resampling

from fetchez.utils import remove_glob2, str2inc

from .base import RasterGlobalHook

logger = logging.getLogger(__name__)


class BinaryCudemStepDown(RasterGlobalHook):
    """
    Multi-Resolution Morphological step-down using Binary Coding.
    """

    name = "ms_binary_cudem"
    default_suffix = "_binary_cudem"
    meta_category = "multi-stack"

    def __init__(
        self,
        weights=[1.0, 0.5, 0],
        resolutions=["3s", "9s", "15s"],  # E.g., 1s=Dense, 3s=Med, 9s=Sparse
        algos=["raster_fill", "raster_fill", "interp_rbf"],
        sparse_smoothing=120.0,
        dense_smoothing=0.1,
        blend_dist=20,
        barrier=None,
        **kwargs,
    ):
        super().__init__(barrier=barrier, strip_bands=True, **kwargs)

        # Parse weights and resolutions
        self.weights = np.sort(
            [
                float(w)
                for w in (weights.split("/") if isinstance(weights, str) else weights)
            ]
        )
        self.resolutions = (
            resolutions.split("/") if isinstance(resolutions, str) else resolutions
        )

        raw_algos = algos.split("/") if isinstance(algos, str) else algos

        # If only one algo, duplicate it for all tiers
        if len(raw_algos) == 1:
            self.algos = [raw_algos[0]] * len(self.resolutions)
        elif len(raw_algos) == len(self.resolutions):
            self.algos = raw_algos
        else:
            logger.warning(
                f"[{self.name}] Algo count {len(raw_algos)} does not match resolution count {len(self.resolutions)}. Defaulting to {raw_algos[0]}."
            )
            self.algos = [raw_algos[0]] * len(self.resolutions)

        self.sparse_smoothing = float(sparse_smoothing)
        self.dense_smoothing = float(dense_smoothing)
        self.blend_dist = int(blend_dist)

    def _get_interp_hook(self, algo_name, smoothing):
        """Dynamically loads and instantiates the requested interpolation hook."""

        if algo_name == "interp_rbf":
            from .rbf_interp import RBFInterp

            return RBFInterp(smoothing=.1, neighbors=20, epsilon=None, degree=1)
            # return RBFInterp(smoothing=smoothing, neighbors=1000, epsilon=6.0)

        elif algo_name == "interp_gmt":
            from .gmt_surface import GmtSurface

            # tension = np.clip(1.0 / (smoothing + 1.0), 0.0, 1.0)
            tension = 0.35
            return GmtSurface(tension=tension)

        elif algo_name == "interp_scipy":
            from .scipy_griddata import ScipyInterp

            return ScipyInterp(method="linear")

        elif algo_name == "raster_fill":
            from .fill import RasterFill

            return RasterFill()

        else:
            logger.warning(
                f"[{self.name}] Unknown algo '{algo_name}'. Falling back to RBF."
            )
            from .rbf_interp import RBFInterp

            return RBFInterp(smoothing=smoothing, neighbors=1000)

    def _decimate_raster(self, src_path, dst_path, target_res):
        """Custom decimation: Averages Z, etc., but uses MAX for the bitmask"""

        with rasterio.open(src_path) as src:
            transform, width, height = calculate_default_transform(
                src.crs,
                src.crs,
                src.width,
                src.height,
                *src.bounds,
                resolution=str2inc(target_res),
            )
            kwargs = src.profile.copy()
            kwargs.update(transform=transform, width=width, height=height)

            with rasterio.open(dst_path, "w", **kwargs) as dst:
                for i in range(1, src.count + 1):
                    resamp_algo = Resampling.max if i in [2,3] else Resampling.average
                    reproject(
                        source=rasterio.band(src, i),
                        destination=rasterio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=src.crs,
                        resampling=resamp_algo,
                        num_threads=1,
                    )

    def _process_tier(
        self,
        step_stack,
        previous_surface,
        current_weight,
        smoothing,
        is_coarsest,
        current_algo,
    ):
        with rasterio.open(step_stack, "r+") as src:
            data = src.read()
            ndv = src.nodata if src.nodata is not None else -9999

            z = data[0].astype("float64")
            w = data[2].astype("float64")

            if is_coarsest:
                current_weight = 0

            valid_mask = (z != ndv) & (~np.isnan(z))
            core_mask = w >= current_weight

            if is_coarsest:
                tier_zone = np.ones_like(core_mask, dtype=bool)
                missing_mask = ~valid_mask
            else:
                struct = scipy.ndimage.generate_binary_structure(2, 2)
                tier_zone = scipy.ndimage.binary_dilation(
                    core_mask, structure=struct, iterations=self.blend_dist
                )
                missing_mask = (~valid_mask) & tier_zone

            # Delegate Interpolation to the requested Hook
            y_miss, x_miss = np.where(missing_mask)
            if len(y_miss) > 0:
                logger.info(
                    f"[{self.name}] Filling {len(y_miss)} voids using '{current_algo}' for Weights above {current_weight}"
                )

                # Create isolated temporary files for the hook
                temp_in = step_stack.replace(".tif", f"_{current_algo}_in.tif")
                temp_out = step_stack.replace(".tif", f"_{current_algo}_out.tif")

                shutil.copy(step_stack, temp_in)

                # Scrub the temp file so the hook ONLY sees the relevant tiers
                with rasterio.open(temp_in, "r+") as tmp_src:
                    tmp_z = tmp_src.read(1)
                    tmp_z[~core_mask] = ndv
                    tmp_src.write(tmp_z, 1)

                # Dispatch to the chosen algorithm!
                interp_hook = self._get_interp_hook(current_algo, smoothing)
                #success = interp_hook.process_raster(temp_in, temp_out, entry={})
                success = interp_hook.process_raster(step_stack, temp_out, entry={})

                # Extract ONLY the targeted voids and bring them back into our main array
                if success and os.path.exists(temp_out):
                    with rasterio.open(temp_out) as filled_src:
                        filled_z = filled_src.read(1)
                        z[y_miss, x_miss] = filled_z[y_miss, x_miss]

                if os.path.exists(temp_in):
                    os.remove(temp_in)
                if os.path.exists(temp_out):
                    os.remove(temp_out)

            # Cross-fade with the Upsampled Background
            if previous_surface:
                bg_aligned = np.full(z.shape, ndv, dtype=z.dtype)
                with rasterio.open(previous_surface) as bg_src:
                    reproject(
                        source=rasterio.band(bg_src, 1),
                        destination=bg_aligned,
                        src_transform=bg_src.transform,
                        src_crs=bg_src.crs,
                        dst_transform=src.transform,
                        dst_crs=src.crs,
                        src_nodata=bg_src.nodata,
                        dst_nodata=ndv,
                        resampling=Resampling.cubic,
                        num_threads=1,
                    )

                dist = scipy.ndimage.distance_transform_cdt(
                    ~core_mask, metric="taxicab"
                )
                weights = np.clip(dist / float(self.blend_dist), 0.0, 1.0)

                bg_only_mask = dist >= self.blend_dist
                z[bg_only_mask] = bg_aligned[bg_only_mask]

                trans_mask = (dist > 0) & (dist < self.blend_dist)
                if np.any(trans_mask):
                    valid_bg = (bg_aligned != ndv) & (~np.isnan(bg_aligned))
                    blend_active = trans_mask & valid_bg
                    z[blend_active] = (
                        (1.0 - weights[blend_active]) * z[blend_active]
                    ) + (weights[blend_active] * bg_aligned[blend_active])

                barrier_mask = self._create_barrier_mask(z.shape, src.transform)
                if barrier_mask is not None:
                    water_mask = (~barrier_mask) & (z != ndv) & (~np.isnan(z))
                    z[water_mask] = np.minimum(z[water_mask], -0.01)

            src.write(z.astype(rasterio.float32), 1)

    def _process_tier_rbf(
        self, step_stack, previous_surface, current_weight, smoothing, is_coarsest
    ):
        """Extracts all data, interpolates local voids, and cross-fades over the background."""

        with rasterio.open(step_stack, "r+") as src:
            data = src.read()
            ndv = src.nodata if src.nodata is not None else -9999

            z = data[0].astype("float64")
            w = data[2].astype("float64")

            valid_mask = (z != ndv) & (~np.isnan(z))
            core_mask = w >= current_weight

            y_valid, x_valid = np.where(valid_mask)
            x_pts, y_pts = self._extract_subpixel_coords(
                data, y_valid, x_valid, src.transform, apply_jitter=False
            )
            points = np.column_stack((x_pts, y_pts))
            values = z[valid_mask]

            if is_coarsest:
                tier_zone = np.ones_like(core_mask, dtype=bool)
                missing_mask = ~valid_mask
            else:
                struct = scipy.ndimage.generate_binary_structure(2, 2)
                tier_zone = scipy.ndimage.binary_dilation(
                    core_mask, structure=struct, iterations=self.blend_dist
                )
                missing_mask = (~valid_mask) & tier_zone

            y_miss, x_miss = np.where(missing_mask)
            if len(y_miss) > 0:
                xq, yq = src.transform * (x_miss + 0.5, y_miss + 0.5)
                query_points = np.column_stack((xq, yq))

                logger.info(
                    f"[{self.name}] Interpolating {len(query_points)} voids for Weight {current_weight} (Smooth: {smoothing:.1f})"
                )
                # if not is_coarsest:
                # rbf = RBFInterpolator(points, values, smoothing=smoothing, neighbors=50)
                # else:
                rbf = RBFInterpolator(
                    points,
                    values,
                    kernel="thin_plate_spline",
                    smoothing=smoothing,
                    neighbors=1000,
                    degree=6,
                    epsilon=None,
                )
                z_filled = rbf(query_points)
                z[y_miss, x_miss] = z_filled

            if previous_surface:
                bg_aligned = np.full(z.shape, ndv, dtype=z.dtype)
                with rasterio.open(previous_surface) as bg_src:
                    reproject(
                        source=rasterio.band(bg_src, 1),
                        destination=bg_aligned,
                        src_transform=bg_src.transform,
                        src_crs=bg_src.crs,
                        dst_transform=src.transform,
                        dst_crs=src.crs,
                        src_nodata=bg_src.nodata,
                        dst_nodata=ndv,
                        resampling=Resampling.cubic,  # Spline upsampling for smooth background!
                        num_threads=1,
                    )

                dist = scipy.ndimage.distance_transform_cdt(
                    ~core_mask, metric="taxicab"
                )
                weights = np.clip(dist / float(self.blend_dist), 0.0, 1.0)

                bg_only_mask = dist >= self.blend_dist
                z[bg_only_mask] = bg_aligned[bg_only_mask]

                trans_mask = (dist > 0) & (dist < self.blend_dist)
                if np.any(trans_mask):
                    valid_bg = (bg_aligned != ndv) & (~np.isnan(bg_aligned))
                    blend_active = trans_mask & valid_bg
                    z[blend_active] = (
                        (1.0 - weights[blend_active]) * z[blend_active]
                    ) + (weights[blend_active] * bg_aligned[blend_active])

            src.write(z.astype(rasterio.float32), 1)

    def process_raster(self, src_path, dst_path, entry):
        previous_surface = None

        highest_bin_index = len(self.weights)
        smoothing_map = np.linspace(
            self.sparse_smoothing, self.dense_smoothing, len(self.resolutions)
        )

        for i, res_str in enumerate(reversed(self.resolutions)):
            # current_weight = self.weights[::-1][i]
            current_weight = self.weights[i]
            current_smoothing = smoothing_map[i]
            current_algo = self.algos[::-1][i]
            is_coarsest = i == 0

            step_stack = f"{src_path}.step_{res_str}.tif"
            self._decimate_raster(src_path, step_stack, target_res=res_str)

            self._process_tier(
                step_stack,
                previous_surface,
                current_weight,
                current_smoothing,
                is_coarsest,
                current_algo,
            )

            previous_surface = step_stack

        if previous_surface:
            shutil.move(previous_surface, dst_path)
            remove_glob2("temp_stack_step*.tif", "temp_interp_step*.tif", "*.blend.tif")
            logger.info(f"--- Successfully built Binary CUDEM DEM: {dst_path} ---")
            return True

        return False
