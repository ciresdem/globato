#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli
~~~~~~~~~~~

The main command-line interface for the Globato framework.

:copyright: (c) 2025 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import click
import logging

# Globato CLI
# from .wafflez import wafflez_group
# from .gritz import gritz_group
# from .pointz import pointz_group
# from .perspecto import perspecto_group

# from .run import run_cmd
from .build import build_cmd
from .sources import sources_group
from fetchez.cli.recipes import run_recipe

from fetchez.cli import setup_logging
from fetchez.utils import FetchezMainGroup

logger = logging.getLogger(__name__)


@click.group(
    cls=FetchezMainGroup,
    fetchez_commands={
        "Commands": [
            "run",
            "build",
            "sources",
        ],
        # "Tools": [
        #     # "cudem",
        #     "gritz",
        #     "dlim",
        #     "perspecto",
        # ],
    },
)
@click.version_option(package_name="globato")
@click.option("--verbose", is_flag=True, help="Enable verbose debug logging.")
@click.option("--quiet", is_flag=True, help="Suppress non-error output.")
def cli(verbose, quiet):
    """Globato: The ContinUous-DEM Generation Framework.

    \b
    This is the GLOBATO automated DEM compilation engine. It takes overlapping
    streams of geospatial data, seamlessly stacks them based on quality weights,
    and interpolates the gaps to build continuous Digital Elevation Models (DEMs).

    \b
    Build a recipe to generate a custom DEM from scratch with the build command,
    or use a curated builtin or custom recipe to construct a DEM for your region
    and specifications with the make command.
    """

    # logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    setup_logging(name="fetchez", quiet=quiet, verbose=verbose)
    setup_logging(name="transformez", quiet=quiet, verbose=verbose)
    setup_logging(name="globato", quiet=quiet, verbose=verbose)


# cli.add_command(run_cmd, name="run")
cli.add_command(build_cmd, name="build")
# cli.add_command(wafflez_group, name="cudem")
# cli.add_command(gritz_group, name="gritz")
# cli.add_command(pointz_group, name="dlim")
# cli.add_command(perspecto_group, name="perspecto")
cli.add_command(sources_group, name="sources")
cli.add_command(run_recipe, name="run")


if __name__ == "__main__":
    cli()
