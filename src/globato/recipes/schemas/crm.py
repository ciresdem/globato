#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.schemas.crm
~~~~~~~~~~~~~~~

Registers CRM DEM-specific schema into the Fetchez engine.

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import logging

from fetchez.recipes.schemas import BaseSchema  # , SchemaRegistry
from fetchez.spatial import parse_region

logger = logging.getLogger(__name__)


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
        logger.debug(
            f"[Schema: {cls.name}] Expanded processing region to {proc_region}."
        )

        global_hooks = config.get("global_hooks", [])
        modules = config.get("modules", [])

        # Update Module reprojections
        for module in modules:
            hooks = module.get("hooks", [])

            for hook in hooks:
                if hook.get("name") in ["stream-reproject", "stream_reproject"]:
                    if not hook.get("args", None):
                        hook.setdefault("args", {})
                    hook["args"].update({"dst_srs": "EPSG:4326+3855"})

            insert_idx = len(hooks)
            for i, hook in enumerate(hooks):
                if hook.get("name") in ["stream-init", "stream_data"]:
                    insert_idx = i
                    break

            # Add range_z between Marians Trench and Mt. Everest for safety
            # right after stream_data starts. This is in meters.
            hooks.insert(
                insert_idx,
                {"name": "range_z", "args": {"min_z": -11000, "max_z": 9000}},
            )
            module["hooks"] = hooks

            mod_name = module.get("module", "unknown")
            logger.debug(
                f"[Schema: {cls.name}] Injected 'range_z' into {mod_name} module."
            )

        # Update Stack parameters
        for hook in global_hooks:
            if hook.get("name") in ["multi_stack", "multi-stack"]:
                hook.setdefault("args", {})
                hook["args"].update(
                    {
                        "resolution": res_deg,
                        "registration": "grid",
                        "srs": "EPSG:4326+3855",
                    }
                )
                logger.debug(
                    f"[Schema: {cls.name}] Changed 'multi-stack' resolution to {res_deg}."
                )
                logger.debug(
                    f"[Schema: {cls.name}] Changed 'multi-stack' registration to 'grid'."
                )
                logger.debug(
                    f"[Schema: {cls.name}] Changed 'multi-stack' srs to 'EPSG:4326+3855'."
                )

            if hook.get("name") == ["viz_geoshade", "viz-geoshade"]:
                hook.setdefault("args", {})
                hook["args"].update(
                    {
                        "z_min": -3500,
                        "z_max": 1500,
                    }
                )
                logger.debug(
                    f"[Schema: {cls.name}] Changed 'viz_geoshade' z_range to [-3500 - 1500]."
                )

        # Find where to insert the cut/crop hooks!
        # We want to put it after dem_uncertainty, but before viz_geoshade
        insert_idx = len(global_hooks)
        for i, hook in enumerate(global_hooks):
            if hook.get("name") in ["viz_geoshade", "viz-geoshade"]:
                insert_idx = i
                break

        proj_name = config.get("project", {}).get("name", "crm_dem")
        base_proj_name = proj_name.split("_")[0]
        delivery_fn = f"{base_proj_name}_{dist_region.format('delivery')}.tif"

        global_hooks.insert(
            insert_idx, {"name": "raster_crop", "args": {"output": delivery_fn}}
        )
        logger.debug(f"[Schema: {cls.name}] Injected 'raster-crop' in global hooks.")

        global_hooks.insert(
            insert_idx,
            {
                "name": "raster_cut",
                "args": {
                    "region": dist_region.to_list(),
                },
            },
        )
        logger.debug(
            f"[Schema: {cls.name}] Injected 'raster-cut' in global hooks with region: {dist_region}."
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
        logger.debug(
            f"[Schema: {cls.name}] Injected 'copy-artifact' in global hooks to copy delivery tifs to ../_crm_deliverables"
        )

        config["global_hooks"] = global_hooks
        return config
