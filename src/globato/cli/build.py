#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.build
~~~~~~~~~~~~~~~~~~

The globato build command to build a fetchez recipe and execute it.

:copyright: (c) 2025 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import sys
import click
import yaml
import logging

from fetchez.recipe import Recipe
from fetchez.registry import HookRegistry
from fetchez.utils import (
    int_or,
    str2inc,
    parse_hook_string,
    compile_sources,
    FetchezMainCommand,
)
from globato.utils import globatize_modules, make_recipe_config

logger = logging.getLogger(__name__)


# --- Build command ---
CONTEXT_SETTINGS = dict(max_content_width=220)


@click.command("build", cls=FetchezMainCommand, context_settings=CONTEXT_SETTINGS)
@click.option("-R", "--region", required=True, help="Bounding box: W/E/S/N")
@click.option(
    "-E", "--increment", required=True, help="Gridding Increment (e.g., 1s, 30m)"
)
@click.option("-O", "--outname", default="globato_dem", help="Output Basename")
@click.option(
    "-D",
    "--outdir",
    # type=click.Path(resolve_path=True),
    type=click.Path(),
    default=None,
    help="Base output directory.",
)
@click.option("-F", "--format", default="GTiff", help="Output Format.")
@click.option("-P", "--t-srs", default="EPSG:4326", help="Target Projection")
@click.option("-N", "--nodata", type=float, default=-9999.0, help="NoData Value.")
@click.option(
    "-M",
    "--algo",
    default="ms_binary_cudem:barrier=coastline,algos=interp_gmt:tension=.95",
    help="Interpolation algorithm",
)
@click.option(
    "-A",
    "--stack-mode",
    type=click.Choice(["mean", "min", "max", "mixed", "supercede"]),
    default="mixed",
)
@click.option("-T", "--filter", "filters", multiple=True, help="Apply Grits Filter.")
@click.option("-C", "--clip", help="Clip output to polygon file.")
@click.option(
    "-B", "--blend", type=str, default=None, help="Blend between weighted data."
)
@click.option(
    "-X", "--extend", type=str, default="0:0", help="Extend region (cells[:percent])."
)
@click.option("-L", "--limits", type=str, default=None, help="Set global DEM limits.")
@click.option(
    "-W", "--weights", default="auto", help="Weight thresholds ('auto' or '1.0/0.5')."
)
@click.option(
    "--modifier",
    multiple=True,
    help="Apply a recipe modifier at runtime (e.g., exclude_module:modules=csb/tnm).",
)
@click.option(
    "--schema",
    multiple=True,
    help="Apply a domain schema validation to the recipe.",
)
@click.option(
    "--shared-cache",
    # type=click.Path(resolve_path=True),
    type=click.Path(),
    help="Centralized cache directory.",
)
@click.option("--metadata", help="Global tags to inject.")
@click.option("--export", is_flag=True, help="Save the generated YAML recipe to disk.")
@click.option(
    "--refresh", is_flag=True, help="Force fresh API fetch, bypassing local cache."
)
@click.option(
    "--fail-fast",
    is_flag=True,
    help="Raise an exception on the first failure, otherwise continue processing through failures.",
)
@click.argument("sources", nargs=-1)
def build_cmd(
    region,
    increment,
    outname,
    outdir,
    format,
    t_srs,
    nodata,
    algo,
    stack_mode,
    filters,
    clip,
    extend,
    limits,
    weights,
    blend,
    modifier,
    schema,
    shared_cache,
    metadata,
    export,
    sources,
    refresh,
    fail_fast,
):
    """Build a Digital Elevation Model recipe, and execute it."""

    HookRegistry.load_all()

    if not sources:
        click.secho(
            "Error: You must provide at least one data source or a modules.yaml file.",
            fg="red",
        )
        sys.exit(1)

    compiled_modules = globatize_modules(
        compile_sources(sources),
        shared_cache=shared_cache,
        crs=t_srs,
        res=increment,
    )

    parsed_modifiers = [parse_hook_string(m) for m in modifier]
    parsed_schemas = [s for s in schema]

    base_outdir = os.path.abspath(outdir) if outdir else os.path.abspath(".")

    try:
        # --- Parse the Extend argument for the Modifier ---
        ext_parts = str(extend).split(":")
        ext_cells = int(ext_parts[0]) if len(ext_parts) > 0 else 0
        ext_pct = float(ext_parts[1]) if len(ext_parts) > 1 else 0.0

        # Set the weights and other tiers
        base_res = str2inc(increment)
        if str(weights).lower() == "auto":
            target_max_res = (
                str2inc("15s") if (base_res < 1 or increment.endswith("s")) else 500
            )
            auto_res_list = [base_res]
            current_res = base_res

            while current_res < target_max_res and len(auto_res_list) < 6:
                current_res *= 3.0
                auto_res_list.append(current_res)

            num_steps = max(1, len(auto_res_list) - 1)

            master_weights = [0.25, 0.5, 1.0, 2.0, 3.0, 4.0]
            weight_list = master_weights[:num_steps][::-1]

            master_blends = [1, 2, 5, 15, 45, 135, 405]
            blend_list = master_blends[: len(auto_res_list)][::-1]

        else:
            weight_list = sorted(
                [float(w) for w in str(weights).split("/")], reverse=True
            )
            auto_res_list = [base_res * (3**i) for i in range(len(weight_list) + 1)]
            blend_list = [int_or(b, 10) for b in str(blend).split("/")] if blend else []

        batch_outname = "%name%_%batch_name%"
        # --- Base Pipeline Standard Hooks ---
        global_hooks = [
            {"name": "spatial-crop"},
            {"name": "audit"},
            {"name": "enrich"},
            {"name": "transfer_log"},
            {"name": "drop_class"},
            {
                "name": "provenance",
                "args": {"res": increment, "output": f"{batch_outname}_provenance.tif"},
            },
            {
                "name": "source_masks",
                "args": {
                    "res": increment,
                    "output": f"{batch_outname}_sources.vrt",
                    "vector_output": f"{batch_outname}_sm.gpkg",
                },
            },
        ]

        # --- Parse Limits ---
        limit_args = {}
        if limits:
            for kv in limits.split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    limit_args[k.strip()] = float(v.strip())

        # --- Multi Stack and Raster Stream ---
        global_hooks.append(
            {
                "name": "multi_stack",
                "args": {
                    "res": increment,
                    "crs": t_srs,
                    "mode": stack_mode,
                    "nodata": nodata,
                    "weight_threshold": "/".join([str(x) for x in weight_list]),
                    "output": f"{batch_outname}_stack.tif",
                },
            }
        )

        global_hooks.append({"name": "focus_sink", "args": {"target": "multi_stack"}})
        global_hooks.append(
            {
                "name": "raster_stream",
                "args": {
                    "stream_type": "raster",
                    "chunk_size": 2048,
                    "stage": "collection",
                },
            }
        )

        if "min_z" in limit_args or "max_z" in limit_args:
            global_hooks.append(
                {
                    "name": "raster_limits",
                    "args": {
                        "min_z": limit_args.get("min_z"),
                        "max_z": limit_args.get("max_z"),
                    },
                }
            )

        # --- Dynamic Blending Tiers ---
        # from fetchez.utils import int_or

        if blend:
            blend_list = [int_or(b, 10) for b in str(blend).split("/")]
            while len(blend_list) <= len(weight_list):
                blend_list.append(blend_list[-1])
            for i, w in enumerate(weight_list):
                if w > 0:
                    global_hooks.append(
                        {
                            "name": "ms_blend",
                            "args": {
                                "weight_threshold": w,
                                "blend_dist": blend_list[i],
                                "random_scale": 0.5,
                                "barrier": "osm",
                            },
                        }
                    )
            global_hooks.append(
                {
                    "name": "raster_write",
                    "args": {
                        "suffix": "_final_blend",
                        "artifact_id": "blended_checkpoint",
                    },
                }
            )
            global_hooks.append(
                {"name": "focus_sink", "args": {"target": "blended_checkpoint"}}
            )

        # --- Add requested Filters (-T) ---
        for f in filters:
            global_hooks.append(parse_hook_string(f))

        # --- Interpolation Algorithm (-M) ---
        algo_hook = parse_hook_string(algo)
        if algo_hook["name"] in ["ms_cudem", "ms_binary_cudem"]:
            args = algo_hook.setdefault("args", {})

            if "resolutions" not in args:
                args["resolutions"] = "/".join([str(r) for r in auto_res_list])

            args["weights"] = weight_list  # Strip the trailing 0
            args["steps"] = len(weight_list)

            if "blend_dists" not in args and blend_list:
                args["blend_dists"] = "/".join(map(str, blend_list))

            if "barrier" not in args:
                args["barrier"] = "osm"

        algo_hook.setdefault("args", {})["output"] = f"{batch_outname}.tif"
        global_hooks.append(algo_hook)

        # --- Add Clipping (-C) ---
        if clip:
            clip_hook = parse_hook_string(clip, default_name="raster_clip")
            if clip_hook["name"] != "raster_clip":
                clip_hook["args"]["barrier"] = clip_hook.pop("name")
                clip_hook["name"] = "raster_clip"
            global_hooks.append(clip_hook)

        if metadata:
            global_hooks.append(
                {
                    "name": "raster_metadata",
                    "args": {"tags": metadata, "bands": "Elevation (meters)"},
                }
            )

        global_hooks.append(
            {
                "name": "format_cog",
                "args": {"overviews": "2/4/8/16/32", "resampling": "average"},
            }
        )
        global_hooks.append(
            {
                "name": "viz_geoshade",
                "args": {"output": f"{batch_outname}_hs.tif", "cmap": "coastal_relief"},
            }
        )

        # Add hook descriptions for the YAML
        for hook in global_hooks:
            hook_name = hook.get("name")
            hook_cls = HookRegistry.get_class(hook_name)
            if hook_cls:
                hook["description"] = getattr(
                    hook_cls, "meta_desc", f"Executes the {hook_name} process."
                )

        # --- Build the recipe ---
        config = make_recipe_config(
            outname,
            region,  # Pass raw region string, fetchez handles parsing
            compiled_modules,
            global_hooks,
            crs=t_srs,
        )

        # --- Inject the Modifier ---
        if ext_cells > 0 or ext_pct > 0:
            config["modifiers"] = [
                {
                    "name": "buffer_and_cut",
                    "args": {
                        "cells": ext_cells,
                        "pct": ext_pct,
                        "increment": increment,
                        "outname": batch_outname,
                    },
                }
            ]

        # Ensure schemas validate the generated recipe
        config["schemas"] = [{"name": "validate-recipe"}]

        if parsed_modifiers:
            config["modifiers"].extend(parsed_modifiers)

        if schema:
            config["schemas"].extend(parsed_schemas)

        # --- Export or Execute ---
        if export:
            os.makedirs(base_outdir, exist_ok=True)
            out_yaml = os.path.join(os.getcwd(), f"{outname}_recipe.yaml")
            with open(out_yaml, "w") as f:
                yaml.dump(config, f, sort_keys=False)
            click.secho(
                f"Globato recipe exported to {out_yaml}.", fg="green", bold=True
            )
        else:
            click.secho(
                f"Executing dynamic recipe for {outname}...", fg="cyan", bold=True
            )
            recipe = Recipe.from_dict(config)

            # Fetchez handles all directory switching, batching, and execution
            recipe.run(
                outdir=outdir,
                shared_cache=shared_cache,
                refresh=refresh,
                ignore_failures=not fail_fast,
            )
            click.secho(
                "✨ Successfully completed Globato build pipeline!",
                fg="green",
                bold=True,
            )

    except Exception as e:
        click.secho(
            f"Failed to execute Globato pipeline!: {str(e)}", fg="red", bold=True
        )
