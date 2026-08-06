# ⚡ Quickstart

**From Zero to DEM in 1 arc-second**

Welcome to **Globato**, the continuous DEM generation framework. In this guide, we will go from installing the software to generating a production-ready Coastal Digital Elevation Model (DEM) of San Diego, California, in just a few terminal commands.

## Prerequisites
Globato requires Python 3.12+. We highly recommend doing this inside a clean virtual environment (like conda or venv).

```bash
pip install globato
```

## Step 1: Discovering Recipes
Globato uses **Recipes** (YAML configuration files) to define how data is downloaded, filtered, stacked, and interpolated. You don't have to write these from scratch! Globato ships with curated recipes out of the box.

Let's see what is available:

```bash
globato recipes list
```

You should see a number of pre-made recipes, including `quick-coastal`, `crm-standard` and `cudem-standard` in the list:

```console
📜 Available Pipeline Recipes:
============================================================
  cncc                      - Test DEM for the Coastal Coupling Workshop Optimized for Newport, Oregon at 1/9 arc-second
  crm-standard              - Production-grade 1-arc-second Coastal Relief Model. Blends high-res local topo (CoNED, 3DEP) with dense bathymetry (MBDB, NOS, eHydro) and fills deep water with GEBCO. Includes an embedded Spatial Uncertainty Band (Band 2).
  cudem-standard            - Production-grade 1/9-arc-second CUDEM.
  etopo-standard            - Production-grade 3-arc-second ETOPO.
  quick-coastal             - Quick Coastal DEM at 3-arc-second
  western_alaska            - This run executes the shared Western Alaska recipe. It gathers elevation and bathymetry inputs for the area, including Copernicus, MBDB, NOAA hydro (BAG/XYZ), chart soundings, and eHydro. Source data is filtered, unzipped, spatially cropped, and reprojected to EPSG:4326+3855. The inputs are combined into a weighted raster stack (test_ak_stack.tif) at approximately 1 arc-second resolution. This stack is blended in two passes to smooth overlaps, and then a CUDEM-style step-down interpolation is applied at 1s / 3s / 9s scales using the coastline as a barrier to generate the final seamless terrain surface (western_alaska_dem.tif).
```

If you want to know exactly what a recipe does before you run it, you can inspect it:

```bash
globato recipes info quick-coastal
```

```console
📜 RECIPE SUMMARY: quick-coastal
============================================================
  Description : Quick Coastal DEM at 3-arc-second

  Data Sources (4):
    + copernicus
     ⤷ weight: 0.5
     ⤷ datatype: 3
    + copernicus
     ⤷ weight: 0.35
     ⤷ datatype: 1
    + margrav
     ⤷ weight: 0.1
    + gebco
     ⤷ weight: 0.1
     ⤷ include_tid: True

  Global Pipeline Steps (15):
    - spatial-crop
    - audit
    - enrich
    - transfer_log
    - drop_class
    - provenance
     ⤷ res: 3s
     ⤷ output: %name%_%batch_name%_provenance.tif
    - source_masks
     ⤷ res: 3s
     ⤷ output: %name%_%batch_name%_sources.vrt
     ⤷ vector_output: %name%_%batch_name%_sm.gpkg
    - multi_stack
     ⤷ res: 3s
     ⤷ crs: epsg:4326+3855
     ⤷ mode: mixed
     ⤷ nodata: -9999.0
     ⤷ weight_threshold: 0.5/.25
     ⤷ output: %name%_%batch_name%_stack.tif
    - focus_sink
     ⤷ target: multi_stack
    - raster_stream
     ⤷ stream_type: raster
     ⤷ chunk_size: 2048
     ⤷ stage: collection
    - ms_binary_cudem
     ⤷ resolutions: 3s/9s/15s
     ⤷ weights: [0.5, 0.25]
     ⤷ steps: 2
     ⤷ blend_dists: 2/10/02
     ⤷ barrier: osm
     ⤷ algos: raster_fill:max_distance=6/raster_fill:max_distance=12/interp_gmt:tension=.75
     ⤷ output: %name%_%batch_name%.tif
    - focus_sink
     ⤷ target: ms_binary_cudem
    - raster_metadata
     ⤷ tags: Project=Coastal Relief Model,Version=6,Author=NCEI
     ⤷ bands: Elevation (meters)
    - format_cog
     ⤷ overviews: 2/4/8/16/32
     ⤷ resampling: average
    - viz_geoshade
     ⤷ output: %name%_%batch_name%_hs.tif
     ⤷ cmap: coastal_relief
============================================================
```

**Note**: `quick-coastal` is a fast recipe that pulls raster-based elevation data to keep the fetching and processing fast.

## Step 2: Running a Curated Recipe (Quick-Coastal)
Let's run the `quick-coastal` recipe with `globato run` to see what our region looks like. We can use the -R (Region) flag to set the region. To keep it simple, we'll use the `loc` feature to set the region to a quarter degree tile around San Diego, California.

```bash
globato run quick-coastal -R loc:"portland, me" --shared-cache sd_data -D sd_quick
```

**What just happened?**

* Globato queried the loc:"portland, me" geocoder and grabbed the bounding box.

* It dispatched the fetchez engine to download elvation and bathymetry data for exactly that area.

* It downloaded OpenStreetMap coastline vectors to act as a coastal barrier.

* It dynamically stacked, interpolated, and cropped the data into a seamless grid.

* It generated a colorized hillshade (_hs.tif) for immediate viewing.

Check your current directory; you should see your brand new `sd_quick` directory with the output DEM and Hillshade ready to load into QGIS or ArcGIS!

![San Diego 3 Arc-Second Quick Coastal DEM](/_static/san_diego_quick.png)

*(Above: The just generated DEM of San Diego, CA)*

## Step 3: Building a Custom Recipe On-the-Fly
What if you want to build a DEM using a Globato curated data bundle, but don't want to hand-write a YAML file or use a pre-made recipe? You can use the build command to string sources together instantly, including all Globato curated modules, bundles and recipes (Globato will strip out the pre-configured modules from a recipe).

You can inspect the curated globato data sources with the `globato sources` command:

```bash
globato sources list
```

```console
Curated Globato Data Sources & Bundles:
============================================================
  coupling-bathy-topo    : Data bundle for the Coastal Coupling Workshop...
  crm-alaska-bathy-topo  : Alaska extention of the CRM bundle....
  crm-bathy-topo         : Common data sources for coastal relief models...
  cudem-bathy-topo       : Common data sources for CUDEMs. 1/9 arc-secon...
  etopo-bathy-topo       : common data sources for coastal relief models...
  glob-charts            : NOS ENC charts bathymetry....
  glob_bag               : NOAA NOS Hydrographic Surveys (BAG)...
  glob_copernicus        : Copernicus Global/European Digital Elevation ...
  glob_fabdem            : FABDEM (Forest And Buildings removed Copernic...
  glob_multibeam         : NOAA Multibeam via ArcGIS Feature Server...
  glob_nos               : NOAA NOS Hydrographic Surveys (XYZ soundings)...
  global-bathy-topo      : The definitive collection for US coastal topo...
  quick-bathy-topo       : A Collection of quick raster streams for gene...
------------------------------------------------------------
```

To see what sources are in a bundle, what hooks are attached to them, or for more information about a globato source, use the `globato sources info` command:

```bash
globato sources info crm-bathy-topo
```

```console
📜 SOURCE SUMMARY: crm-bathy-topo
============================================================
  Description : Common data sources for coastal relief models. Adds some US-specific datasets to the global-bathy-topo bundle. Built for 1/3 arc-seconds @ epsg:4326+3855

  Data Sources (12):
    + copernicus
     ⤷ weight: 0.5
     ⤷ datatype: 3
     ⤷ unzip
     ⤷ filename_filter
     ⤷ raster_warp
     ⤷ point_raster_mask
     ⤷ set_datatype
     ⤷ stream_reproject
     ⤷ range_z
    + copernicus
     ⤷ weight: 0.35
     ⤷ datatype: 1
     ⤷ unzip
     ⤷ filename_filter
     ⤷ raster_warp
     ⤷ point_raster_mask
     ⤷ set_datatype
     ⤷ stream_reproject
     ⤷ range_z
    + copernicus_marine
     ⤷ weight: 0.5
     ⤷ set-datatype
     ⤷ set-srs
     ⤷ filter_field
     ⤷ dynamic-weight
     ⤷ stream_reproject
     ⤷ range_z
    + mbdb
     ⤷ weight: 1.0
     ⤷ want_inf: True
     ⤷ use_cache: True
     ⤷ filename_filter
     ⤷ stream_data
     ⤷ set-srs
     ⤷ stream_reproject
     ⤷ rq
    + sysu
     ⤷ weight: 0.1
     ⤷ set-datatype
     ⤷ set-srs
     ⤷ stream_reproject
     ⤷ rq
    + margrav
     ⤷ weight: 0.1
     ⤷ set-datatype
     ⤷ set-srs
     ⤷ range_z
     ⤷ stream_reproject
     ⤷ rq
    + csb
     ⤷ weight: 0.25
     ⤷ min_year: None
     ⤷ max_year: None
     ⤷ platform: None
     ⤷ provider: None
     ⤷ limit: 2000
     ⤷ outdir: None
     ⤷ uncertainty: 0.0
     ⤷ use_cache: True
     ⤷ set-datatype
     ⤷ set-srs
     ⤷ stream_reproject
     ⤷ rq
    + tnm
     ⤷ weight: 0.5
     ⤷ formats: GeoTIFF
     ⤷ datasets: 1
     ⤷ raster_flats
     ⤷ raster_warp
     ⤷ stream_data
     ⤷ stream_reproject
     ⤷ point_raster_mask
     ⤷ spatial_crop
     ⤷ range_z
    + tnm
     ⤷ weight: 1.0
     ⤷ formats: GeoTIFF
     ⤷ datasets: 3
     ⤷ raster_flats
     ⤷ raster_warp
     ⤷ stream_reproject
     ⤷ point_raster_mask
     ⤷ spatial_crop
     ⤷ range_z
    + nos_hydro
     ⤷ datatype: bag
     ⤷ weight: 3.0
     ⤷ filename_filter
     ⤷ set_datatype
     ⤷ stream_reproject
     ⤷ spatial_crop
    + nos_hydro
     ⤷ datatype: xyz
     ⤷ weight: 0.35
     ⤷ unzip
     ⤷ set_datatype
     ⤷ stream_data
     ⤷ stream_reproject
     ⤷ spatial_crop
     ⤷ outlierz
     ⤷ range_z
    + charts
     ⤷ weight: 0.15
     ⤷ unzip
     ⤷ filename_filter
     ⤷ set_datatype
     ⤷ stream_data
     ⤷ stream_reproject
     ⤷ spatial_crop
```

Let's build a higher resolution DEM of San Diego using the standard `crm-bathy-topo` dataset bundle, which include NOAA MBDB (Multibeam) bathymetry, NED topography, and more. This data source bundle holds many of the typical Topographic-Bathymetric datasets for the continental U.S., suitable for Coastal Releif Model (CRM).

```bash
globato build -R loc:"San Diego" crm-bathy-topo -E 1s -O san_diego --shared-cache sd_data -D sd_crm
```

![San Diego 1 Arc-Second CRM](/_static/san_diego_crm.png)

*(Above: The just generated DEM of San Diego, California)*

## Next Steps
Congratulations! You have successfully navigated the globato CLI, executed a curated recipe, generated a custom coastal DEM on the fly and made a 1 arc-second Coastal DEM of San Diego, CA!

Check out the `CLI Syntax Guide` to master how to chain modules and hooks together.

Read the `Hooks Directory` to see all the filters and algorithms available to you.

Learn how to process local data in the `Local Files Tutorial`.
