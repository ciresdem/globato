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
import numpy as np
import fiona
from pyogrio.raw import write
import shapely
from shapely.geometry import box, LineString, Point, Polygon, mapping
from shapely.ops import linemerge, unary_union, polygonize

from fetchez.modules import FetchModule
from fetchez.core import Fetch, urlencode, CUDEM_USER_AGENT
from fetchez.cli import cli_opts
from fetchez.utils import str2bool

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
    include_water="Macro flag to include both lakes and rivers.",
    include_rivers="If True, carves out rivers and estuaries (waterway=riverbank).",
    include_lakes="If True, carves out inland lakes and ponds (natural=water).",
    include_reefs="If True, returns reefs (natural=reef) as land.",
    include_wetlands="If True, carves out tidal flats, salt marshes, and estuaries.",
    include_breakwaters="If True, returns man-made breakwaters, piers, and groynes as land.",
    min_area_sqm="Minimum area in square meters for a waterbody to be carved out.",
)
class OSMLandmaskModule(FetchModule):
    """Fetches OSM Coastline data and polygonizes it into a landmask."""

    name = "osm_landmask"
    meta_desc = "OpenStreetMap Coastline and Waterbody Generator"
    meta_agency = "OSM"
    meta_category = "Reference"
    meta_tags = ["osm", "coastline", "water", "polygons", "globato"]

    def __init__(
        self,
        include_water=False,
        include_rivers=None,
        include_lakes=None,
        include_reefs=False,
        include_wetlands=False,
        include_breakwaters=False,
        min_area_sqm=0,
        **kwargs,
    ):
        super().__init__(name="osm_landmask", **kwargs)

        self.include_water = str2bool(str(include_water))
        if include_rivers is None:
            self.include_rivers = self.include_water
        else:
            self.include_rivers = str2bool(str(include_rivers))

        if include_lakes is None:
            self.include_lakes = self.include_water
        else:
            self.include_lakes = str2bool(str(include_lakes))

        self.include_reefs = str2bool(str(include_reefs))
        self.include_wetlands = str2bool(str(include_wetlands))
        self.include_breakwaters = str2bool(str(include_breakwaters))

        self.min_area_sqm = float(min_area_sqm)
        self.headers = HEADERS

    def _get_area_sqm(self, poly):
        """Approximates the area of a WGS84 polygon in square meters."""

        lat = poly.centroid.y
        # ~111,320 meters per degree of latitude
        deg_to_m_y = 111320.0
        # Longitude scaling based on latitude
        deg_to_m_x = 111320.0 * math.cos(math.radians(lat))

        return poly.area * deg_to_m_x * deg_to_m_y

    def _get_filename_suffix(self):
        """Generates a unique string based on the active inclusion flags."""

        parts = []
        if self.include_rivers:
            parts.append("r")
        if self.include_lakes:
            parts.append("l")
        if self.include_wetlands:
            parts.append("w")
        if self.include_reefs:
            parts.append("rf")
        if self.include_breakwaters:
            parts.append("b")

        suffix = "".join(parts)

        if self.min_area_sqm > 0:
            suffix += f"_m{int(self.min_area_sqm)}"

        return f"_{suffix}" if suffix else ""

    def run(self):
        if not self.wgs_region:
            logger.error(f"[{self.name}] Requires a bounding box region to run.")
            return

        w, e, s, n = self.wgs_region
        suffix = self._get_filename_suffix()
        out_name = f"osm_landmask_{w}_{s}_{e}_{n}{suffix}.geojson"
        out_path = os.path.join(self._outdir, out_name)

        if os.path.exists(out_path):
            logger.info(f"[OSM] Using existing landmask: {out_path}")
            self.add_entry_to_results(f"file://{out_path}", out_path, "osm_landmask")
            return self

        logger.info(f"[OSM] Fetching coastline for {self.wgs_region}...")
        osm_xml = self._fetch_osm(self.wgs_region)

        if not osm_xml or os.path.getsize(osm_xml) < 100:
            self._handle_fallback(out_path, self.wgs_region)
        else:
            logger.info("[OSM] Polygonizing and classifying coastline...")
            try:
                self._polygonize(osm_xml, out_path, self.wgs_region)
                logger.info(f"[OSM] Generated landmask: {out_path}")
            except Exception as e:
                logger.error(f"[OSM] Polygonization failed: {e}")
                if not os.path.exists(out_path):
                    self._handle_fallback(out_path, self.wgs_region)

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

        # rivers
        if self.include_rivers:
            query += """
            way["waterway"="riverbank"];
            relation["waterway"="riverbank"];
            way["natural"="water"]["water"~"river|estuary|bay"];
            relation["natural"="water"]["water"~"river|estuary|bay"];
            way["natural"="water"]["tidal"="yes"];
            relation["natural"="water"]["tidal"="yes"];
            """
            # way["waterway"="riverbank"];
            # relation["waterway"="riverbank"];
            # way["natural"="water"]["water"="river"];
            # relation["natural"="water"]["water"="river"];
            # """

        # lakes/ponds
        if self.include_lakes:
            query += """
            way["natural"="water"]["water"!~"river|estuary|bay"]["tidal"!="yes"];
            relation["natural"="water"]["water"!~"river|estuary|bay"]["tidal"!="yes"];
            """
            # way["natural"="water"]["water"!="river"];
            # relation["natural"="water"]["water"!="river"];
            # """

        # reefs
        if self.include_reefs:
            query += """
            way["natural"="reef"];
            relation["natural"="reef"];
            """

        # wetlands, sloughs, and estuaries
        if self.include_wetlands:
            query += """
            way["natural"="wetland"];
            relation["natural"="wetland"];
            way["waterway"="tidal_channel"];
            relation["waterway"="tidal_channel"];
            way["natural"="mud"];
            relation["natural"="mud"];
            way["natural"="bay"];
            relation["natural"="bay"];
            way["estuary"="yes"];
            relation["estuary"="yes"];
            """

        # breakwaters, jetties, piers, etc.
        if self.include_breakwaters:
            query += """
            way["man_made"~"breakwater|pier|groyne|jetty"];
            relation["man_made"~"breakwater|pier|groyne|jetty"];
            """

        query += """
        );
        (._;>;);
        out geom;
        """

        params = urlencode({"data": query})
        url = f"{OSM_API}?{params}"

        suffix = self._get_filename_suffix()
        dest = os.path.join(self._outdir, f"temp_osm_{w}_{s}_{e}_{w}{suffix}.json")
        f = Fetch(url, headers=HEADERS)
        if f.fetch_file(dest, method="GET", verbose=False) == 0:
            return dest
        return None

    def _is_land_by_topology(self, poly, lines_geom, buffer_size, threshold=0.5):
        """Determine if polygon is Land using the OSM Left-Hand Rule on local boundaries."""

        check_poly = poly.buffer(buffer_size * 2.0)

        if not check_poly.intersects(lines_geom):
            return None  # Indeterminate

        local_lines = lines_geom.intersection(check_poly)

        # Flatten the geometry collection into a list of LineStrings
        if local_lines.geom_type in ["MultiLineString", "GeometryCollection"]:
            from shapely.geometry import LineString

            geoms = [geom for geom in local_lines.geoms if isinstance(geom, LineString)]
        elif local_lines.geom_type == "LineString":
            geoms = [local_lines]
        else:
            return None

        votes = []
        for line in geoms:
            coords = list(line.coords)
            step = max(1, int(len(coords) / 5))

            for i in range(0, len(coords) - 1, step):
                p1 = coords[i]
                p2 = coords[i + 1]
                dx, dy = p2[0] - p1[0], p2[1] - p1[1]

                # Normal vector pointing left (-dy, dx)
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

        return (sum(votes) / len(votes)) >= threshold

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

    def _write_geojson_pyogrio(self, dst_file, polygons):
        """Writes WGS84 polygons using raw Pyogrio."""

        if not polygons:
            polygons = []

        geometry_wkb = shapely.to_wkb(polygons)

        fields = ["class"]
        field_data = [np.array(["land"] * len(polygons), dtype=object)]

        write(
            dst_file,
            geometry_wkb,
            field_data,
            fields=fields,
            geometry_type="Polygon",
            crs="EPSG:4326",
            driver="GeoJSON",
        )

    def _write_geojson(self, dst_file, polygons):
        schema = {"geometry": "Polygon", "properties": {"class": "str"}}
        with fiona.open(
            dst_file, "w", driver="GeoJSON", crs="EPSG:4326", schema=schema
        ) as dst:
            for poly in polygons:
                dst.write({"geometry": mapping(poly), "properties": {"class": "land"}})

    def _polygonize(self, osm_file, dst_file, region):
        """Polygonize the OSM data correctly by separating coastline, water, and islands."""

        coast_lines = []
        water_polys = []
        water_lines = []
        island_polys = []
        island_lines = []
        reef_polys = []
        breakwater_polys = []

        try:
            with open(osm_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            coast_relation_member_ids = set()
            water_relation_member_ids = set()
            # relation_member_ids = set()
            relations = []
            ways = []

            # --- Sort elements and map relation dependencies ---
            for element in data.get("elements", []):
                if element.get("type") == "relation":
                    relations.append(element)

                    tags = element.get("tags", {})
                    is_coast = tags.get("natural") == "coastline"
                    is_river = (
                        tags.get("waterway") == "riverbank"
                        or tags.get("water") in ["river", "estuary", "bay"]
                        or tags.get("tidal") == "yes"
                    )
                    is_lake = tags.get("natural") == "water" and not is_river
                    # is_river = (
                    #     tags.get("waterway") == "riverbank"
                    #     or tags.get("water") == "river"
                    # )
                    # is_lake = tags.get("natural") == "water" and not is_river

                    is_wetland = (
                        tags.get("waterway") == "tidal_channel"
                        or tags.get("natural") == "mud"
                        or tags.get("natural") == "bay"
                        or tags.get("estuary") == "yes"
                        or tags.get("natural") == "wetland"  # Accept ALL wetlands
                    )

                    is_water = False
                    if self.include_rivers and is_river:
                        is_water = True
                    if self.include_lakes and is_lake:
                        is_water = True
                    if self.include_wetlands and is_wetland:
                        is_water = True

                    for member in element.get("members", []):
                        if member.get("type") == "way":
                            # relation_member_ids.add(member.get("ref"))
                            if is_coast:
                                coast_relation_member_ids.add(member.get("ref"))
                            if is_water:
                                water_relation_member_ids.add(member.get("ref"))
                elif element.get("type") == "way":
                    ways.append(element)

            # --- Process Relations (Inner/Outer/Etc.) ---
            for rel in relations:
                tags = rel.get("tags", {})
                is_coast = tags.get("natural") == "coastline"
                is_breakwater = self.include_breakwaters and tags.get("man_made") in [
                    "breakwater",
                    "pier",
                    "groyne",
                    "jetty",
                ]
                is_river = (
                    tags.get("waterway") == "riverbank" or tags.get("water") == "river"
                )
                is_lake = tags.get("natural") == "water" and not is_river
                # is_water = self.include_water and (tags.get("natural") == "water" or tags.get("waterway") == "riverbank")

                is_wetland = (
                    tags.get("waterway") == "tidal_channel"
                    or tags.get("natural") == "mud"
                    or tags.get("natural") == "bay"
                    or tags.get("estuary") == "yes"
                    or tags.get("natural") == "wetland"
                )

                is_water = False
                if self.include_rivers and is_river:
                    is_water = True
                if self.include_lakes and is_lake:
                    is_water = True
                # Ensure the relation gets flagged as water if it's a wetland
                if self.include_wetlands and is_wetland:
                    is_water = True

                if not is_coast and not is_water and not is_breakwater:
                    continue

                for member in rel.get("members", []):
                    if member.get("type") == "way" and "geometry" in member:
                        coords = [(pt["lon"], pt["lat"]) for pt in member["geometry"]]
                        if len(coords) < 2:
                            continue
                        line = LineString(coords)
                        role = member.get("role", "")

                        if is_coast:
                            coast_lines.append(line)
                        elif is_water:
                            if role == "inner":
                                if line.is_closed and len(coords) >= 4:
                                    island_polys.append(Polygon(coords))
                                else:
                                    island_lines.append(line)
                            else:
                                if line.is_closed and len(coords) >= 4:
                                    water_polys.append(Polygon(coords))
                                else:
                                    water_lines.append(line)

            # --- Process Standalone Ways (Skipping Relation Members) ---
            for way in ways:
                way_id = way.get("id")
                tags = way.get("tags", {})

                is_coast = tags.get("natural") == "coastline"
                is_reef = self.include_reefs and tags.get("natural") == "reef"
                is_breakwater = self.include_breakwaters and tags.get("man_made") in [
                    "breakwater",
                    "pier",
                    "groyne",
                    "jetty",
                ]

                is_river = (
                    tags.get("waterway") == "riverbank" or tags.get("water") == "river"
                )
                is_lake = tags.get("natural") == "water" and not is_river
                is_wetland = (
                    tags.get("waterway") == "tidal_channel"
                    or tags.get("natural") == "mud"
                    or tags.get("natural") == "bay"
                    or tags.get("estuary") == "yes"
                    or tags.get("natural") == "wetland"  # Accept ALL wetlands
                )

                is_water = False
                if self.include_water and (
                    tags.get("natural") == "water"
                    or tags.get("waterway") == "riverbank"
                ):
                    is_water = True
                if self.include_rivers and is_river:
                    is_water = True
                if self.include_lakes and is_lake:
                    is_water = True
                if self.include_wetlands and is_wetland:
                    is_water = True

                # is_water = self.include_water and (
                #     tags.get("natural") == "water"
                #     or tags.get("waterway") == "riverbank"
                # )

                if not is_coast and not is_water and not is_reef and not is_breakwater:
                    continue

                process_as_coast = is_coast and way_id not in coast_relation_member_ids
                process_as_water = is_water and way_id not in water_relation_member_ids
                process_as_reef = is_reef
                process_as_breakwater = is_breakwater

                if (
                    not process_as_coast
                    and not process_as_water
                    and not process_as_reef
                    and not process_as_breakwater
                ):
                    continue

                if "geometry" in way:
                    coords = [(pt["lon"], pt["lat"]) for pt in way["geometry"]]
                    if len(coords) < 2:
                        continue
                    line = LineString(coords)

                    if process_as_coast:
                        coast_lines.append(line)
                    elif process_as_water:
                        if line.is_closed and len(coords) >= 4:
                            water_polys.append(Polygon(coords))
                        else:
                            # water_lines.append(line)
                            pass
                    elif process_as_reef:
                        if line.is_closed and len(coords) >= 4:
                            reef_polys.append(Polygon(coords))
                        else:
                            pass
                    elif process_as_breakwater:
                        if line.is_closed and len(coords) >= 4:
                            breakwater_polys.append(Polygon(coords))

        except Exception as e:
            logger.error(f"Failed to parse OSM JSON natively: {e}")
            self._handle_fallback(dst_file, region)
            return

        # --- Stitch fragmented unclosed boundaries ---
        if water_lines:
            for poly in polygonize(linemerge(water_lines)):
                water_polys.append(poly)

        if island_lines:
            for poly in polygonize(linemerge(island_lines)):
                island_polys.append(poly)

        region_box = box(*region)
        land_polys = []

        if not coast_lines:
            is_land = self._is_land_by_gmrt(region_box)
            if is_land:
                land_polys.append(region_box)
        else:
            merged_coast = linemerge(coast_lines)
            coastline_geom = unary_union(merged_coast)

            cut_width = 1e-6
            cutters = coastline_geom.buffer(cut_width)

            try:
                split_geom = region_box.difference(cutters)
            except Exception:
                self._handle_fallback(dst_file, region)
                return

            polys = (
                [split_geom]
                if split_geom.geom_type == "Polygon"
                else list(split_geom.geoms)
            )

            for poly in polys:
                if poly.is_empty:
                    continue

                is_land = self._is_land_by_topology(
                    poly, coastline_geom, cut_width, threshold=0.5
                )

                if is_land is None:
                    is_land = self._is_land_by_gmrt(poly)

                if is_land:
                    land_polys.append(poly)

        if reef_polys:
            logger.info(
                f"[OSM] Injecting {len(reef_polys)} offshore reefs into landmask..."
            )
            land_polys.extend(reef_polys)

        if breakwater_polys:
            logger.info(
                f"[OSM] Injecting {len(breakwater_polys)} breakwaters/piers into landmask..."
            )
            land_polys.extend(breakwater_polys)

        # --- Carve out Inland Water & Protect Islands ---
        if land_polys and water_polys:
            # Apply the minimum area filter
            if self.min_area_sqm > 0:
                water_polys = [
                    p for p in water_polys if self._get_area_sqm(p) >= self.min_area_sqm
                ]
                island_polys = [
                    p
                    for p in island_polys
                    if self._get_area_sqm(p) >= self.min_area_sqm
                ]

            logger.info(
                f"[OSM] Carving {len(water_polys)} waterbodies from landmask..."
            )
            # Combine all valid water polygons
            unified_water = unary_union(
                [p.buffer(0) for p in water_polys if p.buffer(0).is_valid]
            )
            # unified_water = unary_union([p for p in water_polys if p.is_valid])

            # Punch the islands out of the water!
            if island_polys:
                unified_islands = unary_union([p for p in island_polys if p.is_valid])
                unified_water = unified_water.difference(unified_islands)

            # Punch the water out of the land!
            final_land = []
            for land in land_polys:
                try:
                    diff = land.difference(unified_water)
                    if diff.is_empty:
                        continue

                    if diff.geom_type == "Polygon":
                        final_land.append(diff)
                    elif diff.geom_type == "MultiPolygon":
                        final_land.extend(list(diff.geoms))
                except Exception as e:
                    logger.warning(
                        f"[OSM] Geometry difference failed on a polygon: {e}"
                    )
                    final_land.append(land)

            land_polys = final_land

        # --- Enforce Hole Removal on Final Geometries ---
        if self.min_area_sqm > 0:
            cleaned_land = []
            for poly in land_polys:
                if poly.geom_type == "Polygon":
                    # Keep interiors (holes) only if they are larger than the threshold
                    valid_interiors = [
                        ring
                        for ring in poly.interiors
                        if self._get_area_sqm(Polygon(ring)) >= self.min_area_sqm
                    ]
                    cleaned_land.append(Polygon(poly.exterior, valid_interiors))
                elif poly.geom_type == "MultiPolygon":
                    # Handle multipolygons by cleaning each sub-polygon
                    multi_cleaned = []
                    for sub_poly in poly.geoms:
                        valid_interiors = [
                            ring
                            for ring in sub_poly.interiors
                            if self._get_area_sqm(Polygon(ring)) >= self.min_area_sqm
                        ]
                        multi_cleaned.append(
                            Polygon(sub_poly.exterior, valid_interiors)
                        )
                    from shapely.geometry import MultiPolygon

                    cleaned_land.append(MultiPolygon(multi_cleaned))

            land_polys = cleaned_land

        self._write_geojson(dst_file, land_polys)
