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
from rasterio.warp import reproject, Resampling

from fetchez.spatial import Region
from fetchez.utils import (
    remove_glob2,
    str2inc,
    inc2str,
    int_or,
    float_or,
    str_or,
    parse_hook_string,
    parse_arg_to_list,
)

from .base import RasterGlobalHook

logger = logging.getLogger(__name__)


class BinaryCudemStepDown(RasterGlobalHook):
    """Multi-Resolution Morphological step-down."""

    name = "ms_binary_cudem"
    default_suffix = "_binary_cudem"
    meta_desc = "Intpolate NoData voids using Multi Resolution Morphological stacking."
    meta_tags = ["globato", "interpolation", "multi-stack"]
    meta_requires = "multi-stack"

    def __init__(
        self,
        steps=3,
        weights=None,  # [1.0, 0.5, 0],
        resolutions=None,  # ["3s", "9s", "15s"],  # E.g., 1s=Dense, 3s=Med, 9s=Sparse
        algos=None,  # ,["raster_fill", "raster_fill", "interp_rbf"],
        blend_dists=None,  # 20,
        decimation_mode="weighted_mean",
        bathy_max_z=-0.01,
        keep_steps=False,
        **kwargs,
    ):
        super().__init__(strip_bands=True, **kwargs)

        self.valid_algos = [
            "interp_gmt",
            "interp_rbf",
            "raster_fill",
            "interp_nn",
            "interp_idw",
            "interp_scipy",
        ]
        self.steps = int_or(steps)
        self.weights = parse_arg_to_list(weights, float)
        self.resolutions = parse_arg_to_list(resolutions, str2inc)
        self.blend_dists = parse_arg_to_list(blend_dists, int)
        self.algos = parse_arg_to_list(algos, str)
        self.decimation_mode = str_or(decimation_mode, "weighted_mean")
        self.bathy_max_z = float_or(bathy_max_z, -0.01)
        self.keep_steps = keep_steps

    def _setup_steps(self, src_path):
        # Determine total tiers needed (Target Steps + 1 Base Layer)
        target_tiers = max(
            self.steps + 1,
            len(self.resolutions),
            len(self.weights),
            len(self.blend_dists),
            len(self.algos),
        )

        self.steps = target_tiers - 1

        # Weights: Auto-generate step-downs if not provided
        self.weights = sorted(self.weights, reverse=True)
        while len(self.weights) < self.steps:
            if len(self.weights) == 0:
                self.weights.append(1.0)
            next_weight = self.weights[-1] / 2.0
            if next_weight == 0:
                next_weight = 1e-20
            self.weights.append(next_weight)

        # Ensure the final tier always catches everything (Weight 0)
        if self.weights[-1] > 0:
            self.weights.append(0.0)

        # Resolutions: Auto-multiply by 3 if not provided
        with rasterio.open(src_path) as src:
            base_res = src.profile["transform"][0]

        while len(self.resolutions) < target_tiers:
            if len(self.resolutions) == 0:
                self.resolutions.append(base_res)
            self.resolutions.append(self.resolutions[-1] * 3)

        # Algos: Pad by repeating the last provided algo
        if len(self.algos) == 0:
            self.algos = ["raster_fill:max_dist=10"] * max(0, self.steps)
            self.algos.append("interp_rbf")

        while len(self.algos) < target_tiers:
            self.algos.append(self.algos[-1])

        # Blend Dists: Pad by repeating the last provided distance
        while len(self.blend_dists) < target_tiers:
            if len(self.blend_dists) == 0:
                self.blend_dists.append(20)
            self.blend_dists.append(self.blend_dists[-1])

        logger.info(
            f"{self.weights} | {self.algos} | {self.resolutions} | {self.blend_dists}"
        )

    def _get_interp_hook(self, parsed_algo_hook):
        """Loads the requested interpolation hook."""

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

        local_tmp = os.path.abspath("tmp")
        os.makedirs(local_tmp, exist_ok=True)

        logger.info(
            f"[{self.name}] Decimating to {target_res} using '{self.decimation_mode}'..."
        )

        with rasterio.open(src_path) as src:
            bounds = src.bounds
            region = Region(bounds.left, bounds.right, bounds.bottom, bounds.top)
            src_crs = src.crs.to_string() if src.crs else None
            region.srs = src_crs

        decimated_stack = fetchez.get(
            "file",
            outdir=local_tmp,
            region=region,
            region_srs=src_crs,
            path=src_path,
            use_cache=False,
            hooks=[
                "set_datatype:data_type=multi-stack",
                "stream-init",
                {
                    "name": "multi_stack",
                    "args": {
                        "res": target_res,
                        "output": dst_path,
                        "crs": src_crs,
                        "mode": "mixed",  # self.decimation_mode,
                        "weight_threshold": "/".join([str(x) for x in self.weights]),
                        "overwrite": True,
                    },
                },
                "focus_sink:target=multi_stack",
            ],
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
        base_barrier,
    ):
        with rasterio.open(step_stack, "r+") as src:
            data = src.read()
            ndv = src.nodata if src.nodata is not None else -9999

            z = data[0].astype("float64")
            w = data[2].astype("float64")

            barrier_mask = None
            if base_barrier is not None:
                hr_mask, hr_transform = base_barrier

                # If we are already at the base resolution, just use it
                if src.transform == hr_transform and z.shape == hr_mask.shape:
                    barrier_mask = hr_mask
                else:
                    warped_mask = np.zeros(z.shape, dtype="float32")
                    reproject(
                        source=hr_mask.astype("float32"),
                        destination=warped_mask,
                        src_transform=hr_transform,
                        src_crs=src.crs,
                        dst_transform=src.transform,
                        dst_crs=src.crs,
                        resampling=Resampling.average,  # Area-weighted average!
                        num_threads=1,
                    )
                    # A coarse pixel is land if >50% of its high-res footprint is land
                    barrier_mask = warped_mask >= 0.5

            if self.bathy_max_z is not None and barrier_mask is not None:
                water_mask = (~barrier_mask) & (z != ndv) & (~np.isnan(z))
                z[water_mask] = np.minimum(z[water_mask], self.bathy_max_z)

            # We only process data at or above the current weight
            valid_mask = (z != ndv) & (~np.isnan(z))
            core_mask = w >= current_weight

            if is_coarsest:
                tier_zone = np.ones_like(core_mask, dtype=bool)
                missing_mask = ~valid_mask
            else:
                dist_to_core = scipy.ndimage.distance_transform_edt(~core_mask)
                tier_zone = dist_to_core <= current_blend_dist

                missing_mask = (~valid_mask) & tier_zone
                # struct = scipy.ndimage.generate_binary_structure(2, 2)
                # tier_zone = scipy.ndimage.binary_dilation(
                #     core_mask, structure=struct, iterations=current_blend_dist
                # )
                # missing_mask = (~valid_mask) & tier_zone

            # Delegate Interpolation to the requested Hook
            y_miss, x_miss = np.where(missing_mask)
            if len(y_miss) > 0:
                logger.info(
                    f"[{self.name}] Filling {len(y_miss)} voids using '{current_algo}' for Weights above {current_weight}"
                )

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
                logger.info("Tier is filled by data; nothing to interpolate")
                # logger.info(f"no points in tier {current_weight}")

            # if self.barrier is not None:
            #     barrier_mask = self._create_barrier_mask(z.shape, src.transform)

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
                        resampling=Resampling.bilinear,
                        num_threads=1,
                    )

                if not np.any(core_mask):
                    z[:] = bg_aligned[:]
                else:
                    logger.info(
                        f"Blending step with background [ {current_weight} : {current_blend_dist} ]"
                    )
                    # --- BARRIER-RESTRICTED DISTANCE TRANSFORM ---
                    if barrier_mask is not None:
                        # Distance to the nearest land data
                        dist_land = scipy.ndimage.distance_transform_edt(
                            ~(core_mask & barrier_mask)  # , metric="taxicab"
                        )
                        # Distance to the nearest water data
                        dist_water = scipy.ndimage.distance_transform_edt(
                            ~(core_mask & ~barrier_mask)  # , metric="taxicab"
                        )
                        # Merge them strictly along the barrier
                        dist = np.where(barrier_mask, dist_land, dist_water)
                    else:
                        # Fallback if no barrier is provided
                        dist = scipy.ndimage.distance_transform_edt(
                            ~core_mask  # , metric="taxicab"
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
                        valid_z = (z != ndv) & (~np.isnan(z))

                        blend_active = trans_mask & valid_bg & valid_z
                        z[blend_active] = (
                            (1.0 - weights[blend_active]) * z[blend_active]
                        ) + (weights[blend_active] * bg_aligned[blend_active])

                    remaining_holes = (z == ndv) | np.isnan(z)
                    valid_bg_overall = (bg_aligned != ndv) & (~np.isnan(bg_aligned))

                    fill_from_bg = remaining_holes & valid_bg_overall
                    z[fill_from_bg] = bg_aligned[fill_from_bg]
                    # dist = scipy.ndimage.distance_transform_cdt(
                    #     ~core_mask, metric="taxicab"
                    # )
                    # if current_blend_dist > 0:
                    #     weights = np.clip(dist / float(current_blend_dist), 0.0, 1.0)
                    #     bg_only_mask = dist >= current_blend_dist
                    #     trans_mask = (dist > 0) & (dist < current_blend_dist)
                    # else:
                    #     # Anything outside the core mask is 100% background
                    #     weights = np.ones_like(dist)
                    #     bg_only_mask = dist > 0
                    #     trans_mask = np.zeros_like(dist, dtype=bool)

                    # z[bg_only_mask] = bg_aligned[bg_only_mask]

                    # if np.any(trans_mask):
                    #     valid_bg = (bg_aligned != ndv) & (~np.isnan(bg_aligned))
                    #     valid_z = (z != ndv) & (~np.isnan(z))

                    #     # blend_active = trans_mask & valid_bg
                    #     blend_active = trans_mask & valid_bg & valid_z
                    #     z[blend_active] = (
                    #         (1.0 - weights[blend_active]) * z[blend_active]
                    #     ) + (weights[blend_active] * bg_aligned[blend_active])

                    # remaining_holes = (z == ndv) | np.isnan(z)
                    # valid_bg_overall = (bg_aligned != ndv) & (~np.isnan(bg_aligned))

                    # fill_from_bg = remaining_holes & valid_bg_overall
                    # z[fill_from_bg] = bg_aligned[fill_from_bg]

                if self.bathy_max_z is not None and barrier_mask is not None:
                    # barrier_mask = self._create_barrier_mask(z.shape, src.transform)
                    # if barrier_mask is not None:
                    water_mask = (~barrier_mask) & (z != ndv) & (~np.isnan(z))
                    z[water_mask] = np.minimum(z[water_mask], self.bathy_max_z)

            src.write(z.astype(rasterio.float32), 1)

    def process_raster(self, src_path, dst_path, entry):
        previous_surface = None
        self._setup_steps(src_path)

        base_barrier = None
        with rasterio.open(src_path) as base_src:
            native_res = base_src.transform[0]
            if self.barrier is not None:
                hr_mask = self._create_barrier_mask(base_src.shape, base_src.transform)
                if hr_mask is not None:
                    base_barrier = (hr_mask, base_src.transform)

        for i, res_str in enumerate(reversed(self.resolutions)):
            # current_weight = self.weights[::-1][i]
            current_weight = self.weights[::-1][i]
            current_algo = self.algos[::-1][i]
            current_blend_dist = self.blend_dists[::-1][i]
            is_coarsest = i == 0

            step_stack = src_path.replace(".tif", f"_step_{inc2str(res_str)}.tif")

            if np.isclose(res_str, native_res, atol=1e-9):
                logger.info(
                    f"[{self.name}] Tier resolution matches base stack. Skipping decimation."
                )
                shutil.copy(src_path, step_stack)
            else:
                self._decimate_raster(src_path, step_stack, target_res=res_str)

            self._process_tier(
                step_stack,
                previous_surface,
                current_weight,
                current_algo,
                current_blend_dist,
                is_coarsest,
                base_barrier=base_barrier,
            )

            if previous_surface:
                if not self.keep_steps:
                    remove_glob2(f"{previous_surface.split('.')[0]}.*")

            previous_surface = step_stack

        if previous_surface:
            if self.keep_steps:
                shutil.copy(previous_surface, dst_path)
            else:
                shutil.move(previous_surface, dst_path)
                # sutil.move(previous_surface, dst_path)

                remove_glob2(
                    "temp_stack_step*.tif",
                    "temp_interp_step*.tif",
                    "*.blend.tif",
                    "*_step_*.tif*",
                    f"{previous_surface.split('.')[0]}.*",
                )
            logger.info(f"--- Successfully built Binary CUDEM DEM: {dst_path} ---")
            return True

        return False
