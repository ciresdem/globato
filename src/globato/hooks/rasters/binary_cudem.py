#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.rasters.binary_cudem
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Binary CUDEM: Morphological Multi-Resolution Step-Down.
Uses weights to route specific datasets to specific resolutions
and interpolator settings, bridging gaps in sparse data without
degrading the high-frequency fidelity of dense coastal data.
"""

import os
import shutil
import logging
import numpy as np
import rasterio
import scipy.ndimage
from rasterio.warp import calculate_default_transform, reproject, Resampling

from fetchez.utils import remove_glob2, str2inc, inc2str, int_or, parse_hook_string
from fetchez.spatial import Region

from .base import RasterGlobalHook

logger = logging.getLogger(__name__)


class BinaryCudemStepDown(RasterGlobalHook):
    """
    Multi-Resolution Morphological step-down using Binary Coding.
    """

    name = "ms_binary_cudem"
    default_suffix = "_binary_cudem"
    meta_category = "multi-stack"
    meta_tags = ["globato", "interpolation", "multi-stack"]

    def __init__(
        self,
        steps=3,
        weights=None,  # [1.0, 0.5, 0],
        resolutions=None,  # ["3s", "9s", "15s"],  # E.g., 1s=Dense, 3s=Med, 9s=Sparse
        algos=None,  # ,["raster_fill", "raster_fill", "interp_rbf"],
        blend_dists=None,  # 20,
        barrier=None,
        decimation_mode="weighted_mean",
        **kwargs,
    ):
        super().__init__(barrier=barrier, strip_bands=True, **kwargs)

        self.valid_algos = ["interp_gmt", "interp_rbf", "raster_fill", "interp_nn", "interp_idw", "interp_scipy"]
        self.steps = int_or(steps)

        def _parse_arg(val, cast_type):
            if val is None:
                return []
            if isinstance(val, list):
                return [cast_type(v) for v in val]
            if isinstance(val, str) and "/" in val:
                return [cast_type(v) for v in val.split("/")]
            return [cast_type(val)]

        self.weights = _parse_arg(weights, float)
        self.resolutions = _parse_arg(resolutions, str2inc)
        self.blend_dists = _parse_arg(blend_dists, int)
        self.algos = _parse_arg(algos, str)
        self.decimation_mode = decimation_mode

    def _setup_steps(self, src_path):
        # Steps
        self.steps = (
            max(
                self.steps,
                len(self.resolutions),
                len(self.weights),
                len(self.blend_dists),
                len(self.algos),
            )
            - 1
        )

        # Weights
        # TODO: use src to determine defaults by percentile
        self.weights = sorted(self.weights, reverse=True)
        while len(self.weights) < self.steps:
            if len(self.weights) == 0:
                self.weights.append(1)
            next_weight = self.weights[-1] / 2
            if next_weight == 0:
                next_weight = 1e-20
            self.weights.append(next_weight)

        if self.weights[-1] > 0:
            self.weights.append(0)

        # Resolutions
        with rasterio.open(src_path) as src:
            base_res = src.profile["transform"][0]

        while len(self.resolutions) <= self.steps:
            if len(self.resolutions) == 0:
                self.resolutions.append(base_res)
            self.resolutions.append(self.resolutions[-1] * 3)

        # Algos
        while len(self.algos) < self.steps:
            # if len(self.algos) == 0:
            self.algos.append("raster_fill")
            # else:
            #    # self.algos.append(self.algos[-1])
        # self.algos.append(
        #     f"interp_rbf:smoothing={len(self.algos) * 60},neighbors=500,degree=1"
        # )
        #self.algos.append("interp_gmt:tension=1")
        self.algos.append("interp_scipy")

        # Blend Dists
        while len(self.blend_dists) <= self.steps:
            if len(self.blend_dists) == 0:
                self.blend_dists.append(20)
            self.blend_dists.append(self.blend_dists[-1])

        logger.info(
            f"{self.weights} | {self.algos} | {self.resolutions} | {self.blend_dists}"
        )

    def _get_interp_hook(self, parsed_algo_hook):
        """Dynamically loads the requested interpolation hook."""

        from fetchez.registry import HookRegistry

        HookRegistry.load_all()

        algo_name = parsed_algo_hook["name"]
        algo_args = parsed_algo_hook.get("args", {})

        if algo_name in self.valid_algos:
            algo_class = HookRegistry.get_class(algo_name)
            return algo_class(**algo_args)
        else:
            logger.warning(
                f"[{self.name}] Unknown algo '{algo_name}'. Falling back to RBF."
            )
            return HookRegistry.get_class("interp_rbf")()

    def _decimate_raster(self, src_path, dst_path, target_res):
        """Decimates by converting the high-res grid back to a point cloud
        and re-binning it through the Multi-Stack Accumulator to preserve weights
        """

        import fetchez

        logger.info(f"[{self.name}] Decimating to {target_res} using '{self.decimation_mode}'...")

        with rasterio.open(src_path) as src:
            bounds = src.bounds
            region = [bounds.left, bounds.right, bounds.bottom, bounds.top]
            src_crs = src.crs.to_string() if src.crs else None

        decimated_stack = fetchez.get(
            "file",
            region=region,
            path=src_path,
            hooks=[
                "set_datatype:datatype=multi-stack"
                "stream-init",
                f"multi_stack:res={target_res},output={dst_path},crs={src_crs}",
                "focus_sink:target=multi_stack",
            ]
        )
        return decimated_stack

    def _process_tier(
        self,
        step_stack,
        previous_surface,
        current_weight,
        current_algo,
        current_blend_dist,
        is_coarsest,
    ):
        with rasterio.open(step_stack, "r+") as src:
            data = src.read()
            ndv = src.nodata if src.nodata is not None else -9999

            z = data[0].astype("float64")
            w = data[2].astype("float64")
            valid_mask = (z != ndv) & (~np.isnan(z))
            core_mask = w >= current_weight

            if is_coarsest:
                tier_zone = np.ones_like(core_mask, dtype=bool)
                missing_mask = ~valid_mask
            else:
                struct = scipy.ndimage.generate_binary_structure(2, 2)
                tier_zone = scipy.ndimage.binary_dilation(
                    core_mask, structure=struct, iterations=current_blend_dist
                )
                missing_mask = (~valid_mask) & tier_zone

            # Delegate Interpolation to the requested Hook
            y_miss, x_miss = np.where(missing_mask)
            if len(y_miss) > 0:
                logger.info(
                    f"[{self.name}] Filling {len(y_miss)} voids using '{current_algo}' for Weights above {current_weight}"
                )

                # Create isolated temporary files for the hook
                temp_in = step_stack.replace(".tif", f"_{current_weight}_in.tif")
                temp_out = step_stack.replace(".tif", f"_{current_weight}_out.tif")

                shutil.copy(step_stack, temp_in)

                # Scrub the temp file so the hook only sees the relevant tiers
                # with rasterio.open(temp_in, "r+") as tmp_src:
                #     tmp_z = tmp_src.read(1)
                #     tmp_z[~core_mask] = ndv
                #     tmp_src.write(tmp_z, 1)

                current_algo_hook = parse_hook_string(current_algo)
                interp_hook = self._get_interp_hook(current_algo_hook)
                # success = interp_hook.process_raster(temp_in, temp_out, entry={})
                success = interp_hook.process_raster(step_stack, temp_out, entry={})

                # Extract only the targeted voids and bring them back into our main array
                if success and os.path.exists(temp_out):
                    with rasterio.open(temp_out) as filled_src:
                        filled_z = filled_src.read(1)
                        z[y_miss, x_miss] = filled_z[y_miss, x_miss]

                if os.path.exists(temp_in):
                    os.remove(temp_in)
                if os.path.exists(temp_out):
                    os.remove(temp_out)
            else:
                logger.info(f"no points in tier {current_weight}")

            # Cross-fade with the upsampled background
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

                if not np.any(core_mask):
                    z[:] = bg_aligned[:]
                else:
                    dist = scipy.ndimage.distance_transform_cdt(
                        ~core_mask, metric="taxicab"
                    )
                    if current_blend_dist > 0:
                        weights = np.clip(dist / float(current_blend_dist), 0.0, 1.0)
                        bg_only_mask = dist >= current_blend_dist
                        trans_mask = (dist > 0) & (dist < current_blend_dist)
                    else:
                        # Anything outside the core mask is 100% background
                        weights = np.ones_like(dist)
                        bg_only_mask = dist > 0
                        trans_mask = np.zeros_like(dist, dtype=bool)

                    z[bg_only_mask] = bg_aligned[bg_only_mask]

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

    def process_raster(self, src_path, dst_path, entry):
        previous_surface = None

        self._setup_steps(src_path)

        for i, res_str in enumerate(reversed(self.resolutions)):
            # current_weight = self.weights[::-1][i]
            current_weight = self.weights[::-1][i]
            current_algo = self.algos[::-1][i]
            current_blend_dist = self.blend_dists[::-1][i]
            is_coarsest = i == 0

            step_stack = src_path.replace(".tif", f"_step_{inc2str(res_str)}.tif")
            self._decimate_raster(src_path, step_stack, target_res=res_str)

            self._process_tier(
                step_stack,
                previous_surface,
                current_weight,
                current_algo,
                current_blend_dist,
                is_coarsest,
            )

            previous_surface = step_stack

        if previous_surface:
            shutil.move(previous_surface, dst_path)
            #remove_glob2("temp_stack_step*.tif", "temp_interp_step*.tif", "*.blend.tif")
            logger.info(f"--- Successfully built Binary CUDEM DEM: {dst_path} ---")
            return True

        return False
