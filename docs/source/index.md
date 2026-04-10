# GloBaTo

**Domo Arigato, Multi-Resolution Globato**

**Globato** is the user-facing geospatial engine of the Continuous-DEMs ecosystem. It is designed for the rapid development, blending, and processing of high-accuracy Topo-Bathy Digital Elevation Models (DEMs).

Built on top of the `fetchez` (orchestration) and `transformez` (horizontal and vertical datums) libraries, `globato` abstracts away the complexity of DEM development. It allows users to generate massive, seamless DEMs using declarative YAML recipes or intuitive command-line tools—all while scaling infinitely on standard hardware.

## Key Features

* **Infinite Memory Scaling:** `globato` uses a pure-Python, generator-based streaming architecture to process massive rasters and point clouds chunk-by-chunk, eliminating out-of-memory crashes.
* **Declarative Pipelines:** Define your data sources, regions, and processing hooks in a simple YAML recipe, ensuring 100% reproducibility.
* **API Caching:** Remote API queries (NOAA, Copernicus, etc.) are hashed and cached locally. Tweak your blending weights or hillshade parameters and re-run your DEM in seconds without re-downloading data.
* **On-The-Fly Datum Shifts:** True 3D projection and vertical datum transformations (e.g., MLLW to NAVD88) applied directly to data streams.

## Installation

Install via pip (this will automatically pull in the `fetchez` and `transformez` dependencies):

```bash
pip install globato
```

## The globato CLI
`globato` provides a powerful, user-friendly Command Line Interface to generate DEMs, run community recipes, manipulate rasters, and more.

1. **recipe (The DEM Engine)**
Generate, execute, and batch-process DEM recipes.

```bash
# Build a recipe on the fly from the terminal
globato recipe build -R -120/-119/34/35 -E 1s mbdb copernicus

# Build a recipe and save it to YAML to learn the syntax
globato recipe build -R loc:"Miami" -E 1/3s mbdb+rq:threshold=50 --save-only

# Run a saved recipe
globato recipe run my_dem_recipe.yaml

# Dynamically build and run a recipe with Kriging interpolation and Uncertainty calculation
globato recipe build -R -80/-79/25/26 -E 1s -M interp_krige -T dem_uncertainty:mode=split_sample mbdb copernicus
```

2. **pointz (The Point Cloud Engine)**
Stream, filter, and reproject point clouds using standard UNIX pipelines.

```bash
# Download multibeam data, filter it, project to Web Mercator, and save to XYZ
globato pointz run mbdb -R loc:"Tampa" -F rq:threshold=10 -T EPSG:3857 > clean.xyz

# Pipe existing local data through Globato's statistical outlier filter
cat raw_survey.xyz | globato pointz run - -F outlierz > clean.xyz
```

3. **viz (The Plotting Engine)**
Generate instant visualizations for sanity checks without opening QGIS.

```bash
# Render a beautiful, georeferenced colored hillshade from a DEM
globato viz hillshade my_dem.tif output_hs.tif --cmap etopo --blend soft_light

# Open an interactive 3D window to inspect a local point cloud
globato viz points raw_lidar.laz --3d

# Fetch live multibeam data, apply a filter, and highlight the rejected noise in red!
globato viz points mbdb -R loc:"San Diego" -F rq:threshold=5 --outliers
```

4. **raster (The Grid Engine)**
Perform advanced morphological operations and blending on existing grids.

```bash
# Blend a high-res coastal DEM into a lower-res background auxiliary grid
globato raster blend hi_res.tif blended_out.tif --aux background.tif --blend-dist 50
```

5. **fetch (The Download Engine)**
Discover and download raw elevation data directly from federal/global APIs.

```bash
globato fetch list --search lidar
globato fetch run tnm -R -120/-119/34/35 -O ./my_data
```

6. **region (The Spatial Engine)**
Perform spatial math, geocoding, and tileset generation.

```bash
# Geocode a city and get the bounding box
globato region echo loc:"Seattle, WA"

# Split a massive bounding box into a 0.25-degree GeoJSON tileset for batch processing
globato region split loc:"California" --size 0.25 -O cali_tiles.geojson
```

## The Vision & Community
The transition to this modern, modular architecture is just the beginning. Our vision is to cultivate a thriving open-source community around high-accuracy elevation modeling.

We welcome contributions! Whether you want to write a new data-fetching module, build a custom visualization hook, or simply share a YAML recipe for your local coastline, check out our [GitHub Repository](https://github.com/continuous-dems) or join our [Zulip Chatspace](https://cudem.zulipchat.com/) to get involved.


```{toctree}
:maxdepth: 2
:hidden:
:caption: User Guide:

```

Indices and tables
==================

* {ref}`genindex`
* {ref}`modindex`
* {ref}`search`
