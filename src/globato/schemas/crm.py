#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.schemas.crm
~~~~~~~~~~~~~~~

Registers CRM DEM-specific schema into the Fetchez engine.

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

from fetchez.schemas import BaseSchema  # , SchemaRegistry
from fetchez.spatial import parse_region

class CRMSchema(BaseSchema):
    """The "CRM" schema.

    Enforces:
      1 arc-second resolution
      1/4 degree tiles
      Buffer region for fetching
      Crop output to delivery spec
      Add CRM project metadata
      Output ESPG is 4326+3855
    """

    name = "crm"

    @classmethod
    def apply(cls, config):
        recipe_region = config.get("region")
        dist_region = (
            parse_region(recipe_region) if recipe_region else [None]
        )[0]
        if not dist_region:
            return config

        res_deg = 0.0002777777777777778 # 1 arc-second
        buffer_deg = 100 * res_deg   # 100 cell buffer for fetching/gridding

        proc_region = dist_region.copy()
        proc_region.buffer(10) # Buffering slightly to ensure overlap coverage
        config["region"] = proc_region.to_list()

        global_hooks = config.get("global_hooks", [])
        modules = config.get("modules", [])

        # 1. Update Module reprojections
        for module in modules:
            for hook in module.get("hooks", []):
                if hook.get("name") == "stream_reproject":
                    if not hook.get("args", None):
                        hook.setdefault("args", {})
                    hook["args"].update({"dst_srs": "EPSG:4326+3855"})

        # 2. Update Stack parameters
        for hook in global_hooks:
            if hook.get("name") == "multi_stack":
                hook.setdefault("args", {})
                hook["args"].update({
                    "resolution": res_deg,
                    "registration": "grid",
                    "srs": "EPSG:4326+3855"
                })

        # 3. SMART INJECTION: Find where to insert the cut/crop hooks!
        # We want to put it AFTER dem_uncertainty, but BEFORE viz_geoshade
        insert_idx = len(global_hooks)
        for i, hook in enumerate(global_hooks):
            if hook.get("name") == "viz_geoshade":
                insert_idx = i
                break

        proj_name = config.get("project", {}).get("name", "crm_dem")
        base_proj_name = proj_name.split("_tile_")[0].split("_L_")[0] # Adjust splits based on your batch prefixes

        # Format the final delivery name!
        delivery_fn = f"{base_proj_name}_{dist_region.format('delivery')}.tif"

        # Insert Crop first (so it ends up after Cut)
        global_hooks.insert(insert_idx, {
            "name": "raster_crop",
            "args": {"output": delivery_fn}
        })

        # Insert Cut
        global_hooks.insert(insert_idx, {
            "name": "raster_cut",
            "args": {"region": dist_region.to_list(), }
        })

        config["global_hooks"] = global_hooks
        return config
