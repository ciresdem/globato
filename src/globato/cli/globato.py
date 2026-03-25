#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.globato
~~~~~~~~~~~~~~~~

The main command-line interface for Globato DEM generation tools.
"""

import os
import sys
import tempfile
import requests
import click
import json
import yaml
import logging
import time
import numpy as np

from transformez.spatial import TransRegion

from fetchez.recipe import Recipe

logger = logging.getLogger(__name__)

RECIPE_REPO_BASE = "https://raw.githubusercontent.com/continuous-dems/dem-recipes/refs/heads/main/dems/"


def resolve_recipe(target):
    """Resolves a local file, URL, or GitHub keyword into a local YAML path."""

    if os.path.exists(target):
        return target

    url = target if target.startswith("http") else f"{RECIPE_REPO_BASE}/{target.replace('.yaml', '')}.yaml"

    click.echo(f"Fetching recipe from {url}...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        fd, temp_path = tempfile.mkstemp(suffix=".yaml", prefix="globato_recipe_")
        with os.fdopen(fd, 'w') as f:
            f.write(response.text)
        return temp_path
    except Exception as e:
        click.secho(f"Error resolving recipe '{target}': {e}", fg="red")
        return None

@click.group()
def cli():
    """Globato: The ContinUous-DEM Generation Framework."""

    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

@cli.group()
def recipe():
    """Execute and manage YAML DEM recipes."""

    pass

@recipe.command("run")
@click.argument("target")
@click.option("--region", help="Override the recipe's bounding box (W/E/S/N)")
@click.option("--res", help="Override the recipe's target resolution (e.g., 1s, 30m)")
@click.option("--out", help="Override the recipe's output name (e.g., my-dem)")
@click.option("--save-as", help="Save the customized recipe to a new YAML file without running it.")
def recipe_run(target, region, res, out, save_as):
    """Run a DEM recipe from a local file, URL, or community keyword.

    You can use community recipes as templates by overriding the region and resolution!
    Example: globato recipe run western_ak -R -120/-119/34/35 -E .3s --save-as my_cali_dem.yaml
    """

    yaml_path = resolve_recipe(target)
    if not yaml_path:
        sys.exit(1)

    with open(yaml_path, 'r') as f:
        config_dict = yaml.safe_load(f)

    if region:
        try:
            w, e, s, n = map(float, region.replace(",", "/").split("/"))
            config_dict["region"] = [w, e, s, n]
        except ValueError:
            click.secho("❌ Error: Region must be formatted as W/E/S/N", fg="red")
            sys.exit(1)

    if res:
        for hook in config_dict.get("global_hooks", []):
            if hook.get("name") == "multi_stack":
                hook.setdefault("args", {})["res"] = res
            elif hook.get("name") == "ms_cudem":
                hook.setdefault("args", {})["resolutions"] = res
        patched = True
    else:
        patched = False

    if patched:
        proj = config_dict.setdefault("project", {})
        proj["name"] = proj.get("name", "Recipe") + "_Custom"
        proj["description"] = f"[Template: {target}] " + proj.get("description", "")
        click.secho(f"✨ Applied custom overrides to recipe template.", fg="yellow")

    if save_as:
        with open(save_as, 'w') as f:
            yaml.dump(config_dict, f, sort_keys=False, default_flow_style=False)
        click.secho(f"✅ Customized recipe saved to: {save_as}", fg="green")
        click.echo(f"🚀 Run it later using: globato recipe run {save_as}")
    else:
        click.secho(f"Executing recipe: {target}", fg="green")
        Recipe.from_file(config_dict).run()

    if yaml_path != target and os.path.exists(yaml_path):
        os.remove(yaml_path)

@recipe.command("list")
def recipe_list():
    """List all official community recipes available on GitHub."""

    click.echo("Fetching community recipes catalog from GitHub...")

    # Hit the GitHub API for the directory contents
    api_url = "https://api.github.com/repos/continuous-dems/dem-recipes/contents/dems/general"

    try:
        response = requests.get(api_url, timeout=5)
        response.raise_for_status()
        files = response.json()

        click.secho("\n📚 Available Community Recipes:", fg="cyan", bold=True)
        for f in files:
            if f["name"].endswith(".yaml"):
                click.echo(f"  ➔ {f['name'].replace('.yaml', '')}")

        click.echo("\n💡 Run 'globato recipe info <name>' to see what a recipe does.")

    except Exception as e:
        click.secho(f"Failed to fetch recipes: {e}", fg="red")


@recipe.command("info")
@click.argument("target")
def recipe_info(target):
    """Inspect a recipe's description and sources without running it."""

    yaml_path = resolve_recipe(target)
    if not yaml_path:
        sys.exit(1)

    with open(yaml_path, 'r') as f:
        config_dict = yaml.safe_load(f)

    proj = config_dict.get("project", {})
    region = config_dict.get("region", "Global")

    modules = config_dict.get("modules", [])
    mod_names = []
    for m in modules:
        if isinstance(m, dict):
            mod_names.append(m.get("module", "Unknown"))
        else:
            mod_names.append(str(m))

    unique_mods = list(set(mod_names))

    click.secho(f"\n🏷️  Recipe: {proj.get('name', target)}", fg="cyan", bold=True)
    click.echo(f"Description: {proj.get('description', 'No description provided.')}")
    click.echo(f"Region:      {region}")
    click.echo(f"Sources:     {', '.join(unique_mods)}\n")

    # Clean up the temp file
    if yaml_path != target and os.path.exists(yaml_path):
        os.remove(yaml_path)

# =============================================================================
# DEM COMMANDS
# =============================================================================
@cli.group()
def dem():
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

@dem.command("run")
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
        click.secho("❌ Error: You must provide at least one data source.", fg="red")
        sys.exit(1)

    try:
        w, e, s, n = map(float, region.replace(',', '/').split('/'))
    except ValueError:
        click.secho("❌ Error: Region must be formatted as W/E/S/N", fg="red")
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
        click.secho(f"✅ Generated Recipe saved to: {out_yaml}", fg="green")
        click.echo(f"🚀 Run it later using: globato recipe run {out_yaml}")
    else:
        click.secho(f"🚀 Building DEM '{outname}' at {increment}...", fg="cyan", bold=True)
        Recipe.from_dict(config).run()


@dem.command("list-sources")
def dem_list_sources():
    """List curated DEM sources provided by Globato."""

    from fetchez.registry import ModuleRegistry
    ModuleRegistry.load_all()

    click.secho("\n🌍 Curated Globato Data Sources:", fg="cyan", bold=True)
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
    click.echo(f"💡 Try 'globato dem info-source <name>' for details. Total: {count}\n")


@dem.command("info-source")
@click.argument("source_name")
def dem_info_source(source_name):
    """View details and accepted arguments for a specific Globato source."""

    from fetchez.registry import ModuleRegistry
    ModuleRegistry.load_all()

    registry = ModuleRegistry.get_registry()
    if source_name not in registry:
        click.secho(f"❌ Error: '{source_name}' is not a recognized source.", fg="red")
        sys.exit(1)

    meta = registry[source_name]

    # Ensure it's a Globato module
    if not (meta.get("mod", "").startswith("globato.modules") or meta.get("category") == "Globato"):
        click.secho(f"⚠️  Note: '{source_name}' is a core Fetchez module, not a curated Globato DEM source.", fg="yellow")

    click.secho(f"\n📦 SOURCE: {source_name.upper()}", fg="cyan", bold=True)
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

def generate_raster_receipt(src_path, dst_path, op_name, elapsed):
    """Calculates before/after statistics and prints a beautiful receipt."""

    try:
        import rasterio
    except ImportError:
        return

    click.echo("\n" + "=" * 60)
    click.secho(f"✅ RASTER OPERATION COMPLETE: {op_name.upper()}", bold=True, fg="green")
    click.echo("=" * 60)
    click.echo(f"  Source : {os.path.basename(src_path)}")
    click.echo(f"  Output : {os.path.basename(dst_path)}")
    click.echo(f"  Time   : {elapsed:.2f} seconds")

    try:
        with rasterio.open(src_path) as s, rasterio.open(dst_path) as d:
            # We can only do pixel math if the grids are the exact same size/shape (e.g., not 'cut')
            if s.shape == d.shape:
                s_data = s.read(1)
                d_data = d.read(1)
                s_ndv = s.nodata if s.nodata is not None else -9999
                d_ndv = d.nodata if d.nodata is not None else -9999

                s_valid = (s_data != s_ndv) & ~np.isnan(s_data)
                d_valid = (d_data != d_ndv) & ~np.isnan(d_data)

                s_count = np.sum(s_valid)
                d_count = np.sum(d_valid)

                # Pixel state changes
                modified = np.sum(s_valid & d_valid & (s_data != d_data))
                removed = np.sum(s_valid & ~d_valid)
                added = np.sum(~s_valid & d_valid)

                click.echo("-" * 60)
                click.secho("  Pixel Statistics (Band 1):", bold=True)
                click.echo(f"    Total Pixels    : {s.width * s.height:,}")
                click.echo(f"    Valid Before    : {s_count:,}")
                click.echo(f"    Valid After     : {d_count:,}")
                click.secho(f"    Pixels Modified : {modified:,}", fg="cyan")
                click.secho(f"    Pixels Removed  : {removed:,} (Set to NoData)", fg="red")
                click.secho(f"    Pixels Added    : {added:,} (Filled/Interpolated)", fg="yellow")
    except Exception as e:
        click.secho(f"  [Could not compute pixel stats: {e}]", fg="yellow")

    click.echo("=" * 60 + "\n")

def run_raster_hook(hook_instance, src, dst, strip_bands=False, region=None):
    """Execution wrapper for standalone raster commands."""

    if region:
        try:
            r_vals = [float(x) for x in region.replace(',', '/').split('/')]
            if len(r_vals) == 4:
                hook_instance.region = TransRegion(r_vals)
            else:
                click.secho("❌ Error: Region must be W/E/S/N", fg="red")
                sys.exit(1)
        except Exception as e:
            click.secho(f"❌ Invalid region format: {e}", fg="red")
            sys.exit(1)

    if strip_bands:
        hook_instance.strip_bands = True

    entry = {'src_fn': src, 'dst_fn': dst, 'weight': 1.0}

    click.secho(f"\n🚀 Starting {hook_instance.name} on {os.path.basename(src)}...", fg="cyan", bold=True)
    start_time = time.time()

    #try:
    success = hook_instance.process_raster(src, dst, entry)
    elapsed = time.time() - start_time

    if success:
        generate_raster_receipt(src, dst, hook_instance.name, elapsed)
    else:
        click.secho("❌ Operation failed (hook returned False)", fg="red")
        sys.exit(1)
    #except Exception as e:
    #    click.secho(f"❌ Error during processing: {e}", fg="red")
    #    sys.exit(1)

def raster_io(f):
    """Click Decorator to share standard IO arguments across all raster commands."""

    f = click.option("--strip-bands", is_flag=True, help="Strip extra bands in the output.")(f)
    f = click.argument("dst")(f)
    f = click.argument("src")(f)
    return f


# =============================================================================
# GRITS (RASTER TOOLS)
# =============================================================================
@cli.group()
def raster():
    """Raster manipulation tools (Powered by Grits)."""
    pass

@raster.command("diff")
@raster_io
@click.option("--aux", required=True, help="Auxiliary/Reference Raster")
@click.option("--mode", type=click.Choice(["difference", "filter"]), default="difference")
@click.option("--threshold", type=float, help="Filter threshold")
def raster_diff(src, dst, strip_bands, aux, mode, threshold):
    """Calculate difference (Src - Aux)."""
    from globato.hooks.rasters.diff import RasterDiff
    hook = RasterDiff(aux_path=aux, mode=mode, threshold=threshold)
    run_raster_hook(hook, src, dst, strip_bands)

@raster.command("slope")
@raster_io
@click.option("--min", "min_val", type=float, help="Min Slope")
@click.option("--max", "max_val", type=float, help="Max Slope")
def raster_slope(src, dst, strip_bands, min_val, max_val):
    """Filter by Slope."""
    from globato.hooks.rasters.slope import RasterSlopeFilter
    hook = RasterSlopeFilter(min_val=min_val, max_val=max_val)
    run_raster_hook(hook, src, dst, strip_bands)

@raster.command("cut")
@raster_io
@click.option("-R", "--region", required=True, help="Region W/E/S/N")
def raster_cut(src, dst, strip_bands, region):
    """Cut/Mask to Region."""
    from globato.hooks.rasters.cut import RasterCut
    hook = RasterCut()
    run_raster_hook(hook, src, dst, strip_bands, region=region)

@raster.command("flats")
@raster_io
@click.option("--threshold", type=float, default=1.0, help="Minimum size of a flat-zone")
def raster_flats(src, dst, strip_bands, threshold):
    """Remove Flat-Zones."""
    from globato.hooks.rasters.flats import RasterFlats
    hook = RasterFlats(size_threshold=threshold)
    run_raster_hook(hook, src, dst, strip_bands)

@raster.command("fill")
@raster_io
@click.option("--dist", type=float, default=100.0, help="Max search distance")
@click.option("--smooth", type=int, default=0, help="Smoothing iterations")
def raster_fill(src, dst, strip_bands, dist, smooth):
    """Fill NoData using IDW."""
    from globato.hooks.rasters.fill import RasterFill
    hook = RasterFill(max_dist=dist, smoothing=smooth)
    run_raster_hook(hook, src, dst, strip_bands)

@raster.command("morph")
@raster_io
@click.option("--op", type=click.Choice(["erosion", "dilation", "opening", "closing"]), default="erosion")
@click.option("--kernel", type=int, default=3, help="Kernel size")
def raster_morph(src, dst, strip_bands, op, kernel):
    """Morphology Operations."""
    from globato.hooks.rasters.morphology import RasterMorphology
    hook = RasterMorphology(op=op, kernel=kernel)
    run_raster_hook(hook, src, dst, strip_bands)

@raster.command("interp")
@raster_io
@click.option("--method", type=click.Choice(["linear", "cubic", "nearest"]), default="linear")
def raster_interp(src, dst, strip_bands, method):
    """Interpolate Gaps."""
    from globato.hooks.rasters.scipy_griddata import ScipyInterp
    hook = ScipyInterp(method=method)
    run_raster_hook(hook, src, dst, strip_bands)

@raster.command("blend")
@raster_io
@click.option("--aux", required=True, help="Auxiliary/Reference Raster")
@click.option("--blend-dist", type=float, default=20.0, help="Max blend distance")
@click.option("--core-dist", type=float, default=5.0, help="Max core blend distance")
@click.option("--slope-scale", type=float, default=0.5, help="Normalize the slope-gate")
@click.option("--random-scale", type=float, default=0.05, help="Density of random points")
def raster_blend(src, dst, strip_bands, aux, blend_dist, core_dist, slope_scale, random_scale):
    """Blend rasters (Src -> Aux)."""
    from globato.hooks.rasters.blend import RasterBlend
    hook = RasterBlend(aux_path=aux, blend_dist=blend_dist, core_dist=core_dist,
                       slope_scale=slope_scale, random_scale=random_scale)
    run_raster_hook(hook, src, dst, strip_bands)

@raster.command("zscore")
@raster_io
@click.option("--threshold", type=float, default=3.0, help="Mask zscore over this threshold")
@click.option("--size", type=int, default=5, help="The size of the neighborhood window.")
def raster_zscore(src, dst, strip_bands, threshold, size):
    """Filter based on neighborhood z-score."""
    from globato.hooks.rasters.zscore import RasterZScore
    hook = RasterZScore(threshold=threshold, kernel_size=size)
    run_raster_hook(hook, src, dst, strip_bands)


if __name__ == "__main__":
    cli()
