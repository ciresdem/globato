#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.modules.sources
~~~~~~~~~~~~~~~~~~~~~~~

"Smart" wrappers around standard Fetchez modules.
These ensure data is unzipped, filtered, and ready for streaming into multi_stack.
"""

import os
import logging

from fetchez.hooks import FetchHook
from fetchez.hooks.unzip import Unzip
from fetchez.hooks.datatype import SetDataType
from fetchez.hooks.set_srs import SetSrs
from fetchez.hooks.fn_filter import FilenameFilter
from fetchez.hooks.stream_init import DataStream
from fetchez.registry import ModuleRegistry

# You might need to import your SetSRS or stream_reproject hook here depending on where it lives
from globato.hooks.filters.rq import ReferenceQuality
from globato.hooks.filters.rangez import RangeZ
from globato.hooks.filters.outlierz import OutlierZ

# from globato.hooks.filters.dropclass import DropClass
from globato.hooks.filters.spatial_crop import SpatialCrop

logger = logging.getLogger(__name__)

BaseFabDEM = ModuleRegistry.get_class("fabdem") or object
BaseCopernicus = ModuleRegistry.get_class("copernicus") or object
BaseMultibeam = ModuleRegistry.get_class("mbdb") or object  # Updated to mbdb per yaml
BaseHydroNOS = ModuleRegistry.get_class("nos_hydro") or object


class GlobFabDEM(BaseFabDEM):
    """Cleaned FABDEM Module.

    - Fetch Zip
    - Unzip
    - Stream (Load Points)
    - RQ Filter (Flag Coastal Creep)
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
        "glob-stream",
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


class GlobCopernicus(BaseCopernicus):
    """Cleaned Copernicus DEM.

    Unzips, filters for .tif, sets rio datatype,
    initiates stream, and drops anomalous 0-values.
    """

    name = "glob_copernicus"
    meta_desc = "Copernicus Global/European Digital Elevation Models (COP-30/10)"
    meta_tags = [
        "satellite",
        "dsm",
        "radar",
        "global",
        "europe",
        "clean",
        "globato",
        "glob-stream",
    ]
    meta_category = "Globato"

    def __init__(self, datatype=3, weight=1.0, **kwargs):
        # Pass datatype into the base class to control COP-30 vs COP-90
        super().__init__(name="glob_copernicus", datatype=datatype, **kwargs)

        self.weight = weight

        self.add_hook(Unzip())
        self.add_hook(FilenameFilter(match=".tif"))
        self.add_hook(SetDataType(data_type="rio"))
        self.add_hook(DataStream(chunk_size=100000))
        self.add_hook(RangeZ(min_z=0.01))


class GlobMultibeam(BaseMultibeam):
    """Cleaned NCEI Multibeam (MBDB).

    Mirrors global_bato.yaml: Sets SRS, crops spatially, and runs
    the Reference Quality filter against GMRT to flag outliers.
    """

    name = "glob_multibeam"
    meta_tags = [
        "bathymetry",
        "multibeam",
        "ocean",
        "sonar",
        "noaa",
        "ncei",
        "globato",
        "glob-stream",
    ]
    meta_category = "Globato"

    def __init__(self, weight=1.0, want_inf=False, **kwargs):
        super().__init__(want_inf=want_inf, **kwargs)

        self.weight = weight

        self.add_hook(SetSrs(srs="EPSG:4326+5866"))
        self.add_hook(DataStream())
        self.add_hook(SpatialCrop())
        self.add_hook(
            ReferenceQuality(
                reference="gmrt/gebco",
                threshold=10,
                mode="percent",
                builder="grid",
                res=0.008333,
                set_class=7,
            )
        )


class ValidateBAG(FetchHook):
    """Checks if a BAG file is valid HDF5."""

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
                    if f.read(4) == b"\x89HDF":
                        is_valid = True
            except Exception:
                pass

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
        "globato",
        "glob-stream",
    ]
    meta_category = "Globato"

    def __init__(self, weight=3.0, **kwargs):
        super().__init__(name="glob_bag", **kwargs)
        self.datatype = "bag"
        self.weight = weight

        self.add_hook(ValidateBAG())
        self.add_hook(FilenameFilter(exclude="_Ellipsoid_", stage="pre"))
        self.add_hook(DataStream())
        self.add_hook(SpatialCrop())


class GlobNOSXYZ(BaseHydroNOS):
    name = "glob_nos"
    meta_tags = ["bathymetry", "nos", "noaa", "xyz", "legacy", "globato", "glob-stream"]
    meta_category = "Globato"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.datatype = "xyz"
        self.src_srs = "EPSG:4326+1089"

        self.add_hook(Unzip())
        self.add_hook(SetDataType(data_type="nox-xyz"))
        self.add_hook(SetSrs(srs="EPSG:4326+5866"))
        self.add_hook(OutlierZ())
