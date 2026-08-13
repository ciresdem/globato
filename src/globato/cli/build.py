#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.build
~~~~~~~~~~~~~~~~~~

The globato build command to build a fetchez recipe and execute it.

:copyright: (c) 2025 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import sys
import click
import logging

from fetchez.utils import (
    FetchezMainCommand,
)
import globato.api

logger = logging.getLogger(__name__)


# --- Build command ---
CONTEXT_SETTINGS = dict(max_content_width=220)


@click.command("build", cls=FetchezMainCommand, context_settings=CONTEXT_SETTINGS)
@click.option("-R", "--region", required=True, help="Bounding box: W/E/S/N")
@click.option(
    "-E", "--increment", required=True, help="Gridding Increment (e.g., 1s, 30m)"
)
@click.option("-O", "--outname", default="globato_dem", help="Output Basename")
@click.option(
    "-D",
    "--outdir",
    # type=click.Path(resolve_path=True),
    type=click.Path(),
    default=None,
    help="Base output directory.",
)
@click.option("-F", "--format", default="GTiff", help="Output Format.")
@click.option("-P", "--t-srs", default="EPSG:4326", help="Target Projection")
@click.option("-N", "--nodata", type=float, default=-9999.0, help="NoData Value.")
@click.option(
    "-M",
    "--algo",
    default="ms_binary_cudem:barrier=coastline,algos=interp_gmt:tension=.95",
    help="Interpolation algorithm",
)
@click.option(
    "-A",
    "--stack-mode",
    type=click.Choice(["mean", "min", "max", "mixed", "supercede"]),
    default="mixed",
)
@click.option("-T", "--filter", "filters", multiple=True, help="Apply Grits Filter.")
@click.option("-C", "--clip", help="Clip output to polygon file.")
@click.option(
    "-B", "--blend", type=str, default=None, help="Blend between weighted data."
)
@click.option(
    "-X", "--extend", type=str, default="0:0", help="Extend region (cells[:percent])."
)
@click.option("-L", "--limits", type=str, default=None, help="Set global DEM limits.")
@click.option(
    "-W", "--weights", default="auto", help="Weight thresholds ('auto' or '1.0/0.5')."
)
@click.option(
    "--modifier",
    multiple=True,
    help="Apply a recipe modifier at runtime (e.g., exclude_module:modules=csb/tnm).",
)
@click.option(
    "--schema",
    multiple=True,
    help="Apply a domain schema validation to the recipe.",
)
@click.option(
    "--shared-cache",
    # type=click.Path(resolve_path=True),
    type=click.Path(),
    help="Centralized cache directory.",
)
@click.option("--metadata", help="Global tags to inject.")
@click.option("--export", is_flag=True, help="Save the generated YAML recipe to disk.")
@click.option(
    "--refresh", is_flag=True, help="Force fresh API fetch, bypassing local cache."
)
@click.option(
    "--fail-fast",
    is_flag=True,
    help="Raise an exception on the first failure, otherwise continue processing through failures.",
)
@click.argument("sources", nargs=-1)
def build_cmd(sources, **kwargs):
    """Build a Digital Elevation Model recipe, and execute it."""

    if not sources:
        click.secho(
            "Error: You must provide at least one data source or a modules.yaml file.",
            fg="red",
        )
        sys.exit(1)

    try:
        if not kwargs.get("export"):
            click.secho(
                f"Executing dynamic recipe for {kwargs.get('outname')}...",
                fg="cyan",
                bold=True,
            )

        # Use the API here.
        # for _config, _region, _batch_name, _cache_dir, _base_outdir, _tile_dir in globato.api.build(sources=sources, **kwargs):
        #     click.secho(f"Processing: {batch_name} recipe.", bold=True)
        [g for g in globato.api.build(sources=sources, **kwargs)]

        if kwargs.get("export"):
            click.secho(
                f"Globato recipe exported to {kwargs.get('outname')}_recipe.yaml.",
                fg="green",
                bold=True,
            )
        else:
            click.secho(
                "✨ Successfully completed Globato build pipeline!",
                fg="green",
                bold=True,
            )

    except Exception as e:
        click.secho(
            f"Failed to execute Globato pipeline!: {str(e)}", fg="red", bold=True
        )
        sys.exit(1)
