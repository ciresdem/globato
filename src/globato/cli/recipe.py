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
