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


cli.add_command(recipe_group, name=click.style("recipe", fg="cyan", bold=True))
cli.add_command(dem_group, name=click.style("dem", fg="cyan", bold=True))
cli.add_command(raster_group, name=click.style("raster", fg="cyan", bold=True))
cli.add_command(region_group, name=click.style("region", fg="cyan", bold=True))
cli.add_command(fetch_group, name=click.style("fetch", fg="cyan", bold=True))
cli.add_command(pointz_group, name=click.style("pointz", fg="cyan", bold=True))
cli.add_command(viz_group, name=click.style("viz", fg="cyan", bold=True))
cli.add_command(transformez_cli, name=click.style("transform", fg="cyan", bold=True))


if __name__ == "__main__":
    cli()
