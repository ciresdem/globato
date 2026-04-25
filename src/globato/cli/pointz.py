#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.pointz
~~~~~~~~~~~~~~~~~~
Point cloud filtering and manipulation.

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import click
import yaml
from fetchez.recipe import Recipe
from fetchez.utils import parse_hook_string

from globato.utils import parse_source_string

# --- OLD POINTZ-GROUP --
import os
import sys
import logging
import numpy as np

from fetchez.registry import HookRegistry, ModuleRegistry
from fetchez.core import run_fetchez

from globato.hooks.formats.stream_factory import StreamFactory
from globato.hooks.transforms.reproject import StreamReproject
from transformez.spatial import TransRegion

logger = logging.getLogger(__name__)


@click.command(name="pipeline", hidden=False)
@click.argument("src", nargs=-1, required=True)
@click.option("-R", "--region", help="Spatial crop (W/E/S/N).")
@click.option(
    "-I", "--inc", help="Grid increment (e.g., 1s, 0.0001). Triggers stacking!"
)
@click.option("-T", "--t-srs", help="Target SRS for reprojection (e.g., EPSG:4326).")
@click.option(
    "-h", "--hook", multiple=True, help="Processing hooks (e.g., rq:threshold=2.5)"
)
@click.option("-o", "--output", help="Output file (Default: stdout).")
@click.option("--save-only", is_flag=True, help="Save the pipeline as YAML.")
def pointz_cmd(src, region, inc, t_srs, hook, output, save_only):
    """Build and execute a 3D point cloud processing pipeline."""

    modules = []
    for src_str in src:
        if src_str == "-":
            modules.append({"module": "stdin", "args": {}})
            continue

        parsed = parse_source_string(src_str)
        mod_dict = {"module": parsed["module"], "args": parsed.get("args", {})}
        if parsed.get("hooks"):
            mod_dict["hooks"] = parsed["hooks"]
        modules.append(mod_dict)

    global_hooks = []
    # global_hooks.append({"name": "stream_data", "args": {"stream_type": "xyz"}})

    if t_srs:
        global_hooks.append({"name": "stream_reproject", "args": {"dst_srs": t_srs}})

    if inc and region:
        global_hooks.append({"name": "simple_stack", "args": {"inc": inc}})

    for h_str in hook:
        parsed_hook = parse_hook_string(h_str)
        global_hooks.append(parsed_hook)

    global_hooks.append({"name": "xyz_write", "args": {"output_path": output}})

    config = {
        "project": {"name": "pointz_pipeline"},
        "region": region,
        "modules": modules,
        "global_hooks": global_hooks,
    }

    if save_only:
        out_yaml = "pointz_recipe.yaml"
        with open(out_yaml, "w") as f:
            yaml.dump(config, f, sort_keys=False)
        click.secho(f"Recipe saved to {out_yaml}", fg="green", bold=True, err=True)
    else:
        click.secho("Executing PointZ Pipeline...", fg="cyan", err=True)
        Recipe.from_file(config).run()


# --- OLD POINTZ-GROUP --
@click.group(name="pointz")
def pointz_group():
    """Filter, transform, and stream point cloud data (XYZ/LAS)."""

    pass


def _yield_stdin_chunks(chunk_size=100000):
    """A lightweight generator to stream XYZ data from standard input."""

    import pandas as pd

    try:
        for chunk in pd.read_csv(
            sys.stdin,
            delim_whitespace=True,
            header=None,
            names=["x", "y", "z", "w", "u"],
            usecols=[0, 1, 2],
            chunksize=chunk_size,
            engine="c",
        ):
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
@click.argument("sources", nargs=-1)
@click.option(
    "-F",
    "--filter",
    "global_filters",
    multiple=True,
    help="Apply a global filter (e.g., rq:threshold=10) to all sources.",
)
@click.option("-R", "--region", help="Spatial crop (W/E/S/N).")
@click.option("-T", "--t-srs", help="Target SRS for on-the-fly reprojection.")
@click.option("-O", "--out", help="Output file (default: stdout).")
@click.option(
    "--chunk-size", type=int, default=500000, help="Number of points per memory chunk."
)
def pointz_run(sources, global_filters, region, t_srs, out, chunk_size):
    """Stream, filter, and format point cloud data.

    SOURCES can be local files (data.las), Fetchez modules (mbdb), or '-' for stdin.
    Use the '+' syntax to attach specific arguments and filters directly to a source!

    Examples:\n
      globato pointz run data.xyz+rq:threshold=10 -O clean.xyz

      cat raw.xyz | globato pointz run - -F outlierz > clean.xyz

      globato pointz run mbdb:want_inf=False+rq:threshold=10 -R loc:"Miami" -T EPSG:3857 > miami.xyz
    """

    if not sources:
        click.secho("Error: You must provide at least one source.", fg="red", err=True)
        sys.exit(1)

    HookRegistry.load_all()
    ModuleRegistry.load_all()

    active_global_filters = []
    for f_str in global_filters:
        hook_dict = parse_hook_string(f_str)
        mod_cls = HookRegistry.get_class(hook_dict["name"])
        if not mod_cls:
            click.secho(
                f"Error: Unknown global filter '{hook_dict['name']}'",
                fg="red",
                err=True,
            )
            sys.exit(1)
        active_global_filters.append(mod_cls(**hook_dict.get("args", {})))

    parsed_region = TransRegion.from_string(region) if region else None

    dummy_mod = type("Dummy", (), {"region": parsed_region})()

    for f in active_global_filters:
        if hasattr(f, "setup") and f.setup(dummy_mod, {}) is False:
            click.secho(
                f"Error: Global filter '{f.name}' failed to initialize. It likely requires a --region (-R).",
                fg="red",
                err=True,
            )
            sys.exit(1)

    streams = []

    for src_str in sources:
        if src_str == "-":
            streams.append(
                {
                    "generator": _yield_stdin_chunks(chunk_size),
                    "src_srs": "EPSG:4326",
                    "filters": [],
                }
            )
            click.secho("Reading from standard input...", fg="cyan", err=True)
            continue

        parsed_src = parse_source_string(src_str)
        mod_name = parsed_src["module"]
        mod_args = parsed_src.get("args", {})

        source_filters = []
        for hook_dict in parsed_src.get("hooks", []):
            if hook_dict["name"] == "stream_data":
                continue
            mod_cls = HookRegistry.get_class(hook_dict["name"])
            if mod_cls:
                f_instance = mod_cls(**hook_dict.get("args", {}))
                if hasattr(f_instance, "setup"):
                    f_instance.setup(dummy_mod, {})
                source_filters.append(f_instance)
            else:
                click.secho(
                    f"Warning: Unknown hook '{hook_dict['name']}' attached to {mod_name}",
                    fg="yellow",
                    err=True,
                )

        if mod_name in ["file", "local_fs"]:
            target_path = mod_args.get("paths", mod_args.get("path"))
            reader = StreamFactory.get_reader(target_path, chunk_size=chunk_size)
            if reader:
                streams.append(
                    {
                        "generator": reader.yield_chunks(),
                        "src_srs": reader.get_srs()
                        if hasattr(reader, "get_srs") and reader.get_srs()
                        else "EPSG:4326",
                        "filters": source_filters,
                    }
                )
            click.secho(f"Reading local source: {target_path}", fg="cyan", err=True)
        else:
            mod_cls = ModuleRegistry.get_class(mod_name)
            if not mod_cls:
                click.secho(
                    f"Error: '{mod_name}' is not a file or known module.",
                    fg="red",
                    err=True,
                )
                sys.exit(1)

            if not parsed_region:
                click.secho(
                    f"Error: You must provide a --region (-R) when streaming the '{mod_name}' module.",
                    fg="red",
                    err=True,
                )
                sys.exit(1)

            click.secho(f"Fetching live data from '{mod_name}'...", fg="cyan", err=True)
            fetcher = mod_cls(src_region=parsed_region, **mod_args)
            fetcher.run()
            run_fetchez([fetcher])

            for entry in fetcher.results:
                dst_fn = entry.get("dst_fn")
                if dst_fn and os.path.exists(dst_fn):
                    reader = StreamFactory.get_reader(
                        dst_fn,
                        data_type=entry.get("data_type", None),
                        chunk_size=chunk_size,
                    )
                    if reader:
                        detected_srs = entry.get("src_srs")
                        if not detected_srs and hasattr(reader, "get_srs"):
                            detected_srs = reader.get_srs()

                        streams.append(
                            {
                                "generator": reader.yield_chunks(),
                                "src_srs": detected_srs or "EPSG:4326",
                                "filters": source_filters,
                            }
                        )

    if not streams:
        click.secho(
            "Error: No valid data streams could be established.", fg="red", err=True
        )
        sys.exit(1)

    out_port = open(out, "w") if out else sys.stdout
    total_in = 0
    total_out = 0

    try:
        for stream_dict in streams:
            stream_gen = stream_dict["generator"]
            current_srs = stream_dict["src_srs"]
            local_filters = stream_dict["filters"]

            if t_srs and current_srs and current_srs.lower() != t_srs.lower():
                reproject_hook = StreamReproject(dst_srs=t_srs, src_srs=current_srs)
                pipeline = reproject_hook._get_pipeline(
                    current_srs, region=parsed_region
                )
                if pipeline:
                    stream_gen = reproject_hook._apply_transform(stream_gen, pipeline)
                else:
                    click.secho(
                        "Warning: Could not establish reprojection pipeline.",
                        fg="yellow",
                        err=True,
                    )

            for chunk in stream_gen:
                total_in += len(chunk)

                if parsed_region:
                    mask = (
                        (chunk["x"] >= parsed_region.xmin)
                        & (chunk["x"] <= parsed_region.xmax)
                        & (chunk["y"] >= parsed_region.ymin)
                        & (chunk["y"] <= parsed_region.ymax)
                    )
                    chunk = chunk[mask]

                for f in local_filters:
                    if len(chunk) == 0:
                        break
                    outliers = f.filter_chunk(chunk)
                    chunk = chunk[~outliers]

                for f in active_global_filters:
                    if len(chunk) == 0:
                        break
                    outliers = f.filter_chunk(chunk)
                    chunk = chunk[~outliers]

                if len(chunk) > 0:
                    total_out += len(chunk)
                    np.savetxt(
                        out_port,
                        chunk[["x", "y", "z", "w", "u"]],
                        fmt="%.6f",
                        delimiter=" ",
                    )

    except BrokenPipeError:
        sys.stderr.close()
    finally:
        for f in active_global_filters:
            if hasattr(f, "teardown"):
                f.teardown()

        for s_dict in streams:
            for f in s_dict["filters"]:
                if hasattr(f, "teardown"):
                    f.teardown()

        if out:
            out_port.close()
        click.secho(
            f"\nPointZ Complete: Processed {total_in:,} | Output {total_out:,} points.",
            fg="green",
            bold=True,
            err=True,
        )


@pointz_group.command("info")
@click.argument("source")
def pointz_info(source):
    """Scan a point cloud and return its spatial statistics."""

    from globato.hooks.metadata.globato_inf import generate_stream_inf

    # parsed_src = parse_source_string(source)
    reader = StreamFactory.get_reader(source)  # , chunk_size=chunk_size)

    inf = generate_stream_inf(reader.yield_chunks())  # , "test.inf")
    while True:
        try:
            next(inf)
        except StopIteration as e:
            meta = e.value
            break

    region = meta.get("minmax", None)
    click.secho(f"\n--- Point Cloud Info: {source} ---", fg="cyan", bold=True)
    click.echo(f"Total Points : {meta.get('numpts', 0):,}")
    if region is not None:
        click.echo(f"Bounds (X)   : {region[0]:.6f} to {region[1]:.6f}")
        click.echo(f"Bounds (Y)   : {region[2]:.6f} to {region[3]:.6f}")
        click.echo(f"Elevation (Z): {region[4]:.3f} to {region[5]:.3f}")


pointz_group.add_command(pointz_cmd)
