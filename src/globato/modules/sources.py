#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.modules.sources
~~~~~~~~~~~~~~~~~~~~~~~

"Smart" wrappers around standard Fetchez modules.
These ensure data is unzipped, filtered, and ready for merging (GlobDEM).
"""

import os
import logging
import rasterio

from fetchez.hooks import FetchHook
from fetchez.hooks.unzip import Unzip
from fetchez.hooks.datatype import SetDataType
from fetchez.hooks.fn_filter import FilenameFilter
from fetchez.registry import ModuleRegistry

from fetchez.hooks.stream_init import DataStream
from globato.hooks.filters.rq import ReferenceQuality
from globato.hooks.filters.rangez import RangeZ
from globato.hooks.filters.dropclass import DropClass
from globato.hooks.sinks.simple_stack import SimpleStack

logger = logging.getLogger(__name__)

BaseFabDEM = ModuleRegistry.get_class("fabdem") or object
BaseCopernicus = ModuleRegistry.get_class("copernicus") or object
BaseMultibeam = ModuleRegistry.get_class("multibeam") or object
BaseHydroNOS = ModuleRegistry.get_class("nos_hydro") or object


class GlobFabDEM(BaseFabDEM):
    """Cleaned FABDEM Module.

    - Fetch Zip
    - Unzip
    - Stream (Load Points)
    - RQ Filter (Flag Coastal Creep)
    - Drop Class (Remove Noise)
    - Stack (Save Clean Raster)
    """

    name = "glob_fabdem"
    meta_tags = [
        "fabdem",
        "dem",
        "dtm",
        "copernicus",
        "global",
        "30m",
        "clean",
        "globato",
    ]
    meta_category = "Globato"

    def __init__(self, **kwargs):
        super().__init__(name="glob_fabdem", **kwargs)

        self.weight = 1

        self.add_hook(Unzip())

        self.add_hook(DataStream())
        self.add_hook(
            ReferenceQuality(
                reference="gebco_cog", threshold=50, mode="diff", set_class=7
            )
        )
        self.add_hook(DropClass(classes="7"))
        # self.hooks.append(SimpleStack())
        self.add_hook(SimpleStack(output="_clean.tif", res="1s", mode="mean"))


class GlobCopernicus(BaseCopernicus):
    """Cleaned Copernicus DEM.

    - Automatically Unzips.
    - Filters out water (Copernicus is often valid over ocean as 0 or noisy).
    - Drop Class (Remove Noise)
    - Stack (Save Clean Raster)
    """

    name = "glob_copernicus"
    meta_desc = "Copernicus Global/European Digital Elevation Models (COP-30/10)"
    meta_tags = ["satellite", "dsm", "radar", "global", "europe", "clean", "globato"]
    meta_category = "Globato"

    def __init__(self, **kwargs):
        super().__init__(name="glob_copernicus", **kwargs)

        self.weight = 1
        self.add_hook(Unzip())
        self.add_hook(FilenameFilter(match=".tif"))

        self.add_hook(DataStream())
        self.add_hook(RangeZ(min_z=0.01))
        self.add_hook(DropClass(classes="7"))
        self.add_hook(SimpleStack(output="{base}_clean.tif", res="1s", mode="mean"))


class GlobMultibeam(BaseMultibeam):
    """Cleaned Multibeam

    - Filename_filter
    - Steam (Load Points)
    - RQ Filter
    - drop class
    - Stack (Save Clean Raster)
    """

    name = "glob_multibeam"
    meta_tags = ["bathymetry", "multibeam", "ocean", "sonar", "noaa", "ncei", "globato"]
    meta_category = "Globato"

    def __init__(self, res="1s", **kwargs):
        super().__init__(**kwargs)

        self.add_hook(FilenameFilter(exclude=".inf", stage="pre"))
        self.add_hook(DataStream())
        self.add_hook(
            ReferenceQuality(
                reference="gmrt",
                threshold=5,
                mode="percent",
                builder="grid",
                set_class=7,
            )
        )
        self.add_hook(DropClass(classes="7"))
        self.add_hook(SimpleStack(output="{base}_clean.tif", res=res, mode="mean"))


class ValidateBAG(FetchHook):
    """Checks if a BAG file is valid HDF5.
    If invalid, deletes it so Fetchez will retry the download next time.
    """

    name = "validate_bag"
    meta_stage = "file"

    def run(self, entries):
        for mod, entry in entries:
            fn = entry["dst_fn"]
            if not os.path.exists(fn) or not fn.endswith(".bag"):
                continue

            is_valid = False
            try:
                with open(fn, "rb") as f:
                    header = f.read(4)
                    if header == b"\x89HDF":
                        is_valid = True

                if is_valid:
                    with rasterio.Env(CPL_LOG="/dev/null"):
                        with rasterio.open(fn) as src:
                            logger.info(src)
                            pass
            except Exception:
                is_valid = False

            if not is_valid:
                logger.warning(f"Corrupt file detected: {fn}. Deleting.")
                os.remove(fn)
                entry["status"] = -1

        return entries


class GlobBAG(BaseHydroNOS):
    name = "glob_bag"
    meta_tags = [
        "bathymetry",
        "hydrography",
        "nos",
        "noaa",
        "bag",
        "soundings",
        "globato",
    ]
    meta_category = "Globato"

    def __init__(self, **kwargs):
        super().__init__(name="glob_bag", **kwargs)
        self.datatype = "bag"

        self.add_hook(ValidateBAG())
        self.add_hook(FilenameFilter(exclude="_Ellipsoid_", stage="pre"))


class GlobNOSXYZ(BaseHydroNOS):
    name = "glob_nos"
    meta_tags = ["bathymetry", "nos", "noaa", "xyz", "legacy", "globato"]
    meta_category = "Globato"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.datatype = "xyz"
        self.src_srs = "EPSG:4326+1089"

        self.add_hook(Unzip())
        self.add_hook(SetDataType(data_type="nox_xyz"))
