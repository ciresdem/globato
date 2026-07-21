#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.sinks.multi_stack
~~~~~~~~~~~~~~~~~~~~~~~

Multi-band Statistical Gridder.
Generates Z, Count, Weight, Uncertainty, etc.
Maintains a '.sums.tif' for continuous updates and provenance tracking.

:copyright: (c) 2016 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import json
import logging
import threading
import numpy as np
# from tqdm import tqdm

import rasterio
from rasterio.windows import Window
from rasterio.crs import CRS
from rasterio.enums import ColorInterp

from fetchez.spatial import Region
from fetchez.hooks import FetchHook
from fetchez.utils import (
    colorize,
    CYAN,
    BLUE,
    BOLD,
    format_dataset_id,
)

from ..transforms.point_pixels import PointPixels

from globato import __version__

logger = logging.getLogger(__name__)


# MULTI_STACK ACCUMULATOR
class MultiStackAccumulator:
    """Multi-band statistical grid accumulator"""

    BAND_MAP = {
        "z": 1,
        "count": 2,
        "weights": 3,
        "uncertainty": 4,
        "src_uncertainty": 5,
        "x": 6,
        "y": 7,
        # "bitmask": 8,
    }

    def __init__(
        self,
        region,
        x_inc,
        y_inc,
        output_fn,
        mode="mean",
        weight_threshold="1",
        crs="EPSG:4326",
        verbose=False,
        overwrite=False,
    ):
        self.region = Region.from_list(region)
        self.x_inc = abs(float(x_inc))
        self.y_inc = abs(float(y_inc))
        # self.x_inc = float(x_inc)
        # self.y_inc = float(y_inc) * -1
        self.output_fn = output_fn
        self.mode = mode.lower()
        self.crs = crs
        self.verbose = verbose
        self.lock = threading.Lock()
        self.overwrite = overwrite

        base, ext = os.path.splitext(self.output_fn)
        self.sums_fn = f"{base}.sums{ext}"

        self.wts = np.sort([float(x) for x in str(weight_threshold).split("/")])

        self.xcount, self.ycount, self.dst_gt = self.region.geo_transform(
            x_inc=self.x_inc, y_inc=self.y_inc, node="grid"
        )

        self.transform = rasterio.transform.from_origin(
            self.dst_gt[0], self.dst_gt[3], self.dst_gt[1], abs(self.dst_gt[5])
        )

        self._init_raster()

        self.dataset = rasterio.open(self.sums_fn, "r+")
        # Convert the point-stream to a raster-stream.
        self.pixel_binner = PointPixels(
            src_region=self.region, x_size=self.xcount, y_size=self.ycount
        )

        logger.info(
            f"Initializing Multi_Stack internal arrays at {self.xcount}/{self.ycount}"
        )

    def _init_raster(self):
        """Create the zero-filled accumulation file or load existing."""

        if os.path.exists(self.sums_fn):
            if not self.overwrite:
                with rasterio.open(self.sums_fn, "r") as existing:
                    if existing.width != self.xcount or existing.height != self.ycount:
                        logger.warning(
                            f"Existing sums file dimensions ({existing.width}x{existing.height}) "
                            f"do not match current settings ({self.xcount}x{self.ycount}). "
                            "Overwriting old file to prevent dimension errors."
                        )
                        pass
                    else:
                        logger.info(
                            f"Found existing sums file: {os.path.basename(self.sums_fn)}. Operating in UPDATE mode."
                        )
                        return

            os.remove(self.sums_fn)

        if not os.path.exists(os.path.dirname(os.path.abspath(self.sums_fn))):
            os.makedirs(os.path.dirname(os.path.abspath(self.sums_fn)))

        profile = {
            "driver": "GTiff",
            "dtype": "float64",
            "nodata": -9999,
            "width": self.xcount,
            "height": self.ycount,
            "count": 7,
            "crs": CRS.from_string(self.crs) if self.crs else None,
            "transform": self.transform,
            "tiled": True,
            "compress": "lzw",
            "predictor": 2,
            "bigtiff": "YES",
        }

        with rasterio.open(self.sums_fn, "w", **profile) as dst:
            for key, idx in self.BAND_MAP.items():
                dst.set_band_description(idx, key)

            dst.update_tags(GLOBATO_PROVENANCE="[]")

    def is_registered(self, dataset_id):
        """Check the GeoTIFF header to see if dataset is already stacked."""

        if not hasattr(self, "dataset") or self.dataset.closed:
            return False

        with self.lock:
            reg_str = self.dataset.tags().get("GLOBATO_PROVENANCE", "[]")
            registry = json.loads(reg_str)
            return dataset_id in registry

    def mark_registered(self, dataset_id):
        """Add dataset to the GeoTIFF header registry."""

        with self.lock:
            reg_str = self.dataset.tags().get("GLOBATO_PROVENANCE", "[]")
            registry = json.loads(reg_str)
            if dataset_id not in registry:
                registry.append(dataset_id)
                self.dataset.update_tags(GLOBATO_PROVENANCE=json.dumps(registry))

    def update(self, points):
        """Process a chunk of points: Bin in memory -> Update Disk."""

        if points is None or len(points) == 0:
            return

        arrays, sub_win, _ = self.pixel_binner(points, mode="sums")
        if arrays is None or arrays.get("z") is None:
            return

        col_off, row_off, width, height = sub_win
        window = Window(col_off, row_off, width, height)

        with self.lock:
            # with rasterio.open(self.sums_fn, 'r+') as dst:
            current_data = self.dataset.read(window=window)

            def get_band(name):
                return current_data[self.BAND_MAP[name] - 1]

            valid_new = arrays["count"] > 0

            # current_data[current_data == -9999] = 0
            # current_data[np.isnan(current_data)] = 0

            # ONLY zero out pixels that are actively receiving new data!
            for i in range(current_data.shape[0]):
                band = current_data[i]
                mask = valid_new & ((band == -9999) | np.isnan(band))
                band[mask] = 0

            if self.mode in ["mean", "weighted_mean"]:
                get_band("z")[valid_new] += arrays["z"][valid_new]

                # Safely fallback for weight keys
                wt_arr = arrays.get("weights", arrays.get("weight", 0))
                get_band("weights")[valid_new] += wt_arr[valid_new]

                get_band("count")[valid_new] += arrays["count"][valid_new]
                get_band("uncertainty")[valid_new] += np.square(
                    arrays["uncertainty"][valid_new]
                )

                if (
                    "src_uncertainty" in arrays
                    and arrays["src_uncertainty"] is not None
                ):
                    get_band("src_uncertainty")[valid_new] += arrays["src_uncertainty"][
                        valid_new
                    ]

                get_band("x")[valid_new] += arrays["x"][valid_new]
                get_band("y")[valid_new] += arrays["y"][valid_new]

            elif self.mode in ["supercede", "mixed"]:
                cur_cnt = get_band("count")
                cur_wt = get_band("weights")

                with np.errstate(divide="ignore", invalid="ignore"):
                    arr_w_avg = np.where(
                        valid_new, arrays["weight"] / arrays["count"], 0
                    )
                    cur_w_avg = np.where(cur_cnt > 0, cur_wt / cur_cnt, 0)

                if self.mode == "supercede":
                    sup_mask = valid_new & (arr_w_avg > cur_w_avg)
                    avg_mask = np.zeros_like(sup_mask, dtype=bool)
                else:
                    arr_tier = np.digitize(arr_w_avg, self.wts)
                    cur_tier = np.digitize(cur_w_avg, self.wts)
                    cur_tier[cur_cnt == 0] = -1

                    sup_mask = valid_new & (arr_tier > cur_tier)
                    avg_mask = valid_new & (arr_tier == cur_tier)

                if np.any(sup_mask):
                    get_band("z")[sup_mask] = arrays["z"][sup_mask]
                    get_band("weights")[sup_mask] = arrays["weight"][sup_mask]
                    get_band("count")[sup_mask] = arrays["count"][sup_mask]
                    get_band("uncertainty")[sup_mask] = np.square(
                        arrays["uncertainty"][sup_mask]
                    )

                    if (
                        "src_uncertainty" in arrays
                        and arrays["src_uncertainty"] is not None
                    ):
                        get_band("src_uncertainty")[sup_mask] = arrays[
                            "src_uncertainty"
                        ][sup_mask]

                    get_band("x")[sup_mask] = arrays["x"][sup_mask]
                    get_band("y")[sup_mask] = arrays["y"][sup_mask]

                if np.any(avg_mask):
                    get_band("z")[avg_mask] += arrays["z"][avg_mask]
                    get_band("weights")[avg_mask] += arrays["weight"][avg_mask]
                    get_band("count")[avg_mask] += arrays["count"][avg_mask]
                    get_band("uncertainty")[avg_mask] += np.square(
                        arrays["uncertainty"][avg_mask]
                    )

                    if (
                        "src_uncertainty" in arrays
                        and arrays["src_uncertainty"] is not None
                    ):
                        get_band("src_uncertainty")[avg_mask] += arrays[
                            "src_uncertainty"
                        ][avg_mask]

                    get_band("x")[avg_mask] += arrays["x"][avg_mask]
                    get_band("y")[avg_mask] += arrays["y"][avg_mask]

            elif self.mode == "min":
                cur_z = get_band("z")
                cur_z[~valid_new & (cur_z == 0)] = 999999
                update_mask = valid_new & (arrays["z"] < cur_z)
                get_band("z")[update_mask] = arrays["z"][update_mask]
                get_band("count")[update_mask] = 1

            elif self.mode == "max":
                cur_z = get_band("z")
                cur_z[~valid_new & (cur_z == 0)] = -999999
                update_mask = valid_new & (arrays["z"] > cur_z)
                get_band("z")[update_mask] = arrays["z"][update_mask]
                get_band("count")[update_mask] = 1

            # # Determine the tier (0, 1, 2) based on the incoming point weights
            # bit_tiers = np.digitize(arrays["weight"], self.wts)
            # bits = 1 << bit_tiers
            # bitmask = get_band("bitmask")[valid_new].astype(np.uint16)
            # bitmask |= bits[valid_new].astype(np.uint16)
            # get_band("bitmask")[valid_new] = bitmask.astype(np.float64)

            # dst.write(current_data, window=window)
            self.dataset.write(current_data, window=window)

    def finalize(self, ndv=-9999):
        """Convert accumulated sums from .sums.tif into the final output .tif."""

        if self.dataset and not self.dataset.closed:
            self.dataset.close()

        if self.verbose:
            logger.debug(
                f"Finalizing Averages: {os.path.basename(self.sums_fn)} -> {os.path.basename(self.output_fn)}"
            )

        with rasterio.open(self.sums_fn, "r") as src:
            profile = src.profile.copy()
            profile["dtype"] = "float32"

            with rasterio.open(self.output_fn, "w", **profile) as dst:
                dst.colorinterp = [ColorInterp.undefined] * dst.count
                for _, window in src.block_windows(1):
                    data = src.read(window=window)

                    z = data[self.BAND_MAP["z"] - 1]
                    cnt = data[self.BAND_MAP["count"] - 1]
                    w = data[self.BAND_MAP["weights"] - 1]
                    unc = data[self.BAND_MAP["uncertainty"] - 1]
                    src_u = data[self.BAND_MAP["src_uncertainty"] - 1]
                    x = data[self.BAND_MAP["x"] - 1]
                    y = data[self.BAND_MAP["y"] - 1]

                    valid = cnt > 0
                    data[:, ~valid] = ndv

                    if self.mode in ["mean", "weighted_mean", "mixed", "supercede"]:
                        with np.errstate(divide="ignore", invalid="ignore"):
                            z[valid] = z[valid] / w[valid]
                            x[valid] = x[valid] / w[valid]
                            y[valid] = y[valid] / w[valid]
                            src_u[valid] = src_u[valid] / w[valid]
                            unc[valid] = np.sqrt(unc[valid]) / cnt[valid]
                            w[valid] = w[valid] / cnt[valid]

                    data[np.isinf(data)] = ndv
                    data[np.isnan(data)] = ndv

                    dst.write(data.astype("float32"), window=window)

                # Copy the provenance registry over to the final file!
                dst.update_tags(**src.tags())
                dst.update_tags(GLOBATO_DATATYPE="MULTI_STACK", VERSION=__version__)

        # Generate Statistics on the Finalized TIF
        with rasterio.open(self.output_fn, "r+") as dst:
            all_stats = dst.stats(approx=False)
            for i, stats in enumerate(all_stats):
                idx = i + 1  # Bands are 1-indexed

                desc = [k for k, v in self.BAND_MAP.items() if v == idx][0]
                dst.update_tags(
                    bidx=idx,
                    STATISTICS_MINIMUM=str(stats.min),
                    STATISTICS_MAXIMUM=str(stats.max),
                    STATISTICS_MEAN=str(stats.mean),
                    STATISTICS_STDDEV=str(stats.std),
                    DESCRIPTION=desc,
                )

        return self.output_fn


# MULTI_STACK HOOK
class MultiStackHook(FetchHook):
    """Multi-Stack Gridding Hook.

    accumulates streaming data into a multi-band statistical grid.
    Maintains a continuous .sums.tif to prevent duplication.
    """

    name = "multi_stack"
    meta_stage = "stream"
    meta_category = "stream-sink"
    meta_aliases = ["multi-stack"]

    def __init__(
        self,
        res="1s",
        output="multi_stack_output.tif",
        mode="mean",
        weight_threshold="1",
        crs=None,
        drop_classes=None,
        overwrite=False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.res = res
        self.output = output
        self.mode = mode.lower()
        self.weight_threshold = weight_threshold
        self.crs = crs
        self._accumulator = None
        self.drop_classes = (
            [int(x) for x in str(drop_classes).split("/")] if drop_classes else []
        )
        self.overwrite = overwrite

    def _init_accumulator(self, region):
        if self._accumulator:
            return

        if isinstance(self.res, str) and self.res.endswith("s"):
            inc = float(self.res[:-1]) / 3600.0
            x_inc, y_inc = inc, inc
        elif "/" in str(self.res):
            x_inc, y_inc = map(float, self.res.split("/"))
        else:
            inc = float(self.res)
            x_inc, y_inc = inc, inc

        logger.info(
            f"Initializing Multi_Stack: {self.output} @ {x_inc},{y_inc} ({self.mode})"
        )
        self._accumulator = MultiStackAccumulator(
            region=region,
            x_inc=x_inc,
            y_inc=y_inc,
            output_fn=self.output,
            mode=self.mode,
            weight_threshold=self.weight_threshold,
            crs=self.crs,
            verbose=True,
            overwrite=self.overwrite,
        )

    def run(self, entries):
        if not self._accumulator:
            region = next(
                (getattr(mod, "original_region", mod.region) for mod, _ in entries if hasattr(mod, "region")), None
            )
            # (mod.region for mod, _ in entries if getattr(mod, "region", None)), None
            if region:
                region_str = region.format("fn")
                base, ext = os.path.splitext(self.output)

                if region_str not in base:
                    self.output = f"{base}_{region_str}{ext}"
                self._init_accumulator(region)
            else:
                return entries

        for mod, entry in entries:
            dataset_id = entry.get("checksum")

            if not dataset_id:
                url = entry.get("url", "")
                dst_fn = entry.get("dst_fn")

                if url and not url.startswith("file://"):
                    dataset_id = url

                elif dst_fn and os.path.exists(dst_fn):
                    size = os.path.getsize(dst_fn)
                    dataset_id = f"{os.path.basename(dst_fn)}|{size}B"

                else:
                    dataset_id = os.path.basename(dst_fn or url or "unknown_dataset")

            if self._accumulator and self._accumulator.is_registered(dataset_id):
                logger.debug(f"Dataset '{dataset_id}' already inside stack. Skipping.")
                entry.pop("stream", None)
                entry.pop("raster_stream", None)
            else:
                if self.has_stream(entry):
                    stream = entry.get("stream")
                    entry["stream"] = self._intercept(stream, dataset_id)

            entry.setdefault("artifacts", {})[self.name] = self.output

        return entries

    def _intercept(self, stream, dataset_id):
        """Generator wrapper to feed the accumulator and mark registry."""

        count = 0
        z_min, z_max = float("inf"), float("-inf")
        w_min, w_max = float("inf"), float("-inf")
        u_min, u_max = float("inf"), float("-inf")

        dataset_str = format_dataset_id(dataset_id)
        logger.debug(f"Streaming data from: {dataset_str}")
        for chunk in stream:
            if isinstance(chunk, tuple) and len(chunk) >= 3:
                # Raster stream chunk: (window, buff_win, data, ndv, transform)
                data = chunk[2]
                ndv = chunk[3] if len(chunk) > 3 else -9999
                z_data = data[0] if data.ndim == 3 else data
                valid_mask = (z_data != ndv) & ~np.isnan(z_data)
                valid_z = z_data[valid_mask]
                count += valid_z.size

            elif isinstance(chunk, np.ndarray) and "z" in chunk.dtype.names:
                if self.drop_classes and "classification" in chunk.dtype.names:
                    keep_mask = ~np.isin(chunk["classification"], self.drop_classes)
                    chunk = chunk[keep_mask]

                    if len(chunk) == 0:
                        continue

                # Point rec-array stream chunk
                valid_z = chunk["z"][~np.isnan(chunk["z"])]
                count += len(chunk)

                # Track W
                if "w" in chunk.dtype.names:
                    valid_w = chunk["w"][~np.isnan(chunk["w"])]
                    if valid_w.size > 0:
                        w_min = min(w_min, float(np.min(valid_w)))
                        w_max = max(w_max, float(np.max(valid_w)))

                # Track U
                if "u" in chunk.dtype.names:
                    valid_u = chunk["u"][~np.isnan(chunk["u"])]
                    if valid_u.size > 0:
                        u_min = min(u_min, float(np.min(valid_u)))
                        u_max = max(u_max, float(np.max(valid_u)))
            else:
                valid_z = np.array([])
                count += len(chunk)

            if valid_z.size > 0:
                z_min = min(z_min, float(np.min(valid_z)))
                z_max = max(z_max, float(np.max(valid_z)))

                # check for valid w/u?
                w_min = min(w_min, float(np.min(valid_w)))
                w_max = max(w_max, float(np.max(valid_w)))

                u_min = min(u_min, float(np.min(valid_u)))
                u_max = max(u_max, float(np.max(valid_u)))

            if self._accumulator:
                self._accumulator.update(chunk)
            yield chunk

        if z_min == float("inf") or z_max == float("-inf"):
            # z_str = "No valid Z data"
            stats_str = "No valid Z data"
        else:
            # if abs(z_min) > 1e10 or abs(z_max) > 1e10:
            #     z_str = f"Z: [{z_min:.2e} to {z_max:.2e}]"
            # else:
            #     z_str = f"Z: [{z_min:,.2f} to {z_max:,.2f}]"

            # Format Z
            if abs(z_min) > 1e10 or abs(z_max) > 1e10:
                z_str = f"Z: [{z_min:.2e} to {z_max:.2e}]"
            else:
                z_str = f"Z: [{z_min:,.2f} to {z_max:,.2f}]"

            # Format W
            w_str = ""
            if w_min != float("inf"):
                if w_min == w_max:
                    w_str = f" | W: [{w_min:.2f}]"
                else:
                    w_str = f" | W: [{w_min:.2f} to {w_max:.2f}]"

            # Format U
            u_str = ""
            if u_min != float("inf"):
                if u_min == u_max:
                    u_str = f" | U: [{u_min:.2f}]"
                else:
                    u_str = f" | U: [{u_min:.2f} to {u_max:.2f}]"

            stats_str = f"{z_str}{w_str}{u_str}"

        # logger_str = f"Integrated {colorize(f'{count:,}', BOLD)} valid points {colorize(f'({z_str})', CYAN)} from {colorize(dataset_str, BLUE)} into stack"
        pts_str = colorize(f"{count:,}", BOLD) + " pts"
        ds_str = colorize(dataset_str, BLUE)
        st_str = colorize(f"({stats_str})", CYAN)

        logger_str = f"Stacked {ds_str} -> {pts_str} {st_str}"
        # logger_str = f"Integrated {colorize(f'{count:,}', BOLD)} valid points {colorize(f'({stats_str})', CYAN)} from {colorize(dataset_str, BLUE)} into stack"
        logger.info(logger_str)

        # The stream is exhausted; permanently mark this dataset as completed
        if self._accumulator and dataset_id:
            self._accumulator.mark_registered(dataset_id)

    def teardown(self):
        """Finalize the grid after all streams are exhausted."""

        if self._accumulator:
            logger.debug("Streams finished. Finalizing averages...")
            self._accumulator.finalize()
            self._accumulator = None
