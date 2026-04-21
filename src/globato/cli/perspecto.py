#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.perspecto
~~~~~~~~~~~~~~~~~~~~~
Visualization tools for DEMs and Point Clouds.
"""

import click
import yaml
from fetchez.recipe import Recipe
from fetchez.utils import parse_source_string, parse_hook_string


@click.command(name="perspecto", hidden=True)
@click.argument("src", nargs=-1, required=True)
@click.option(
    "-h", "--hook", multiple=True, help="Visualization hooks (e.g., hillshade:exag=2.0)"
)
@click.option("-o", "--output", help="Output image file (e.g., render.png)")
@click.option(
    "--save-only", is_flag=True, help="Save the pipeline as YAML without running."
)
def perspecto_cmd(src, hook, output, save_only):
    """Generate visual perspectives of DEMs and Point Clouds."""

    modules = []
    for src_str in src:
        parsed = parse_source_string(src_str)
        mod_dict = {
            "module": parsed.get("module", "file"),
            "args": parsed.get("args", {"paths": src_str})
            if parsed.get("module") == "file"
            else parsed.get("args", {}),
        }
        modules.append(mod_dict)

    global_hooks = []

    for h_str in hook:
        global_hooks.append(parse_hook_string(h_str))

    if output:
        global_hooks.append({"name": "raster_write", "args": {"output_path": output}})
    else:
        global_hooks.append({"name": "raster_write"})

    config = {
        "project": {"name": "perspecto_render"},
        "modules": modules,
        "global_hooks": global_hooks,
    }

    if save_only:
        out_yaml = "perspecto_recipe.yaml"
        with open(out_yaml, "w") as f:
            yaml.dump(config, f, sort_keys=False)
        click.secho(f"Recipe saved to {out_yaml}", fg="green", bold=True, err=True)
    else:
        click.secho("Rendering Perspecto Visualization...", fg="cyan", err=True)
        Recipe.from_file(config).run()
