
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

logger = logging.getLogger(__name__)


# System Command Functions
cmd_exists = lambda x: any(os.access(os.path.join(path, x), os.X_OK)
                           for path in os.environ['PATH'].split(os.pathsep))


def run_cmd(cmd, data_fun=None, verbose=False, cwd='.'):
    """Run a system command while optionally passing data.

    `data_fun` should be a function to write to a file-port:
    >> data_fun = lambda p: datalist_dump(wg, dst_port = p, ...)
    """

    out = None
    cols, _ = shutil.get_terminal_size()
    width = cols - 55

    with tqdm(desc=f'`{cmd.rstrip()[:width]}...`', leave=verbose) as pbar:
        pipe_stdin = subprocess.PIPE if data_fun is not None else None

        p = subprocess.Popen(
            cmd, shell=True, stdin=pipe_stdin, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True, cwd=cwd
        )

        if data_fun is not None:
            if verbose:
                logger.info('Piping data to cmd subprocess...')
            data_fun(p.stdin)
            p.stdin.close()

        io_reader = io.TextIOWrapper(p.stderr, encoding='utf-8')
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
            logger.info(f'Ran cmd {cmd.rstrip()} and returned {p.returncode}')

    return out, p.returncode


def yield_cmd(cmd, data_fun=None, verbose=False, cwd='.'):
    """Yield output from a system command.

    `data_fun` should be a function to write to a file-port:
    >> data_fun = lambda p: datalist_dump(wg, dst_port = p, ...)
    """

    if verbose:
        logger.info(f'Running cmd {cmd.rstrip()}...')

    pipe_stdin = subprocess.PIPE if data_fun is not None else None

    p = subprocess.Popen(
        cmd, shell=True, stdin=pipe_stdin, stdout=subprocess.PIPE,
        close_fds=True, cwd=cwd
    )

    if data_fun is not None:
        if verbose:
            logger.info('Piping data to cmd subprocess...')
        data_fun(p.stdin)
        p.stdin.close()

    while p.poll() is None:
        line = p.stdout.readline().decode('utf-8')
        if not line:
            break
        yield line

    p.stdout.close()
    if verbose:
        logger.info(f'Ran cmd {cmd.rstrip()}, returned {p.returncode}.')


def cmd_check(cmd_str, cmd_vers_str):
    """check system for availability of 'cmd_str'"""

    if cmd_exists(cmd_str):
        cmd_vers, status = run_cmd(f'{cmd_vers_str}')
        return cmd_vers.rstrip()
    return b"0"


def add_field_to_recarray(rec, name, dtype, default_val):
    """Append a new field to a structured array/recarray."""

    if name not in rec.dtype.names:
        new_col = np.full(len(rec), default_val, dtype=dtype)

        return append_fields(rec, name, new_col, usemask=False, asrecarray=True)
    return rec


def parse_hook_string(hook_str, default_name=None):
    """Parses 'name:key=val:key2=val2' into a dictionary for hooks."""
    parts = hook_str.split(":")
    name = parts[0] if len(parts) > 0 and "=" not in parts[0] else default_name

    args = {}
    for part in parts[1:] if name == parts[0] else parts:
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                v = float(v) if "." in v else int(v)
            except ValueError:
                if v.lower() in ['true', 'yes']: v = True
                elif v.lower() in ['false', 'no']: v = False
            args[k] = v

    hook = {"name": name}
    if args:
        hook["args"] = args
    return hook


def parse_source_string(src_str):
    """Parses 'module:key=val+hook:k=v+hook2' or local paths into a module dictionary."""

    components = src_str.split("+")
    mod_part = components[0]
    hook_parts = components[1:]

    # 1. Parse the Module Part
    parts = mod_part.split(":", 1)
    mod_name = parts[0]
    args = {}

    if len(parts) > 1:
        for kv in parts[1].split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                try:
                    v = float(v) if "." in v else int(v)
                except ValueError:
                    if v.lower() in ['true', 'yes']: v = True
                    elif v.lower() in ['false', 'no']: v = False
                args[k] = v

    # Auto-detect local files and directories
    if os.path.exists(mod_name):
        if os.path.isfile(mod_name):
            args['paths'] = mod_name
            mod_name = 'file'
        elif os.path.isdir(mod_name):
            args['path'] = mod_name
            mod_name = 'local_fs'

    mod_dict = {
        "module": mod_name,
        "hooks": [{"name": "stream_data"}] # Always auto-inject stream_data
    }
    if args:
        mod_dict["args"] = args

    # Parse and Append Additional Hooks using the standard hook parser
    for h_str in hook_parts:
        mod_dict["hooks"].append(parse_hook_string(h_str))

    return mod_dict


def yield_parsed_regions(region_str):
    """Universally parses a region string, location, or geojson file.

    Yields (TransRegion, feature_name) for every region found.
    """

    from fetchez.spatial import parse_region
    from transformez.spatial import TransRegion

    if not region_str:
        yield None, None
        return

    try:
        raw_regions = parse_region(region_str)
    except Exception as e:
        raise ValueError(f"Error parsing region '{region_str}': {e}")

    # If parse_region returned multiple items, it's a batch job!
    is_batch = len(raw_regions) > 1

    for i, r in enumerate(raw_regions):
        t_reg = TransRegion(*r)
        feat_name = f"tile_{i:03d}" if is_batch else None
        yield t_reg, feat_name
