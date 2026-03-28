<!-- <p align="center"> -->
<!-- 	<a href="https://github.com/continuous-dems"> -->
<!-- 		<img src="https://github.com/continuous-dems/fetchez/blob/modules/docs/source/_static/continuous_dems_logo.svg" height="80" alt="Continuous DEMs Logo"> -->
<!-- 	</a> -->
<!-- </p> -->
<h1 align="center">Globato</h1>
<p align="center"><em>Domo Arigato, Multi-Resolution Globato.</em></p>

<p align="center">
  <a href="https://github.com/continuous-dems/globato"><img src="https://img.shields.io/badge/version-0.2.0-blue.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.12+-yellow.svg" alt="Python"></a>
  <a href="https://badge.fury.io/py/globato"><img src="https://badge.fury.io/py/globato.svg" alt="PyPI version"></a>
  <a href="https://cudem.zulip.org"><img src="https://img.shields.io/badge/zulip-join_chat-brightgreen.svg" alt="Project Chat"></a>
</p>

**Globato** is the user-facing geospatial engine of the Continuous-DEMs ecosystem. It is designed for the rapid development, blending, and processing of high-accuracy Topo-Bathy Digital Elevation Models (DEMs).

Built on top of the `fetchez` (orchestration) and `transformez` (horizontal and vertical datums) libraries, `globato` abstracts away the complexity of DEM development. It allows users to generate massive, seamless DEMs using declarative YAML recipes or intuitive command-line tools—all while scaling infinitely on standard hardware.

## Key Features

* **Infinite Memory Scaling:** `globato` uses a pure-Python, generator-based streaming architecture to process massive rasters and point clouds chunk-by-chunk, eliminating out-of-memory crashes.
* **Declarative Pipelines:** Define your data sources, regions, and processing hooks in a simple YAML recipe, ensuring 100% reproducibility.
* **API Caching:** Remote API queries (NOAA, Copernicus, etc.) are hashed and cached locally. Tweak your blending weights or hillshade parameters and re-run your DEM in seconds without re-downloading data.
* **Native Processing:** Generate complex shaded reliefs, sieves, and morphological filters using native Python.
* **Super-Modules:** Access advanced, automated workflows like `glob_coast`, which dynamically resolves and generates high-resolution coastline masks on the fly.

## Installation

Install via pip (this will automatically pull in the `fetchez` and `transformez` dependencies):

```bash
pip install globato
```

## The globato CLI
`globato` provides a powerful, user-friendly Command Line Interface to generate DEMs, run community recipes, manipulate rasters, and more.

1. **Generate a DEM on the Fly**
Don't want to write a YAML file? You can build a DEM directly from the terminal by specifying your region, resolution, and desired data sources:

```bash
# Generate a 1 arc-second DEM of Southern California using NOAA NOS and Copernicus data
globato recipe build -R -120/-119/34/35 -I 1s nos_hydro copernicus
```

2. **Run Community Recipes**
You can execute pre-configured YAML recipes directly from your local machine, a URL, or the official ContinUous-DEMs community repository.

```bash
# Run a local recipe
globato recipe run my_local_dem.yaml


# Run an official community recipe directly from GitHub (*to be implemented*)
globato recipe run western_ak
```

3. **Raster Tools**
globato includes a suite of raster manipulation tools for cropping, inspecting, and modifying your outputs:

```bash
globato raster info my_dem.tif
globato raster clip -R -120/-119/34/35 my_dem.tif clipped_dem.tif
```

## The Vision & Community
The transition to this modern, modular architecture is just the beginning. Our vision is to cultivate a thriving open-source community around high-accuracy elevation modeling.

We welcome contributions! Whether you want to write a new data-fetching module, build a custom visualization hook, or simply share a YAML recipe for your local coastline, check out our [GitHub Repository](https://github.com/continuous-dems) or join our [Zulip Chatspace](https://cudem.zulipchat.com/) to get involved.
