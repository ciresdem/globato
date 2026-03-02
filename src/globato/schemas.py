#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.schemas
~~~~~~~~~~~~~~~
Registers DEM-specific schemas (CUDEM, CRM, ETOPO, NTHMP, Etc.) into the Fetchez engine.

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

from fetchez.schema import BaseSchema, SchemaRegistry

class CUDEMSchema(BaseSchema):
    """The "cudem" schema.

    Enforces:
      1/9 arc-second resolution
      1/4 degree tiles
      6 cell delivery overlap
      Buffer region for fetching
      Crop output to delivery spec
      Add cudem project metadata
    """

    name = "cudem"

    @classmethod
    def apply(cls, config):
        domain = config.get("domain", {})
        dist_region = domain.get("region")
        if not dist_region:
            return config

        res_deg = 0.0000308641975  # 1/9 arc-second
        buffer_deg = 6 * res_deg   # 6 cell overlap

        proc_region = [
            dist_region[0] - buffer_deg,
            dist_region[1] + buffer_deg,
            dist_region[2] - buffer_deg,
            dist_region[3] + buffer_deg
        ]

        config["region"] = proc_region

        global_hooks = config.get("global_hooks", [])

        for hook in global_hooks:
            if hook.get("name") == "multi_stack":
                hook.setdefault("args", {})
                hook["args"].update({
                    "resolution": res_deg,
                    "registration": "grid",
                    "srs": "EPSG:4269+5703"
                })

        global_hooks.append({
            "name": "raster_crop",
            "args": {"bounds": dist_region}
        })

        config["global_hooks"] = global_hooks
        return config

# Register it with Fetchez!
SchemaRegistry.register(CUDEMSchema)
