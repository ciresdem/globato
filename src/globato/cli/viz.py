#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.viz
~~~~~~~~~~~~~~~
Visualization tools for DEMs and Point Clouds.
Replaces the legacy 'perspecto' suite.
"""

import os
import sys
import time
import click
import logging

logger = logging.getLogger(__name__)

@click.group(name="viz")
def viz_group():
    """Visualize DEMs and Point Clouds (Legacy Perspecto)."""
    pass

@viz_group.command("hillshade")
@click.argument("src")
@click.argument("dst")
@click.option("--azimuth", type=float, default=315.0, help="Sun azimuth angle in degrees (default: 315).")
@click.option("--altitude", type=float, default=45.0, help="Sun altitude angle in degrees (default: 45).")
@click.option("--exag", type=float, default=1.0, help="Vertical exaggeration factor (default: 1.0).")
@click.option("--cmap", default="etopo", help="Matplotlib colormap or CPT file/name (default: 'etopo').")
@click.option("--blend", type=click.Choice(["multiply", "screen", "overlay", "hard_light", "soft_light"]), default="soft_light", help="Blending mode (default: soft_light).")
@click.option("--alpha", is_flag=True, help="Add an alpha channel to mask NoData.")
@click.option("--gamma", type=float, help="Gamma correction factor.")
@click.option("--z-min", type=float, help="Force minimum Z value for the colormap.")
@click.option("--z-max", type=float, help="Force maximum Z value for the colormap.")
@click.option("--split-cpt", type=float, default=0.0, help="Hinge point for divergent colormaps (default: 0.0).")
def viz_hillshade(src, dst, azimuth, altitude, exag, cmap, blend, alpha, gamma, z_min, z_max, split_cpt):
    """Generate a beautiful, georeferenced colored hillshade.

    SRC: Input DEM (GeoTIFF or NetCDF)
    DST: Output colored hillshade (GeoTIFF)

    Example: globato viz hillshade my_dem.tif my_hillshade.tif --cmap etopo --exag 3 --blend soft_light
    """
    try:
        from globato.hooks.viz.geohillshade import GeoHillshade
    except ImportError as e:
        click.secho(f"❌ Error importing GeoHillshade: {e}", fg="red")
        sys.exit(1)

    if not os.path.exists(src):
        click.secho(f"❌ Error: Input file not found: {src}", fg="red")
        sys.exit(1)

    hook = GeoHillshade(
        azimuth=azimuth,
        altitude=altitude,
        vert_exag=exag,
        cmap=cmap,
        blend_mode=blend,
        alpha=alpha,
        gamma=gamma,
        z_min=z_min,
        z_max=z_max,
        split_cpt=split_cpt
    )

    click.secho(f"\n🎨 Generating Colored Hillshade: {os.path.basename(src)}...", fg="cyan", bold=True)
    click.echo(f"   Colormap: {cmap} | Exag: {exag}x | Blend: {blend}")

    start_time = time.time()

    entry = {'src_fn': src, 'dst_fn': dst}
    success = hook.process_raster(src, dst, entry)

    elapsed = time.time() - start_time

    if success:
        click.secho(f"\n✅ Visualization Complete! Saved to: {dst}", fg="green", bold=True)
        click.echo(f"   Time: {elapsed:.2f} seconds\n")
    else:
        click.secho("\n❌ Failed to generate hillshade.", fg="red")
        sys.exit(1)
