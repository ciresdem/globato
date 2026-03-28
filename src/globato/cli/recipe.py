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


@recipe_group.command("batch")
@click.argument("template")
@click.argument("tileset")
@click.option("--outdir", default=".", help="Base output directory for the tiles.")
def recipe_batch(template, tileset, outdir):
    """Run a recipe template over multiple regions defined in a GeoJSON tileset.

    TEMPLATE: The base YAML recipe to use.
    TILESET:  A GeoJSON file containing polygons/bounding boxes for the tiles.

    Example: globato recipe batch crm_vol6_template.yaml crm_vol6_south.geojson --outdir ./crm_output
    """

    yaml_path = resolve_recipe(template)
    if not yaml_path:
        sys.exit(1)

    with open(yaml_path, 'r') as f:
        template_str = f.read()

    # with open(yaml_path, 'r') as f:
    #     template_dict = yaml.safe_load(f)

    if not os.path.exists(tileset):
        click.secho(f"Error: Tileset not found: {tileset}", fg="red")
        sys.exit(1)

    with open(tileset, 'r') as f:
        geojson = json.load(f)

    features = geojson.get("features", [])
    if not features:
        click.secho("Error: No features found in tileset.", fg="red")
        sys.exit(1)

    click.secho(f"\n  Batch Processing {len(features)} tiles from {os.path.basename(tileset)}...", fg="cyan", bold=True)

    base_outdir = os.path.abspath(outdir)
    os.makedirs(base_outdir, exist_ok=True)
    original_cwd = os.getcwd()

    for i, feature in enumerate(features, 1):
        try:
            coords = feature["geometry"]["coordinates"][0]
            xs = [pt[0] for pt in coords]
            ys = [pt[1] for pt in coords]
            w, e, s, n = min(xs), max(xs), min(ys), max(ys)

            props = feature.get("properties", {})
            tile_name = props.get("NAME") or props.get("ID") or f"tile_{i:03d}"

            click.echo("\n" + "="*60)
            click.secho(f"TILE {i}/{len(features)}: {tile_name}", fg="green", bold=True)
            click.secho(f"   Bounds: [{w:.3f}, {e:.3f}, {s:.3f}, {n:.3f}]", fg="green")

            tile_dir = os.path.join(base_outdir, tile_name)
            os.makedirs(tile_dir, exist_ok=True)
            os.chdir(tile_dir)

            config_str = template_str.replace("{name}", tile_name)
            # config = template_dict.copy()
            config = yaml.safe_load(config_str)
            proj = config.setdefault("project", {})
            if "Batch" in proj.get("name", "Batch"):
                proj["name"] = f"{proj.get('name', 'Batch')}_{tile_name}"
            config["region"] = [w, e, s, n]

            tile_config_fn = f"{tile_name}_recipe.yaml"
            with open(tile_config_fn, 'w') as f:
                yaml.dump(config, f, sort_keys=False, default_flow_style=False)

            Recipe.from_file(config).run()

        except Exception as e:
            click.secho(f"Failed on tile {tile_name}: {e}", fg="red")
        finally:
            os.chdir(original_cwd)

    click.secho("\nBatch Processing Complete!", fg="green", bold=True)

    if yaml_path != template and os.path.exists(yaml_path):
        os.remove(yaml_path)


@recipe_group.command("run")
@click.argument("target")
@click.option("--region", help="Override the recipe's bounding box (W/E/S/N)")
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
    if not yaml_path:
        sys.exit(1)

    with open(yaml_path, 'r') as f:
        template_str = f.read()

    if not name:
        name = os.path.splitext(os.path.basename(target))[0]

    config_str = template_str.replace("{name}", name)
    config_dict = yaml.safe_load(config_str)

    if region:
        try:
            w, e, s, n = map(float, region.replace(",", "/").split("/"))
            config_dict["region"] = [w, e, s, n]
        except ValueError:
            click.secho("Error: Region must be formatted as W/E/S/N", fg="red")
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
        click.secho(f"Applied custom overrides to recipe template.", fg="yellow")

    if save_as:
        with open(save_as, 'w') as f:
            yaml.dump(config_dict, f, sort_keys=False, default_flow_style=False)
        click.secho(f"Customized recipe saved to: {save_as}", fg="green")
        click.echo(f"Run it later using: globato recipe run {save_as}")
    else:
        click.secho(f"Executing recipe: {target}", fg="green")
        Recipe.from_file(config_dict).run()

    if yaml_path != target and os.path.exists(yaml_path):
        os.remove(yaml_path)


@recipe_group.command("list")
def recipe_list():
    """List all official community recipes available on GitHub."""

    click.echo("Fetching community recipes catalog from GitHub...")

    # Hit the GitHub API for the directory contents
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

    # Hit the GitHub API for the directory contents
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
    """Eager callback to inspect a specific data source and exit."""
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


@recipe_group.command("build")
@click.option("--list-sources", is_flag=True, is_eager=True, expose_value=False, callback=_list_sources, help="List available data sources and exit.")
@click.option("--info-source", metavar="NAME", is_eager=True, expose_value=False, callback=_info_source, help="Show details for a specific data source and exit.")
@click.option("-R", "--region", required=True, help="Bounding box: W/E/S/N")
@click.option("-E", "--increment", required=True, help="Gridding Increment (e.g., 1s, 30m)")
@click.option("-O", "--outname", default="globato_dem", help="Output Basename (default: globato_dem)")
@click.option("-P", "--crs", default="EPSG:4326", help="Target Projection (default: EPSG:4326)")
@click.option("-M", "--algo", default="interp_gmt", help="Interpolation algorithm (interp_gmt, raster_fill, etc.)")
@click.option("-A", "--stack-mode", type=click.Choice(['mean', 'min', 'max', 'mixed', 'supercede']), default="mixed", help="Stacking mode")
@click.option("--save-only", is_flag=True, help="Save the generated YAML recipe to disk WITHOUT running it.")
@click.argument("sources", nargs=-1)
def recipe_build(region, increment, outname, crs, algo, stack_mode, save_only, sources):
    """Build and run a recipe on the fly using modules or local files.

    SOURCES can be Fetchez modules or local files.
    Append hooks using '+' (stream_data is added automatically).

    Examples:
      globato recipe build -R -120/-119/34/35 -E 1s ./my_lidar.laz copernicus:weight=1.5
      globato recipe build -R -120/-119/34/35 -E 1s mbdb+rq:threshold=50+outlierz --save-only
    """

    if not sources:
        click.secho("Error: You must provide at least one data source.", fg="red")
        sys.exit(1)

    config = {
        "project": {"name": outname},
        "region": region,
        "modules": [_parse_source(s) for s in sources],
        "global_hooks": [
            {
                "name": "multi_stack",
                "args": {"res": increment, "crs": crs, "mode": stack_mode, "output": f"{outname}_stack.tif"}
            },
            {
                "name": "ms_cudem",
                "args": {"algo": algo}
            }
        ]
    }

    yaml_str = yaml.dump(config, sort_keys=False)

    if save_only:
        out_yaml = f"{outname}_recipe.yaml"
        with open(out_yaml, "w") as f:
            f.write(yaml_str)
        click.secho(f"Recipe saved to {out_yaml}. Run it later with 'globato recipe run {out_yaml}'", fg="green", bold=True)
        sys.exit(0)

    click.secho(f"Building and executing on-the-fly recipe: {outname}", fg="cyan", bold=True)

    try:
        Recipe.from_dict(config).run()
    except Exception as e:
        click.secho(f"\nPipeline failed: {e}", fg="red")
        sys.exit(1)
