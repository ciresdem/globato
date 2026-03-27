#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.dem
~~~~~~~~~~~~~~~~

The command-line interface for the dem group
"""

import sys
import click
import yaml

from fetchez.recipe import Recipe

@click.group(name="dem")
def dem_group():
    """Generate custom Digital Elevation Models (Legacy Waffles style)."""
    pass

def _parse_source(src_str):
    """Parses 'module:key=val,key2=val2' into a dictionary for the recipe."""
    parts = src_str.split(":", 1)
    mod_name = parts[0]
    args = {}
    if len(parts) > 1:
        for kv in parts[1].split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                try:
                    v = float(v) if "." in v else int(v)
                except ValueError:
                    pass
                args[k] = v
    return {"module": mod_name, "args": args}

@dem_group.command("run")
@click.option("-R", "--region", required=True, help="Bounding box: W/E/S/N")
@click.option("-E", "--increment", required=True, help="Gridding Increment (e.g., 1s, 30m)")
@click.option("-O", "--outname", default="waffles_dem", help="Output Basename (default: waffles_dem)")
@click.option("-P", "--crs", default="EPSG:4326", help="Target Projection (default: EPSG:4326)")
@click.option("-M", "--algo", default="interp_gmt", help="Interpolation algorithm (interp_gmt, raster_fill, etc.)")
@click.option("-A", "--stack-mode", type=click.Choice(['mean', 'min', 'max', 'mixed', 'supercede']), default="mixed", help="Stacking mode")
@click.option("--save-recipe", is_flag=True, help="Save the generated YAML recipe to disk without running.")
@click.argument("sources", nargs=-1)
def dem_run(region, increment, outname, crs, algo, stack_mode, save_recipe, sources):
    """Generate a DEM using curated Globato sources.

    Example: globato dem run -R -120/-119/34/35 -E 1s -O socal_dem glob_copernicus:weight=1.5 glob_multibeam
    """
    if not sources:
        click.secho("Error: You must provide at least one data source.", fg="red")
        sys.exit(1)

    try:
        w, e, s, n = map(float, region.replace(',', '/').split('/'))
    except ValueError:
        click.secho("Error: Region must be formatted as W/E/S/N", fg="red")
        sys.exit(1)

    # Build the Recipe Dictionary
    config = {
        "project": {
            "name": outname,
            "description": f"Auto-generated via globato dem run for region {region}"
        },
        "region": [w, e, s, n],
        "modules": [_parse_source(src) for src in sources],
        "global_hooks": [
            {"name": "audit"},
            {"name": "drop_class"},
            {
                "name": "multi_stack",
                "args": {
                    "res": increment,
                    "mode": stack_mode,
                    "crs": crs,
                    "output": f"{outname}_stack.tif"
                }
            },
            {"name": "focus_sink", "args": {"target": "multi_stack"}},
            {
                "name": "ms_cudem",
                "args": {
                    "resolutions": increment,
                    "algo": algo,
                    "output": f"{outname}.tif"
                }
            }
        ]
    }

    # Save YAML or Execute
    if save_recipe:
        out_yaml = f"{outname}_recipe.yaml"
        with open(out_yaml, 'w') as f:
            yaml.dump(config, f, sort_keys=False, default_flow_style=False)
        click.secho(f"Generated Recipe saved to: {out_yaml}", fg="green")
        click.echo(f"Run it later using: globato recipe run {out_yaml}")
    else:
        click.secho(f"Building DEM '{outname}' at {increment}...", fg="cyan", bold=True)
        Recipe.from_file(config).run()


@dem_group.command("list-sources")
def dem_list_sources():
    """List curated DEM sources provided by Globato."""

    from fetchez.registry import ModuleRegistry
    ModuleRegistry.load_all()

    click.secho("\nCurated Globato Data Sources:", fg="cyan", bold=True)
    click.echo("=" * 60)

    registry = ModuleRegistry.get_registry()
    count = 0
    for name, meta in sorted(registry.items()):
        # Filter for Globato-owned modules
        if meta.get("mod", "").startswith("globato.modules") or meta.get("category") == "Globato":
            if name in meta.get("aliases", []):
                continue # Skip aliases

            desc = meta.get("desc", "No description provided.")
            click.echo(f"  {click.style(name, bold=True, fg='yellow'):<25} : {desc}")
            count += 1

    click.echo("-" * 60)
    click.echo(f"Try 'globato dem info-source <name>' for details. Total: {count}\n")


@dem_group.command("info-source")
@click.argument("source_name")
def dem_info_source(source_name):
    """View details and accepted arguments for a specific Globato source."""

    from fetchez.registry import ModuleRegistry
    ModuleRegistry.load_all()

    registry = ModuleRegistry.get_registry()
    if source_name not in registry:
        click.secho(f"Error: '{source_name}' is not a recognized source.", fg="red")
        sys.exit(1)

    meta = registry[source_name]

    # Ensure it's a Globato module
    if not (meta.get("mod", "").startswith("globato.modules") or meta.get("category") == "Globato"):
        click.secho(f" Note: '{source_name}' is a core Fetchez module, not a curated Globato DEM source.", fg="yellow")

    click.secho(f"\nSOURCE: {source_name.upper()}", fg="cyan", bold=True)
    click.echo("=" * 60)
    click.echo(f"  Description : {meta.get('desc', 'N/A')}")
    click.echo(f"  Tags        : {', '.join(meta.get('tags', []))}")

    # Extract argument hints from the class __init__
    mod_cls = ModuleRegistry.get_class(source_name)
    if mod_cls:
        import inspect
        sig = inspect.signature(mod_cls.__init__)
        params = []
        for p_name, param in sig.parameters.items():
            if p_name not in ["self", "kwargs", "src_region", "callback", "outdir", "name"]:
                default = param.default if param.default is not inspect.Parameter.empty else "None"
                params.append(f"{p_name}={default}")

        if params:
            click.echo("\n  Accepted Options (use via module:key=val):")
            for p in params:
                click.echo(f"    ➔ {p}")

    click.echo("=" * 60 + "\n")
