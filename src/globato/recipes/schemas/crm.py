#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.schemas.crm
~~~~~~~~~~~~~~~

Registers CRM DEM-specific schema into the Fetchez engine.

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

from fetchez.recipes.schemas import BaseSchema  # , SchemaRegistry
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
    meta_desc = "Coastal Relief Model Schema"
    meta_category = "NCEI"

    @classmethod
    def apply(cls, config):
        recipe_region = config.get("region")
        dist_region = (parse_region(recipe_region) if recipe_region else [None])[0]
        if not dist_region:
            return config

        res_deg = 0.0002777777777777778  # 1 arc-second
        # buffer_deg = 100 * res_deg   # 100 cell buffer for fetching/gridding

        proc_region = dist_region.copy()
        proc_region.buffer(10)  # Buffering slightly to ensure overlap coverage
        config["region"] = proc_region.to_list()

        global_hooks = config.get("global_hooks", [])
        modules = config.get("modules", [])

        # Update Module reprojections
        for module in modules:
            hooks = module.get("hooks", [])

            for hook in hooks:
                if hook.get("name") == "stream_reproject":
                    if not hook.get("args", None):
                        hook.setdefault("args", {})
                    hook["args"].update({"dst_srs": "EPSG:4326+3855"})

            insert_idx = len(hooks)
            for i, hook in enumerate(hooks):
                if hook.get("name") == "stream_data":
                    insert_idx = i
                    break

            # Add range_z between Marians Trench and Mt. Everest for safety
            # right after stream_data starts. This is in meters.
            hooks.insert(
                insert_idx,
                {"name": "range_z", "args": {"min_z": -11000, "max_z": 9000}},
            )
            module["hooks"] = hooks

        # Update Stack parameters
        for hook in global_hooks:
            if hook.get("name") == "multi_stack":
                hook.setdefault("args", {})
                hook["args"].update(
                    {
                        "resolution": res_deg,
                        "registration": "grid",
                        "srs": "EPSG:4326+3855",
                    }
                )
            if hook.get("name") == "viz_geoshade":
                hook.setdefault("args", {})
                hook["args"].update(
                    {
                        "z_min": -3500,
                        "z_max": 1500,
                    }
                )

        # Find where to insert the cut/crop hooks!
        # We want to put it after dem_uncertainty, but before viz_geoshade
        insert_idx = len(global_hooks)
        for i, hook in enumerate(global_hooks):
            if hook.get("name") == "viz_geoshade":
                insert_idx = i
                break

        proj_name = config.get("project", {}).get("name", "crm_dem")
        base_proj_name = proj_name.split("_")[0]
        delivery_fn = f"{base_proj_name}_{dist_region.format('delivery')}.tif"

        global_hooks.insert(
            insert_idx, {"name": "raster_crop", "args": {"output": delivery_fn}}
        )

        global_hooks.insert(
            insert_idx,
            {
                "name": "raster_cut",
                "args": {
                    "region": dist_region.to_list(),
                },
            },
        )

        global_hooks.append(
            {
                "name": "copy_artifact",
                "args": {
                    "target_dir": "../_crm_deliverables",
                    "match": [delivery_fn, "hillshade.tif"],
                },
            }
        )

        config["global_hooks"] = global_hooks
        return config
