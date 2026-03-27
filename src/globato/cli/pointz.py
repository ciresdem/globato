#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.pointz
~~~~~~~~~~~~~~~~~~
Point cloud filtering and manipulation.
"""

import os
import sys
import click
import logging
import numpy as np

from fetchez.registry import HookRegistry
from fetchez.core import run_fetchez

from globato.hooks.formats.stream_factory import StreamFactory
from globato.hooks.transforms.reproject import StreamReproject
from transformez.spatial import TransRegion

logger = logging.getLogger(__name__)


@click.group(name="pointz")
def pointz_group():
    """Filter, transform, and stream point cloud data (XYZ/LAS)."""

    pass


def _parse_filter_string(f_str):
    """Parses 'outlierz:percentile=95,res=50' into a name and kwargs dict."""

    parts = f_str.split(":", 1)
    name = parts[0]
    kwargs = {}
    if len(parts) > 1:
        for kv in parts[1].split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                try:
                    v = float(v) if "." in v else int(v)
                except ValueError:
                    if v.lower() in ['true', 'yes']: v = True
                    elif v.lower() in ['false', 'no']: v = False
                kwargs[k] = v
    return name, kwargs


def _yield_stdin_chunks(chunk_size=100000):
    """A lightweight generator to stream XYZ data from standard input."""

    import pandas as pd
    try:
        for chunk in pd.read_csv(sys.stdin, delim_whitespace=True, header=None,
                                 names=['x', 'y', 'z', 'w', 'u'], usecols=[0, 1, 2],
                                 chunksize=chunk_size, engine='c'):
            yield chunk.to_records(index=False)
    except Exception as e:
        logger.error(f"Error reading from stdin: {e}")


@pointz_group.command("list-filters")
def pointz_list_filters():
    """List all available point cloud filters."""

    HookRegistry.load_all()
    registry = HookRegistry.get_registry()

    click.secho("\n Available PointZ Filters:", fg="cyan", bold=True)
    click.echo("=" * 50)
    for name, meta in sorted(registry.items()):
        if meta.get("category") == "stream-filter":
            desc = meta.get("desc", "No description provided.")
            click.echo(f"  {click.style(name, bold=True, fg='yellow'):<15} : {desc}")
    click.echo("=" * 50 + "\n")


@pointz_group.command("run")
@click.argument("source")
@click.option("-F", "--filter", "filters", multiple=True, help="Apply a filter (e.g., rq:threshold=10). Can be used multiple times.")
@click.option("-R", "--region", help="Spatial crop (W/E/S/N).")
@click.option("-S", "--s-srs", help="Source SRS (e.g., EPSG:4326). Optional, will auto-detect if possible.")
@click.option("-T", "--t-srs", help="Target SRS for on-the-fly reprojection.")
@click.option("-O", "--out", help="Output file (default: stdout).")
@click.option("--chunk-size", type=int, default=500000, help="Number of points per memory chunk.")
def pointz_run(source, filters, region, s_srs, t_srs, out, chunk_size):
    """Stream, filter, and format point cloud data.

    SOURCE can be a local file (data.las), a Fetchez module (mbdb), or '-' for stdin.

    Examples:
      globato pointz run data.xyz -F rq:threshold=10 -O clean.xyz
      cat raw.xyz | globato pointz run - -F outlierz > clean.xyz
      globato pointz run mbdb -R loc:"Miami" -T EPSG:3857 > miami_web_mercator.xyz
    """

    HookRegistry.load_all()
    active_filters = []

    for f_str in filters:
        f_name, f_kwargs = _parse_filter_string(f_str)
        mod_cls = HookRegistry.get_class(f_name)
        if not mod_cls:
            click.secho(f"Error: Unknown filter '{f_name}'", fg="red", err=True)
            sys.exit(1)

        # if "res" not in f_kwargs: f_kwargs["res"] = 0.001
        active_filters.append(mod_cls(**f_kwargs))

    streams = []
    parsed_region = TransRegion.from_string(region) if region else None

    if source == "-":
        streams.append({
            "generator": _yield_stdin_chunks(chunk_size),
            "src_srs": s_srs or "EPSG:4326"
        })
        click.secho("Reading from standard input...", fg="cyan", err=True)

    elif os.path.exists(source):
        reader = StreamFactory.get_reader(source, chunk_size=chunk_size)
        if reader:
            detected_srs = s_srs
            if not detected_srs and hasattr(reader, "get_srs"):
                detected_srs = reader.get_srs()

            streams.append({
                "generator": reader.yield_chunks(),
                "src_srs": detected_srs or "EPSG:4326"
            })
        click.secho(f"Reading local file: {source}", fg="cyan", err=True)

    else:
        mod_cls = HookRegistry.get_class(source)
        if not mod_cls:
            click.secho(f"Error: '{source}' is not a file or known module.", fg="red", err=True)
            sys.exit(1)

        if not parsed_region:
            click.secho("Error: You must provide a --region (-R) when streaming a module.", fg="red", err=True)
            sys.exit(1)

        click.secho(f"Fetching live data from '{source}'...", fg="cyan", err=True)
        fetcher = mod_cls(src_region=parsed_region)
        fetcher.run()
        run_fetchez([fetcher])

        for entry in fetcher.results:
            dst_fn = entry.get("dst_fn")
            if dst_fn and os.path.exists(dst_fn):
                reader = StreamFactory.get_reader(dst_fn, chunk_size=chunk_size)
                if reader:
                    detected_srs = s_srs or entry.get("src_srs")
                    if not detected_srs and hasattr(reader, "get_srs"):
                        detected_srs = reader.get_srs()

                    streams.append({
                        "generator": reader.yield_chunks(),
                        "src_srs": detected_srs or "EPSG:4326"
                    })

    if not streams:
        click.secho("Error: No valid data streams could be established.", fg="red", err=True)
        sys.exit(1)

    out_port = open(out, 'w') if out else sys.stdout
    total_in = 0
    total_out = 0

    try:
        dummy_mod = type("Dummy", (), {"region": parsed_region})()
        for f in active_filters:
            if hasattr(f, 'setup'):
                if f.setup(dummy_mod, {}) is False:
                    click.secho(f"Error: Filter '{f.name}' failed to initialize. It likely requires a --region (-R).", fg="red", err=True)
                    sys.exit(1)

        for stream_dict in streams:
            stream_gen = stream_dict["generator"]
            current_srs = stream_dict["src_srs"]

            reproject_hook = None
            if t_srs and current_srs.lower() != t_srs.lower():
                click.secho(f"Reprojecting via hook: {current_srs} ➔ {t_srs}", fg="cyan", err=True)
                reproject_hook = StreamReproject(dst_srs=t_srs, src_srs=current_srs)

                pipeline = reproject_hook._get_pipeline(current_srs, region=parsed_region)
                if pipeline:
                    stream_gen = reproject_hook._apply_transform(stream_gen, pipeline)
                else:
                    click.secho("Warning: Could not establish reprojection pipeline. Skipping transform.", fg="yellow", err=True)

            for chunk in stream_gen:
                total_in += len(chunk)

                if parsed_region:
                    mask = (
                        (chunk['x'] >= parsed_region.xmin) & (chunk['x'] <= parsed_region.xmax) &
                        (chunk['y'] >= parsed_region.ymin) & (chunk['y'] <= parsed_region.ymax)
                    )
                    chunk = chunk[mask]

                for f in active_filters:
                    if len(chunk) == 0: break
                    outliers = f.filter_chunk(chunk)
                    chunk = chunk[~outliers]

                if len(chunk) > 0:
                    total_out += len(chunk)
                    np.savetxt(out_port, chunk[['x', 'y', 'z']], fmt='%.6f', delimiter=' ')

    except BrokenPipeError:
        sys.stderr.close()
    finally:
        for f in active_filters:
            if hasattr(f, 'teardown'):
                f.teardown()

        if out: out_port.close()

        click.secho(f"\nPointZ Complete: Processed {total_in:,} | Output {total_out:,} points.", fg="green", bold=True, err=True)
