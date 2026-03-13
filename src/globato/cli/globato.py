#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import tempfile
import requests
import click
import yaml
import logging

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
def recipe_run(target):
    """Run a DEM recipe from a local file, URL, or community keyword.

    Example: globato recipe run western_ak
    """

    yaml_path = resolve_recipe(target)
    if not yaml_path:
        sys.exit(1)

    click.secho(f"Executing recipe: {target}", fg="green")

    with open(yaml_path, 'r') as f:
        config_dict = yaml.safe_load(f)

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
    """Generate custom Digital Elevation Models on the fly."""

    pass

@dem.command("run")
@click.option("-R", "--region", required=True, help="Bounding box: W/E/S/N (e.g., -120/-119/34/35)")
@click.option("-I", "--increment", required=True, help="Cell size/resolution (e.g., 1s, 1/3s, 10m)")
@click.option("-O", "--outdir", default=".", help="Output directory")
@click.argument("sources", nargs=-1)
def dem_run(region, increment, outdir, sources):
    """Generate a DEM from specific data sources without writing a YAML file.

    Example: globato dem run -R -120/-119/34/35 -I 1s nos_hydro copernicus
    """

    if not sources:
        click.secho("Error: You must provide at least one data source (e.g., nos_hydro, gebco).", fg="red")
        sys.exit(1)

    click.secho(f"Building {increment} DEM for region {region} using {', '.join(sources)}...", fg="cyan")

    config = {
        "region": region,
        "increment": increment,
        "outdir": outdir,
        "modules": []
    }

    # Add each source the user requested as a module
    for source in sources:
        config["modules"].append({source: {}})

    config["global_hooks"] = [{"ms_cudem": {}}]

    Recipe.from_file(config).run()


# =============================================================================
# GRITS (RASTER TOOLS)
# =============================================================================
@cli.group()
def raster():
    """Raster manipulation tools."""

    pass


if __name__ == "__main__":
    cli()
