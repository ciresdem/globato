#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.cli.recipe
~~~~~~~~~~~~~~~~~~
The command-line interface for the recipe group.

list, dump, copy, validate, run, build
"""

import os
import sys
import click
import json
import yaml
import logging

from fetchez.recipe import Recipe
from fetchez.registry import RecipeRegistry
from fetchez.utils import parse_hook_string, str2inc
from globato.utils import parse_source_string, yield_parsed_regions

logger = logging.getLogger(__name__)


@click.group(name="recipe")
def recipe_group():
    """Execute and manage YAML DEM recipes."""
    pass


@recipe_group.command("list")
@click.option("--search", "-s", help="Filter recipes by name or keyword.")
def recipe_list(search):
    """List all available curated DEM recipes."""

    RecipeRegistry.load_all()
    registry = RecipeRegistry.get_registry()

    click.secho("\nAvailable Curated Recipes:", fg="cyan", bold=True)
    click.echo("=" * 60)

    count = 0
    for name, meta in sorted(registry.items()):
        if (
            search
            and search.lower() not in name.lower()
            and search.lower() not in meta["desc"].lower()
        ):
            continue

        click.secho(f"  {name:<25}", fg="green", bold=True, nl=False)
        click.echo(f" - {meta['desc']}")
        count += 1

    click.echo("=" * 60)
    click.echo(f"Total recipes found: {count}\n")


def _load_yaml(target):
    base_config = None
    if os.path.exists(target) and not os.path.isdir(target):
        with open(target, "r", encoding="utf-8") as f:
            base_config = yaml.safe_load(f)
    else:
        recipe_meta = RecipeRegistry.get_recipe(target)
        if recipe_meta:
            base_config = recipe_meta["config"]
            click.secho(f"Loaded curated recipe: {target}", fg="cyan")

    return base_config


def _absolutize_local_sources(config, base_dir):
    """Converts relative local paths in a recipe to absolute paths."""

    for mod in config.get("modules", []):
        mod_name = mod.get("module")
        if mod_name in ["file", "local_fs"]:
            args = mod.setdefault("args", {})

            # Handle local_fs 'path'
            if "path" in args:
                args["path"] = os.path.normpath(
                    os.path.join(base_dir, str(args["path"]))
                )

            # Handle file 'paths' (which could be comma-separated)
            if "paths" in args:
                paths = str(args["paths"]).split(",")
                abs_paths = [
                    os.path.normpath(os.path.join(base_dir, p.strip())) for p in paths
                ]
                args["paths"] = ",".join(abs_paths)

    return config


@recipe_group.command("run")
@click.argument("target")
@click.option(
    "-R",
    "--region",
    help="Override region. Can be a bounding box, loc string, or geojson file to trigger batch mode.",
)
@click.option(
    "-E", "--increment", help="Override gridding increment/resolution (e.g., 3s, 10)."
)
@click.option("-P", "--crs", help="Override target CRS (e.g., EPSG:3857).")
@click.option("-O", "--outname", help="Override project name / output basename.")
@click.option(
    "--outdir",
    type=click.Path(resolve_path=True),
    default=None,
    help="Base output directory for the tiles.",
)
@click.option(
    "--overwrite",
    is_flag=True,
    help="Force rebuild of already completed tiles in a batch run.",
)
@click.option(
    "--shared-cache",
    type=click.Path(resolve_path=True),
    help="Centralized directory to cache fetched data across all tiles.",
)
def recipe_run(
    target, region, increment, crs, outname, outdir, overwrite, shared_cache
):
    """Execute a YAML recipe. Supports single runs, batch execution, and config overrides."""
    import copy

    RecipeRegistry.load_all()

    base_config = _load_yaml(target)
    if not base_config:
        click.secho(
            f"Error: Recipe '{target}' not found locally or in the registry.", fg="red"
        )
        sys.exit(1)

    if outname:
        base_config.setdefault("project", {})["name"] = outname

    if increment or crs or outname:
        increment = str2inc(increment)
        for module in base_config.get("modules", []):
            for hook in module.get("hooks", []):
                if hook.get("name") == "stream_reproject":
                    if crs:
                        hook.setdefault("args", {})["dst_srs"] = crs

        for hook in base_config.get("global_hooks", []):
            hook_name = hook.get("name")
            if hook_name == "provenance":
                if increment:
                    hook.setdefault("args", {})["res"] = increment
                if outname:
                    hook.setdefault("args", {})["output"] = f"{outname}_provenance.tif"
            if hook_name == "multi_stack":
                if increment:
                    hook.setdefault("args", {})["res"] = increment
                if crs:
                    hook.setdefault("args", {})["crs"] = crs
                if outname:
                    hook.setdefault("args", {})["output"] = f"{outname}_stack.tif"

            if (
                hook_name == "ms_cudem"
                or hook_name == "interp_gmt"
                or hook_name == "raster_fill"
            ):
                if outname:
                    hook.setdefault("args", {})["output"] = f"{outname}_dem.tif"

                if increment and hook_name == "ms_cudem":
                    args = hook.setdefault("args", {})
                    old_res = args.get("resolutions", ["1s", "3s"])

                    if isinstance(old_res, str):
                        old_res_list = [str2inc(x) for x in old_res.split("/")]
                    else:
                        old_res_list = [str2inc(str(x)) for x in old_res]

                    num_steps = len(old_res_list)
                    old_base_res = old_res_list[0] if num_steps > 0 else str2inc("1s")
                    new_base_res = increment

                    args["resolutions"] = [
                        new_base_res * (3**i) for i in range(num_steps)
                    ]

                    if "blend_dist" in args:
                        old_blend = args["blend_dist"]
                        if isinstance(old_blend, str):
                            old_blend_list = [int(x) for x in old_blend.split("/")]
                        elif isinstance(old_blend, list):
                            old_blend_list = [int(x) for x in old_blend]
                        else:
                            old_blend_list = [int(old_blend)]

                        ratio = old_base_res / new_base_res
                        args["blend_dist"] = [
                            int(round(b * ratio)) for b in old_blend_list
                        ]
            # if (
            #     hook_name == "ms_cudem"
            #     or hook_name == "interp_gmt"
            #     or hook_name == "raster_fill"
            # ):
            #     if increment and hook_name == "ms_cudem":
            #         hook.setdefault("args", {})["resolutions"] = increment
            #     if outname:
            #         hook.setdefault("args", {})["output"] = f"{outname}_dem.tif"

            if hook_name == "viz_geoshade":
                if outname:
                    hook.setdefault("args", {})["output"] = f"{outname}_hillshade.tif"

    if outdir is None:
        base_outdir = os.path.abspath(".")
    else:
        base_outdir = os.path.abspath(outdir)
    os.makedirs(base_outdir, exist_ok=True)
    original_cwd = os.getcwd()
    base_config = _absolutize_local_sources(base_config, original_cwd)

    state_file = os.path.join(original_cwd, ".globato_batch_state.json")
    completed_tiles = []

    if os.path.exists(state_file) and not overwrite:
        try:
            with open(state_file, "r") as f:
                completed_tiles = json.load(f)
        except Exception:
            pass  # If the state file is corrupted, we just ignore it

    for t_reg, feat_name in yield_parsed_regions(region):
        try:
            _is_batch = False
            config = copy.deepcopy(base_config)
            if t_reg:
                config["region"] = (
                    f"{t_reg.xmin}/{t_reg.xmax}/{t_reg.ymin}/{t_reg.ymax}"
                )

            if feat_name:
                _is_batch = True
                orig_name = config.get("project", {}).get("name", "globato_dem")
                batch_name = f"{orig_name}_{feat_name}"
                config.setdefault("project", {})["name"] = batch_name
                click.secho(
                    f"\n--- Running Batch Tile: {batch_name} ({config['region']}) ---",
                    fg="cyan",
                    bold=True,
                )
            elif outname:
                batch_name = outname
                click.secho(
                    f"\n--- Running Recipe with Override: {batch_name} ---",
                    fg="cyan",
                    bold=True,
                )
            else:
                batch_name = config.get("project", {}).get("name", "globato_dem")

            if batch_name in completed_tiles and not overwrite:
                click.secho(
                    f"  Skipping completed tile: {batch_name} (use --overwrite to force)",
                    fg="yellow",
                    bold=True,
                )
                continue

            for hook in config.get("global_hooks", []):
                hook_name = hook.get("name")
                if hook_name == "provenance":
                    hook.setdefault("args", {})["output"] = (
                        f"{batch_name}_provenance.tif"
                    )
                if hook_name == "multi_stack":
                    hook.setdefault("args", {})["output"] = f"{batch_name}_stack.tif"
                if (
                    hook_name == "ms_cudem"
                    or hook_name == "interp_gmt"
                    or hook_name == "raster_fill"
                ):
                    hook.setdefault("args", {})["output"] = f"{batch_name}_dem.tif"
                if hook_name == "viz_geoshade":
                    hook.setdefault("args", {})["output"] = (
                        f"{batch_name}_hillshade.tif"
                    )

            if _is_batch or not outdir:
                tile_dir = os.path.join(base_outdir, batch_name)
                os.makedirs(tile_dir, exist_ok=True)
                os.chdir(tile_dir)

            if shared_cache:
                abs_cache = os.path.abspath(shared_cache)
                os.makedirs(abs_cache, exist_ok=True)

                for mod in config.get("modules", []):
                    if mod.get("module") not in ["file", "local_fs", "stdin"]:
                        mod.setdefault("args", {})["outdir"] = abs_cache
                    for hook in mod.get("hooks", []):
                        if hook.get("name") == "stream_reproject":
                            if not hook.get("args", None):
                                hook.setdefault("args", {})
                            hook["args"].update({"cache_dir": abs_cache})

            batch_config_fn = f"{batch_name}_recipe.yaml"
            with open(batch_config_fn, "w") as f:
                yaml.dump(config, f, sort_keys=False, default_flow_style=False)

            try:
                Recipe.from_file(config).run()

                completed_tiles.append(batch_name)
                with open(state_file, "w") as f:
                    json.dump(completed_tiles, f, indent=2)

            except Exception as e:
                click.secho(f"\n Tile {batch_name} failed: {e}", fg="red", bold=True)
                click.secho(
                    "Batch processing halted. Re-run command to resume from this tile.",
                    fg="yellow",
                )
                sys.exit(1)

        except ValueError as e:
            click.secho(str(e), fg="red")
        finally:
            os.chdir(original_cwd)


@recipe_group.command("info")
@click.argument("target")
def recipe_info(target):
    """Inspect a recipe's description and sources without running it."""

    RecipeRegistry.load_all()

    base_config = _load_yaml(target)

    proj = base_config.get("project", {})
    region = base_config.get("region", "Global")

    modules = base_config.get("modules", [])
    mod_names = []
    for m in modules:
        if isinstance(m, dict):
            mod_names.append(m.get("module", "Unknown"))
        else:
            mod_names.append(str(m))

    unique_mods = list(set(mod_names))

    click.secho(f"\n Recipe: {proj.get('name', target)}", fg="cyan", bold=True)
    click.echo(f"Description: {proj.get('description', 'No description provided.')}")
    click.echo(f"Region:      {region}")
    click.echo(f"Sources:     {', '.join(unique_mods)}\n")


@recipe_group.command("dump")
@click.argument("name")
def recipe_dump(name):
    """Dump the contents of a registered recipe to the terminal."""

    RecipeRegistry.load_all()
    recipe_meta = RecipeRegistry.get_recipe(name)

    if not recipe_meta:
        click.secho(f"Error: Recipe '{name}' not found in registry.", fg="red")
        sys.exit(1)

    click.secho(f"--- Recipe: {name} ---", fg="cyan", bold=True)
    click.echo(yaml.dump(recipe_meta["config"], sort_keys=False))


@recipe_group.command("copy")
@click.argument("name")
@click.option("-O", "--outdir", default=".", help="Where to save the recipe.")
def recipe_copy(name, outdir):
    """Copy a registered recipe to your local directory for editing."""

    RecipeRegistry.load_all()
    recipe_meta = RecipeRegistry.get_recipe(name)

    if not recipe_meta:
        click.secho(f"Error: Recipe '{name}' not found in registry.", fg="red")
        sys.exit(1)

    out_path = os.path.join(outdir, f"{name}_custom.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(yaml.dump(recipe_meta["config"], sort_keys=False))

    click.secho(f"Copied '{name}' to {out_path}", fg="green", bold=True)
    click.echo(
        "You can now edit this file and run it with: globato recipe run " + out_path
    )


@recipe_group.command("validate")
@click.argument("target")
def recipe_validate(target):
    """Check a YAML recipe for syntax errors and missing modules/hooks."""

    from fetchez.registry import ModuleRegistry, HookRegistry

    ModuleRegistry.load_all()
    HookRegistry.load_all()

    base_config = _load_yaml(target)
    if not base_config:
        click.secho(
            f"Error: Recipe '{target}' not found locally or in the registry.", fg="red"
        )
        sys.exit(1)

    errors = 0
    click.secho(f"Validating {target}...", fg="blue")

    for mod in base_config.get("modules", []):
        mod_name = mod.get("module")
        if not ModuleRegistry.get_class(mod_name) and mod_name not in [
            "file",
            "local_fs",
        ]:
            click.secho(f"  Missing Module: '{mod_name}'", fg="red")
            errors += 1
        else:
            click.secho(f"  Valid Module: '{mod_name}'", fg="green")

        for hook in mod.get("hooks", []):
            if not HookRegistry.get_class(hook.get("name")):
                click.secho(
                    f"  Missing Hook: '{hook.get('name')}' (in module {mod_name})",
                    fg="red",
                )
                errors += 1
            else:
                click.secho(
                    f"  Valid Hook: '{hook.get('name')}' (in module {mod_name})",
                    fg="green",
                )

    for hook in base_config.get("global_hooks", []):
        if not HookRegistry.get_class(hook.get("name")):
            click.secho(f"  Missing Global Hook: '{hook.get('name')}'", fg="red")
            errors += 1
        else:
            click.secho(f"  Valid Hook: '{hook.get('name')}'", fg="green")

    if errors == 0:
        click.secho("Recipe appears valid!", fg="green", bold=True)
    else:
        click.secho(f"Failed validation with {errors} errors.", fg="red", bold=True)
        sys.exit(1)


# --- Build command ---


def _parse_source(src_str):
    """Parses 'module:key=val+hook:k=v' or local paths into a dictionary for the recipe."""

    # Split the module definition from any appended hooks using '+'
    components = src_str.split("+")
    mod_part = components[0]
    hook_parts = components[1:]

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
                    if v.lower() in ["true", "yes"]:
                        v = True
                    elif v.lower() in ["false", "no"]:
                        v = False
                args[k] = v

    if os.path.exists(mod_name):
        if os.path.isfile(mod_name):
            args["paths"] = mod_name
            mod_name = "file"
        elif os.path.isdir(mod_name):
            args["path"] = mod_name
            mod_name = "local_fs"

    mod_dict = {
        "module": mod_name,
    }
    if args:
        mod_dict["args"] = args

    mod_dict["hooks"] = [{"name": "stream_data"}]

    for h_str in hook_parts:
        h_parts = h_str.split(":", 1)
        h_name = h_parts[0]
        h_args = {}

        if len(h_parts) > 1:
            for kv in h_parts[1].split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    try:
                        v = float(v) if "." in v else int(v)
                    except ValueError:
                        if v.lower() in ["true", "yes"]:
                            v = True
                        elif v.lower() in ["false", "no"]:
                            v = False
                    h_args[k] = v

        hook_dict = {"name": h_name}
        if h_args:
            hook_dict["args"] = h_args

        mod_dict["hooks"].append(hook_dict)

    return mod_dict


def _list_sources(ctx, param, value):
    """Eager callback to list available data sources and exit."""

    if not value or ctx.resilient_parsing:
        return

    from fetchez.registry import ModuleRegistry

    ModuleRegistry.load_all()
    registry = ModuleRegistry.get_registry()

    click.secho("\nCurated Globato Data Sources:", fg="cyan", bold=True)
    click.echo("=" * 60)

    count = 0
    for name, meta in sorted(registry.items()):
        if (
            meta.get("mod", "").startswith("globato.modules")
            or meta.get("category") == "Globato"
        ):
            if name in meta.get("aliases", []):
                continue

            desc = meta.get("desc", "No description provided.")
            click.echo(f"  {click.style(name, bold=True, fg='yellow'):<25} : {desc}")
            count += 1

    click.echo("-" * 60)
    click.secho("\nLocal File Support:", fg="cyan", bold=True)
    click.echo("=" * 60)
    click.echo("  You can also pass local files and directories directly!")
    click.echo("  Files will be wrapped in the 'file' module.")
    click.echo("  Directories will be crawled using the 'local_fs' module.")
    click.echo(
        "  Example: globato recipe build -R ... ./my_data.tif ./my_folder:ext=.xyz"
    )

    click.echo(
        f"\nTry 'globato recipe build --info-source <name>' for details. Total: {count}\n"
    )
    ctx.exit()


def _info_source(ctx, param, value):
    """inspect a specific data source and exit."""

    if not value or ctx.resilient_parsing:
        return

    from fetchez.registry import ModuleRegistry

    ModuleRegistry.load_all()
    registry = ModuleRegistry.get_registry()

    source_name = value
    if source_name not in registry:
        click.secho(f"Error: '{source_name}' is not a recognized source.", fg="red")
        ctx.exit(1)

    meta = registry[source_name]

    if not (
        meta.get("mod", "").startswith("globato.modules")
        or meta.get("category") == "Globato"
    ):
        click.secho(
            f" Note: '{source_name}' is a core Fetchez module, not a curated Globato DEM source.",
            fg="yellow",
        )

    click.secho(f"\nSOURCE: {source_name.upper()}", fg="cyan", bold=True)
    click.echo("=" * 60)
    click.echo(f"  Description : {meta.get('desc', 'N/A')}")
    click.echo(f"  Tags        : {', '.join(meta.get('tags', []))}")

    mod_cls = ModuleRegistry.get_class(source_name)
    if mod_cls:
        import inspect

        sig = inspect.signature(mod_cls.__init__)
        params = []
        for p_name, param in sig.parameters.items():
            if p_name not in [
                "self",
                "kwargs",
                "src_region",
                "callback",
                "outdir",
                "name",
            ]:
                default = (
                    param.default
                    if param.default is not inspect.Parameter.empty
                    else "None"
                )
                params.append(f"{p_name}={default}")
        if params:
            click.echo(f"  Arguments   : {', '.join(params)}")
    click.echo("\n")
    ctx.exit()


@recipe_group.command("build")
@click.option(
    "--list-sources",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_list_sources,
    help="List available data sources and exit.",
)
@click.option(
    "--info-source",
    metavar="NAME",
    is_eager=True,
    expose_value=False,
    callback=_info_source,
    help="Show details for a specific data source and exit.",
)
@click.option("-R", "--region", required=True, help="Bounding box: W/E/S/N")
@click.option(
    "-E", "--increment", required=True, help="Gridding Increment (e.g., 1s, 30m)"
)
@click.option(
    "-O",
    "--outname",
    default="globato_dem",
    help="Output Basename (default: globato_dem)",
)
@click.option(
    "-F",
    "--format",
    default="GTiff",
    help="Output Format (GTiff, NetCDF, etc.). Default: GTiff.",
)
@click.option(
    "-P", "--crs", default="EPSG:4326", help="Target Projection (default: EPSG:4326)"
)
@click.option(
    "-N", "--nodata", type=float, default=-9999.0, help="NoData Value. Default: -9999."
)
@click.option(
    "-M",
    "--algo",
    default="ms_cudem",
    help="Interpolation algorithm and options (e.g., interp_gmt:tension=0.35)",
)
@click.option(
    "-A",
    "--stack-mode",
    type=click.Choice(["mean", "min", "max", "mixed", "supercede"]),
    default="mixed",
    help="Stacking mode",
)
@click.option(
    "-T",
    "--filter",
    "filters",
    multiple=True,
    help="Apply Grits Filter (e.g. 'blur:radius=3'). May be set multiple times.",
)
@click.option("-C", "--clip", help="Clip output to polygon file. e.g. 'clip_ply.shp'")
@click.option(
    "--save-only",
    is_flag=True,
    help="Save the generated YAML recipe to disk WITHOUT running it.",
)
@click.argument("sources", nargs=-1)
def recipe_build(
    region,
    increment,
    outname,
    format,
    crs,
    nodata,
    algo,
    stack_mode,
    filters,
    clip,
    save_only,
    sources,
):
    """Build and run a recipe on the fly, mimicking the legacy Waffles CLI."""

    if not sources:
        click.secho("Error: You must provide at least one data source.", fg="red")
        sys.exit(1)

    try:
        for t_reg, feat_name in yield_parsed_regions(region):
            r_str = f"{t_reg.xmin}/{t_reg.xmax}/{t_reg.ymin}/{t_reg.ymax}"
            tile_outname = f"{outname}_{feat_name}" if feat_name else outname

            if feat_name:
                click.secho(
                    f"\n--- Building Batch Tile: {feat_name} ({r_str}) ---",
                    fg="cyan",
                    bold=True,
                )

            global_hooks = []

            # The Base Stack
            global_hooks.append({"name": "drop_class"})
            global_hooks.append(
                {
                    "name": "multi_stack",
                    "args": {
                        "res": increment,
                        "crs": crs,
                        "mode": stack_mode,
                        "nodata": nodata,
                        "output": f"{tile_outname}_stack.tif",
                    },
                }
            )
            global_hooks.append(
                {"name": "focus_sink", "args": {"target": "multi_stack"}}
            )
            global_hooks.append(
                {
                    "name": "raster_stream",
                    "args": {
                        "stream_type": "raster",
                        "chunk_size": 2048,
                        "stage": "collection",
                    },
                }
            )

            # Add requested Filters (-T)
            for f in filters:
                global_hooks.append(parse_hook_string(f))

            global_hooks.append(
                {
                    "name": "ms_blend",
                    "args": {
                        "weight_threshold": 0.5,
                        "blend_dist": 20,
                        "random_scale": 0.25,
                    },
                }
            )

            # Add requested Interpolation Algorithm (-M)
            algo_hook = parse_hook_string(algo)
            if algo_hook["name"] == "ms_cudem":
                algo_hook.setdefault("args", {})["resolutions"] = increment

            algo_hook.setdefault("args", {})["output"] = f"{tile_outname}.tif"
            global_hooks.append(algo_hook)

            # Add Clipping (-C)
            if clip:
                clip_hook = parse_hook_string(clip, default_name="raster_clip")
                if clip_hook["name"] != "raster_clip":
                    clip_hook["args"]["barrier"] = clip_hook.pop("name")
                    clip_hook["name"] = "raster_clip"
                global_hooks.append(clip_hook)

            config = {
                "project": {"name": tile_outname},
                "region": r_str,
                "modules": [parse_source_string(s) for s in sources],
                "global_hooks": global_hooks,
            }

            yaml_str = yaml.dump(config, sort_keys=False)

            out_yaml = f"{tile_outname}_recipe.yaml"
            with open(out_yaml, "w") as f:
                f.write(yaml_str)
            click.secho(f"Recipe saved to {out_yaml}.", fg="green", bold=True)

            if not save_only:
                click.secho(
                    f"Executing dynamic recipe: {tile_outname}", fg="cyan", bold=True
                )
                Recipe.from_file(config).run()

    except ValueError as e:
        click.secho(str(e), fg="red")
        sys.exit(1)
