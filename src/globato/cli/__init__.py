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
from .regions import regions_group
# from .pointz import pointz_group
# from .fetch import fetch_group


@click.group()
def cli():
    """Globato: The ContinUous-DEM Generation Framework.

    \b
    Commands:
      recipe  : Run reproducible, declarative YAML pipelines.
      dem     : Generate custom DEM surfaces (Legacy Waffles).
      raster  : Manipulate and process existing grids (Legacy Grits).
      pointz  : Filter and process point clouds (Legacy Dlim).
      regions : Generate and manipulate spatial tilesets.
      fetch   : Quickly download raw data from public APIs.
    """
    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

cli.add_command(recipe_group, name="recipe")
cli.add_command(dem_group, name="dem")
cli.add_command(raster_group, name="raster")
cli.add_command(regions_group, name="regions")
# cli.add_command(pointz_group, name="pointz")
# cli.add_command(fetch_group, name="fetch")

if __name__ == "__main__":
    cli()
