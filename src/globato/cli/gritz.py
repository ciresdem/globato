#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.gritz
~~~~~~~~~~~~~~~~

The command-line interface for the raster/grits group.
"""

import click
import yaml

from fetchez.recipe import Recipe
from fetchez.utils import parse_source_string, parse_hook_string
from fetchez.registry import HookRegistry
from fetchez.cli import print_hook_info

from globato.utils import parse_source_string

GRITZ_HOOKS = {
    "blend": "raster_blend",
    "crop": "raster_crop",
    "cut": "raster_cut",
    "clip": "raster_clip",
    "diff": "raster_diff",
    "flats": "raster_flats",
    "morph": "raster_morphology",
    "slope": "raster_slope",
    "zscore": "raster_zscore",
    "sieve": "raster_sieve",
    "fill": "raster_fill",
    "interp": "interp_scipy",
}


def list_gritz_tools(ctx, param, value):
    """Print all available GRITZ_HOOKS."""

    if not value or ctx.resilient_parsing:
        return

    HookRegistry.load_fast()  # Assuming you added the fast cache!
    click.secho("\n Available Gritz Tools:", fg="cyan", bold=True)
    click.echo("=" * 60)

    for short_name, full_name in sorted(GRITZ_HOOKS.items()):
        meta = HookRegistry.get_info(full_name)
        desc = meta.get("desc", "No description provided.")
        click.echo(f"  {click.style(short_name, bold=True, fg='yellow'):<10} : {desc}")

    click.echo("=" * 60)
    click.echo("Use --tool-info <name> to see specific arguments.\n")
    ctx.exit()


def show_tool_info(ctx, param, value):
    """Print the fetchez hook info for a specific gritz tool."""

    if not value or ctx.resilient_parsing:
        return

    if value not in GRITZ_HOOKS:
        click.secho(f"Error: '{value}' is not a valid Gritz tool.", fg="red", err=True)
        ctx.exit(1)

    full_hook_name = GRITZ_HOOKS[value]
    HookRegistry.load_fast()

    print_hook_info(full_hook_name)
    ctx.exit()


@click.command(name="gritz")
@click.argument("src", nargs=-1, required=True)
@click.option(
    "--list-tools",
    is_flag=True,
    callback=list_gritz_tools,
    expose_value=False,
    is_eager=True,
    help="List available raster tools and exit.",
)
@click.option(
    "--tool-info",
    metavar="TOOL",
    callback=show_tool_info,
    expose_value=False,
    is_eager=True,
    help="Show detailed arguments for a specific tool.",
)
@click.option(
    "-h", "--hook", multiple=True, help="Raster tools (e.g., blend:aux=ref.tif)"
)
@click.option("--stream/--no-stream", default=True, help="Process in memory chunks")
@click.option("-o", "--output", help="Final output path")
@click.option("--save-only", is_flag=True, help="Save YAML without running")
def gritz_cmd(src, hook, stream, output, save_only):
    """Chain multiple raster tools together using Fetchez recipes."""

    modules = []
    for src_str in src:
        parsed = parse_source_string(src_str)
        parsed_args = parsed.get("args", {})
        parsed_args["data_type"] = "raster"
        mod_dict = {
            "module": parsed["module"],
            "args": parsed_args,
        }
        if parsed.get("hooks"):
            mod_dict["hooks"] = parsed["hooks"]
        modules.append(mod_dict)

    # modules = [{"module": "file", "args": {"paths": list(src), "data_type": "raster"}}]

    global_hooks = []
    if stream:
        global_hooks.append({"name": "raster_stream", "args": {}})

    for h_str in hook:
        parsed_hook = parse_hook_string(h_str)
        if parsed_hook.get("name") in GRITZ_HOOKS:
            parsed_hook["name"] = GRITZ_HOOKS[parsed_hook["name"]]
            global_hooks.append(parsed_hook)
        else:
            click.secho(
                f"{parsed_hook.get('name')} is not a valid raster hook",
                err=True,
                fg="red",
            )

    if output:
        global_hooks.append({"name": "raster_write", "args": {"output_path": output}})
    else:
        global_hooks.append({"name": "raster_write"})

    # --- CONSTRUCT THE RECIPE ---
    config = {
        "project": {"name": "gritz_pipeline"},
        "modules": modules,
        "global_hooks": global_hooks,
    }

    #  --- EXECUTE / SAVE THE RECIPE ---
    if save_only:
        out_yaml = "gritz_recipe.yaml"
        with open(out_yaml, "w") as f:
            yaml.dump(config, f, sort_keys=False)
        click.secho(f"Recipe saved to {out_yaml}", fg="green", bold=True, err=True)
    else:
        click.secho("Executing PointZ Pipeline...", fg="cyan", err=True)
        Recipe.from_file(config).run()
