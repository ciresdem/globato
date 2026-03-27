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
from fetchez.registry import HookRegistry
from transformez.spatial import TransRegion
from globato.cli.pointz import _parse_filter_string
import numpy.lib.recfunctions as rfn

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
    """Generate a georeferenced colored hillshade.

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


@viz_group.command("points")
@click.argument("src")
@click.option("-F", "--filter", "filters", multiple=True, help="Apply filters on-the-fly to classify outliers.")
@click.option("-R", "--region", help="Spatial crop (required if using spatial filters like rq).")
@click.option("--3d", "is_3d", is_flag=True, help="Render an interactive 3D plot (auto-decimates large data).")
@click.option("--outliers", is_flag=True, help="Highlight rejected points (Class 7) in red.")
@click.option("--cmap", default="viridis", help="Colormap for Z-elevation (default: viridis).")
@click.option("--max-points", type=int, default=100000, help="Max points to render before decimating.")
def viz_points(src, filters, region, is_3d, outliers, cmap, max_points):
    """Visualize a point cloud for quick sanity checks and filter tuning.

    Examples:
      globato viz points data.xyz --3d
      globato viz points mbdb -R loc:"Miami" -F rq:threshold=5 --outliers
    """

    import numpy as np
    import matplotlib.pyplot as plt
    from globato.hooks.formats.stream_factory import StreamFactory
    from fetchez.core import run_fetchez

    if is_3d:
        from mpl_toolkits.mplot3d import Axes3D

    HookRegistry.load_all()
    active_filters = []
    parsed_region = TransRegion.from_string(region) if region else None
    dummy_mod = type("Dummy", (), {"region": parsed_region})()

    for f_str in filters:
        f_name, f_kwargs = _parse_filter_string(f_str)
        mod_cls = HookRegistry.get_class(f_name)
        if not mod_cls:
            click.secho(f"❌ Error: Unknown filter '{f_name}'", fg="red")
            sys.exit(1)

        f = mod_cls(**f_kwargs)
        if hasattr(f, 'setup') and f.setup(dummy_mod, {}) is False:
            click.secho(f"❌ Error: Filter '{f.name}' failed to initialize. Did you forget a --region (-R)?", fg="red")
            sys.exit(1)
        active_filters.append(f)

    click.secho(f"📥 Loading point cloud: {src}...", fg="cyan")

    if os.path.exists(src):
        reader = StreamFactory.get_reader(src)
        stream = reader.yield_chunks() if reader else []
    else:
        mod_cls = HookRegistry.get_class(src)
        if not mod_cls or not parsed_region:
            click.secho("❌ Error: Invalid file, or missing -R for module streaming.", fg="red")
            sys.exit(1)
        fetcher = mod_cls(src_region=parsed_region)
        fetcher.run()
        run_fetchez([fetcher])
        stream = []
        for entry in fetcher.results:
            if entry.get("dst_fn"):
                r = StreamFactory.get_reader(entry["dst_fn"])
                if r:
                    stream.extend(r.yield_chunks())

    processed_chunks = []
    for chunk in stream:
        if 'classification' not in chunk.dtype.names:
            #chunk = utils.add_field_to_recarray(chunk, 'classification', np.zeros(len(chunk), dtype=int))
            chunk = rfn.append_fields(chunk, 'classification', np.zeros(len(chunk), dtype=int), usemask=False)

        for f in active_filters:
            mask = f.filter_chunk(chunk)
            chunk['classification'][mask] = getattr(f, 'set_class', 7)

        processed_chunks.append(chunk)

    for f in active_filters:
        if hasattr(f, 'teardown'): f.teardown()

    if not processed_chunks:
        click.secho("❌ Error: No points found.", fg="red")
        sys.exit(1)

    points = rfn.stack_arrays(processed_chunks, asrecarray=True, usemask=False)
    total_pts = len(points)
    click.secho(f"📊 Ready to render {total_pts:,} points.", fg="green")

    fig = plt.figure(figsize=(10, 8))

    if outliers:
        click.echo("🎨 Rendering Outlier Showcase...")
        ax = fig.add_subplot(111)
        noise_mask = points['classification'] == 7
        valid_pts, noise_pts = points[~noise_mask], points[noise_mask]

        if len(valid_pts) > 0:
            ax.scatter(valid_pts['x'], valid_pts['y'], c='lightgray', s=1, alpha=0.5, label='Valid')
        if len(noise_pts) > 0:
            ax.scatter(noise_pts['x'], noise_pts['y'], c='red', s=5, marker='x', label='Rejected (Class 7)')

        ax.set_title(f"Filter Results: {len(noise_pts):,} Outliers Found")
        ax.legend()
        ax.set_aspect('equal', 'datalim')

    elif is_3d:
        click.echo("🧊 Rendering Interactive 3D View...")
        ax = fig.add_subplot(111, projection='3d')
        render_pts = points
        if total_pts > max_points:
            click.secho(f"⚠️ Decimating to {max_points:,} points for 3D performance...", fg="yellow")
            indices = np.random.choice(total_pts, max_points, replace=False)
            render_pts = points[indices]

        p = ax.scatter(render_pts['x'], render_pts['y'], render_pts['z'], c=render_pts['z'], cmap=cmap, s=2, alpha=0.8)
        fig.colorbar(p, ax=ax, label='Elevation (Z)')
        ax.set_title(f"3D Sanity Check ({len(render_pts):,} points)")

    else:
        click.echo("🗺️ Rendering Fast 2D Top-Down View...")
        ax = fig.add_subplot(111)
        if total_pts > max_points:
            hb = ax.hexbin(points['x'], points['y'], C=points['z'], gridsize=100, cmap=cmap, reduce_C_function=np.mean)
            fig.colorbar(hb, ax=ax, label='Mean Elevation (Z)')
        else:
            sc = ax.scatter(points['x'], points['y'], c=points['z'], cmap=cmap, s=2)
            fig.colorbar(sc, ax=ax, label='Elevation (Z)')
        ax.set_title(f"2D Elevation Map ({total_pts:,} points)")
        ax.set_aspect('equal', 'datalim')

    plt.tight_layout()
    plt.show()
