#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli
~~~~~~~~~~~
The main command-line interface for the Globato framework.
"""

import click
import logging

from .recipe import recipe_group
from .dem import dem_group
from .raster import raster_group
from .region import region_group
from .fetch import fetch_group
from .pointz import pointz_group
from .viz import viz_group
from transformez.cli import transformez_cli


@click.group()
@click.version_option(package_name='transformez')
def cli():
    """Globato: The ContinUous-DEM Generation Framework."""

    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

cli.add_command(recipe_group, name="recipe")
cli.add_command(dem_group, name="dem")
cli.add_command(raster_group, name="raster")
cli.add_command(region_group, name="region")
cli.add_command(fetch_group, name="fetch")
cli.add_command(pointz_group, name="pointz")
cli.add_command(viz_group, name="viz")
cli.add_command(transformez_cli, name="transform")

if __name__ == "__main__":
    cli()
