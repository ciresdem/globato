#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.utils
~~~~~~~~~~~~~

Some utility functions for globato. Taken from cudem.utils

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import sys
import shutil
import subprocess
import logging
import io

from tqdm import tqdm
import numpy as np
from numpy.lib.recfunctions import append_fields

from fetchez.utils import parse_source_string as fetchez_parse_source

from transformez.utils import cmd_exists

logger = logging.getLogger(__name__)


def run_cmd(cmd, data_fun=None, verbose=False, cwd="."):
    """Run a system command while optionally passing data.

    `data_fun` should be a function to write to a file-port:
    >> data_fun = lambda p: datalist_dump(wg, dst_port = p, ...)
    """

    out = None
    cols, _ = shutil.get_terminal_size()
    width = cols - 55

    with tqdm(desc=f"`{cmd.rstrip()[:width]}...`", leave=verbose) as pbar:
        pipe_stdin = subprocess.PIPE if data_fun is not None else None

        p = subprocess.Popen(
            cmd,
            shell=True,
            stdin=pipe_stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            cwd=cwd,
        )

        if data_fun is not None:
            if verbose:
                logger.info("Piping data to cmd subprocess...")
            data_fun(p.stdin)
            p.stdin.close()

        io_reader = io.TextIOWrapper(p.stderr, encoding="utf-8")
        while p.poll() is None:
            err_line = io_reader.readline()
            if verbose and err_line:
                pbar.write(err_line.rstrip())
                sys.stderr.flush()
            pbar.update()

        out = p.stdout.read()
        p.stderr.close()
        p.stdout.close()

        if verbose:
            logger.info(f"Ran cmd {cmd.rstrip()} and returned {p.returncode}")

    return out, p.returncode


def yield_cmd(cmd, data_fun=None, verbose=False, cwd="."):
    """Yield output from a system command.

    `data_fun` should be a function to write to a file-port:
    >> data_fun = lambda p: datalist_dump(wg, dst_port = p, ...)
    """

    if verbose:
        logger.info(f"Running cmd {cmd.rstrip()}...")

    pipe_stdin = subprocess.PIPE if data_fun is not None else None

    p = subprocess.Popen(
        cmd,
        shell=True,
        stdin=pipe_stdin,
        stdout=subprocess.PIPE,
        close_fds=True,
        cwd=cwd,
    )

    if data_fun is not None:
        if verbose:
            logger.info("Piping data to cmd subprocess...")
        data_fun(p.stdin)
        p.stdin.close()

    while p.poll() is None:
        line = p.stdout.readline().decode("utf-8")
        if not line:
            break
        yield line

    p.stdout.close()
    if verbose:
        logger.info(f"Ran cmd {cmd.rstrip()}, returned {p.returncode}.")


def cmd_check(cmd_str, cmd_vers_str):
    """check system for availability of 'cmd_str'"""

    if cmd_exists(cmd_str):
        cmd_vers, status = run_cmd(f"{cmd_vers_str}")
        return cmd_vers.rstrip()
    return b"0"


def add_field_to_recarray(rec, name, dtype, default_val):
    """Append a new field to a structured array/recarray."""

    if name not in rec.dtype.names:
        new_col = np.full(len(rec), default_val, dtype=dtype)

        return append_fields(rec, name, new_col, usemask=False, asrecarray=True)
    return rec


# --- Region parsing ---
def yield_parsed_regions(region_str):
    """Universally parses a region string, location, or geojson file.

    Yields (Region, feature_name) for every region found.
    """

    from fetchez.spatial import parse_region, Region

    if not region_str:
        yield None, None
        return

    try:
        raw_regions = parse_region(region_str)
    except Exception as e:
        raise ValueError(f"Error parsing region '{region_str}': {e}")

    is_batch = len(raw_regions) > 1
    for i, r in enumerate(raw_regions):
        t_reg = Region(*r)
        # feat_name = f"tile_{i:03d}" if is_batch else None
        feat_name = t_reg.format("fn") if is_batch else None
        yield t_reg, feat_name


# --- Source and Hook parsing ---
def parse_source_string(source_str):
    """Globato-specific wrapper that guarantees stream_data is injected."""

    # return fetchez_parse_source(source_str, default_hooks=[{"name": "stream_data"}])
    # return fetchez_parse_source(source_str, default_hooks=[{"name": "stream-init"}])
    return fetchez_parse_source(source_str)#, default_hooks=[{"name": "stream-init"}])


def compile_sources(sources, shared_cache=None):

    import yaml

    compiled_modules = []
    for src in sources:
        if str(src).lower().endswith((".yaml", ".yml")) and os.path.exists(src):
            try:
                with open(src, "r") as f:
                    partial_recipe = yaml.safe_load(f)
                    if "modules" in partial_recipe:
                        compiled_modules.extend(partial_recipe["modules"])
                        logger.debug(f"Imported {len(partial_recipe['modules'])} modules from {src}")
            except Exception as e:
                logger.debug(f"Failed to read modules from {src}: {e}")
                continue
        elif src == "-":
            continue  # TODO: add stdin support
        else:
            compiled_modules.append(parse_source_string(src))

    return compiled_modules


def globatize_modules(modules, shared_cache=None, crs=None):
    abs_cache = os.path.abspath(shared_cache) if shared_cache else None

    for mod in modules:
        hooks = mod.setdefault("hooks", [])

        # -- Shared Cache Directory --
        if abs_cache and mod.get("module") not in ["file", "local_fs", "stdin"]:
            mod.setdefault("args", {})["outdir"] = abs_cache

        # # -- Make sure the source has a stream initiator ---
        # has_stream = any(h.get("name") in stream_initiators for h in hooks)
        # if not has_stream:
        #     hooks.append({"name": "stream-init"})
        #     logger.debug(
        #         f"Auto-injected 'stream-init' into module '{mod.get('module')}'"
        #     )

        # --- Insert the target crs into stream-reproject ---
        if crs:
            reproject_hook = None
            for h in hooks:
                if h.get("name") in ["stream_reproject", "stream-reproject"]:
                    reproject_hook = h
                    break

            if reproject_hook:
                reproject_hook.setdefault("args", {})["dst_srs"] = crs

            else:
                hooks.append({"name": "stream_reproject", "args": {"dst_srs": crs}})

    return modules


# --- Recipe building ---

def make_recipe_config(name, r_str, modules, hooks, threads=4):
    config = {
        "project": {"name": name},
        "region": r_str,  # Provide the buffered region to the modules
        "modules": modules,  # Use our compiled modules list
        "l_hooks": hooks,  # Use compiled global dem-building hooks
        "execution": {"threads": threads},
    }

    return config


# -- rasterio helpers ---
def is_valid_window(window_tuple):
    """Safeguard against Rasterio's zero-width truncation quirk.
    Accepts a tuple of (col_off, row_off, width, height) or a Rasterio Window.
    """

    from rasterio.windows import Window

    if isinstance(window_tuple, Window):
        w, h = window_tuple.width, window_tuple.height
    else:
        _, _, w, h = window_tuple

    return w > 0 and h > 0


def safe_window_read(src, window):
    """Reads a window from a Rasterio dataset safely.
    Prevents the GDAL/NumPy broadcasting crash on edge chunks.
    """

    if not is_valid_window(window):
        return None

    data = src.read(window=window)

    # Rasterio can still truncate at the exact file edge, so we verify
    # the returned array actually has data to broadcast against.
    if 0 in data.shape:
        return None

    return data
