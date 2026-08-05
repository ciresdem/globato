# 🏗️ The Core Architecture: Fetchez vs. Globato
It is vital to understand that `globato` does not replace `fetchez`; it is completely powered by and extends it.

* `fetchez` is the Engine: It provides the generator-based streaming framework, the infinite memory handling, coordinate transformations, and the foundational pipeline registries.

* `globato` is the Domain Toolkit: It is a collection of curated, elevation-focused modules (like mbdb or nos_hydro) and morphological hooks (like ms_binary_cudem) designed to click into the fetchez engine like building blocks.

Every time you execute a command like `globato cudem build`, the toolkit is simply assembling a native fetchez YAML recipe behind the scenes.

# 🔀 Streaming Pipelines vs. Monolithic Processing
Traditional GIS scripts read an entire dataset into memory, process it, write it to a temporary file, and repeat. This method breaks when scaling globally.

`fetchez` uses a pure Generator-Based Streaming Architecture. Data flows through the pipeline in manageable chunks. The hooks act as inline transformers:

[API / Local File] ➔ [Reproject Hook] ➔ [Filter Hook] ➔ [Gridding Hook] ➔ [Output]
   (Stream Chunk)        (Mutate Chunk)     (Filter Chunk)    (Accumulate Chunk)

Memory footprints remain entirely flat, whether you are processing a single harbor or an entire archipelago.

# 🧪 Advanced Scripting Examples
To prove that the recipe engine is a generic geospatial ETL framework, let's include two contrasting examples in the guide: one showing topobathy production and the other showing direct satellite validation.

## Example 1: Standardizing Topobathy (The Southern California CRM Recipe)
This recipe highlights how multiple, completely disparate remote networks (USGS, Copernicus, NCEI Multibeam, USACE eHydro) can be dynamically harvested, vertically reconciled, and merged smoothly into a localized target system (EPSG:4326+3855) using a single file.

```yaml
project:
  name: socal_crm_validation
region: -118.275/-117.975/32.975/33.275
region_srs: epsg:4326+3855

modules:
  - module: tnm
    args:
      weight: 3.0
      datasets: 3/4
    hooks:
      - name: raster_flats
      - name: stream_data
      - name: stream_reproject
        args: {dst_srs: "epsg:4326+3855"}
      - name: spatial_crop

  - module: mbdb
    args:
      weight: 0.75
      want_inf: true
    hooks:
      - name: filename_filter
        args: {match: .fbt}
      - name: stream_data
      - name: stream_reproject
        args: {src_srs: "EPSG:4326+9003", dst_srs: "epsg:4326+3855"}
      - name: rq
        args: {reference: "gmrt/gebco/cudem", threshold: 5, mode: "percent"}

global_hooks:
  - name: multi_stack
    args:
      res: 1s
      crs: "epsg:4326+3855"
      mode: mixed
      weight_threshold: "1.0/0.5"
  - name: focus_sink
    args: {target: "multi_stack"}
  - name: raster_stream
    args: {stream_type: "raster", chunk_size: 2048, stage: "collection"}
  - name: ms_binary_cudem
    args:
      resolutions: 1s/3s/15s
      barrier: osm
```

## Example 2: Validate with IceSat2 (Satellite DEM Validation Script)
This example proves that the ecosystem goes far beyond gridding. Here, we stream raw ICESat-2 laser altimetry photon data, dynamically reproject it across complex horizontal/vertical datums, grid the profiles, difference them against a target local validation DEM, and output a raw residual point cloud for statistics—all streamed on the fly.

```yaml
project:
  name: icesat2_dem_validation
region: -118.50016975308472/-118.24983024941527/33.499830249415275/33.75016975308472

modules:
  - module: icesat2
    args:
      subset: true
      time_start: "2025-01-01"
      time_end: "2025-04-01"
    hooks:
      - name: set-datatype
        args: {data_type: "atl03-xyz"}
      - name: stream-reproject
        args: {src_srs: "epsg:4326+3855", dst_srs: "epsg:4269+5703"}
      - name: points2pixels
        args: {x_inc: "0.11111111s", y_inc: "0.11111111s", want_sums: false}
      - name: pixels2points

global_hooks:
  - name: diff-z
    args:
      raster: "cudem_test_final.tif"
  - name: xyz_write
    args:
      output_path: "icesat2_residuals.xyz"
```
