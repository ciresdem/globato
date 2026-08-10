#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.streams.readers.pyogrio
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Pyogrio/shapely vector reader

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import logging
import numpy as np
from fetchez.utils import float_or

import pyogrio
import shapely
import pandas as pd

from .base import BaseGlobatoReader

logger = logging.getLogger(__name__)


class PyogrioReader(BaseGlobatoReader):
    """Vectorized Pyogrio Vector Parser for lightning-fast point extraction."""

    name = "pyogrio-point-reader"
    meta_category = "point-stream"
    meta_dtype = "pyogrio-vector"
    meta_desc = "Read vector data through pyogrio into a point stream"
    meta_extensions = ["shp", "000", "json", "geojson", "kml", "gdb"]

    KNOWN_LAYERS = [
        "SOUNDG",
        "SurveyPoint_HD",
        "SurveyPoint",
        "Mass_Point",
        "Spot_Elevation",
    ]
    KNOWN_Z_FIELDS = [
        "VALSOU",
        "Elevation",
        "elev",
        "z",
        "depth",
        "height",
        "value",
        "Z_use",
        "Z_depth",
    ]

    def __init__(
        self,
        path,
        layer=None,
        z_field=None,
        weight_field=None,
        unc_field=None,
        z_scale=1.0,
        elevation_value=None,
        chunk_size=50000,
        **kwargs,
    ):
        super().__init__(path, **kwargs)
        self.src_fn = path

        # Pyogrio handles zip:// paths perfectly, just like Fiona
        if self.src_fn.lower().endswith(".zip"):
            import zipfile

            internal_target = ""
            try:
                with zipfile.ZipFile(self.src_fn, "r") as z:
                    for name in z.namelist():
                        if name.lower().endswith(".gdb/") or name.lower().endswith(
                            ".gdb"
                        ):
                            internal_target = f"!{name.strip('/')}"
                            break
                        elif name.lower().endswith(".shp"):
                            internal_target = f"!{name}"
            except Exception as e:
                logger.debug(f"Could not inspect zip file: {e}")
            self.src_fn = f"zip://{self.src_fn}{internal_target}"

        self.target_layer = layer
        self.z_field = z_field
        self.weight_field = weight_field
        self.unc_field = unc_field
        self.z_scale = float_or(z_scale, 1.0)
        self.elevation_value = float_or(elevation_value)
        self.chunk_size = chunk_size

    def get_srs(self):
        """Extracts the Coordinate Reference System (SRS) from the vector file."""

        try:
            layer_name = self._resolve_layer()

            # read_info returns a dictionary of metadata, including the CRS string
            # (e.g., 'EPSG:4326' or a full WKT projection string)
            info = pyogrio.read_info(self.src_fn, layer=layer_name)
            crs_val = info.get("crs")

            if crs_val:
                # Some versions of GDAL/Pyogrio might return pyproj objects or dicts,
                # so ensuring it's cast or formatted correctly is safe:
                return str(crs_val)

            return None

        except Exception as e:
            logger.debug(f"Could not extract SRS from {self.src_fn}: {e}")
            return None

    def _resolve_layer(self):
        layers = pyogrio.list_layers(self.src_fn)
        layer_names = [lyr[0] for lyr in layers]

        if self.target_layer and self.target_layer in layer_names:
            return self.target_layer

        for name in self.KNOWN_LAYERS:
            if name in layer_names:
                logger.debug(f"Auto-detected vector layer: {name}")
                return name
        return layer_names[0]

    def _resolve_z_field(self, columns):
        if self.z_field:
            return self.z_field
        for f in self.KNOWN_Z_FIELDS:
            if f in columns:
                return f
        return None

    def _yield_raw_chunks(self):
        try:
            layer_name = self._resolve_layer()
            skip = 0

            while True:
                chunk_gdf = pyogrio.read_dataframe(
                    self.src_fn,
                    layer=layer_name,
                    max_features=self.chunk_size,
                    skip_features=skip,
                )

                if chunk_gdf.empty:
                    break

                coords = shapely.get_coordinates(chunk_gdf.geometry, include_z=True)
                x = coords[:, 0]
                y = coords[:, 1]

                counts = shapely.get_num_coordinates(chunk_gdf.geometry)

                # Z
                z_attr_name = self._resolve_z_field(chunk_gdf.columns)
                if z_attr_name:
                    z_series = pd.to_numeric(chunk_gdf[z_attr_name], errors="coerce")
                    z_raw = z_series.fillna(self.elevation_value or 0.0).values
                    z = np.repeat(z_raw, counts)
                elif coords.shape[1] == 3:
                    z = coords[:, 2]
                else:
                    z = np.full(len(x), self.elevation_value or 0.0)

                z = z * self.z_scale

                # Weight
                if self.weight_field and self.weight_field in chunk_gdf.columns:
                    w_raw = chunk_gdf[self.weight_field].fillna(1.0).values
                    w = np.repeat(w_raw, counts)
                else:
                    w = np.ones(len(x))

                # Uncertainty
                if self.unc_field and self.unc_field in chunk_gdf.columns:
                    u_raw = chunk_gdf[self.unc_field].fillna(0.0).values
                    u = np.repeat(u_raw, counts)
                else:
                    u = np.zeros(len(x))

                yield self._pack(x, y, z, w, u)

                if len(chunk_gdf) < self.chunk_size:
                    break

                skip += self.chunk_size

        except Exception as e:
            logger.exception(f"Pyogrio processing failed for {self.src_fn}: {e}")

    def _pack(self, x, y, z, w, u):
        dt = [("x", "f8"), ("y", "f8"), ("z", "f4"), ("w", "f4"), ("u", "f4")]
        data = [np.array(x), np.array(y), np.array(z), np.array(w), np.array(u)]
        return np.rec.fromarrays(data, dtype=dt)
