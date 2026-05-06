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

from fetchez.utils import str_or
from globato.streams import BaseGlobatoReader

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

                    if len(points_x) == 0:
                        continue

                    w = np.ones_like(points_z)
                    u = np.zeros_like(points_z)

                    dataset = np.column_stack((points_x, points_y, points_z, w, u))
                    points = np.rec.fromrecords(dataset, names="x, y, z, w, u")
                    yield points
        except Exception as e:
            logger.error(f"LAS/Z processing failed for {self.src_fn}: {e}")
            return None
