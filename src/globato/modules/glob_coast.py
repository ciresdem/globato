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


@cli.cli_opts(
    help_text="Generate a High-Resolution Coastline Mask raster.",
    res="Target resolution (e.g. '1s', '30m')",
    sources="Comma-separated sources (default: copernicus,nhd,osm_landmask,hydrolakes)",
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

    def _process_raster(self, src_path, weight):
        """Warp raster to grid and apply voting logic."""

        try:
            with rasterio.open(src_path) as src:
                buffer = np.full((self.height, self.width), np.nan, dtype=np.float32)

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

                valid_mask = ~np.isnan(buffer)

                if not np.any(valid_mask):
                    return

                # Z > 0 is Land (+1), Z <= 0 is Water (-1)
                vote_grid = np.zeros_like(buffer)

                land_mask = valid_mask & (buffer > 0)
                vote_grid[land_mask] = 1.0

                water_mask = valid_mask & (buffer <= 0)
                vote_grid[water_mask] = -1.0

                if weight > 0:
                    self.grid[valid_mask] += vote_grid[valid_mask] * weight
                else:
                    # Binary mask vs Elevation
                    if np.nanmin(buffer) >= 0:
                        feature_mask = valid_mask & (buffer > 0)
                        self.grid[feature_mask] -= abs(weight)
                    else:
                        self.grid[valid_mask] += vote_grid[valid_mask] * weight
        except Exception as e:
            logger.warning(f"Raster processing failed for {src_path}: {e}")

    def _process_vector(self, src_path, weight):
        """Rasterize vector to grid and apply voting logic."""

        try:
            with fiona.open(src_path) as src:
                geoms = [f["geometry"] for f in src]

            if not geoms:
                return

            # 1 where polygon exists, 0 otherwise
            mask = rasterize(
                geoms,
                out_shape=(self.height, self.width),
                transform=self.transform,
                default_value=1,
                dtype=np.uint8,
            )

            if weight > 0:
                self.grid[mask == 1] += weight
            else:
                self.grid[mask == 1] -= abs(weight)

        except Exception as e:
            logger.warning(f"Vector processing failed for {src_path}: {e}")

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
                    mod_instance.run()
                    core.run_fetchez([mod_instance])
                    fetched_files.extend(
                        [
                            entry.get("dst_fn") if isinstance(entry, dict) else entry[1]
                            for entry in mod_instance.results
                        ]
                    )
                except Exception as e:
                    logger.error(f"Failed to fetch {mod_name}: {e}")

            else:
                mod_cls = ModuleRegistry.get_class(mod_name)
                if not mod_cls:
                    logger.warning(f"Unknown source: {mod_name}")
                    continue

                mod_instance = mod_cls(
                    src_region=fetch_region,
                    outdir=os.path.join(self._outdir, "sources", mod_name),
                )

                try:
                    mod_instance.run()
                    core.run_fetchez([mod_instance])
                    fetched_files.extend(
                        [
                            entry.get("dst_fn") if isinstance(entry, dict) else entry[1]
                            for entry in mod_instance.results
                        ]
                    )
                except Exception as e:
                    logger.error(f"Failed to fetch {mod_name}: {e}")

            for f_path in fetched_files:
                if not f_path or not os.path.exists(f_path):
                    continue

                logger.debug(
                    f"Voting: {os.path.basename(f_path)} as '{mod_name}' (Weight: {weight})"
                )
                ext = os.path.splitext(f_path)[1].lower()

                if ext in [".tif", ".nc", ".vrt"]:
                    self._process_raster(f_path, weight)
                elif ext in [".shp", ".gpkg", ".geojson", ".json", ".gdb"]:
                    self._process_vector(f_path, weight)

        self._finalize()

        self.results = []
        if os.path.exists(self.out_fn):
            self.add_entry_to_results(
                url=f"file://{self.out_fn}",
                dst_fn=self.out_fn,
                data_type="coastline_mask",
            )

        return self
