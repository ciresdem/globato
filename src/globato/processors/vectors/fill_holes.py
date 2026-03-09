#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.processors.vectors.vector_fill_holes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Removes interior rings (holes/ponds) from polygon vectors.
Perfect for solidifying landmasks and delineating continuous ocean boundaries.

:copyright: (c) 2016 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import logging
import fiona
from shapely.geometry import shape, mapping, Polygon, MultiPolygon
from fetchez.hooks import FetchHook

logger = logging.getLogger(__name__)

class VectorFillHoles(FetchHook):
    """Removes interior holes from vector polygons.

    Args:
        min_area (float): Minimum area a hole must be to be kept.
                          Defaults to 0.0 (removes ALL holes, creating solid landmasses).
                          Note: Area is calculated in the native CRS units (e.g., square degrees or meters).
        suffix (str): Suffix for the output file.
    """

    name = "vector_fill_holes"
    meta_stage = "post"
    meta_category = "vector-op"

    def __init__(self, min_area=0.0, suffix="_filled", **kwargs):
        super().__init__(**kwargs)
        self.min_area = float(min_area)
        self.suffix = suffix

    def _remove_holes(self, geom):
        """Recursively removes small interior rings from Polygons/MultiPolygons."""
        if geom.geom_type == 'Polygon':

            if self.min_area <= 0.0:
                return Polygon(geom.exterior)

            new_interiors = []
            for ring in geom.interiors:
                ring_poly = Polygon(ring)
                if ring_poly.area > self.min_area:
                    new_interiors.append(ring)

            return Polygon(geom.exterior, new_interiors)

        elif geom.geom_type == 'MultiPolygon':
            new_parts = []
            for part in geom.geoms:
                new_parts.append(self._remove_holes(part))
            return MultiPolygon(new_parts)

        return geom

    def run(self, entries):
        new_entries = []

        for mod, entry in entries:
            src_fn = entry.get("dst_fn")

            if not src_fn or not src_fn.lower().endswith((".gpkg", ".shp", ".geojson")):
                new_entries.append((mod, entry))
                continue

            base, ext = os.path.splitext(src_fn)
            dst_fn = f"{base}{self.suffix}{ext}"

            logger.info(f"[{self.name}] Filling inland holes in {os.path.basename(src_fn)}...")

            try:
                with fiona.open(src_fn, "r") as src:
                    profile = src.profile

                    with fiona.open(dst_fn, "w", **profile) as dst:
                        for feature in src:
                            if not feature.get("geometry"):
                                dst.write(feature)
                                continue

                            geom = shape(feature["geometry"])

                            filled_geom = self._remove_holes(geom)

                            new_feature = {
                                "type": "Feature",
                                "properties": dict(feature["properties"]),
                                "geometry": mapping(filled_geom)
                            }

                            dst.write(new_feature)

                entry["src_fn"] = src_fn
                entry["dst_fn"] = dst_fn
                entry.setdefault("artifacts", {})[self.name] = dst_fn

            except Exception as e:
                logger.error(f"[{self.name}] Failed to fill holes: {e}")

            new_entries.append((mod, entry))

        return new_entries
