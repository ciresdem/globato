#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.modules.osm_landmask
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Super-Module that generates a high-quality Coastline Mask.
Merges Vectors (NHD, OSM) and Rasters (Copernicus, GMRT) into a unified product using weighted voting.
"""

import os
import logging
import json
import math
import fiona
from shapely.geometry import box, LineString, Point, mapping
from shapely.ops import linemerge, unary_union

from fetchez.modules import FetchModule
from fetchez.core import Fetch, urlencode, CUDEM_USER_AGENT
from fetchez.cli import cli_opts

try:
    from fetchez.modules.gmrt import gmrt_fetch_point
except ImportError:
    gmrt_fetch_point = None

logger = logging.getLogger(__name__)

OSM_API = "https://lz4.overpass-api.de/api/interpreter"

HEADERS = {
    "User-Agent": CUDEM_USER_AGENT,
    "Content-Type": "application/x-www-form-urlencoded",
}


@cli_opts(
    help_text="Generates a Land/Water mask vector from OpenStreetMap.",
    include_water="If True, carves out inland lakes and rivers from the landmask.",
)
class OSMLandmaskModule(FetchModule):
    """Fetches OSM Coastline data and polygonizes it into a landmask."""

    name = "osm_landmask"
    meta_desc = "OpenStreetMap Coastline and Waterbody Generator"
    meta_agency = "OSM"
    meta_category = "Reference"
    meta_tags = ["osm", "coastline", "water", "polygons", "globato"]

    def __init__(self, include_water=False, **kwargs):
        super().__init__(name="osm_landmask", **kwargs)
        self.include_water = str(include_water).lower() in ["true", "1", "t", "yes"]

        self.headers = HEADERS

    def run(self):
        if not self.region:
            logger.error(f"[{self.name}] Requires a bounding box region to run.")
            return

        w, e, s, n = self.region
        out_name = f"osm_landmask_{w}_{s}.geojson"
        out_path = os.path.join(self._outdir, out_name)

        if os.path.exists(out_path):
            logger.info(f"[OSM] Using existing landmask: {out_path}")
            self.add_entry_to_results(f"file://{out_path}", out_path, "osm_landmask")
            return self

        logger.info(f"[OSM] Fetching coastline for {self.region}...")
        osm_xml = self._fetch_osm(self.region)

        if not osm_xml or os.path.getsize(osm_xml) < 100:
            self._handle_fallback(out_path, self.region)
        else:
            logger.info("[OSM] Polygonizing and classifying coastline...")
            try:
                self._polygonize(osm_xml, out_path, self.region)
                logger.info(f"[OSM] Generated landmask: {out_path}")
            except Exception as e:
                logger.error(f"[OSM] Polygonization failed: {e}")
                if not os.path.exists(out_path):
                    self._handle_fallback(out_path, self.region)

        if osm_xml and os.path.exists(osm_xml):
            os.remove(osm_xml)

        if os.path.exists(out_path):
            self.add_entry_to_results(f"file://{out_path}", out_path, "osm_landmask")

        return self

    def _fetch_osm(self, region):
        w, e, s, n = region
        bbox = f"{s},{w},{n},{e}"
        query = f"""
        [timeout:120][out:json][bbox:{bbox}];
        (
          way["natural"="coastline"];
          relation["natural"="coastline"];
        """
        if self.include_water:
            query += """
          way["natural"="water"];
          relation["natural"="water"];
          way["waterway"="riverbank"];
          relation["waterway"="riverbank"];
            """
        query += """
        );
        (._;>;);
        out geom;
        """

        params = urlencode({"data": query})
        url = f"{OSM_API}?{params}"
        dest = os.path.join(self._outdir, f"temp_osm_{w}_{s}.json")
        f = Fetch(url, headers=HEADERS)
        if f.fetch_file(dest, method="POST", verbose=False) == 0:
            return dest
        return None

    def _is_land_by_topology(self, poly, lines_geom, buffer_size):
        """Determine if polygon is Land using the OSM Left-Hand Rule."""

        check_poly = poly.buffer(buffer_size * 2.0)

        if not check_poly.intersects(lines_geom):
            return None  # Indeterminate

        if lines_geom.geom_type == "MultiLineString":
            geoms = list(lines_geom.geoms)
        else:
            geoms = [lines_geom]

        votes = []
        for line in geoms:
            if not check_poly.intersects(line):
                continue

            coords = list(line.coords)
            step = max(1, int(len(coords) / 5))

            for i in range(0, len(coords) - 1, step):
                p1 = coords[i]
                p2 = coords[i + 1]
                dx, dy = p2[0] - p1[0], p2[1] - p1[1]

                # Normal Vector pointing LEFT (-dy, dx)
                nx, ny = -dy, dx
                mag = math.sqrt(nx * nx + ny * ny)
                if mag == 0:
                    continue

                scale = buffer_size * 4.0
                test_pt = Point(
                    p1[0] + dx * 0.5 + (nx / mag) * scale,
                    p1[1] + dy * 0.5 + (ny / mag) * scale,
                )

                if poly.contains(test_pt):
                    votes.append(True)
                else:
                    votes.append(False)

        if not votes:
            return None

        return (sum(votes) / len(votes)) > 0.5

    def _is_land_by_gmrt(self, poly):
        """Fallback: Check GMRT elevation."""

        if not gmrt_fetch_point:
            return False
        try:
            pt = poly.centroid
            val = float(gmrt_fetch_point(latitude=pt.y, longitude=pt.x))
            return val >= 0
        except Exception:
            return False

    def _handle_fallback(self, dst_file, region):
        """If OSM fails, guess whole tile based on center point."""

        w, e, s, n = region
        cx, cy = (w + e) / 2, (s + n) / 2
        is_land = False
        if gmrt_fetch_point:
            try:
                is_land = float(gmrt_fetch_point(latitude=cy, longitude=cx)) >= 0
            except Exception:
                pass

        poly = box(w, s, e, n) if is_land else None
        self._write_geojson(dst_file, [poly] if poly else [])

    def _write_geojson(self, dst_file, polygons):
        schema = {"geometry": "Polygon", "properties": {"class": "str"}}
        with fiona.open(
            dst_file, "w", driver="GeoJSON", crs="EPSG:4326", schema=schema
        ) as dst:
            for poly in polygons:
                dst.write({"geometry": mapping(poly), "properties": {"class": "land"}})

    def _polygonize(self, osm_file, dst_file, region):
        """Polygonize the osm data"""

        lines = []

        try:
            with open(osm_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for element in data.get("elements", []):
                # 'out geom' embeds the lat/lon directly into the way elements!
                if element.get("type") == "way" and "geometry" in element:
                    coords = [(pt["lon"], pt["lat"]) for pt in element["geometry"]]
                    if len(coords) >= 2:
                        lines.append(LineString(coords))

        except Exception as e:
            logger.error(f"Failed to parse OSM JSON natively: {e}")
            self._handle_fallback(dst_file, region)
            return

        if not lines:
            self._handle_fallback(dst_file, region)
            return

        merged = linemerge(lines)
        coastline_geom = unary_union(merged)

        w, e, s, n = region
        region_box = box(w, s, e, n)

        cut_width = 1e-6
        cutters = coastline_geom.buffer(cut_width)

        try:
            split_geom = region_box.difference(cutters)
        except Exception:
            self._handle_fallback(dst_file, region)
            return

        land_polys = []
        polys = (
            [split_geom]
            if split_geom.geom_type == "Polygon"
            else list(split_geom.geoms)
        )

        for poly in polys:
            if poly.is_empty:
                continue

            is_land = self._is_land_by_topology(poly, coastline_geom, cut_width)

            if is_land is None:
                is_land = self._is_land_by_gmrt(poly)

            if is_land:
                land_polys.append(poly)

        self._write_geojson(dst_file, land_polys)
