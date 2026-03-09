#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.processors.rasters.clip
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Clip a raster to a vector

:copyright: (c) 2016 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import logging
import fiona
import numpy as np
from rasterio.features import rasterize

from .base import RasterHook

logger = logging.getLogger(__name__)


class RasterClipHook(RasterHook):
    """Clips a raster array using vector geometries."""

    name = "raster_clip"
    default_suffix = "_clipped"
    meta_stage = "file"

    def __init__(self, invert=False, **kwargs):
        super().__init__(**kwargs)

        self.invert = str(invert).lower() == "true"
        #self.clip_geoms = None

    def process_chunk(self, data, ndv, entry, transform=None, window=None):
        """Process individual windows/chunks passed by RasterHook."""

        if not self.barrier_geoms:
            return data

        #out_shape = data.shape[-2:] if data.ndim >= 2 else data.shape

        geom_mask = rasterize(
            self.barrier_geoms,
            out_shape=data.shape,
            transform=transform,
            fill=0,
            default_value=1,
            dtype='uint8'
        ).astype(bool)

        # if data.ndim == 3:
        #     geom_mask = np.broadcast_to(geom_mask, data.shape)

        if self.invert:
            # Set pixels INSIDE the polygons to nodata
            clipped_data = np.where(~geom_mask, data, ndv)
        else:
            # Set pixels OUTSIDE the polygons to nodata
            clipped_data = np.where(geom_mask, data, ndv)

        return clipped_data
