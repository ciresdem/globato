#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli
~~~~~~~~~~~
The main command-line interface for the Globato framework.
"""

import click
import logging

# Globato CLI
from .wafflez import wafflez_group
from .gritz import gritz_group
from .pointz import pointz_group
from .perspecto import perspecto_group
from .region import region_group

# Transformez CLI
from transformez.cli import transformez_cli

# Fetchez CLI
from fetchez.cli import setup_logging
from fetchez.utils import FetchezMainGroup

from fetchez.cli.hooks import hooks_group
from fetchez.cli.modules import modules_group
from fetchez.cli.recipes import recipes_group

from fetchez.cli.streams import streams_group
from fetchez.cli.pipeline import pipeline_group

logger = logging.getLogger(__name__)


@click.group(
    cls=FetchezMainGroup,
    help="Continuous Digital Elevation Models",
    fetchez_commands={
        "Commands": [
            "cudem",
            "fetchez",
            "gritz",
            "regions",
            "dlim",
            "perspecto",
            "transformez",
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

    setup_logging(name="fetchez", quiet=quiet, verbose=verbose)
    setup_logging(name="transformez", quiet=quiet, verbose=verbose)
    setup_logging(name="globato", quiet=quiet, verbose=verbose)


cli.add_command(wafflez_group, name="cudem")
cli.add_command(pipeline_group, name="fetchez")
cli.add_command(gritz_group, name="gritz")
cli.add_command(region_group, name="regions")
cli.add_command(pointz_group, name="dlim")
cli.add_command(perspecto_group, name="perspecto")
cli.add_command(transformez_cli, name="transformez")
cli.add_command(hooks_group, name="hooks")
cli.add_command(modules_group, name="modules")
cli.add_command(recipes_group, name="recipes")
cli.add_command(streams_group, name="streams")


if __name__ == "__main__":
    cli()
