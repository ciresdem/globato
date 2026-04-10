<p align="center">
	<a href="https://github.com/continuous-dems">
		<img src="https://raw.githubusercontent.com/continuous-dems/globato/refs/heads/main/docs/source/_static/continuous_dems_logo_mini.svg" height="80" alt="Continuous DEMs Logo">
	</a>
</p>
<h1 align="center">GloBaTo</h1>
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

---

## ❓ Why Globato?

Traditional DEM generation is often a disjointed, manual process of hunting down, organizing and processing disparate datasets using a wide variety of software and one-off processing scripts.

**Globato** standardizes this workflow by unifying data fetching, spatial filtering, datum transformations, and gridding into a single, memory-safe streaming pipeline. Using declarative YAML recipes, it allows you to orchestrate massive, reproducible Topo-Bathy DEMs directly from your terminal!

---

## 📦 Installation
Install via pip (this will automatically pull in the `fetchez` and `transformez` dependencies):

```bash
pip install globato
```

## 🐄 Quickstart

If you are new to Globato, the best place to start is the Quickstart guide, which will take you from installation to generating your first DEM in under 5 minutes.

* **[Quickstart: From Zero to DEM in 1 arc-second](https://globato.readthedocs.io/user_guide/quickstart.md)**

---

## 📚 Documentation
Would you like to know more? Check out our [Official Documentation](https://globato.readthedocs.io) to learn about:

* **The Python API:** Build custom Digital Elevation Models and process elevation data in your apps.

* **The Globato CLI:** Generate and run globato recipes, fetch elevation data sources, visualize your data all in the command line.

* **DEM Recipes:** Build reproducable DEMs using simple YAML recipes.

---

## ⚖ License

This project is licensed under the MIT License - see the [LICENSE](https://github.com/continuous-dems/fetchez/blob/main/LICENSE) file for details.

Copyright (c) 2010-2026 Regents of the University of Colorado

