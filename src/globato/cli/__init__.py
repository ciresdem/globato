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
# from .hook import hook_group
# from .bundles import bundle_group

# from .dem import dem_group
from .raster import raster_group
from .region import region_group

# from .fetch import fetch_group
from .pointz import pointz_group
from .viz import viz_group

from transformez.cli import transformez_cli

# from fetchez.cli import cli as fetchez_cli, setup_logging
from fetchez.cli import setup_logging
from fetchez.utils import FetchezMainGroup

# from fetchez.cli.bundles import bundles_group
from fetchez.cli.hooks import hooks_group
from fetchez.cli.modules import modules_group
from fetchez.cli.recipes import recipes_group
from fetchez.cli.streams import streams_group
from fetchez.cli.pipeline import pipeline_group


@click.group(
    cls=FetchezMainGroup,
    help="Continuous Digital Elevation Models",
    fetchez_commands={
        "Commands": [
            "cudem",
            "fetchez",
            "gritz",
            "regions",
            "pointz",
            "transformez",
            "perspecto",
        ],
        "Discovery and Management": [
            "modules",
            "hooks",
            "recipes",
            "streams",
        ],
    },
)
@click.version_option(package_name="globato")
@click.option("--verbose", is_flag=True, help="Enable verbose debug logging.")
@click.option("--quiet", is_flag=True, help="Suppress non-error output.")
def cli(verbose, quiet):
    """Globato: The ContinUous-DEM Generation Framework."""

    # logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    setup_logging(quiet=quiet, verbose=verbose)


cli.add_command(recipe_group, name="cudem")
cli.add_command(pipeline_group, name="fetchez")
# cli.add_command(hook_group, name="hook")
# cli.add_command(bundle_group, name="bundles")
cli.add_command(raster_group, name="gritz")
cli.add_command(region_group, name="regions")
# cli.add_command(fetch_group, name="fetch")
cli.add_command(pointz_group, name="pointz")
cli.add_command(viz_group, name="perspecto")
cli.add_command(transformez_cli, name="transformez")
cli.add_command(hooks_group, name="hooks")
cli.add_command(modules_group, name="modules")
cli.add_command(recipes_group, name="recipes")
cli.add_command(streams_group, name="streams")


if __name__ == "__main__":
    cli()
