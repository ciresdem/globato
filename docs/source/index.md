# GloBaTo

*Domo Arigato, Multi-Resolution Globato*

**Globato** is the user-facing geospatial engine of the Continuous-DEMs ecosystem. It is designed for the rapid development, blending, and processing of high-accuracy Topo-Bathy Digital Elevation Models (DEMs).


Globato is part of the [Continuous DEMs Project](https://continuous-dems.readthedocs.io/), an ecosystem of tools for modern, continuous digital elevation model generation.

---

## 🚀 Key Features

* **Infinite Memory Scaling:** `globato` uses a pure-Python, generator-based streaming architecture to process massive rasters and point clouds chunk-by-chunk, eliminating out-of-memory crashes.
* **Declarative Pipelines:** Define your data sources, regions, and processing hooks in a simple YAML recipe, ensuring 100% reproducibility.
* **API Caching:** Remote API queries (NOAA, Copernicus, etc.) are hashed and cached locally. Tweak your blending weights or hillshade parameters and re-run your DEM in seconds without re-downloading data.
* **On-The-Fly Datum Shifts:** True 3D projection and vertical datum transformations (e.g., MLLW to NAVD88) applied directly to data streams.
* **Native Projection Support:** Pass projected bounding boxes (e.g., UTM, State Plane) natively. Globato seamlessly warps regions on-the-fly to fetch remote data and projects it back to your target coordinate system.

---

## 🗺️ Where to Start?

If you are new to Globato, the best place to start is the Quickstart guide, which will take you from installation to generating your first DEM in under 5 minutes.

* **[Quickstart: From Zero to DEM](user_guide/quickstart.md)**
* **Anatomy of a Recipe**
* **CLI Syntax Cheatsheet**

---

## 🤝 The Vision & Community
The transition to this modern, modular architecture is just the beginning. Our vision is to cultivate a thriving open-source community around high-accuracy elevation modeling.

We welcome contributions! Whether you want to write a new data-fetching module, build a custom visualization hook, or simply share a YAML recipe for your local coastline, check out our [GitHub Repository](https://github.com/continuous-dems/globato) or join our [Zulip Chatspace](https://cudem.zulipchat.com/) to get involved.


```{toctree}
---
maxdepth: 2
caption: User Guide
hidden: true
---
user_guide/index
api/index
```

Indices and tables
==================

* {ref}`genindex`
* {ref}`modindex`
* {ref}`search`
