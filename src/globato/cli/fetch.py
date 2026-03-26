#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.fetch
~~~~~~~~~~~~~~~~~
Direct data discovery and downloading using the fetchez engine.
"""

import os
import sys
import click
import logging

from fetchez.registry import ModuleRegistry
from fetchez.core import run_fetchez
from .region import _parse_region

logger = logging.getLogger(__name__)

@click.group(name="fetch")
def fetch_group():
    """Discover and download raw elevation/bathymetry data."""

    pass

@fetch_group.command("list")
@click.option("--search", "-s", help="Filter modules by name or keyword.")
def fetch_list(search):
    """List all available data modules in the fetchez registry."""

    ModuleRegistry.load_all()
    registry = ModuleRegistry.get_registry()

    click.secho(f"\n📦 Available Fetchez Modules:", fg="cyan", bold=True)
    click.echo("=" * 50)

    count = 0
    for name, cls in sorted(registry.items()):
        if search and search.lower() not in name.lower():
            continue

        meta = ModuleRegistry.get_info(name)
        desc = meta.get('desc', 'No description available')

        click.secho(f"  {name}", fg="green", bold=True, nl=False)
        click.echo(f" - {desc}")
        count += 1

    click.echo("=" * 50)
    click.echo(f"Total modules found: {count}\n")


@fetch_group.command("run")
@click.argument("module_name")
@click.option("-R", "--region", required=True, help="Bounding box or location (e.g., loc:'Miami').")
@click.option("--outdir", "-O", default=".", help="Output directory for downloaded files.")
# Pass arbitrary kwargs like `datatype=3` into the fetchez module
@click.argument("extra_args", nargs=-1)
def fetch_run(module_name, region, outdir, extra_args):
    """Download data for a specific region.

    MODULE_NAME: The data source (e.g., gebco_cog, copernicus, tnm)
    EXTRA_ARGS: Optional key=value pairs to pass to the module (e.g., datatype=3)

    Example: globato fetch run copernicus -R loc:"Denver, CO" -O ./denver_data
    """

    ModuleRegistry.load_all()
    mod_cls = ModuleRegistry.get_class(module_name)

    if not mod_cls:
        click.secho(f"❌ Error: Unknown module '{module_name}'. Run 'globato fetch list' to see available options.", fg="red")
        sys.exit(1)

    parsed_region = _parse_region(region)
    click.secho(f"🌍 Target Region: [{parsed_region.xmin:.4f}, {parsed_region.xmax:.4f}, {parsed_region.ymin:.4f}, {parsed_region.ymax:.4f}]", fg="blue")

    kwargs = {}
    for arg in extra_args:
        if "=" in arg:
            k, v = arg.split("=", 1)
            try:
                v = float(v) if '.' in v else int(v)
            except ValueError:
                pass
            kwargs[k] = v

    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    original_cwd = os.getcwd()
    os.chdir(outdir)

    click.secho(f"🚀 Initializing {module_name} fetcher...", fg="cyan", bold=True)
    if kwargs:
        click.echo(f"   Using custom arguments: {kwargs}")

    try:
        fetcher = mod_cls(src_region=parsed_region, **kwargs)

        fetcher.run()
        run_fetchez([fetcher])

        click.secho(f"\n✅ Download complete! Files saved to: {outdir}", fg="green", bold=True)

    except Exception as e:
        click.secho(f"\n❌ Fetch failed: {e}", fg="red")
    finally:
        os.chdir(original_cwd)
