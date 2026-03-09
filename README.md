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

**Globato** (*Global Bathymetry & Topography*) is the next-generation DEM generation suite for the [Fetchez](https://github.com/continuous-dems/fetchez) ecosystem. Originally part of the [CUDEM](https://github.com/ciresdem/cudem) project, Globato unifies data discovery, download, and processing into a single, streaming pipeline.

## ❓ Why Globato?

Building Digital Elevation Models (DEMs) typically involves a "download-then-process" workflow that requires massive storage and directories full of custom scripts.

**Globato changes this.** It acts as a streaming extension to `fetchez`, allowing you to:
* **Stream, not store:** Process points from remote sources (LiDAR, Multibeam, COGs) on-the-fly without saving raw files to disk.
* **Harmonize Resolution:** Seamlessly blend high-resolution multibeam with coarse global topography (hence the **M.R.** in *Mr. Globato*).
* **Standardize Metadata:**
* **ETC**

Whether you are building a quick 30m regional map or a precision 1m surface, `globato` keeps your pipeline clean, reproducible, and memory-efficient.

---

## 🌎 Features

* **Streaming Gridders:**
    * **`simple_stack`**: A lightweight, memory-safe stream for generating standard Z-elevation rasters (weighted mean).
    * **`multi_stack`**: A heavy-duty statistical engine that generates 7-band GeoTIFFs containing Elevation, Weight, Count, Uncertainty, Source Uncertainty, and average X/Y locations for every pixel.
* **Provenance Tracking:** Automatically generate bitmask rasters that map exactly which datasets contributed to every pixel in your output.
* **Data Readers:**
    * **Native BAG Support:** A Bathymetric Attributed Grid reader that handles Variable Resolution (VR).
    * **COG Subsetting:** Windowed fetching for Cloud Optimized GeoTIFFs.
* **Modern Architecture:** Built on `rasterio`, `numpy`, and `fetchez`, dropping heavy legacy dependencies for a pure Python experience.
* **Declarative Recipes:** Define complex, multi-sensor build pipelines in simple `yaml` files.

## 🔌 How Globato Extends Fetchez

`globato` does not provide a separate CLI tool. Instead, it acts as a plugin suite that injects advanced processing capabilities directly into the `fetchez` engine.

When you install `globato`, `fetchez` automatically detects and registers these new capabilities, allowing you to chain them into your existing workflows using the standard `--hook` syntax.

***The Globato Toolkit***

`globato` extends the core `fetchez` ecosystem by adding three types of components:

1. **Data Streams** Standard fetchez downloads files. `globato` turns those files into streaming point clouds.

`stream_data`: Auto-detects file types (LAS, LAZ, BAG, XYZ, OGR) and converts them into a standardized stream of x,y,z,weight,uncertainty records.

`stream_reproject`: Reprojects streaming points on-the-fly using pyproj (e.g., converting WGS84 to UTM Zone 10N in memory).

2. **Filters** Clean your data before it ever hits a grid.

`block_thin`: Decimate your data stream for faster processing.

`outierz`: Remove statistical outliers from you data stream

3. **Stackers** The core of the `globato` engine; turning streams into surfaces.

`simple_stack`: A fast, memory-safe stacker for generating standard weighted-mean Elevation rasters.

`multi_stack`: The heavy-duty statistical engine. Generates 7-band GeoTIFFs (Z, Count, Weight, Uncertainty, Source Uncertainty, X-mean, Y-mean) for rigorous analysis.

`provenance`: Generates a bitmask raster tracking exactly which dataset contributed to each pixel.

4. **Specialized Modules**

`gebco_cog`: A specialized fetch module for the GEBCO global bathymetry dataset, optimized for COG subsetting.

`glob_coast`: A "Super-Module" that fetches topographic, bathymetri can hydrographic datasets to generate a `coastline-mask`.

## 🤖 Usage Example

Because `globato` is just a set of hooks, a complex ETL job looks just like a standard `fetchez` command.

Example: The `globato` pipeline fetches multibeam data, filters outliers, reprojects to NAVD88, and grids it without saving intermediate files.

```bash
fetchez multibeam -R -124.5/-124.0/43.0/43.5 \
    --weight 1.0 --uncertainty 0.5 \
    # Turn download into a stream
    --hook stream_data \
    # Reproject stream to WGS84/NAVD88
    --hook stream_reproject:dst_srs=EPSG:4326+5703 \
    # Filter statistical outliers (3-sigma)
    --hook filter:method=outlierz:threshold=3.0 \
    # Grid into a 7-band statistical surface
    --hook multi_stack:res=10:mode=mean:output=coos_bay_stack.tif
```
