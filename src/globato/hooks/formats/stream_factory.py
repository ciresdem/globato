#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.formats.stream_factory
~~~~~~~~~~~~~

This turns files into point streams.

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import logging

from .rio import RasterioReader
from .fio import FionaReader
from .bag import BAGReader
from .lidar import LASReader
from .multibeam import MBSReader
from .xyz import XYZReader
from .gtpc import GTPCReader
from .datalist import DatalistReader

# from .hdf_points import HDFPointReader  # testing
from .icesat2 import ATL03Reader
from .schema import ensure_schema
from fetchez.spatial import Region

# --- gdal is required to use these ---
# from .gdal_proc import GDALReader
# from .ogr_proc import OGRReader

from fetchez.hooks import FetchHook

logger = logging.getLogger(__name__)
logging.getLogger("rasterio").setLevel(logging.ERROR)


class StreamFactory:
    """Auto-detects file type and returns the appropriate streaming iterator."""

    FORMAT_PROFILES = {
        "nos_xyz": {
            "reader": XYZReader,
            "delimiter": ",",
            "skiprows": 1,
            "z_scale": -1,
            "usecols": [2, 1, 3],
            "names": ["y", "x", "z"],
        },
        "csb_csv": {
            "reader": XYZReader,
            "delimiter": ",",
            "skiprows": 1,
            "z_scale": -1,
            "usecols": [2, 3, 4],
        },
        "margrav_xyz": {
            "reader": XYZReader,
            "skiprows": 1,
            "delimiter": " ",
            "x_offset": "REM",
        },
        "charts_000": {
            "reader": FionaReader,
            "layer": "SOUNDG",
            "z_scale": -1,
        },
        "dnc_geojson": {
            "reader": FionaReader,
            "z_field": "hdp",
            "z_scale": -1,
        },
        "gdb": {
            "reader": FionaReader,
        },
        "fiona": {
            "reader": FionaReader,
        },
        "ehydro_gdb": {
            "reader": FionaReader,
            "z_field": "Z_label",
            "z_scale": -0.3048,
            "vert_srs": "EPSG:5866",
        },
        "atl03": {
            "reader": ATL03Reader,
            "classes": 1,
        },
    }

    @staticmethod
    def get_stream(src_fn, **kwargs):
        """Returns a generator (yield_chunks) for the given file."""

        # logger.info(src_fn)
        if not os.path.exists(src_fn):
            return None

        ext = os.path.splitext(src_fn)[1].lower()

        # LiDAR (LAS/LAZ)
        if ext in [".las", ".laz"]:
            return LASReader(src_fn, **kwargs).yield_chunks()

        # Vector Data (OGR)
        # .shp, .000 (S-57), .gdb, .geojson
        # TODO: update this to fiona
        if ext in [".shp", ".000", ".json", ".geojson", ".kml"] or (
            ext == ".gdb" and os.path.isdir(src_fn)
        ):
            return FionaReader(src_fn, **kwargs).yield_chunks()

        # ASCII / XYZ
        if ext in [".xyz", ".txt", ".csv", ".dat"]:
            return XYZReader(src_fn, **kwargs).yield_chunks()

        # Raster Data (Rasterio)
        if ext in [".tif", ".tiff", ".nc", ".vrt", ".dt0", ".dt1", ".dt2"]:
            return RasterioReader(src_fn, **kwargs).yield_chunks()

        # BAG (Rasterio)
        if ext in [".bag"]:
            return BAGReader(src_fn, **kwargs).yield_chunks()

        # Multibeam (MB-System)
        if ext in [".fbt"]:
            return MBSReader(src_fn, **kwargs).yield_chunks()

        if ext == ".gtpc":
            return GTPCReader(src_fn, **kwargs).yield_chunks()

        if ext == ".datalist":
            return DatalistReader(src_fn, **kwargs).yield_chunks()

        # If unknown extension, try to open with GDAL.
        try:
            from osgeo import gdal

            ds = gdal.Open(src_fn)
            if ds:
                # return GDALReader(src_fn, **kwargs).yield_chunks()
                # return RasterioReader(src_fn, **kwargs).yield_chunks()
                ds = None
        except Exception:
            pass

        logger.warning(f"Could not detect stream type for {src_fn}")
        return None

    @classmethod
    def get_reader(cls, src_fn, data_type=None, **kwargs):
        """Returns a generator (yield_chunks) for the given file."""

        if not os.path.exists(src_fn):
            return None

        # logger.info(f"data_type: {data_type}")
        if data_type in cls.FORMAT_PROFILES:
            profile = cls.FORMAT_PROFILES[data_type].copy()
            TargetReader = profile.pop("reader")

            merged_kwargs = {**profile, **kwargs}
            logger.debug(f"Applying '{data_type}' profile to {src_fn}")
            return TargetReader(src_fn, **merged_kwargs)

        ext = os.path.splitext(src_fn)[1].lower()

        # LiDAR (LAS/LAZ)
        if ext in [".las", ".laz"]:
            return LASReader(src_fn, **kwargs)

        # Vector Data (OGR)
        # .shp, .000 (S-57), .gdb, .geojson
        # TODO: update this to fiona
        if ext in [".shp", ".000", ".json", ".geojson", ".kml"] or (
            ext == ".gdb" and os.path.isdir(src_fn)
        ):
            # return OGRReader(src_fn, **kwargs)
            return FionaReader(src_fn, **kwargs)

        # ASCII / XYZ
        if ext in [".xyz", ".txt", ".csv", ".dat"]:
            # XYZReader needs to be updated to yield recarrays like the others
            # For now, we assume it does.
            return XYZReader(src_fn, **kwargs)

        # Raster Data (Rasterio)
        if ext in [".tif", ".tiff", ".nc", ".vrt", ".dt0", ".dt1", ".dt2"]:
            return RasterioReader(src_fn, **kwargs)

        if ext in [".bag"]:
            return BAGReader(src_fn, **kwargs)

        if ext in [".fbt"]:
            return MBSReader(src_fn, **kwargs)

        if ext == ".gtpc":
            return GTPCReader(src_fn, **kwargs)

        if ext == ".datalist":
            return DatalistReader(src_fn, **kwargs)

        # If unknown extension, try to open with GDAL.
        try:
            from osgeo import gdal

            ds = gdal.Open(src_fn)
            if ds:
                # return GDALReader(src_fn, **kwargs)
                ds = None
        except Exception:
            pass

        logger.warning(f"Could not detect stream type for {src_fn}")
        return None


class DataStream(FetchHook):
    """Auto-detects file type and attaches a stream.

    Usage:
      --hook stream_data:stream_type=xyz
      --hook stream_data:stream_type=raster:x_inc=1s,y_inc=1s
    """

    name = "stream_data"
    meta_stage = "file"
    meta_desc = "Setup a data stream from input data."
    meta_category = "format-stream"

    def __init__(self, stream_type="xyz", **kwargs):
        super().__init__(**kwargs)
        self.stream_type = stream_type.lower()
        self.reader_kwargs = kwargs

    def run(self, entries):
        for mod, entry in entries:
            if entry.get("stream") or entry.get("raster_stream"):
                continue

            src = entry.get("dst_fn")
            if not src:
                continue

            dtype = entry.get("data_type")

            # try and sanitize raster chunk_sizes so we don't crash
            kwargs_copy = self.reader_kwargs.copy()

            hook_dtype = kwargs_copy.pop("data_type", None)
            dtype = hook_dtype or dtype

            kwargs_copy["region"] = getattr(mod, "region", None)
            if dtype in ["raster", "bag"] or src.lower().endswith(
                (".tif", ".tiff", ".nc", ".vrt", ".bag")
            ):
                c_size = kwargs_copy.get("chunk_size")
                # If they ask for a chunk > 8192 on a raster, it was meant for points. Fallback to default.
                if c_size and str(c_size).isdigit() and int(c_size) > 8192:
                    logger.debug(
                        f"Ignoring massive chunk_size ({c_size}) for raster. Using native blocks."
                    )
                    kwargs_copy.pop("chunk_size")

            reader = StreamFactory.get_reader(src, data_type=dtype, **kwargs_copy)
            if not reader:
                continue

            w = float(getattr(mod, "weight", 1.0))
            u = float(getattr(mod, "uncertainty", 0.0))
            raw_stream = reader.yield_chunks()
            mod.region = Region.from_list(mod.region)

            if raw_stream:
                base_srs = "EPSG:4326"
                if hasattr(reader, "get_srs"):
                    base_srs = reader.get_srs() or base_srs

                profile = StreamFactory.FORMAT_PROFILES.get(dtype, {})
                vert_srs = kwargs_copy.get("vert_srs") or profile.get("vert_srs")

                if vert_srs and "+" not in base_srs:
                    base_srs = f"{base_srs}+{vert_srs}"

                entry["src_srs"] = base_srs

                entry["stream"] = ensure_schema(
                    raw_stream, module_weight=w, module_unc=u
                )
                entry["stream_type"] = "xyz_recarray"

                if self.stream_type == "raster":
                    from globato.hooks.transforms.point_pixels import Point2PixelStream

                    pp_hook = Point2PixelStream(**kwargs_copy)
                    pp_hook.run([(mod, entry)])

        return entries
