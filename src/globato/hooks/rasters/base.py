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
import numpy as np
import rasterio
from rasterio.windows import Window
import fiona
from transformez.spatial import TransRegion
from fetchez.hooks import FetchHook

logger = logging.getLogger(__name__)


# =============================================================================
# 1. THE SHARED BASE (Utilities)
# =============================================================================
class RasterBaseHook(FetchHook):
    """Shared utilities for both Streaming and Global raster hooks."""

    meta_stage = "collection"
    default_suffix = "_processed"

    def __init__(self, output=None, suffix=None, barrier=None, **kwargs):
        super().__init__(**kwargs)
        self.output = output
        self.suffix = suffix or self.default_suffix
        self.barrier = barrier
        self.barrier_geoms = None

    def modify_profile(self, profile):
        """Override this to change dtype, count, or nodata for the output raster."""

        return profile

    def _get_barrier_geometries(self):
        if not self.barrier:
            return None

        barrier_path = self.barrier

        # AUTO-GENERATE COASTLINE!
        if os.path.basename(barrier_path).lower() in ["coastline", "landmask"]:
            mod = getattr(self, "current_mod", None)
            if not mod or not getattr(mod, "region", None):
                logger.error("Region is required to auto-generate a coastline barrier.")
                return None

            w, e, s, n = mod.region
            res = "1s"

            # Place the mask inside the current project's output directory
            outdir = getattr(mod, "outdir", None)
            base_dir = os.path.join(outdir or os.getcwd(), "glob_coast")

            found_poly = None
            if os.path.exists(base_dir):
                import glob
                search_pattern = os.path.join(base_dir, f"coastline_{w}_{s}_{res}*.gpkg")
                matches = glob.glob(search_pattern)
                if matches:
                    # If both exist, prefer the filled version
                    matches.sort(key=lambda x: 'filled' in x, reverse=True)
                    found_poly = matches[0]

            if found_poly:
                barrier_path = found_poly
            else:
                logger.info(f"[{self.name}] Auto-generating coastline barrier for region...")
                from fetchez.registry import ModuleRegistry
                from fetchez.core import run_fetchez

                coast_mod = ModuleRegistry.get_class("glob_coast")
                if coast_mod:
                    # Spin up the module programmatically!
                    coast_instance = coast_mod(
                        src_region=mod.region,
                        res=res,
                        outdir=outdir,
                        fill_inland_holes=True
                    )
                    coast_instance.run()
                    run_fetchez([coast_instance])

                    # Fish the newly generated polygon out of the artifact registry
                    for r in coast_instance.results:
                        artifacts = r.get("artifacts", {})
                        if "vector_fill_holes" in artifacts:
                            barrier_path = artifacts["vector_fill_holes"]
                            break
                        elif "raster_polygonize" in artifacts:
                            barrier_path = artifacts["raster_polygonize"]
                            break
                    else:
                        logger.error("Failed to find polygonized coastline artifact.")
                        return None
                else:
                    logger.error("glob_coast module not found!")
                    return None

        if not os.path.exists(barrier_path):
            logger.warning(f"Barrier file not found: {barrier_path}")
            return None

        try:
            with fiona.open(barrier_path, "r") as vec:
                return [feature["geometry"] for feature in vec]
        except Exception as e:
            logger.error(f"Could not parse geometries from {barrier_path}: {e}")
            return None

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
                    windows.append(((row_off, col_off), Window(col_off, row_off, width, height)))
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

            buffered_window = Window.from_slices((row_start, row_stop), (col_start, col_stop))
            yield window, buffered_window


# =============================================================================
# 2. THE STREAMING HOOK
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
            stream = entry.get("raster_stream")

            if stream:
                entry["raster_stream"] = self._stream_wrapper(stream, entry)
                new_entries.append((mod, entry))
                continue

            src_fn = entry.get("dst_fn")
            if not src_fn or not os.path.exists(src_fn):
                new_entries.append((mod, entry))
                continue

            dst_fn = self.output or f"{os.path.splitext(src_fn)[0]}{self.suffix}.tif"

            logger.info(f"Running local {self.name} on {os.path.basename(src_fn)}")
            try:
                success = self._process_file_fallback(src_fn, dst_fn, entry)
                if success:
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

            with rasterio.open(dst_path, 'w', **profile) as dst:
                for window, buff_win in self.yield_buffered_windows(src, self.buffer, self.chunk_size):
                    data = src.read(window=buff_win)
                    chunk_transform = rasterio.windows.transform(buff_win, src.transform)

                    result = self.process_chunk(data, src.nodata, entry, transform=chunk_transform, window=buff_win)

                    y_off = window.row_off - buff_win.row_off
                    x_off = window.col_off - buff_win.col_off

                    if result.ndim == 3:
                        final_chunk = result[:, y_off : y_off + window.height, x_off : x_off + window.width]
                        dst.write(final_chunk, window=window)
                    else:
                        final_chunk = result[y_off : y_off + window.height, x_off : x_off + window.width]
                        dst.write(final_chunk, 1, window=window)

        return True
    process_raster = _process_file_fallback

# =============================================================================
# 3. THE GLOBAL HOOK
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
            stream = entry.get("raster_stream")
            src_fn = entry.get("dst_fn")

            if stream:
                logger.info(f"[{self.name}] Global hook detected active stream. Draining to disk...")
                from globato.hooks.sinks.raster_writer import RasterWrite

                drain_fn = f"{os.path.splitext(src_fn)[0]}_drained_{self.name}.tif"
                entry["dst_fn"] = drain_fn

                drainer = RasterWrite(suffix="", inline=False)
                drainer.run([(mod, entry)])

                src_fn = entry.get("dst_fn")

            if not src_fn or not os.path.exists(src_fn):
                new_entries.append((mod, entry))
                continue

            dst_fn = self.output or f"{os.path.splitext(src_fn)[0]}{self.suffix}.tif"

            logger.info(f"Running global {self.name} on {os.path.basename(src_fn)}")
            try:
                success = self.process_raster(src_fn, dst_fn, entry)
                if success:
                    entry["src_fn"] = src_fn
                    entry["dst_fn"] = dst_fn
                    entry.setdefault("artifacts", {})[self.name] = dst_fn
            except Exception as e:
                logger.error(f"GlobalHook {self.name} failed on {src_fn}: {e}")

            new_entries.append((mod, entry))

        return new_entries
