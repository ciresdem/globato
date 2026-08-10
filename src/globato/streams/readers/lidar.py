#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.streams.readers.lidar
~~~~~~~~~~~~~

This readers lidar to a point stream.

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import logging
import numpy as np
import laspy as lp
from rasterio.warp import transform_bounds

from fetchez.utils import str_or, int_or, str2bool
from .base import BaseGlobatoReader

logger = logging.getLogger(__name__)


class LASReader(BaseGlobatoReader):
    """Process LAS/LAZ and COPC lidar files using laspy."""

    name = "lidar-point-reader"
    meta_category = "point-stream"
    meta_dtype = "lidar"
    meta_desc = "Read lidar data through laspy into a point stream"
    meta_extensions = ["las", "laz"]

    def __init__(
        self,
        path: str,
        classes="2/29/40",
        chunk_size=1000000,
        in_memory=False,
        **kwargs,
    ):
        super().__init__(path, **kwargs)
        self.src_fn = path
        self.chunk_size = int_or(chunk_size, 1000000)
        self.in_memory = str2bool(in_memory)

        try:
            if isinstance(str_or(classes), str):
                self.classes = [int(x) for x in str(classes).split("/")]
            elif isinstance(classes, (list, tuple)):
                self.classes = [int(x) for x in classes]
            else:
                self.classes = []
        except Exception:
            self.classes = []

    def get_srs(self):
        """Attempt to parse EPSG/WKT from LAS Header using laspy."""

        try:
            with lp.open(self.src_fn) as lasf:
                try:
                    crs = lasf.header.parse_crs()
                    if crs is not None:
                        return crs.to_wkt()
                except Exception:
                    pass

                # Manual VLR check
                for vlr in lasf.header.vlrs:
                    if vlr.record_id == 2112:
                        try:
                            srs = vlr.string
                            if isinstance(srs, bytes):
                                return srs.decode("utf-8").strip("\0")
                            return srs
                        except Exception:
                            pass
        except Exception:
            pass

        return None

    def _yield_raw_chunks(self):
        """Yield points from local file using standard laspy."""

        try:
            with lp.open(self.src_fn) as lasf:
                is_copc = hasattr(lasf, "query")

                if self.src_fn.lower().endswith(".copc.laz") and not is_copc:
                    logger.debug(
                        f"File {self.src_fn} is named .copc.laz but lacks COPC structural VLRs. "
                        "Falling back to standard chunked LAZ reading."
                    )

                w, e, s, n = -np.inf, np.inf, -np.inf, np.inf

                if self.region is not None:
                    w, e, s, n = self.region
                    las_srs = self.get_srs()

                    # Transform region to match LAS SRS
                    if las_srs and las_srs != "EPSG:4326":
                        try:
                            w, s, e, n = transform_bounds(
                                "EPSG:4326", las_srs, w, s, e, n
                            )
                        except Exception:
                            pass

                    las_w, las_s = lasf.header.x_min, lasf.header.y_min
                    las_e, las_n = lasf.header.x_max, lasf.header.y_max

                    if las_w > e or las_e < w or las_s > n or las_n < s:
                        logger.debug(
                            f"Skipping {self.src_fn}: Bounding box falls outside requested region."
                        )
                        return

                # If in_memory is true, just read in the whole file at once.
                if self.in_memory and not is_copc:
                    logger.debug(f"Reading {self.src_fn} using in-memory mode...")
                    chunk_iter = [lasf.read()]
                elif is_copc and self.region is not None:
                    mins = np.array([w, s])
                    maxs = np.array([e, n])
                    logger.debug(f"Using COPC spatial query for {self.src_fn}")
                    try:
                        chunk_iter = lasf.query(mins, maxs)
                    except Exception as e:
                        logger.debug(
                            f"Copc query failed; falling back to standard chunking: {e}"
                        )
                        chunk_iter = lasf.chunk_iterator(self.chunk_size)
                else:
                    chunk_iter = lasf.chunk_iterator(self.chunk_size)

                full_dtype = [
                    ("x", "f8"),
                    ("y", "f8"),
                    ("z", "f4"),
                    ("w", "f4"),
                    ("u", "f4"),
                    ("classification", "u1"),
                    ("confidence", "i2"),
                ]

                for chunk in chunk_iter:
                    if len(chunk) == 0:
                        continue

                    if self.classes:
                        class_mask = np.isin(chunk.classification, self.classes)
                        if not np.any(class_mask):
                            continue
                        chunk = chunk[class_mask]

                    if self.region is not None and not is_copc:
                        x_vals = chunk.x
                        y_vals = chunk.y
                        spatial_mask = (
                            (x_vals >= w)
                            & (x_vals <= e)
                            & (y_vals >= s)
                            & (y_vals <= n)
                        )
                        if not np.any(spatial_mask):
                            continue
                        chunk = chunk[spatial_mask]

                    count = len(chunk)
                    if count == 0:
                        continue

                    points = np.empty(count, dtype=full_dtype)
                    points["x"] = chunk.x
                    points["y"] = chunk.y
                    points["z"] = chunk.z
                    points["classification"] = chunk.classification
                    points["w"] = 1.0
                    points["u"] = 0.0
                    points["confidence"] = 1

                    if self.in_memory and count > self.chunk_size:
                        for i in range(0, count, self.chunk_size):
                            yield points[i : i + self.chunk_size]
                    else:
                        yield points

        except Exception as e:
            logger.error(f"LAS/Z processing failed for {self.src_fn}: {e}")
            return None
