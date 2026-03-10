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
        domain = config.get("domain", {})
        dist_region = domain.get("region")
        if not dist_region:
            return config

        res_deg = 0.0002777777777777778 # 1 arc-second
        buffer_deg = 20 * res_deg   # 20 cell buffer for fetching

        proc_region = [
            dist_region[0] - buffer_deg,
            dist_region[1] + buffer_deg,
            dist_region[2] - buffer_deg,
            dist_region[3] + buffer_deg
        ]

        config["region"] = proc_region

        global_hooks = config.get("global_hooks", [])
        modules = config.get("modules", [])

        for module in modules:
            for hook in module.get("hooks", []):
                if hook.get("name") == "stream_reproject":
                    if not hook.get("args", None):
                        hook.setdefault("args", {})
                    hook["args"].update({"dst_srs": "EPSG:4326+3855"})

        for hook in global_hooks:
            if hook.get("name") == "multi_stack":
                hook.setdefault("args", {})
                hook["args"].update({
                    "resolution": res_deg,
                    "registration": "grid",
                    "srs": "EPSG:4326+3855"
                })

        global_hooks.append({
            "name": "raster_crop",
            "args": {"bounds": dist_region}
        })

        config["global_hooks"] = global_hooks
        return config
