# 🌎 DEM Generation Architecture
Globato produces seamless, high-resolution Digital Elevation Models (DEMs) from heterogeneous, multi-source point clouds and rasters. Globato pairs a rigorous statistical accumulator (`multi_stack`) with a morphological multi-resolution step-down gridding engine (`ms_binary_cudem`). Together, these components preserve the high-frequency fidelity of dense datasets (such as coastal bathymetric LiDAR) while seamlessly bridging coverage gaps across sparse datasets.

## The Multi-Resolution Globato Workflow: `multi_stack` & `ms_binary_cudem`

1. **The Workflow Execution Lifecycle:**
When Globato executes a DEM recipe, the data stream passes through seven distinct processing stages:

	```mermaid
	graph TD
		A[1. Discovery] --> B[2. Caching]
		B --> C[3. Filtering]
		C --> D[4. Transform]
		D --> E[5. Accumulate: multi_stack]
		E --> F[6. Interpolate: ms_binary_cudem]
		F -->|Step-Up & Blend| E
		F --> G[7. Finalize]

		classDef default fill:#1e1e1e,stroke:#0074D9,stroke-width:2px,color:#d4d4d4;
		classDef core fill:#0074D9,stroke:#ffffff,stroke-width:2px,color:#ffffff,font-weight:bold;
		class E,F core;
	```

	1. **Data Discovery & Access:** Fetchez queries remote APIs (NASA CMR, NOAA, TNM) or crawls local file systems (`local_fs`) to compile the initial data manifest.

	2. **Cache Inspection:** Modules check the module-specific `{module}.fetchez_cache` directory for valid, pre-existing queries to bypass unnecessary network I/O.

	3. **Preparation & Filtering:**	Point streams are passed through spatial hooks (such as `point_raster_mask`, `range_z` or `vector_crop`) to remove land/water noise, clip bounding boxes, or filter points.
	4. **Spatial & Datum Transformations:** Coordinates are transformed to the target horizontal and vertical reference systems (e.g., EPSG:4326, UTM zones, tidal datums, etc.).

	5. **Accumulation & Binning (`multi_stack`):** Points stack into target grid cells. High-priority weight tiers supersede lower-priority data, while identical weights are combined via weighted statistical means.

	6. **Multi-Resolution Interpolation (`ms_binary_cudem`):** The aggregated grid is decimated to coarser scales to close spatial voids, interpolated step-by-step, and blended back up to full resolution.

	7. **Product Finalization:** Globato exports the final DEM, visual inspection products, multi-band statistical stacks, and vector/raster provenance metadata.

2. **The Core Engine:** `multi_stack` & `ms_binary_cudem`
The core gridding pipeline forms an interdependent loop: ms_binary_cudem steps down to coarser resolutions during gap-filling, calling multi_stack internally to re-bin data while preserving all statistical weights.

	```mermaid
	graph TD
		classDef core fill:#0074D9,stroke:#ffffff,stroke-width:2px,color:#ffffff,font-weight:bold;
		classDef process fill:#f8f9fa,stroke:#cccccc,stroke-width:1px,color:#333333;
		classDef output fill:#2ECC40,stroke:#ffffff,stroke-width:2px,color:#ffffff,font-weight:bold;

		Stream[Raw Point Streams] --> Accumulate

		subgraph The Core Engine
			Accumulate[multi_stack]:::core <-->|Decimate & Re-bin| Interpolate[ms_binary_cudem]:::core
			Interpolate -->|1. Build Coarse Base| InterpStep[Interpolate Voids]:::process
			InterpStep -->|2. Step-Up to High Res| BlendStep[Blend & Supersede]:::process
			BlendStep -->|3. Loop Until Native Res| Interpolate
		end

		BlendStep -->|Final Pass| DEM[Seamless Continuous DEM]:::output
	```

* **multi_stack:** Statistical Binning & Accumulation
   The multi_stack hook aggregates points directly from the data streams into pixel bins at your target resolution. It performs no spatial interpolation.

	* **Weight-Based Superseding:** Data are grouped by assigned weights. Data in higher weight tiers completely overwrite data from lower weight tiers. Within the same weight tier, multiple observations are merged using a weighted mean.

	* **Low Memory Footprint:** It maintains a running .sums.tif file on local storage to track cell state incrementally. This allows Globato to process billions of points without exceeding system RAM.

	* **The 7-Band Output:** It generates a multi-band statistical grid summarizing the accumulated point stream:


	| Band | Channel Name       | Description                                       |
	|:----:|:-------------------|:--------------------------------------------------|
	| 1    | z                  | Weighted-average elevation                        |
	| 2    | count              | Number of accumulated observations                |
	| 3    | weight             | Data weight / priority value                      |
	| 4    | uncertainty        | Accumulated measurement uncertainty               |
	| 5    | source_uncertainty | Native uncertainty inherent to the source dataset |
	| 6    | x                  | Weighted-average X coordinate                     |
	| 7    | y                  | Weighted-average Y coordinate                     |

---

* **ms_binary_cudem:** Multi-Resolution Interpolation
The `ms_binary_cudem` hook is a Morphological Multi-Resolution Step-Down gridding tool. It fills data voids without smoothing or degrading dense, high-resolution features (like coastlines or structures).

	* **Decimation:** It takes the multi_stack grid and decimates it to lower resolution tiers specified by the steps parameter. At lower resolutions, data gaps shrink significantly.
	* **Iterative Step-Down / Step-Up:**
		1. It builds an interpolated base surface at the coarsest scale.
		2. It iterates back up toward full resolution, interpolating voids surrounding each weight group at each step.
		3. Higher-weighted data tiers blend over and supersede lower-resolution tiers and interpolated regions.
	* **Topological Landmasks:** Integrates OpenStreetMap topology to apply multi-class interpolation limits and define physical boundaries in the ocean for breakwaters and reefs.
	* **Morphological Caps:** Stretches taut, distance-bounded linear caps across near-shore voids to prevent artificial interpolation bulges while enforcing natural coastal drop-offs.

	* **Result:** A continuous elevation surface that preserves high resolution detail where data exist and gracefully fills voids where data are missing.

	* 💡 **Configuration Note on steps:**
	The steps parameter sets how many times the grid "zooms out". Setting `steps=3` results in 4 total resolution tiers (1 Base Native Tier + 3 Decimation Steps). If an array parameter (such as `algos` or `blend_dists`) contains fewer items than the total tier count, Globato automatically pads the array by repeating the last element.

3. **Output Products & Lineage Tracking:**
When processing completes, Globato exports the primary elevation surface alongside a suite of supporting lineage artifacts.

**Overview of Generated Products**

| Product File / Pattern | High-Level Description | What It Tells You |
| :---:     | :----   | :---     |
| *_final.tif | Finished Coastal DEM | The primary seamless elevation surface. |
| *_hs.tif | Hillshade Raster | Topographic relief rendering for visual inspection. |
| tmp_sources/*.tif | Per-File Source Masks | Binary rasters (1 = data present, 0 = no data) for each input file. |
| *_sources.vrt | Virtual Source Stack | A multi-band VRT aggregating all individual source masks. |
| *_sm.gpkg | Spatial Metadata Vector | Vector polygons showing source bounds, dissolved by module + weight. |
| provenance.tif | Bitmask Provenance Raster | A single uint32 raster storing cell-by-cell source contributions using bit IDs. |
| *_stack.tif | 7-Band Accumulation Stack | Full statistical grid ($Z, Count, Weight, Uncertainty, Source\ Uncertainty, X, Y$). |

## Understanding Provenance: Bitmasks vs. Vector Metadata
Globato provides three complementary ways to inspect where your elevation data originated:

### Lineage Tracking System
| Type                       | Output                            |                                 Description |
|:---------------------------|:---------------------------------:|--------------------------------------------:|
| Individual Source Masks    | tmp_sources/*.tif & *_sources.vrt |  Simple 1 / 0 Mask of data source locations |
| Provenance Bitmask         | *_provenance.tif                  | Compact bitwise OR encoded source locations |
| Vector Metadata            | *_sm.gpkg                         |                   Dissolved source polygons |

1. **The Source Masks**
The individual source masks track each file that gets stacked and record a 1 in each cell where data from that source exists. All the individual source masks are combined into a fianl `*_sources.vrt` Virtual Raster to view them all as a group in GIS for inspection and reference.

2. **The Provenance Bitmask (provenance.tif)**
The provenance raster uses bitwise encoding to store source contributions compactly in a single uint32 raster. Each contributing module is assigned a unique bit value equal to a power of two ($2^n$):

	```
	MOD_csb         = 1   (Bit 0: 2^0 -> 00001)
	MOD_nos_hydro   = 2   (Bit 1: 2^1 -> 00010)
	MOD_charts      = 4   (Bit 2: 2^2 -> 00100)
	MOD_tnm         = 8   (Bit 3: 2^3 -> 01000)
	MOD_ehydro      = 16  (Bit 4: 2^4 -> 10000)
	```

	If multiple sources contribute observations to the exact same cell, their bit values are combined using a Bitwise OR (\|) operation:

	$$\text{Pixel Value} = \text{Bit}_1 \mid \text{Bit}_2 \mid \dots \mid \text{Bit}_n$$

	* **Example:** If a cell contains observations from both CSB (1) and Charts (4), the stored pixel value is 5 (00001 | 00100 = 00101 or $1 + 4 = 5$).
	* **Decoding in GIS:** To verify if a specific dataset (e.g., Charts = 4) contributed to a cell with value $V$, perform a Bitwise AND (&):

	$$\text{Cell Contains Module} \iff (V \ \& \ 4) == 4$$

3. **Spatial Metadata GeoPackage (`*_sm.gpkg`)**
For standard GIS mapping, Globato converts individual source masks into a lightweight GeoPackage (`*_sm.gpkg`). Polygons are dissolved by module and weight, preserving spatial attributes (such as dataset agency, dates, resolutions, and source URLs). Globato also auto-generates a companion QGIS Style File (`.qml`) for instant categorised symbology.
