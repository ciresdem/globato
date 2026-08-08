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

from fetchez.utils import str_or
from .base import BaseGlobatoReader

logger = logging.getLogger(__name__)


class LASReader(BaseGlobatoReader):
    """Process LAS/LAZ lidar files using laspy."""

    name = "lidar-point-reader"
    meta_category = "point-stream"
    meta_dtype = "lidar"
    meta_desc = "Read lidar data through laspy into a point stream"
    meta_extensions = ["las", "laz"]

    def __init__(
        self,
        path: str,
        classes="2/29/40",
        **kwargs,
    ):
        super().__init__(path, **kwargs)
        self.src_fn = path
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
                    # Record ID 2112 is "OGC Coordinate System WKT"
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
                if self.region is not None:
                    w, e, s, n = self.region
                    las_srs = self.get_srs()

                    # If the LAS file has an SRS, transform our 4326 region bounds to match it
                    if las_srs and las_srs != "EPSG:4326":
                        try:
                            w, s, e, n = transform_bounds(
                                "EPSG:4326", las_srs, w, s, e, n
                            )
                        except Exception:
                            pass

                    # Native LAS bounds
                    las_w, las_s = lasf.header.x_min, lasf.header.y_min
                    las_e, las_n = lasf.header.x_max, lasf.header.y_max

                    # If the file entirely misses the region, abort
                    if las_w > e or las_e < w or las_s > n or las_n < s:
                        logger.debug(
                            f"Skipping {self.src_fn}: Bounding box falls outside requested region."
                        )
                        return

                for chunk in lasf.chunk_iterator(2_000_000):
                    if self.classes:
                        mask = np.isin(chunk.classification, self.classes)
                        points_x = chunk.x[mask]
                        points_y = chunk.y[mask]
                        points_z = chunk.z[mask]
                    else:
                        points_x = chunk.x
                        points_y = chunk.y
                        points_z = chunk.z

                    count = len(points_x)
                    if count == 0:
                        continue

                    points = np.zeros(
                        count,
                        dtype=[
                            ("x", "f8"),
                            ("y", "f8"),
                            ("z", "f4"),
                            ("w", "f4"),
                            ("u", "f4"),
                        ],
                    )

                    points["x"] = points_x
                    points["y"] = points_y
                    points["z"] = points_z
                    points["w"] = 1.0
                    points["u"] = 0.0

                    yield points

        except Exception as e:
            logger.error(f"LAS/Z processing failed for {self.src_fn}: {e}")
            return None
