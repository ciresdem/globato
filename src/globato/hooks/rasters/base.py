#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.rasters.base
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Core architecture for Raster processing hooks.
Separates Streaming (Local/Chunked) operations from Global (Whole-File) operations.

:copyright: (c) 2016 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import logging
import shutil
import numpy as np
import tempfile
import rasterio
from rasterio.windows import Window
import fiona
from fetchez.spatial import parse_region
from fetchez.hooks import FetchHook
from fetchez.utils import float_or

logger = logging.getLogger(__name__)


tmp_dir = tempfile.gettempdir()


# =============================================================================
# THE SHARED BASE (Utilities)
# =============================================================================
class RasterBaseHook(FetchHook):
    """Shared utilities for both Streaming and Global raster hooks."""

    meta_stage = "collection"
    default_suffix = "_processed"

    def __init__(
        self,
        suffix=None,
        barrier=None,
        region=None,
        output=None,
        upper=None,
        lower=None,
        strip_bands=False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.output = output
        self.suffix = suffix or self.default_suffix
        self.barrier = barrier
        self.barrier_geoms = None
        self.region = parse_region(region)
        self.upper = float_or(upper)
        self.lower = float_or(lower)
        self.strip_bands = strip_bands

    def modify_profile(self, profile):
        """Override this to change dtype, count, or nodata for the output raster."""

        profile.update(
            tiled=True,
            blockxsize=256,
            blockysize=256,
            compress="deflate",
            predictor=3,
            bigtiff="YES",
        )
        return profile

    def _strip_to_single_band(self, raster_path):
        """Removes auxiliary bands from a GeoTIFF, retaining only Band 1."""

        if not self.strip_bands:
            return

        with rasterio.open(raster_path) as src:
            if src.count == 1:
                return

            profile = src.profile.copy()
            profile.update(count=1)

            temp_path = raster_path + ".strip.tif"
            with rasterio.open(temp_path, "w", **profile) as dst:
                dst.write(src.read(1), 1)

        shutil.move(temp_path, raster_path)
        logger.debug(
            f"[{self.name}] Stripped auxiliary bands, retaining only Elevation (Band 1)."
        )

    def _clamp_raster(self, raster_path):
        """Clamp raster values to enforce lower/upper bounds."""

        if self.upper is None and self.lower is None:
            return

        with rasterio.open(raster_path, "r+") as src:
            data = src.read(1)
            nodata = src.nodata if src.nodata is not None else -9999

            is_float = data.dtype.kind == "f"
            if is_float:
                valid_mask = (data != nodata) & (~np.isnan(data))
            else:
                valid_mask = data != nodata

            clamped = False
            if self.upper is not None:
                mask = (data > self.upper) & valid_mask
                if np.any(mask):
                    data[mask] = self.upper
                    clamped = True

            if self.lower is not None:
                mask = (data < self.lower) & valid_mask
                if np.any(mask):
                    data[mask] = self.lower
                    clamped = True

            if clamped:
                logger.info(
                    f"[{self.name}] Clamped values to bounds (Lower: {self.lower}, Upper: {self.upper})"
                )
                src.write(data, 1)

    def _get_barrier_geometries(self):
        if not self.barrier:
            return None

        barrier_path = self.barrier
        barrier_lower = os.path.basename(barrier_path).lower()

        if barrier_lower in ["coastline", "landmask", "osm", "glob_coast"]:
            mod = getattr(self, "current_mod", None)
            if not mod or not getattr(mod, "region", None):
                logger.error("Region is required to auto-generate a coastline barrier.")
                return None

            if self.output:
                outdir = os.path.dirname(self.output)
            else:
                outdir = os.getcwd()
            # (
            #     getattr(mod, "_outdir", None)
            #     or getattr(mod, "outdir", None)
            #     or os.getcwd()
            # )

            logger.info(f"[{self.name}] Generating coastline with outdir of {outdir}")

            target_mod_name = (
                "osm_landmask" if barrier_lower in ["osm", "landmask"] else "glob_coast"
            )

            logger.info(
                f"[{self.name}] Auto-generating barrier using {target_mod_name}..."
            )
            from fetchez.registry import ModuleRegistry
            from fetchez.core import run_fetchez

            generator_mod = ModuleRegistry.get_class(target_mod_name)
            if not generator_mod:
                logger.error(f"{target_mod_name} module not found!")
                return None

            gen_instance = generator_mod(
                src_region=mod.region,
                outdir=os.path.join(outdir, "auto_barriers"),
                res="1s",  # Ignored by osm_landmask, used by glob_coast
                include_water=True,  # Ignored by glob_coast, used by osm_landmask
            )

            gen_instance.run()
            run_fetchez([gen_instance])

            if gen_instance.results:
                if target_mod_name == "glob_coast":
                    for r in gen_instance.results:
                        artifacts = r.get("artifacts", {})
                        if "vector_fill_holes" in artifacts:
                            barrier_path = artifacts["vector_fill_holes"]
                            break
                        elif "raster_polygonize" in artifacts:
                            barrier_path = artifacts["raster_polygonize"]
                            break
                else:
                    barrier_path = gen_instance.results[0].get("dst_fn")

        if not barrier_path or not os.path.exists(barrier_path):
            logger.debug(
                f"Barrier file not found or failed to generate: {barrier_path}"
            )
            return None

        try:
            with fiona.open(barrier_path, "r") as vec:
                return [feature["geometry"] for feature in vec]
        except Exception as e:
            logger.error(f"Could not parse geometries from {barrier_path}: {e}")
            return None

    def _create_barrier_mask(self, shape, transform):
        """Generates a boolean numpy mask from the barrier.
        Automatically fetches or generates the geometries on-demand.
        Returns True inside the polygons, False outside.
        """

        if not self.barrier:
            return None

        # _get_barrier_geometries fetches the data if not provided
        if not self.barrier_geoms:
            self.barrier_geoms = self._get_barrier_geometries()

        # If fetching failed or returned nothing, abort
        if not self.barrier_geoms:
            return None

        from rasterio.features import rasterize

        mask = rasterize(
            self.barrier_geoms,
            out_shape=shape,
            transform=transform,
            fill=0,
            default_value=1,
            dtype="uint8",
        ).astype(bool)

        return mask

    def get_outliers(self, in_array, percentile=75, k=1.5):
        if np.all(np.isnan(in_array)):
            return np.nan, np.nan
        p_max = np.nanpercentile(in_array, percentile)
        p_min = np.nanpercentile(in_array, 100 - percentile)
        iqr = (p_max - p_min) * k
        return p_max + iqr, p_min - iqr

    def yield_buffered_windows(self, src, buffer_size=0, chunk_size=None):
        if str(chunk_size).lower() == "full" or chunk_size == -1:
            windows = [((0, 0), Window(0, 0, src.width, src.height))]
        elif chunk_size:
            windows = []
            c_size = int(chunk_size)
            for row_off in range(0, src.height, c_size):
                for col_off in range(0, src.width, c_size):
                    width = min(c_size, src.width - col_off)
                    height = min(c_size, src.height - row_off)
                    windows.append(
                        ((row_off, col_off), Window(col_off, row_off, width, height))
                    )
        else:
            windows = list(src.block_windows(1))

        for block_index, window in windows:
            if buffer_size == 0:
                yield window, window
                continue

            row_start = max(0, window.row_off - buffer_size)
            col_start = max(0, window.col_off - buffer_size)
            row_stop = min(src.height, window.row_off + window.height + buffer_size)
            col_stop = min(src.width, window.col_off + window.width + buffer_size)

            buffered_window = Window.from_slices(
                (row_start, row_stop), (col_start, col_stop)
            )
            yield window, buffered_window

    def _extract_subpixel_coords(
        self, data_stack, rows, cols, transform, apply_jitter=True
    ):
        """Extracts X/Y coordinates from a MultiStack or falls back to cell-centers."""

        # transform = src.transform
        x_vals, y_vals = transform * (cols + 0.5, rows + 0.5)

        #    is_multi_stack = src.tags().get("GLOBATO_DATATYPE") == "MULTI_STACK"

        # if is_multi_stack and data_stack is not None and data_stack.shape[0] >= 7:
        if data_stack is not None and data_stack.ndim == 3 and data_stack.shape[0] >= 7:
            x_band = data_stack[5][rows, cols]
            y_band = data_stack[6][rows, cols]

            nan_xy = np.isnan(x_band) | np.isnan(y_band)
            if np.any(nan_xy):
                x_band[nan_xy] = x_vals[nan_xy]
                y_band[nan_xy] = y_vals[nan_xy]

            x_vals = x_band
            y_vals = y_band
            logger.debug(f"[{self.name}] Using x/y bands from input")

        if apply_jitter:
            rng = np.random.default_rng(seed=42)
            x_vals = x_vals + rng.uniform(-1e-10, 1e-10, size=len(x_vals))
            y_vals = y_vals + rng.uniform(-1e-10, 1e-10, size=len(y_vals))

        return x_vals, y_vals


# =============================================================================
# THE STREAMING HOOK
# =============================================================================
class RasterStreamHook(RasterBaseHook):
    """For localized, chunk-by-chunk operations (Morphology, Slopes, Sieve)."""

    meta_category = "raster-stream"

    def __init__(self, buffer=0, chunk_size=None, **kwargs):
        super().__init__(**kwargs)
        self.buffer = int(buffer)
        self.chunk_size = chunk_size

    def _stream_wrapper(self, input_stream, entry):
        """Pass-through generator for in-memory stream pipelines."""
        profile = next(input_stream)
        profile = self.modify_profile(profile)
        yield profile

        self.barrier_geoms = self._get_barrier_geometries()
        for window, buff_win, data, ndv, transform in input_stream:
            processed_data = self.process_chunk(data, ndv, entry, transform, buff_win)
            yield window, buff_win, processed_data, ndv, transform

    def process_chunk(self, data, ndv, entry, transform=None, window=None):
        raise NotImplementedError("Streaming hooks must implement process_chunk()")

    def run(self, entries):
        new_entries = []
        for mod, entry in entries:
            # SET CURRENT MOD FOR COASTLINE GENERATION
            self.current_mod = mod
            if self.has_stream(entry):
                stream = entry.get("stream")
                entry["stream"] = self._stream_wrapper(stream, entry)
                entry["steam_type"] = "raster-stream"
                new_entries.append((mod, entry))
                continue

            src_fn = entry.get("dst_fn")
            if not src_fn or not os.path.exists(src_fn):
                new_entries.append((mod, entry))
                continue

            # dst_fn = self.output or f"{os.path.splitext(src_fn)[0]}{self.suffix}.tif"
            # dst_fn = f"{os.path.splitext(src_fn)[0]}{self.suffix}.tif"

            if self.output:
                dst_fn = self.output
            else:
                base_name = os.path.splitext(os.path.basename(src_fn))[0]
                dst_fn = os.path.join(tmp_dir, f"{base_name}{self.suffix}.tif")

            logger.debug(f"Running local {self.name} on {os.path.basename(src_fn)}")
            try:
                success = self._process_file_fallback(src_fn, dst_fn, entry)
                if success:
                    self._clamp_raster(dst_fn)
                    self._strip_to_single_band(dst_fn)

                    entry["src_fn"] = src_fn
                    entry["dst_fn"] = dst_fn
                    entry.setdefault("artifacts", {})[self.name] = dst_fn
            except Exception as e:
                logger.error(f"StreamHook {self.name} failed on {src_fn}: {e}")

            new_entries.append((mod, entry))

        return new_entries

    def _process_file_fallback(self, src_path, dst_path, entry):
        self.barrier_geoms = self._get_barrier_geometries()

        with rasterio.open(src_path) as src:
            profile = src.profile.copy()
            profile = self.modify_profile(profile)

            with rasterio.open(dst_path, "w", **profile) as dst:
                for window, buff_win in self.yield_buffered_windows(
                    src, self.buffer, self.chunk_size
                ):
                    data = src.read(window=buff_win)
                    chunk_transform = rasterio.windows.transform(
                        buff_win, src.transform
                    )

                    result = self.process_chunk(
                        data,
                        src.nodata,
                        entry,
                        transform=chunk_transform,
                        window=buff_win,
                    )

                    y_off = window.row_off - buff_win.row_off
                    x_off = window.col_off - buff_win.col_off

                    if result.ndim == 3:
                        final_chunk = result[
                            :,
                            y_off : y_off + window.height,
                            x_off : x_off + window.width,
                        ]
                        dst.write(final_chunk, window=window)
                    else:
                        final_chunk = result[
                            y_off : y_off + window.height, x_off : x_off + window.width
                        ]
                        dst.write(final_chunk, 1, window=window)

        return True

    process_raster = _process_file_fallback


# =============================================================================
# THE GLOBAL HOOK
# =============================================================================
class RasterGlobalHook(RasterBaseHook):
    """For operations requiring full spatial context (Splines, FillNodata, Orchestrators)."""

    meta_category = "raster-global"

    def process_raster(self, src_path, dst_path, entry):
        raise NotImplementedError("Global hooks must implement process_raster()")

    def run(self, entries):
        new_entries = []
        for mod, entry in entries:
            # SET CURRENT MOD FOR COASTLINE GENERATION
            self.current_mod = mod
            stream = entry.get("stream")
            src_fn = entry.get("dst_fn")

            if stream:
                logger.debug(
                    f"[{self.name}] Global hook detected active stream. Draining to disk..."
                )
                from globato.hooks.sinks.raster_writer import RasterWrite

                base_name = os.path.basename(src_fn)
                drain_fn = os.path.join(
                    tmp_dir, f"{os.path.splitext(base_name)[0]}_drained_{self.name}.tif"
                )
                # drain_fn = f"{os.path.splitext(src_fn)[0]}_drained_{self.name}.tif"
                entry["dst_fn"] = drain_fn

                drainer = RasterWrite(suffix="", inline=False)
                drainer.run([(mod, entry)])

                src_fn = entry.get("dst_fn")

            if not src_fn or not os.path.exists(src_fn):
                new_entries.append((mod, entry))
                continue

            if self.output:
                dst_fn = self.output
            else:
                base_name = os.path.splitext(os.path.basename(src_fn))[0]
                dst_fn = os.path.join(tmp_dir, f"{base_name}{self.suffix}.tif")

            logger.debug(f"Running global {self.name} on {os.path.basename(src_fn)}")
            try:
                success = self.process_raster(src_fn, dst_fn, entry)
                if success:
                    self._clamp_raster(dst_fn)
                    self._strip_to_single_band(dst_fn)

                    entry["src_fn"] = src_fn
                    entry["dst_fn"] = dst_fn
                    entry.setdefault("artifacts", {})[self.name] = dst_fn
            except Exception as e:
                logger.exception(f"GlobalHook {self.name} failed on {src_fn}: {e}")

            new_entries.append((mod, entry))

        return new_entries


class RasterCOG(RasterGlobalHook):
    """Converts a standard GeoTIFF into a strict Cloud-Optimized GeoTIFF (COG).
    Builds overviews (2, 4, 8, 16, 32) and aligns the byte structure for HTTP streaming.
    """

    name = "format_cog"
    default_suffix = "_cog"
    meta_category = "raster-global"

    def __init__(self, overviews="2/4/8/16/32", resampling="average", **kwargs):
        super().__init__(**kwargs)
        self.overviews = [int(x) for x in str(overviews).split("/")]
        self.resampling = resampling

    def process_raster(self, src_path, dst_path, entry):
        from rasterio.shutil import copy
        from rasterio.enums import Resampling

        logger.info(
            f"[{self.name}] Building {self.overviews} overviews and aligning COG..."
        )

        resampling_enum = getattr(
            Resampling, self.resampling.lower(), Resampling.average
        )
        with rasterio.open(src_path, "r+") as src:
            src.build_overviews(self.overviews, resampling_enum)
            src.update_tags(ns="rio_overview", resampling=self.resampling.lower())

        with rasterio.Env(GDAL_TIFF_OVR_BLOCKSIZE=256):
            copy(
                src_path,
                dst_path,
                copy_src_overviews=True,
                driver="COG",
                compress="deflate",
                predictor=3,
                blockxsize=256,
                blockysize=256,
                bigtiff="YES",
            )

        return True
