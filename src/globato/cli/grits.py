#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
The GRITS Command Line Interface.
Direct access to globato raster processors with in-memory streaming pipelines.
"""

import sys
import argparse
import logging
import os

from transformez.spatial import TransRegion
from fetchez.cli import parse_hook_arg
from fetchez.registry import HookRegistry

from globato.hooks.formats.raster_stream import RasterStreamInit
from globato.hooks.sinks.raster_writer import RasterWrite

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("grits")

class DummyMod:
    """A mock module to satisfy hook requirements."""
    def __init__(self, region=None):
        self.region = region

def run_raster_pipeline(src, dst, hooks, region=None, chunk_size=2048):
    """Orchestrates an in-memory raster stream."""

    if not os.path.exists(src):
        logger.error(f"Source file not found: {src}")
        return

    mod = DummyMod()
    if region:
        try:
            r_vals = [float(x) for x in region.split('/')]
            if len(r_vals) == 4:
                mod.region = TransRegion(r_vals)
            else:
                logger.error("Region must be W/E/S/N")
                return
        except Exception as e:
            logger.error(f"Invalid region format: {e}")
            return

    entry = {
        'dst_fn': src,
        'weight': 1.0
    }
    entries = [(mod, entry)]

    logger.info(f"Igniting raster stream for {src}")
    streamer = RasterStreamInit(chunk_size=chunk_size, buffer=20)
    entries = streamer.run(entries)

    HookRegistry.load_all() # Ensure all your hooks are discovered

    for hook_str in hooks:
        hook_name, kwargs = parse_hook_arg(hook_str)

        hook_cls = HookRegistry.get_class(hook_name)
        if not hook_cls:
            logger.error(f"Unknown hook: {hook_name}")
            return

        logger.info(f"Applying filter: {hook_name} {kwargs}")
        hook_instance = hook_cls(**kwargs)

        entries = hook_instance.run(entries)

    logger.info(f"Draining stream to {dst}")

    entry['dst_fn'] = dst

    writer = RasterWrite(suffix="", inline=False)
    entries = writer.run(entries)

    logger.info("Pipeline complete!")


def main():
    parser = argparse.ArgumentParser(description="GRITS Raster Streaming CLI")

    parser.add_argument("src", help="Source Raster (GeoTIFF)")
    parser.add_argument("dst", help="Destination Raster (GeoTIFF)")

    parser.add_argument(
        "-H", "--hook",
        action="append",
        default=[],
        help="Raster hooks to chain (e.g., -H ms_blend:weight_threshold=0.5)"
    )

    parser.add_argument("-R", "--region", help="Region constraint W/E/S/N")
    parser.add_argument("-C", "--chunk-size", type=int, default=2048, help="Stream chunk size")

    args = parser.parse_args()

    if not args.hook:
        logger.warning("No hooks specified! The output will just be a direct copy of the input.")

    run_raster_pipeline(args.src, args.dst, args.hook, args.region, args.chunk_size)

if __name__ == "__main__":
    main()
