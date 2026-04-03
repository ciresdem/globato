#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.recipe
~~~~~~~~~~~~~~~~

The command-line interface for the recipe group.
"""

import os
import sys
import tempfile
import requests
import click
import yaml
import json

from fetchez.recipe import Recipe

from globato.utils import parse_source_string, parse_hook_string, yield_parsed_regions

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


@click.group(name="recipe")
def recipe_group():
    """Execute and manage YAML DEM recipes."""

    pass


@recipe_group.command("run")
@click.argument("target")
@click.option("-R", "--region", help="Override region. Can be a bounding box, loc string, or geojson file to trigger batch mode.")
@click.option("--res", help="Override the recipe's target resolution (e.g., 1s, 30m)")
@click.option("--name", help="Inject a custom {name} variable (defaults to recipe filename)")
@click.option("--out", help="Override the recipe's output name (e.g., my-dem)")
@click.option("--save-as", help="Save the customized recipe to a new YAML file without running it.")
def recipe_run(target, region, res, name, out, save_as):
    """Run a DEM recipe from a local file, URL, or community keyword.

    You can use community recipes as templates by overriding the region and resolution!
    Example: globato recipe run western_ak -R -120/-119/34/35 -E .3s --save-as my_cali_dem.yaml
    """

    yaml_path = resolve_recipe(target)
    if not yaml_path: sys.exit(1)

    with open(yaml_path, 'r') as f:
        base_config = yaml.safe_load(f)

    try:
        for t_reg, feat_name in yield_parsed_regions(region):
            config = base_config.copy()

            if t_reg:
                config['region'] = t_reg.format("gmt")

            if feat_name:
                orig_name = config.get('project', {}).get('name', 'globato_dem')
                config.setdefault('project', {})['name'] = f"{orig_name}_{feat_name}"
                click.secho(f"\n--- Running Batch Tile: {feat_name} ({config['region']}) ---", fg="cyan", bold=True)

            Recipe.from_file(config).run()

    except ValueError as e:
        click.secho(str(e), fg="red")
        sys.exit(1)

    # yaml_path = resolve_recipe(target)
    # if not yaml_path:
    #     sys.exit(1)

    # with open(yaml_path, 'r') as f:
    #     template_str = f.read()

    # if not name:
    #     name = os.path.splitext(os.path.basename(target))[0]

    # config_str = template_str.replace("{name}", name)
    # config_dict = yaml.safe_load(config_str)

    # if region:
    #     try:
    #         w, e, s, n = map(float, region.replace(",", "/").split("/"))
    #         config_dict["region"] = [w, e, s, n]
    #     except ValueError:
    #         click.secho("Error: Region must be formatted as W/E/S/N", fg="red")
    #         sys.exit(1)

    # if res:
    #     for hook in config_dict.get("global_hooks", []):
    #         if hook.get("name") == "multi_stack":
    #             hook.setdefault("args", {})["res"] = res
    #         elif hook.get("name") == "ms_cudem":
    #             hook.setdefault("args", {})["resolutions"] = res
    #     patched = True
    # else:
    #     patched = False

    # if patched:
    #     proj = config_dict.setdefault("project", {})
    #     proj["name"] = proj.get("name", "Recipe") + "_Custom"
    #     proj["description"] = f"[Template: {target}] " + proj.get("description", "")
    #     click.secho(f"Applied custom overrides to recipe template.", fg="yellow")

    # if save_as:
    #     with open(save_as, 'w') as f:
    #         yaml.dump(config_dict, f, sort_keys=False, default_flow_style=False)
    #     click.secho(f"Customized recipe saved to: {save_as}", fg="green")
    #     click.echo(f"Run it later using: globato recipe run {save_as}")
    # else:
    #     click.secho(f"Executing recipe: {target}", fg="green")
    #     Recipe.from_file(config_dict).run()

    # if yaml_path != target and os.path.exists(yaml_path):
    #     os.remove(yaml_path)


@recipe_group.command("list")
def recipe_list():
    """List all official community recipes available on GitHub."""

    click.echo("Fetching community recipes catalog from GitHub...")
    api_url = "https://api.github.com/repos/continuous-dems/dem-recipes/contents/dems/general"

    try:
        response = requests.get(api_url, timeout=5)
        response.raise_for_status()
        files = response.json()

        click.secho("\nAvailable Community Recipes:", fg="cyan", bold=True)
        for f in files:
            if f["name"].endswith(".yaml"):
                click.echo(f"  ➔ {f['name'].replace('.yaml', '')}")

        click.echo("\nRun 'globato recipe info <name>' to see what a recipe does.")

    except Exception as e:
        click.secho(f"Failed to fetch recipes: {e}", fg="red")


@recipe_group.command("info")
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

    click.secho(f"\n Recipe: {proj.get('name', target)}", fg="cyan", bold=True)
    click.echo(f"Description: {proj.get('description', 'No description provided.')}")
    click.echo(f"Region:      {region}")
    click.echo(f"Sources:     {', '.join(unique_mods)}\n")

    if yaml_path != target and os.path.exists(yaml_path):
        os.remove(yaml_path)


@recipe_group.command("list")
def recipe_list():
    """List all official community recipes available on GitHub."""

    click.echo("Fetching community recipes catalog from GitHub...")
    api_url = "https://api.github.com/repos/continuous-dems/dem-recipes/contents/dems/general"

    try:
        response = requests.get(api_url, timeout=5)
        response.raise_for_status()
        files = response.json()

        click.secho("\nAvailable Community Recipes:", fg="cyan", bold=True)
        for f in files:
            if f["name"].endswith(".yaml"):
                click.echo(f"  ➔ {f['name'].replace('.yaml', '')}")

        click.echo("\nRun 'globato recipe info <name>' to see what a recipe does.")

    except Exception as e:
        click.secho(f"Failed to fetch recipes: {e}", fg="red")


@recipe_group.command("info")
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

    click.secho(f"\n Recipe: {proj.get('name', target)}", fg="cyan", bold=True)
    click.echo(f"Description: {proj.get('description', 'No description provided.')}")
    click.echo(f"Region:      {region}")
    click.echo(f"Sources:     {', '.join(unique_mods)}\n")

    if yaml_path != target and os.path.exists(yaml_path):
        os.remove(yaml_path)


def _parse_source(src_str):
    """Parses 'module:key=val+hook:k=v' or local paths into a dictionary for the recipe."""

    # Split the module definition from any appended hooks using '+'
    components = src_str.split("+")
    mod_part = components[0]
    hook_parts = components[1:]

    parts = mod_part.split(":", 1)
    mod_name = parts[0]
    args = {}

    if len(parts) > 1:
        for kv in parts[1].split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                try:
                    v = float(v) if "." in v else int(v)
                except ValueError:
                    if v.lower() in ['true', 'yes']: v = True
                    elif v.lower() in ['false', 'no']: v = False
                args[k] = v

    if os.path.exists(mod_name):
        if os.path.isfile(mod_name):
            args['paths'] = mod_name
            mod_name = 'file'
        elif os.path.isdir(mod_name):
            args['path'] = mod_name
            mod_name = 'local_fs'

    mod_dict = {
        "module": mod_name,
    }
    if args:
        mod_dict["args"] = args

    mod_dict["hooks"] = [{"name": "stream_data"}]

    for h_str in hook_parts:
        h_parts = h_str.split(":", 1)
        h_name = h_parts[0]
        h_args = {}

        if len(h_parts) > 1:
            for kv in h_parts[1].split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    try:
                        v = float(v) if "." in v else int(v)
                    except ValueError:
                        if v.lower() in ['true', 'yes']: v = True
                        elif v.lower() in ['false', 'no']: v = False
                    h_args[k] = v

        hook_dict = {"name": h_name}
        if h_args:
            hook_dict["args"] = h_args

        mod_dict["hooks"].append(hook_dict)

    return mod_dict


def _list_sources(ctx, param, value):
    """Eager callback to list available data sources and exit."""

    if not value or ctx.resilient_parsing:
        return

    from fetchez.registry import ModuleRegistry
    ModuleRegistry.load_all()
    registry = ModuleRegistry.get_registry()

    click.secho("\nCurated Globato Data Sources:", fg="cyan", bold=True)
    click.echo("=" * 60)

    count = 0
    for name, meta in sorted(registry.items()):
        if meta.get("mod", "").startswith("globato.modules") or meta.get("category") == "Globato":
            if name in meta.get("aliases", []):
                continue

            desc = meta.get("desc", "No description provided.")
            click.echo(f"  {click.style(name, bold=True, fg='yellow'):<25} : {desc}")
            count += 1

    click.echo("-" * 60)
    click.secho("\nLocal File Support:", fg="cyan", bold=True)
    click.echo("=" * 60)
    click.echo("  You can also pass local files and directories directly!")
    click.echo("  Files will be wrapped in the 'file' module.")
    click.echo("  Directories will be crawled using the 'local_fs' module.")
    click.echo("  Example: globato recipe build -R ... ./my_data.tif ./my_folder:ext=.xyz")

    click.echo(f"\nTry 'globato recipe build --info-source <name>' for details. Total: {count}\n")
    ctx.exit()


def _info_source(ctx, param, value):
    """inspect a specific data source and exit."""

    if not value or ctx.resilient_parsing:
        return

    from fetchez.registry import ModuleRegistry
    ModuleRegistry.load_all()
    registry = ModuleRegistry.get_registry()

    source_name = value
    if source_name not in registry:
        click.secho(f"Error: '{source_name}' is not a recognized source.", fg="red")
        ctx.exit(1)

    meta = registry[source_name]

    if not (meta.get("mod", "").startswith("globato.modules") or meta.get("category") == "Globato"):
        click.secho(f" Note: '{source_name}' is a core Fetchez module, not a curated Globato DEM source.", fg="yellow")

    click.secho(f"\nSOURCE: {source_name.upper()}", fg="cyan", bold=True)
    click.echo("=" * 60)
    click.echo(f"  Description : {meta.get('desc', 'N/A')}")
    click.echo(f"  Tags        : {', '.join(meta.get('tags', []))}")

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
            click.echo(f"  Arguments   : {', '.join(params)}")
    click.echo("\n")
    ctx.exit()


def _parse_hook(hook_str, default_name=None):
    """Parses 'name:key=val:key2=val2' into a dictionary for global_hooks."""

    parts = hook_str.split(":")
    name = parts[0] if len(parts) > 0 and "=" not in parts[0] else default_name

    args = {}
    for part in parts[1:] if name == parts[0] else parts:
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                v = float(v) if "." in v else int(v)
            except ValueError:
                if v.lower() in ['true', 'yes']: v = True
                elif v.lower() in ['false', 'no']: v = False
            args[k] = v

    hook = {"name": name}
    if args:
        hook["args"] = args
    return hook


@recipe_group.command("build")
@click.option("--list-sources", is_flag=True, is_eager=True, expose_value=False, callback=_list_sources, help="List available data sources and exit.")
@click.option("--info-source", metavar="NAME", is_eager=True, expose_value=False, callback=_info_source, help="Show details for a specific data source and exit.")
@click.option("-R", "--region", required=True, help="Bounding box: W/E/S/N")
@click.option("-E", "--increment", required=True, help="Gridding Increment (e.g., 1s, 30m)")
@click.option("-O", "--outname", default="globato_dem", help="Output Basename (default: globato_dem)")
@click.option("-F", "--format", default="GTiff", help="Output Format (GTiff, NetCDF, etc.). Default: GTiff.")
@click.option("-P", "--crs", default="EPSG:4326", help="Target Projection (default: EPSG:4326)")
@click.option("-N", "--nodata", type=float, default=-9999.0, help="NoData Value. Default: -9999.")
@click.option("-M", "--algo", default="ms_cudem", help="Interpolation algorithm and options (e.g., interp_gmt:tension=0.35)")
@click.option("-A", "--stack-mode", type=click.Choice(['mean', 'min', 'max', 'mixed', 'supercede']), default="mixed", help="Stacking mode")
@click.option("-T", "--filter", "filters", multiple=True, help="Apply Grits Filter (e.g. 'blur:radius=3'). May be set multiple times.")
@click.option("-C", "--clip", help="Clip output to polygon file. e.g. 'clip_ply.shp'")
@click.option("--save-only", is_flag=True, help="Save the generated YAML recipe to disk WITHOUT running it.")
@click.argument("sources", nargs=-1)
def recipe_build(region, increment, outname, format, crs, nodata, algo, stack_mode, filters, clip, save_only, sources):
    """Build and run a recipe on the fly, mimicking the legacy Waffles CLI."""

    if not sources:
        click.secho("Error: You must provide at least one data source.", fg="red")
        sys.exit(1)

    try:
        for t_reg, feat_name in yield_parsed_regions(region):
            r_str = f"{t_reg.xmin}/{t_reg.xmax}/{t_reg.ymin}/{t_reg.ymax}"
            tile_outname = f"{outname}_{feat_name}" if feat_name else outname

            if feat_name:
                click.secho(f"\n--- Building Batch Tile: {feat_name} ({r_str}) ---", fg="cyan", bold=True)

            global_hooks = []

            # The Base Stack
            global_hooks.append({"name": "drop_class"})
            global_hooks.append({
                "name": "multi_stack",
                "args": {"res": increment, "crs": crs, "mode": stack_mode, "nodata": nodata, "output": f"{tile_outname}_stack.tif"}
            })
            global_hooks.append({"name": "focus_sink", "args": {"target": "multi_stack"}})
            global_hooks.append({"name": "raster_stream", "args": {"stream_type": "raster", "chunk_size": 2048, "stage": "collection"}})

            # Add requested Filters (-T)
            for f in filters:
                global_hooks.append(parse_hook_string(f))

            global_hooks.append({"name": "ms_blend", "args": {"weight_threshold": .5, "blend_dist": 20, "random_scale": .25}})

            # Add requested Interpolation Algorithm (-M)
            algo_hook = _parse_hook(algo)
            if algo_hook["name"] == "ms_cudem":
                algo_hook.setdefault("args", {})["resolutions"] = increment

            algo_hook.setdefault("args", {})["output"] = f"{tile_outname}.tif"
            global_hooks.append(algo_hook)

            # Add Clipping (-C)
            if clip:
                clip_hook = _parse_hook(clip, default_name="raster_clip")
                if clip_hook["name"] != "raster_clip":
                    clip_hook["args"]["barrier"] = clip_hook.pop("name")
                    clip_hook["name"] = "raster_clip"
                global_hooks.append(clip_hook)

            config = {
                "project": {"name": tile_outname},
                "region": region,
                "modules": [parse_source_string(s) for s in sources],
                "global_hooks": global_hooks
            }

            yaml_str = yaml.dump(config, sort_keys=False)

            out_yaml = f"{tile_outname}_recipe.yaml"
            with open(out_yaml, "w") as f:
                f.write(yaml_str)
            click.secho(f"Recipe saved to {out_yaml}.", fg="green", bold=True)

            if not save_only:
                click.secho(f"Executing dynamic recipe: {tile_outname}", fg="cyan", bold=True)
                Recipe.from_file(config).run()

    except ValueError as e:
        click.secho(str(e), fg="red")
        sys.exit(1)
