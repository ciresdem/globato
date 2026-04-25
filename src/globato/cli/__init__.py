#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli
~~~~~~~~~~~
The main command-line interface for the Globato framework.
"""

import click
# import logging

from .recipe import recipe_group
from .hook import hook_group
from .bundles import bundle_group

# from .dem import dem_group
from .raster import raster_group
from .region import region_group
from .fetch import fetch_group
from .pointz import pointz_group
from .viz import viz_group

from transformez.cli import transformez_cli
from fetchez.cli import setup_logging


@click.group()
@click.version_option(package_name="globato")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose debug logging.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress non-error output.")
def cli(verbose, quiet):
    """Globato: The ContinUous-DEM Generation Framework."""

    # logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    setup_logging(quiet=quiet, verbose=verbose)


cli.add_command(recipe_group, name="recipe")
# cli.add_command(dem_group, name="dem")
cli.add_command(hook_group, name="hook")
cli.add_command(bundle_group, name="bundles")
cli.add_command(raster_group, name="raster")
# cli.add_command(gritz_cmd, name="gritz")
cli.add_command(region_group, name="region")
cli.add_command(fetch_group, name="fetch")
# cli.add_command(pointz_cmd, name="pointz_new")
cli.add_command(pointz_group, name="pointz")
cli.add_command(viz_group, name="viz")
# cli.add_command(perspecto_cmd, name="perspecto")
cli.add_command(transformez_cli, name="transformez")


if __name__ == "__main__":
    cli()
