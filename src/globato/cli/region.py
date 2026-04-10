#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.region
~~~~~~~~~~~~~~~~~~~
Spatial management commands for generating, formatting, and splitting bounding boxes.
"""

import click
import json
import math
import sys

from globato.utils import yield_parsed_regions


@click.group(name="region")
def region_group():
    """Generate and manipulate spatial bounding boxes and tilesets."""

    pass


@region_group.command("echo")
@click.option(
    "-R",
    "--region",
    "region_str",
    required=True,
    help="Bounding box (W/E/S/N) or location string, or geojson file.",
)
@click.option(
    "--format",
    "-F",
    type=click.Choice(["gmt", "bbox", "wkt", "geojson", "fn"]),
    default="gmt",
    help="Output format.",
)
def region_echo(region_str, format):
    """Parse a region and echo it to stdout.

    Useful for geocoding a location and piping it to another command.
    Example: globato region echo --region loc:"San Diego, CA" -F wkt
    """

    try:
        for region, feat_name in yield_parsed_regions(region_str):
            prefix = f"{feat_name}: " if feat_name else ""
            click.echo(f"{prefix}{region.format(format)}")

    except ValueError as e:
        click.secho(str(e), fg="red")
        sys.exit(1)


@region_group.command("buffer")
@click.option(
    "-R",
    "--region",
    "region_str",
    required=True,
    help="Bounding box (W/E/S/N) or location string.",
)
@click.option(
    "--pct",
    type=float,
    default=5.0,
    help="Percentage to buffer the region (default: 5.0).",
)
@click.option(
    "--format",
    "-F",
    type=click.Choice(["gmt", "bbox", "wkt", "geojson", "fn"]),
    default="gmt",
)
def region_buffer(region_str, pct, format):
    """Expand a bounding box by a given percentage.

    Example: globato region buffer --region -120/-119/34/35 --pct 10
    """

    try:
        for region, feat_name in yield_parsed_regions(region_str):
            prefix = f"{feat_name}: " if feat_name else ""
            buffered = region.copy().buffer(pct=pct)
            click.echo(f"{prefix}{buffered.format(format)}")

    except ValueError as e:
        click.secho(str(e), fg="red")
        sys.exit(1)


@region_group.command("split")
@click.option(
    "-R",
    "--region",
    "region_str",
    required=True,
    help="Bounding box (W/E/S/N) or location string.",
)
@click.option(
    "--size",
    type=float,
    required=True,
    help="Tile size in decimal degrees (e.g., 0.25 for 1/4 degree tiles).",
)
@click.option(
    "--out", "-O", required=True, help="Output GeoJSON file to save the tileset."
)
@click.option(
    "--prefix",
    default="tile",
    help="Prefix for the generated tile names (default: 'tile').",
)
def region_split(region_str, size, out, prefix):
    """Split a massive region into a GeoJSON tileset for batch processing.

    Example: globato region split loc:"California" --size 0.5 -O cali_tiles.geojson
    """

    try:
        for region, feat_name in yield_parsed_regions(region_str):
            prefix = f"{feat_name}: " if feat_name else ""

            width = region.xmax - region.xmin
            height = region.ymax - region.ymin

            cols = math.ceil(width / size)
            rows = math.ceil(height / size)

            features = []
            count = 1

            for r in range(rows):
                for c in range(cols):
                    tile_w = region.xmin + (c * size)
                    tile_e = min(tile_w + size, region.xmax)
                    tile_s = region.ymin + (r * size)
                    tile_n = min(tile_s + size, region.ymax)

                    geom = {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [tile_w, tile_s],
                                [tile_w, tile_n],
                                [tile_e, tile_n],
                                [tile_e, tile_s],
                                [tile_w, tile_s],
                            ]
                        ],
                    }

                    # Format a tile name: e.g., tile_001
                    tile_name = f"{prefix}_{count:03d}"

                    features.append(
                        {
                            "type": "Feature",
                            "properties": {
                                "NAME": tile_name,
                                "w": tile_w,
                                "e": tile_e,
                                "s": tile_s,
                                "n": tile_n,
                            },
                            "geometry": geom,
                        }
                    )
                    count += 1

            feature_collection = {"type": "FeatureCollection", "features": features}

            with open(out, "w") as f:
                json.dump(feature_collection, f, indent=2)

            click.secho(
                f"Generated {len(features)} tiles ({cols}x{rows}) and saved to: {out}",
                fg="green",
            )
            click.echo(f"Run these using: globato recipe batch my_recipe.yaml {out}")

    except ValueError as e:
        click.secho(str(e), fg="red")
        sys.exit(1)


@region_group.command("transform")
@click.option(
    "-R",
    "--region",
    "region_str",
    required=True,
    help="Bounding box (W/E/S/N) or location string.",
)
@click.option(
    "--t-srs", required=True, help="Target spatial reference system (e.g., EPSG:3857)."
)
@click.option(
    "--s-srs",
    default="EPSG:4326",
    help="Source spatial reference system (default: EPSG:4326).",
)
@click.option(
    "--format",
    "-F",
    type=click.Choice(["gmt", "bbox", "wkt", "geojson", "fn"]),
    default="gmt",
    help="Output format.",
)
def region_transform(region_str, t_srs, s_srs, format):
    """Transform a region to a new coordinate reference system.

    Densifies the boundary before projecting to ensure safe encapsulation.

    Example: globato region transform loc:"San Francisco" --t-srs EPSG:3857
    """

    try:
        for region, feat_name in yield_parsed_regions(region_str):
            prefix = f"{feat_name}: " if feat_name else ""
            region.srs = s_srs
            warped = region.warp(dst_srs=t_srs)
            click.echo(f"{prefix}{warped.format(format)}")

    except ValueError as e:
        click.secho(str(e), fg="red")
        sys.exit(1)
