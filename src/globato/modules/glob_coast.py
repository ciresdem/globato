#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.modules.glob_coast
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Super-Module that generates a high-quality Coastline Mask.
Merges Vectors (NHD, OSM) and Rasters (Copernicus, GMRT) into a unified product using weighted voting.
"""

import os
import logging
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.enums import Resampling
from rasterio.warp import reproject
import fiona

from fetchez import core, cli, utils
from fetchez.hooks.unzip import Unzip
from fetchez.hooks.fn_filter import FilenameFilter
from fetchez.registry import ModuleRegistry
from fetchez.modules import FetchModule

# from globato.hooks.tools.osm_landmask import OSMLandmask
from globato.hooks.rasters.polygonize import RasterPolygonizeHook

logger = logging.getLogger(__name__)

_VECTOR_EXTS = {".shp", ".gpkg", ".geojson", ".json"}
_RASTER_EXTS = {".tif", ".nc", ".vrt"}


def _ensure_shp_spatial_index(shp_path):
    """Build a GDAL .qix spatial index alongside *shp_path* if one is not present.

    Without a .qix index, GDAL's shapefile driver must read every record's
    bounding box sequentially when filtering by bbox.  For a global file like
    HydroLAKES (1.4 M polygons, 1.1 GB) that scan takes ~15 s even though
    nearly zero features intersect a typical DEM tile.  With a .qix index the
    same query completes in milliseconds.

    The index is written once next to the .shp file; subsequent calls return
    immediately.
    """
    qix_path = os.path.splitext(shp_path)[0] + ".qix"
    if os.path.exists(qix_path):
        return
    try:
        from osgeo import ogr

        ds = ogr.Open(shp_path, 1)  # 1 = update / write mode
        if ds is None:
            logger.debug(
                "Cannot open %s in write mode; skipping .qix creation.", shp_path
            )
            return
        layer = ds.GetLayer(0)
        layer_name = layer.GetName()
        logger.info(
            "Building spatial index for %s (one-time) …", os.path.basename(shp_path)
        )
        ds.ExecuteSQL(f"CREATE SPATIAL INDEX ON {layer_name}")
        ds = None
        logger.info("Spatial index ready.")
    except Exception as exc:
        logger.debug("Could not build spatial index for %s: %s", shp_path, exc)


def _scan_source_dir(source_dir):
    """Return paths of already-extracted data files under *source_dir*.

    Skips the `.fetchez_cache` metadata subdirectory and does not recurse
    into `.gdb` directories (they are returned as a single path instead).
    """
    found = []
    if not os.path.exists(source_dir):
        return found
    for dirpath, dirnames, filenames in os.walk(source_dir):
        # Skip fetchez metadata cache
        dirnames[:] = [d for d in dirnames if d != ".fetchez_cache"]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in _VECTOR_EXTS or ext in _RASTER_EXTS:
                found.append(os.path.join(dirpath, fn))
        # Treat .gdb directories as single processable paths; don't recurse in
        gdb_dirs = [d for d in dirnames if d.lower().endswith(".gdb")]
        for gdb in gdb_dirs:
            found.append(os.path.join(dirpath, gdb))
            dirnames.remove(gdb)
    return found


@cli.cli_opts(
    help_text="Generate a High-Resolution Coastline Mask raster.",
    res="Target resolution (e.g. '1s', '30m')",
    sources="Comma-separated sources (default: copernicus,nhd,osm_landmask,hydrolakes). "
    "Use 'osm_water' in place of 'osm_landmask' to also carve OSM lakes/rivers out of the land vote.",
)
class GlobCoast(FetchModule):
    """Synthesizes a coastline raster from multiple sources.

    Uses 'Weighted Voting' to resolve conflicts (e.g. NHD water overrides Copernicus land).
    """

    name = "glob_coast"
    meta_desc = "Fetch and glob a coastline"
    meta_agency = "Globato"
    meta_tags = ["global", "globato", "coastline", "landmask"]
    meta_category = "Tools"
    meta_resolution = "Varies"
    meta_license = "N/A"

    def __init__(
        self, res="1s", sources=None, weights=None, fill_inland_holes=False, **kwargs
    ):
        super().__init__(name="glob_coast", **kwargs)

        # Default Hierarchy:
        # High-Res Hydrography (NHD, HydroLakes)
        # High-Res DEM (Copernicus/NASADEM)
        # Vector Coastline (OSM)
        # Background (GMRT)
        if not sources:
            self.source_list = ["nhd", "osm_landmask", "copernicus", "gmrt"]
        else:
            self.source_list = sources.split(",")

        self.res_val = utils.str2inc(res)

        w, e, s, n = self.region if self.region else (0, 0, 0, 0)
        self.out_fn = os.path.join(self._outdir, f"coastline_{w}_{s}_{res}.tif")

        # Voting Weights
        self.weights = {
            "nhd": -10.0,
            "hydrolakes": -10.0,
            "copernicus": 5.0,
            "nasadem": 5.0,
            "wsf": 5.0,
            "osm_landmask": 5.0,
            # osm_water is osm_landmask with include_water=True: lake/river areas are
            # carved out of the land polygons, so they receive no land vote instead of +5.
            "osm_water": 5.0,
            "gmrt": 0.1,
            "gebco": 0.1,
        }
        if weights:
            self.weights.update(weights)

        # We need to fix this hook, it creates too many artifacts currently...
        # Sieve the raster to remove salt & pepper ocean noise
        # sieve_cls = self.add_hook(RasterSieveHook())
        # if sieve_cls:
        #     self.add_hook(sieve_cls(chunk="full", size=2))

        # Convert the cleaned raster to vector polygons
        self.add_hook(RasterPolygonizeHook(target_value=1))

        if fill_inland_holes:
            try:
                from globato.hooks.vectors.fill_holes import VectorFillHoles

                self.add_hook(VectorFillHoles(min_area=10.0))
            except ImportError:
                logger.warning(
                    "VectorFillHoles hook not found. Ponds will not be filled."
                )

    def _init_grid(self):
        """Initialize the empty voting grid based on the region and resolution."""

        w, e, s, n = self.region
        self.width = int((e - w) / self.res_val)
        self.height = int((n - s) / self.res_val)
        self.transform = from_origin(w, n, self.res_val, self.res_val)
        self.grid = np.zeros((self.height, self.width), dtype=np.float32)

    def _apply_source_votes(self, fetched_files, weight):
        """Apply voting for all files belonging to one source.

        Multiple overlapping tiles from the same source would each apply the
        full weight to shared pixels if processed individually, inflating the
        effective vote.  This method merges all tiles into a single per-source
        vote before modifying self.grid, so each pixel is counted exactly once
        per source regardless of how many tiles cover it.

        Vectors: all geometries from all files are collected and rasterized
        together, producing a single binary coverage mask.

        Rasters: tiles are merged using first-valid-wins so that overlapping
        regions are counted only once.
        """
        valid_files = [f for f in fetched_files if f and os.path.exists(f)]
        if not valid_files:
            return

        w, e, s, n = self.region
        _SHP_INDEX_THRESHOLD = 50 * 1024 * 1024  # 50 MB

        all_geoms = []
        raster_files = []
        for f_path in valid_files:
            ext = os.path.splitext(f_path)[1].lower()
            if ext in _VECTOR_EXTS or f_path.lower().endswith(".gdb"):
                if (
                    f_path.lower().endswith(".shp")
                    and os.path.getsize(f_path) > _SHP_INDEX_THRESHOLD
                ):
                    _ensure_shp_spatial_index(f_path)
                try:
                    # NOTE: bbox must be passed to src.filter(), NOT to fiona.open().
                    # fiona.open() does not accept a bbox parameter; passing it there
                    # is a silent no-op that returns the entire dataset.
                    with fiona.open(f_path) as src:
                        all_geoms.extend(
                            f["geometry"] for f in src.filter(bbox=(w, s, e, n))
                        )
                except Exception as exc:
                    logger.warning("Vector read failed for %s: %s", f_path, exc)
            elif ext in _RASTER_EXTS:
                raster_files.append(f_path)

        # --- Vectors: rasterize all geometries at once ---
        if all_geoms:
            try:
                mask = rasterize(
                    all_geoms,
                    out_shape=(self.height, self.width),
                    transform=self.transform,
                    default_value=1,
                    dtype=np.uint8,
                )
                if weight > 0:
                    self.grid[mask == 1] += weight
                else:
                    self.grid[mask == 1] -= abs(weight)
            except Exception as exc:
                logger.warning("Vector rasterization failed: %s", exc)

        # --- Rasters: merge tiles (first-valid-wins), apply weight once ---
        if raster_files:
            # source_vote: +1 = land, -1 = water, 0 = not yet covered
            source_vote = np.zeros((self.height, self.width), dtype=np.float32)
            covered = np.zeros((self.height, self.width), dtype=bool)

            for f_path in raster_files:
                try:
                    buffer = np.full(
                        (self.height, self.width), np.nan, dtype=np.float32
                    )
                    with rasterio.open(f_path) as src:
                        reproject(
                            source=rasterio.band(src, 1),
                            destination=buffer,
                            src_transform=src.transform,
                            src_crs=src.crs,
                            dst_transform=self.transform,
                            dst_crs=rasterio.crs.CRS.from_epsg(4326),
                            src_nodata=src.nodata,
                            dst_nodata=np.nan,
                            resampling=Resampling.nearest,
                        )
                    new_pixels = ~np.isnan(buffer) & ~covered
                    if not np.any(new_pixels):
                        continue
                    # Z > 0 is Land (+1), Z <= 0 is Water (-1)
                    source_vote[new_pixels & (buffer > 0)] = 1.0
                    source_vote[new_pixels & (buffer <= 0)] = -1.0
                    covered |= new_pixels
                except Exception as exc:
                    logger.warning("Raster processing failed for %s: %s", f_path, exc)

            if np.any(covered):
                self.grid[covered] += source_vote[covered] * weight

    def _finalize(self):
        """Convert voting grid to binary mask and save to TIFF."""

        # Vote > 0 is Land (1), Vote <= 0 is Water (0)
        final_mask = (self.grid > 0).astype(np.uint8)

        profile = {
            "driver": "GTiff",
            "height": self.height,
            "width": self.width,
            "count": 1,
            "dtype": "uint8",
            "crs": "EPSG:4326",
            "transform": self.transform,
            "compress": "lzw",
            "nodata": None,
        }

        with rasterio.open(self.out_fn, "w", **profile) as dst:
            dst.write(final_mask, 1)

    def run(self):
        """Fetch sources and generate the coastline mask."""

        if not self.region:
            logger.error("GlobCoast requires a region.")
            return

        self._init_grid()

        w, e, s, n = self.region
        pad = 0.1
        fetch_region = [w - pad, e + pad, s - pad, n + pad]

        for mod_name in self.source_list:
            fetched_files = []
            weight = self.weights.get(mod_name, 0.1)

            # if mod_name == "osm_landmask":
            #     landmask_fn = os.path.join(
            #         self._outdir, f"temp_landmask_{w}_{s}.geojson"
            #     )
            #     osm_hook = OSMLandmask(filename=landmask_fn)

            #     mock_entries = [(self, {"dst_fn": "dummy"})]
            #     osm_hook.run(mock_entries)

            #     if os.path.exists(landmask_fn):
            #         fetched_files.append(landmask_fn)

            if mod_name == "nhd":
                # NHD GDB tiles cover entire HU-4/HU-8 hydrologic units — much
                # larger than any single DEM tile.  Once the tiles are on disk
                # for any DEM in the region, they are valid for all adjacent
                # DEMs.  Skip the TNM API call (which fires per-DEM because the
                # cache key includes the bbox) and reuse whatever is already in
                # the source directory.
                nhd_src_dir = os.path.join(self._outdir, "sources", mod_name, "tnm")
                existing = _scan_source_dir(nhd_src_dir)
                if existing:
                    logger.info(f"[NHD] Using {len(existing)} cached dataset(s).")
                    fetched_files.extend(existing)
                else:
                    mod_cls = ModuleRegistry.get_class("tnm")
                    mod_instance = mod_cls(
                        src_region=fetch_region,
                        datasets="14",
                        extents="'HU-8 Subbasin,HU-4 Subregion'",
                        outdir=os.path.join(self._outdir, "sources", mod_name),
                    )
                    mod_instance.add_hook(FilenameFilter(match="GDB", stage="pre"))
                    mod_instance.add_hook(Unzip())

                    try:
                        logger.info(
                            "[NHD] Querying USGS National Map for NHD hydrography data..."
                        )
                        mod_instance.run()
                        n_found = len(mod_instance.results)
                        logger.info(f"[NHD] Found {n_found} dataset(s). Fetching...")
                        final = core.run_fetchez([mod_instance])
                        if final:
                            fetched_files.extend(
                                [
                                    e.get("dst_fn")
                                    for _, e in final
                                    if isinstance(e, dict) and e.get("dst_fn")
                                ]
                            )
                        else:
                            fetched_files.extend(
                                [
                                    entry.get("dst_fn")
                                    if isinstance(entry, dict)
                                    else entry[1]
                                    for entry in mod_instance.results
                                ]
                            )
                        logger.info(f"[NHD] {n_found} dataset(s) ready.")
                    except Exception as e:
                        logger.error(f"[NHD] Failed to fetch NHD data: {e}")

            else:
                # osm_water reuses the osm_landmask module with include_water=True.
                # It gets its own outdir so its cache doesn't collide with osm_landmask.
                registry_name = "osm_landmask" if mod_name == "osm_water" else mod_name
                mod_cls = ModuleRegistry.get_class(registry_name)
                if not mod_cls:
                    logger.warning(f"Unknown source: {mod_name}")
                    continue

                extra_kwargs = (
                    {"include_water": True} if mod_name == "osm_water" else {}
                )
                mod_instance = mod_cls(
                    src_region=fetch_region,
                    outdir=os.path.join(self._outdir, "sources", mod_name),
                    **extra_kwargs,
                )
                mod_instance.add_hook(Unzip())

                # osm_landmask and osm_water print their own [OSM] status lines; skip here.
                tag = mod_name.upper()
                log_progress = mod_name not in ("osm_landmask", "osm_water")

                try:
                    mod_instance.run()
                    n_found = len(mod_instance.results)

                    new_fetch = False
                    if log_progress:
                        if n_found == 0:
                            logger.info(f"[{tag}] No datasets found for region.")
                        else:
                            source_dir = os.path.join(self._outdir, "sources", mod_name)
                            if _scan_source_dir(source_dir):
                                logger.info(f"[{tag}] Using existing dataset(s).")
                            else:
                                new_fetch = True
                                logger.info(
                                    f"[{tag}] Found {n_found} dataset(s). Fetching..."
                                )

                    final = core.run_fetchez([mod_instance])
                    if final:
                        fetched_files.extend(
                            [
                                e.get("dst_fn")
                                for _, e in final
                                if isinstance(e, dict) and e.get("dst_fn")
                            ]
                        )
                    else:
                        fetched_files.extend(
                            [
                                entry.get("dst_fn")
                                if isinstance(entry, dict)
                                else entry[1]
                                for entry in mod_instance.results
                            ]
                        )

                    if new_fetch:
                        logger.info(f"[{tag}] {n_found} dataset(s) ready.")
                except Exception as e:
                    logger.error(f"[{tag}] Failed to fetch {mod_name} data: {e}")

            logger.debug(
                f"Voting: {len(fetched_files)} file(s) as '{mod_name}' (Weight: {weight})"
            )
            self._apply_source_votes(fetched_files, weight)

        self._finalize()

        self.results = []
        if os.path.exists(self.out_fn):
            self.add_entry_to_results(
                url=f"file://{self.out_fn}",
                dst_fn=self.out_fn,
                data_type="coastline_mask",
            )

        return self
