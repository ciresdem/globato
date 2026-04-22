#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.formats.icesat2
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

ICESat-2 Data Parser (ATL03, ATL24) ported from CUDEM for Fetchez-Globato.

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import glob
import numpy as np
import h5py as h5
import pandas as pd
import logging
import fiona
import shapely
from shapely.strtree import STRtree
from shapely.geometry import shape

from fetchez import utils
from fetchez.core import run_fetchez
from fetchez.hooks import FetchHook

try:
    from sklearn.cluster import DBSCAN
    # from sklearn.preprocessing import StandardScaler

    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

import rasterio

logger = logging.getLogger(__name__)


class IceSat2Stream(FetchHook):
    name = "icesat2_stream"
    meta_stage = "format"
    meta_category = "format-stream"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.params = kwargs

    def run(self, entries):
        for mod, entry in entries:
            dst_fn = entry.get("dst_fn")

            if dst_fn and dst_fn.endswith(".h5") and os.path.exists(dst_fn):
                logger.info(
                    f"[{self.name}] Initiating IceSat2 stream for {os.path.basename(dst_fn)}"
                )

                reader = ATL03Reader(dst_fn, **self.params)
                entry["stream"] = reader.yield_chunks()
                entry["stream_type"] = "xyz_recarray"

        return entries


# ==============================================
# IceSat2Reader (generic)
# ==============================================
class IceSat2Reader:
    """Base class for ICESat-2 Readers."""

    def __init__(self, src_fn, **kwargs):
        self.fn = src_fn
        self.verbose = kwargs.get("verbose", False)
        self.cache_dir = kwargs.get("cache_dir", ".")

    def yield_chunks(self):
        raise NotImplementedError


# ==============================================
# ATL-24 Dataset (Bathymetry)
# ==============================================
class ATL24Reader(IceSat2Reader):
    """ICESat-2 ATL24 (Bathymetry) Data Parser."""

    def __init__(
        self, src_fn, min_confidence=None, classes="40", water_surface="ortho", **kwargs
    ):
        super().__init__(src_fn, **kwargs)
        self.orientDict = {0: "l", 1: "r", 21: "error"}
        self.water_surface = (
            water_surface
            if water_surface in ["surface", "ortho", "ellipse"]
            else "ortho"
        )
        self.min_confidence = utils.float_or(min_confidence)

        self.classes = []
        if classes is not None:
            self.classes = [int(x) for x in str(classes).split("/")]

    def yield_chunks(self):
        """Yield points from ATL24 HDF5 file."""

        with h5.File(self.fn, "r") as f:
            for b in range(1, 4):
                for p in ["l", "r"]:
                    beam = f"gt{b}{p}"
                    if beam not in f:
                        continue

                    try:
                        # Read Arrays
                        lat_ph = f[f"{beam}/lat_ph"][...]
                        lon_ph = f[f"{beam}/lon_ph"][...]
                        class_ph = f[f"{beam}/class_ph"][...]
                        conf_ph = f[f"{beam}/confidence"][...]

                        ## Select Height Type
                        if self.water_surface == "surface":
                            ph_height = f[f"{beam}/surface_h"][...]
                        elif self.water_surface == "ellipse":
                            ph_height = f[f"{beam}/ellipse_h"][...]
                        else:
                            ph_height = f[f"{beam}/ortho_h"][...]

                        # Metadata columns
                        laser_arr = np.full(ph_height.shape, beam, dtype="object")
                        fn_arr = np.full(ph_height.shape, self.fn, dtype="object")

                        dataset = pd.DataFrame(
                            {
                                "latitude": lat_ph,
                                "longitude": lon_ph,
                                "photon_height": ph_height,
                                "laser": laser_arr,
                                "fn": fn_arr,
                                "confidence": conf_ph,
                                "ph_h_classed": class_ph,
                            }
                        )

                        # Filter by Class
                        if self.classes:
                            dataset = dataset[
                                dataset["ph_h_classed"].isin(self.classes)
                            ]

                        # Filter by Confidence
                        if self.min_confidence is not None:
                            dataset = dataset[
                                dataset["confidence"] >= self.min_confidence
                            ]

                        if dataset.empty:
                            continue

                        # Normalize columns for Globato (x, y, z)
                        dataset.rename(
                            columns={
                                "longitude": "x",
                                "latitude": "y",
                                "photon_height": "z",
                            },
                            inplace=True,
                        )

                        if hasattr(self, "region") and self.region:
                            xmin = getattr(self.region, "xmin", self.region[0])
                            xmax = getattr(self.region, "xmax", self.region[1])
                            ymin = getattr(self.region, "ymin", self.region[2])
                            ymax = getattr(self.region, "ymax", self.region[3])

                            dataset = dataset[
                                (dataset["x"] >= xmin)
                                & (dataset["x"] <= xmax)
                                & (dataset["y"] >= ymin)
                                & (dataset["y"] <= ymax)
                            ]

                        if dataset.empty:
                            continue

                        # Convert to Numpy Recarray for Globato StreamFactory
                        yield dataset.to_records(index=False)

                    except KeyError as e:
                        logger.warning(f"Missing dataset in {beam}: {e}")
                        continue


# ==============================================
# ATL03 Dataset (full) - classified
# ==============================================
class ATL03Reader(IceSat2Reader):
    """ICESat-2 ATL03 (Global Geolocated Photon Data) Parser."""

    def __init__(
        self,
        src_fn,
        water_surface="geoid",
        classes=None,
        confidence_levels="2/3/4",
        region=None,
        reject_failed_qa=True,
        append_atl24=False,
        min_bathy_confidence=None,
        use_external_masks=False,
        known_bathymetry=None,
        known_bathy_threshold=5.0,
        use_dbscan=False,
        dbscan_eps=1.5,
        dbscan_min_samples=10,
        **kwargs,
    ):

        super().__init__(src_fn, **kwargs)

        self.water_surface = (
            water_surface
            if water_surface in ["mean_tide", "geoid", "ellipsoid"]
            else "mean_tide"
        )
        self.classes = (
            [int(x) for x in str(classes).split("/")] if classes is not None else []
        )
        self.confidence_levels = (
            [int(x) for x in str(confidence_levels).split("/")]
            if confidence_levels is not None
            else []
        )
        self.region = region

        self.reject_failed_qa = reject_failed_qa
        self.append_atl24 = append_atl24
        self.min_bathy_confidence = utils.float_or(min_bathy_confidence)
        self.use_external_masks = use_external_masks

        # --- SciKit Algo Classification Options ---
        self.known_bathymetry = known_bathymetry
        self.known_bathy_threshold = utils.float_or(known_bathy_threshold, 5.0)
        self.use_dbscan = use_dbscan
        self.dbscan_eps = utils.float_or(dbscan_eps, 1.5)
        self.dbscan_min_samples = utils.int_or(dbscan_min_samples, 10)

        self.orientDict = {0: "l", 1: "r", 21: "error"}

    # ==============================================
    # Fetch AUX ATL* Data (Stubbed for Fetchez)
    # ==============================================
    def fetch_atlxx(self, atl03_fn, short_name="ATL08"):
        """Fetch associated ATLxx file."""

        try:
            from fetchez.modules import earthdata
        except ImportError:
            logger.warning(
                "Fetchez Earthdata module not found. Cannot fetch aux ATL data."
            )
            return None

        bn = os.path.basename(atl03_fn)
        parts = bn.split("_")
        if len(parts) < 4:
            return None

        atlxx_filter = "_".join(parts[1:4])
        atlxx_filter_no_ver = "_".join(parts[1:3])

        # Check Local/Cache
        for d in [os.path.dirname(atl03_fn), self.cache_dir]:
            for filt in [atlxx_filter, atlxx_filter_no_ver]:
                matches = glob.glob(os.path.join(d, f"{short_name}_{filt}*.h5"))
                if matches:
                    return matches[0]

        try:
            for filt in [atlxx_filter, atlxx_filter_no_ver]:
                fetcher = earthdata.IceSat2(
                    src_region=None,
                    verbose=self.verbose,
                    outdir=os.path.dirname(os.path.abspath(self.cache_dir)),
                    short_name=short_name,
                    filename_filter=filt,
                    version="",
                )
                fetcher.run()
                # run_fetchez([fetcher])

                if fetcher.results:
                    fetcher.fetch_entry(fetcher.results[0], check_size=True)
                    return fetcher.results[0]["dst_fn"]
        except Exception as e:
            logger.debug(f"Aux fetch failed: {e}")

        return None

    # ==============================================
    # Processing Methods (Ported from CUDEM)
    # ==============================================
    def apply_atl09_data(self, df, atl09_fn, laser):
        """Map ATL09 Apparent Surface Reflectance to ATL03 photons."""

        try:
            with h5.File(atl09_fn, "r") as f:
                target_profile = None
                current_gt = laser[:3]
                for p_num in range(1, 4):
                    profile = f"profile_{p_num}"
                    if profile not in f:
                        continue
                    if f"profile_{current_gt[-1]}" == profile:
                        target_profile = profile
                        break

                if target_profile is None:
                    target_profile = f"profile_{laser[2]}"
                if target_profile not in f:
                    return df

                grp = f[f"{target_profile}/high_rate"]
                if "apparent_surf_reflec" not in grp:
                    return df

                reflec = grp["apparent_surf_reflec"][...]
                seg_beg = grp["ds_segment_id_beg"][...]
                seg_end = grp["ds_segment_id_end"][...]
                reflec[reflec > 1e30] = np.nan

                min_seg = df["ph_segment_id"].min()
                max_seg = df["ph_segment_id"].max()
                overlap_mask = (seg_end >= min_seg) & (seg_beg <= max_seg)
                if not np.any(overlap_mask):
                    return df

                r_sub = reflec[overlap_mask]
                b_sub = seg_beg[overlap_mask]
                e_sub = seg_end[overlap_mask]

                lookup_len = max_seg - min_seg + 1
                lookup_arr = np.full(lookup_len, np.nan, dtype=np.float32)

                for r_val, s_start, s_stop in zip(r_sub, b_sub, e_sub):
                    start_idx = max(0, s_start - min_seg)
                    stop_idx = min(lookup_len, s_stop - min_seg + 1)
                    if start_idx < stop_idx:
                        lookup_arr[start_idx:stop_idx] = r_val

                ph_offsets = df["ph_segment_id"].values - min_seg
                valid_offsets = (ph_offsets >= 0) & (ph_offsets < lookup_len)

                if "reflectance" not in df.columns:
                    df["reflectance"] = np.nan

                mapped_values = lookup_arr[ph_offsets[valid_offsets]]
                df.loc[df.index[valid_offsets], "reflectance"] = mapped_values
        except Exception as e:
            logger.warning(f"Failed to apply ATL09 data: {e}")
        return df

    def calculate_pseudo_reflectance(self, df, is_strong=True):
        try:
            if is_strong:
                signal_mask = df["confidence"] >= 3
                divisor = 29.0
                min_photons = 0
            else:
                signal_mask = df["confidence"] >= 4
                divisor = 7.25
                min_photons = 5

            if not np.any(signal_mask):
                df["reflectance"] = np.nan
                return df

            segment_counts = df.loc[signal_mask].groupby("ph_segment_id").size()
            if min_photons > 0:
                segment_counts = segment_counts.where(
                    segment_counts >= min_photons, np.nan
                )

            pseudo_reflec = segment_counts / divisor
            pseudo_reflec = pseudo_reflec.clip(upper=4.0)
            df["reflectance"] = df["ph_segment_id"].map(pseudo_reflec)

        except Exception as e:
            logger.warning(f"Pseudo-reflectance calculation failed: {e}")
        return df

    def apply_atl08_classifications(self, df, atl08_fn, laser, segment_index_dict):
        try:
            with h5.File(atl08_fn, "r") as f:
                if laser not in f:
                    return df
                sig = f[f"/{laser}/signal_photons"]
                atl08_flag = sig["classed_pc_flag"][...]
                atl08_seg = sig["ph_segment_id"][...]
                atl08_idx = sig["classed_pc_indx"][...]

                relevant_segments = df["ph_segment_id"].unique()
                mask = np.isin(atl08_seg, relevant_segments)
                if not np.any(mask):
                    return df

                seg_starts = np.array(
                    [segment_index_dict.get(s, -1) for s in atl08_seg[mask]]
                )
                valid_seg_mask = seg_starts != -1
                atl03_indices = seg_starts[valid_seg_mask] + (
                    atl08_idx[mask][valid_seg_mask] - 1
                )

                valid_idx_mask = (atl03_indices >= 0) & (atl03_indices < len(df))
                final_indices = atl03_indices[valid_idx_mask]

                values_to_assign = atl08_flag[mask][valid_seg_mask][valid_idx_mask]
                df.loc[df.index[final_indices], "ph_h_classed"] = values_to_assign
        except Exception as e:
            logger.warning(f"Failed to apply ATL08 classifications: {e}")
        return df

    def apply_atl12_classifications(self, df, atl12_fn, laser):
        try:
            with h5.File(atl12_fn, "r") as f:
                if laser not in f:
                    return df
                path = f"/{laser}/ssh_segments/stats"
                if path not in f or "segment_id_beg" not in f[path]:
                    return df

                atl12_seg = f[f"{path}/segment_id_beg"][...]
                is_ocean = df["ph_segment_id"].isin(atl12_seg)
                mask = is_ocean & (df["ph_h_classed"] < 40)
                df.loc[mask, "ph_h_classed"] = 44
        except Exception as e:
            logger.warning(f"Failed to apply ATL12 classifications: {e}")
        return df

    def apply_atl13_classifications(self, df, atl13_fn, laser):
        try:
            with h5.File(atl13_fn, "r") as f:
                if laser not in f:
                    return df
                if f"/{laser}/segment_id_beg" not in f:
                    return df
                atl13_seg = f[f"/{laser}/segment_id_beg"][...]
                is_water = df["ph_segment_id"].isin(atl13_seg)
                _classes = [1, 41, 44]
                mask = is_water & (df["ph_h_classed"].isin(_classes))
                df.loc[mask, "ph_h_classed"] = 42
        except Exception as e:
            logger.warning(f"Failed to apply ATL13 classifications: {e}")
        return df

    def apply_atl24_classifications(self, df, atl24_fn, laser, geoseg_beg, geoseg_end):
        try:
            with h5.File(atl24_fn, "r") as f:
                if laser not in f:
                    return df
                grp = f[laser]
                try:
                    atl24_class = grp["class_ph"][...]
                    atl24_seg = grp["index_seg"][...]
                    atl24_idx = grp["index_ph"][...]
                    atl24_conf = grp["confidence"][...]
                    atl24_lat = grp["lat_ph"][...]
                    atl24_lon = grp["lon_ph"][...]
                    atl24_z = grp["ortho_h"][...]
                except KeyError:
                    return df

                orig_segs = np.arange(geoseg_beg, geoseg_end + 1)
                try:
                    atl24_real_seg_ids = orig_segs[atl24_seg]
                except IndexError:
                    return df

                atl24_df = pd.DataFrame(
                    {
                        "ph_segment_id": atl24_real_seg_ids,
                        "ph_index_within_seg": atl24_idx,
                        "atl24_class": atl24_class,
                        "atl24_conf": atl24_conf,
                        "atl24_lat": atl24_lat,
                        "atl24_lon": atl24_lon,
                        "atl24_z": atl24_z,
                    }
                )

                atl24_df = atl24_df[
                    atl24_df["ph_segment_id"].isin(df["ph_segment_id"].unique())
                ]
                if atl24_df.empty:
                    return df

                is_bathy = atl24_df["atl24_class"] == 40
                if self.min_bathy_confidence is not None:
                    is_bathy &= atl24_df["atl24_conf"] >= self.min_bathy_confidence
                atl24_df = atl24_df[is_bathy]

                merged = df.merge(
                    atl24_df, on=["ph_segment_id", "ph_index_within_seg"], how="left"
                )
                mask = merged["atl24_class"].notna()
                if np.any(mask):
                    df.loc[mask, "ph_h_classed"] = merged.loc[
                        mask, "atl24_class"
                    ].astype(int)
                    df.loc[mask, "bathy_confidence"] = merged.loc[
                        mask, "atl24_conf"
                    ].astype(int)
                    df.loc[mask, "latitude"] = merged.loc[mask, "atl24_lat"]
                    df.loc[mask, "longitude"] = merged.loc[mask, "atl24_lon"]
                    df.loc[mask, "photon_height"] = merged.loc[mask, "atl24_z"]
        except Exception as e:
            logger.warning(f"Failed to apply ATL24 data: {e}")
        return df

    def classify_outliers_algo(self, df, multiplier=3.0):
        try:
            candidate_mask = (df["confidence"] >= 3) & (df["ph_h_classed"] != 0)
            if not np.any(candidate_mask):
                return df
            subset = df[candidate_mask]
            grouped = subset.groupby("ph_segment_id")["photon_height"]
            q1 = grouped.quantile(0.25)
            q3 = grouped.quantile(0.75)
            mapped_q1 = subset["ph_segment_id"].map(q1)
            mapped_q3 = subset["ph_segment_id"].map(q3)
            iqr = mapped_q3 - mapped_q1
            lower_bound = mapped_q1 - (multiplier * iqr)
            upper_bound = mapped_q3 + (multiplier * iqr)
            is_outlier = (subset["photon_height"] < lower_bound) | (
                subset["photon_height"] > upper_bound
            )
            outlier_indices = subset.index[is_outlier]
            if len(outlier_indices) > 0:
                df.loc[outlier_indices, "ph_h_classed"] = 0
        except Exception as e:
            logger.warning(f"Outlier classification failed: {e}")
        return df

    def classify_bathymetry_algo(self, df, chunk_size=3000, overlap=100):
        # Known Bathymetry Check
        if self.known_bathymetry:
            try:
                pts = list(zip(df["longitude"].values, df["latitude"].values))
                with rasterio.open(self.known_bathymetry) as src:
                    ref_z = np.fromiter(
                        (x[0] for x in src.sample(pts)), dtype=np.float32
                    )

                diff = np.abs(df["photon_height"].values - ref_z)
                is_bathy = diff <= self.known_bathy_threshold
                mask = is_bathy & (df["ph_h_classed"] >= 40)
                df.loc[mask, "ph_h_classed"] = 40
            except Exception as e:
                logger.warning(f"Known bathymetry classification failed: {e}")

        # DBSCAN
        if self.use_dbscan and HAS_SKLEARN:
            try:
                mask_candidates = (
                    (df["ph_h_classed"].isin([-1, 0, 1, 41, 42, 44]))
                    & (df["photon_height"] < 0)
                    & (df["photon_height"] > -100)
                )
                if np.count_nonzero(mask_candidates) < self.dbscan_min_samples:
                    return df

                candidate_indices = df.index[mask_candidates]
                subset = df.loc[candidate_indices].copy()
                subset.sort_values("latitude", inplace=True)

                total_points = len(subset)
                chunk_step = chunk_size - overlap
                confirmed_bathy_indices = set()

                for i in range(0, total_points, chunk_step):
                    chunk = subset.iloc[i : i + chunk_size].copy()
                    if len(chunk) < self.dbscan_min_samples:
                        continue

                    lat_scale = (chunk["latitude"] - chunk["latitude"].min()) * 111000
                    X = np.column_stack(
                        (lat_scale.values, chunk["photon_height"].values * 5.0)
                    )

                    db = DBSCAN(
                        eps=self.dbscan_eps,
                        min_samples=self.dbscan_min_samples,
                        metric="euclidean",
                        n_jobs=-1,
                    )
                    labels = db.fit_predict(X)

                    unique_labels = set(labels)
                    if -1 in unique_labels:
                        unique_labels.remove(-1)

                    for k in unique_labels:
                        valid_chunk_indices = chunk.index[labels == k]
                        confirmed_bathy_indices.update(valid_chunk_indices)

                if confirmed_bathy_indices:
                    final_mask = df.index.isin(confirmed_bathy_indices)
                    df.loc[final_mask, "ph_h_classed"] = 40
            except Exception as e:
                logger.warning(f"DBSCAN classification failed: {e}")
        return df

    def classify_buildings_algo(
        self,
        df,
        min_height=4,
        max_roughness=0.25,
        max_range=2.0,
        max_thickness=0.5,
        roughness_window=35,
        ground_window=60,
        min_reflectance=0.6,
        dark_veto_threshold=0.25,
        max_building_length=150,
    ):
        try:
            signal_mask = (df["confidence"] >= 3) & (df["ph_h_classed"] != 0)
            if not np.any(signal_mask):
                return df
            _signal_df = df[signal_mask]

            ground_proxy = (
                df["photon_height"]
                .rolling(
                    window=ground_window, center=True, min_periods=ground_window // 3
                )
                .quantile(0.05)
            )
            ground_proxy = ground_proxy.bfill().ffill()
            hag = df["photon_height"] - ground_proxy

            is_elevated = (
                (hag >= min_height)
                & (df["confidence"] >= 3)
                & (df["ph_h_classed"] != 0)
            )
            if not np.any(is_elevated):
                return df

            elevated_df = df[is_elevated].copy()
            roller = elevated_df["photon_height"].rolling(
                window=roughness_window, center=True, min_periods=5
            )
            elevated_df["local_roughness"] = roller.std()
            elevated_df["local_range"] = roller.max() - roller.min()
            elevated_df["local_thickness"] = roller.quantile(0.90) - roller.quantile(
                0.10
            )

            mask_geo = (
                (elevated_df["local_roughness"] <= max_roughness)
                & (elevated_df["local_range"] <= max_range)
                & (elevated_df["local_thickness"] <= max_thickness)
            )
            if "reflectance" in df.columns:
                is_too_dark = elevated_df["reflectance"] < dark_veto_threshold
                mask_geo = mask_geo & (~is_too_dark)

            mask_rad = np.zeros(len(elevated_df), dtype=bool)
            if "reflectance" in df.columns and elevated_df["reflectance"].notna().any():
                mask_rad = (
                    (elevated_df["local_roughness"] <= 1.5)
                    & (elevated_df["reflectance"] >= min_reflectance)
                    & (elevated_df["local_range"] <= 3.0)
                    & (elevated_df["local_thickness"] <= 1.5)
                )

            is_building = mask_geo | mask_rad
            building_candidates = elevated_df[is_building].copy()
            if len(building_candidates) == 0:
                return df

            idx_series = building_candidates.index.to_series()
            gap_check = idx_series.diff() > 20
            group_ids = gap_check.cumsum()
            max_photon_span = max_building_length / 0.7
            groups = idx_series.groupby(group_ids)
            group_spans = groups.max() - groups.min()

            full_diffs = df["photon_height"].diff().abs()
            is_wall_jump = full_diffs > min_height
            candidate_has_wall = is_wall_jump.loc[building_candidates.index]
            group_has_wall = candidate_has_wall.groupby(group_ids).any()

            valid_group_ids = group_spans.index[
                (group_spans <= max_photon_span) & (group_has_wall)
            ]
            final_mask = group_ids.isin(valid_group_ids)
            final_indices = building_candidates.index[final_mask]

            if len(final_indices) > 0:
                protected_classes = [40, 41, 42, 44]
                mask = df.index.isin(final_indices) & (
                    ~df["ph_h_classed"].isin(protected_classes)
                )
                df.loc[mask, "ph_h_classed"] = 7
        except Exception as e:
            logger.warning(f"Building classification failed: {e}")
        return df

    def classify_nearshore_roughness(
        self,
        df,
        height_window=2.5,
        max_roughness=1.5,
        use_reflectance=True,
        surf_reflectance=0.4,
    ):
        try:
            signal_mask = (df["confidence"] >= 3) & (df["ph_h_classed"] != 0)
            if not np.any(signal_mask):
                return df
            signal_df = df[signal_mask]

            aggs = {"photon_height": ["median", "std"]}
            if use_reflectance and "reflectance" in df.columns:
                aggs["reflectance"] = "median"
            grouped = signal_df.groupby("ph_segment_id")
            seg_stats = grouped.agg(aggs)
            seg_stats.columns = [
                "_".join(col).strip() for col in seg_stats.columns.values
            ]

            is_near_geoid = seg_stats["photon_height_median"].abs() <= height_window
            is_calm = is_near_geoid & (seg_stats["photon_height_std"] <= 0.3)

            is_surf = np.zeros(len(seg_stats), dtype=bool)
            if use_reflectance and "reflectance_median" in seg_stats.columns:
                is_surf = (
                    is_near_geoid
                    & (seg_stats["photon_height_std"] > 0.3)
                    & (seg_stats["photon_height_std"] <= max_roughness)
                    & (seg_stats["reflectance_median"] >= surf_reflectance)
                )
            elif not use_reflectance:
                is_surf = is_near_geoid & (
                    seg_stats["photon_height_std"] <= max_roughness
                )

            valid_water_segs = seg_stats.index[is_calm | is_surf]
            if len(valid_water_segs) == 0:
                return df

            is_water_segment = df["ph_segment_id"].isin(valid_water_segs)
            is_within_window = df["photon_height"].abs() <= height_window
            mask = is_water_segment & is_within_window & (df["ph_h_classed"] < 40)
            df.loc[mask, "ph_h_classed"] = 41
        except Exception as e:
            logger.warning(f"Nearshore classification failed: {e}")
        return df

    def classify_inland_water_algo(
        self,
        df,
        max_roughness=0.3,
        max_reflectance=0.25,
        max_range=0.75,
        fill_gaps=True,
        gap_window=15,
        fill_threshold=0.3,
    ):
        try:
            if "reflectance" not in df.columns:
                return df
            signal_mask = (df["confidence"] >= 3) & (df["ph_h_classed"] != 0)
            if not np.any(signal_mask):
                return df
            signal_df = df[signal_mask].copy()

            grouped = signal_df.groupby("ph_segment_id")
            seg_stats = grouped.agg(
                {
                    "photon_height": ["std", "count", "max", "min"],
                    "reflectance": "median",
                }
            )
            seg_stats.columns = [
                "_".join(col).strip() for col in seg_stats.columns.values
            ]
            seg_stats["height_ptp"] = (
                seg_stats["photon_height_max"] - seg_stats["photon_height_min"]
            )

            is_dark_water = (
                (seg_stats["photon_height_std"] <= max_roughness)
                & (seg_stats["reflectance_median"] <= max_reflectance)
                & (seg_stats["photon_height_count"] > 3)
            )
            is_specular_water = (seg_stats["reflectance_median"] > 1.5) & (
                seg_stats["photon_height_std"] <= max_roughness
            )
            is_sparse_water = (
                (seg_stats["photon_height_count"] >= 3)
                & (seg_stats["photon_height_count"] <= 10)
                & (seg_stats["photon_height_std"] <= 0.1)
            )

            seg_stats["is_water"] = (
                is_dark_water | is_specular_water | is_sparse_water
            ).astype(int)

            if fill_gaps:
                seg_stats.sort_index(inplace=True)
                neighbor_water_rate = (
                    seg_stats["is_water"]
                    .rolling(window=gap_window, center=True, min_periods=1)
                    .mean()
                )
                is_safe_to_overwrite = (seg_stats["height_ptp"] <= 4.0) & (
                    seg_stats["photon_height_std"] <= 2.5
                )
                is_gap_fill = (
                    (neighbor_water_rate > fill_threshold)
                    & (is_safe_to_overwrite)
                    & (seg_stats["is_water"] == 0)
                )
                seg_stats.loc[is_gap_fill, "is_water"] = 1

            valid_water_segs = seg_stats.index[seg_stats["is_water"] == 1]
            if len(valid_water_segs) == 0:
                return df

            is_water_photon = df["ph_segment_id"].isin(valid_water_segs)
            mask = is_water_photon & (df["ph_h_classed"] < 40)
            df.loc[mask, "ph_h_classed"] = 42
        except Exception as e:
            logger.warning(f"Inland water classification failed: {e}")
        return df

    def classify_by_mask_geoms(
        self, dataset, mask_file, classification, except_classes=[]
    ):
        """Uses Fiona and Shapely STRtree for point-in-polygon classification."""

        if not os.path.exists(mask_file):
            return dataset

        try:
            geoms = []
            with fiona.open(mask_file, "r") as src:
                for feat in src:
                    if feat.get("geometry") is not None:
                        geoms.append(shape(feat["geometry"]))

            if not geoms:
                return dataset

            tree = STRtree(geoms)
            x_vals = (
                dataset["x"].values
                if "x" in dataset.columns
                else dataset["longitude"].values
            )
            y_vals = (
                dataset["y"].values
                if "y" in dataset.columns
                else dataset["latitude"].values
            )
            points = shapely.points(x_vals, y_vals)

            _, pt_idx = tree.query(points, predicate="intersects")
            intersecting_indices = np.unique(pt_idx)

            if len(intersecting_indices) > 0:
                real_indices = dataset.iloc[intersecting_indices].index
                mask = dataset.index.isin(real_indices) & (
                    ~dataset["ph_h_classed"].isin(except_classes)
                )
                dataset.loc[mask, "ph_h_classed"] = classification

        except Exception as e:
            logger.warning(f"Failed to apply mask {mask_file}: {e}")

        return dataset

    def apply_external_masks(self, df):
        """Dynamically fetches building and land masks to classify photons."""

        if not self.use_external_masks or not self.region:
            return df

        from fetchez.modules.bing import Bing
        from fetchez.modules.wsf import WSF
        from fetchez.modules.gba import GBA
        from globato.hooks.hooks.osm_landmask import OSMLandmask
        from transformez.spatial import TransRegion

        if isinstance(self.region, list):
            region_obj = TransRegion.from_list(self.region)
        else:
            region_obj = self.region

        logger.info("Fetching external masks to classify ICESat-2 photons...")

        # Bing Buildings -> Class 7 (Buildings/Noise)
        bing_fetcher = Bing(src_region=region_obj)
        run_fetchez([bing_fetcher])
        for res in bing_fetcher.results:
            if res.get("dst_fn"):
                df = self.classify_by_mask_geoms(
                    df, res["dst_fn"], 7, except_classes=[40, 41, 42, 44]
                )

        # Global Building Atlas -> Class 7 (Buildings/Noise)
        gba_fetcher = GBA(src_region=region_obj, fmt="geojson")
        run_fetchez([gba_fetcher])
        for res in gba_fetcher.results:
            if res.get("dst_fn"):
                df = self.classify_by_mask_geoms(
                    df, res["dst_fn"], 7, except_classes=[40, 41, 42, 44]
                )

        # World Settlement Footprint -> Class 7 (Buildings/Noise)
        wsf_fetcher = WSF(src_region=region_obj)
        run_fetchez([wsf_fetcher])
        for res in wsf_fetcher.results:
            if res.get("dst_fn"):
                # WSF returns GeoTIFFs, so we need to vectorize it first.
                logger.info(
                    f"WSF tile fetched: {res['dst_fn']}. (Raster masking hook needed here)"
                )

        # OSM Landmask -> Class 1 (Land / Unclassified)
        osm_hook = OSMLandmask(filename="temp_icesat2_landmask.geojson")
        dummy_mod = type("Dummy", (), {"region": region_obj})()
        osm_hook.run([(dummy_mod, {})])

        if os.path.exists("temp_icesat2_landmask.geojson"):
            df = self.classify_by_mask_geoms(
                df,
                "temp_icesat2_landmask.geojson",
                1,
                except_classes=[7, 40, 41, 42, 44],
            )

        return df

    # ==============================================
    # Main Reader
    # ==============================================
    def read_atl03(
        self,
        f,
        laser_num,
        orientation=None,
        atl08_fn=None,
        atl09_fn=None,
        atl24_fn=None,
        atl06_fn=None,
        atl12_fn=None,
        atl13_fn=None,
    ):
        if orientation is None:
            orientation = f["/orbit_info/sc_orient"][0]
        laser = "gt" + laser_num + self.orientDict[orientation]
        if laser not in f or "heights" not in f[laser]:
            return None

        try:
            true_sc_orient = f["/orbit_info/sc_orient"][0]
        except KeyError:
            true_sc_orient = 0
        side = laser[-1]
        is_strong = (true_sc_orient == 0 and side == "l") or (
            true_sc_orient == 1 and side == "r"
        )

        try:
            h_grp = f[f"/{laser}/heights"]
            geo_grp = f[f"/{laser}/geolocation"]
            geophys_grp = f[f"/{laser}/geophys_corr"]
            anc = f["ancillary_data"]

            lat = h_grp["lat_ph"][...]
            lon = h_grp["lon_ph"][...]
            h_ph = h_grp["h_ph"][...]
            conf = h_grp["signal_conf_ph"][..., 0]
            dt = h_grp["delta_time"][...]
            seg_ph_cnt = geo_grp["segment_ph_cnt"][...]
            seg_id = geo_grp["segment_id"][...]
            geoseg_beg = anc["start_geoseg"][0]
            geoseg_end = anc["end_geoseg"][0]
            surf_type = geo_grp["surf_type"][...]
            geoid = geophys_grp["geoid"][...]
            geoid_f2m = geophys_grp["geoid_free2mean"][...]
            dem_h = geophys_grp["dem_h"][...]
        except KeyError:
            return None

        ph_seg_ids = np.repeat(seg_id, seg_ph_cnt)
        seg_is_ocean = surf_type[:, 1]
        ph_is_ocean = np.repeat(seg_is_ocean, seg_ph_cnt)

        min_len = min(len(ph_seg_ids), len(h_ph))
        ph_seg_ids = ph_seg_ids[:min_len]
        lat = lat[:min_len]
        lon = lon[:min_len]
        h_ph = h_ph[:min_len]
        conf = conf[:min_len]
        dt = dt[:min_len]

        if len(ph_seg_ids) < np.sum(seg_ph_cnt):
            unique, counts = np.unique(ph_seg_ids, return_counts=True)
            ph_index_counters = np.concatenate([np.arange(1, c + 1) for c in counts])
        else:
            ph_index_counters = np.concatenate(
                [np.arange(1, c + 1) for c in seg_ph_cnt]
            )
            ph_index_counters = ph_index_counters[:min_len]

        h_geoid_map = dict(zip(seg_id, geoid))
        h_f2m_map = dict(zip(seg_id, geoid_f2m))
        h_dem_map = dict(zip(seg_id, dem_h))
        p_geoid = np.array([h_geoid_map.get(s, 0) for s in ph_seg_ids])
        p_f2m = np.array([h_f2m_map.get(s, 0) for s in ph_seg_ids])
        p_dem = np.array([h_dem_map.get(s, 0) for s in ph_seg_ids])

        h_ortho = h_ph - p_geoid
        h_meantide = h_ph - (p_geoid + p_f2m)
        h_dem = p_dem - (p_geoid + p_f2m)

        if self.water_surface == "mean_tide":
            z_out = h_meantide
        elif self.water_surface == "geoid":
            z_out = h_ortho
        else:
            z_out = h_ph

        df = pd.DataFrame(
            {
                "latitude": lat,
                "longitude": lon,
                "photon_height": z_out,
                "laser": laser,
                "fn": self.fn,
                "confidence": conf,
                "delta_time": dt,
                "photon_h_dem": h_dem,
                "photon_meantide": h_meantide,
                "ph_h_classed": -1,
                "bathy_confidence": -1,
                "ph_segment_id": ph_seg_ids,
                "ph_index_within_seg": ph_index_counters,
            }
        )

        seg_starts = np.concatenate(([0], np.cumsum(seg_ph_cnt)[:-1]))
        seg_idx_dict = dict(zip(seg_id, seg_starts))

        if atl08_fn:
            df = self.apply_atl08_classifications(df, atl08_fn, laser, seg_idx_dict)
        if atl09_fn:
            df = self.apply_atl09_data(df, atl09_fn, laser)
        if "reflectance" not in df.columns or df["reflectance"].isna().all():
            df = self.calculate_pseudo_reflectance(df, is_strong=is_strong)

        is_open_ocean = (ph_is_ocean == 1) & (np.abs(h_ortho) < 2)
        df.loc[is_open_ocean, "ph_h_classed"] = 44
        df = self.classify_outliers_algo(df, multiplier=3.0)

        if atl24_fn:
            df = self.apply_atl24_classifications(
                df, atl24_fn, laser, geoseg_beg, geoseg_end
            )
        if atl13_fn:
            df = self.apply_atl13_classifications(df, atl13_fn, laser)
        if atl12_fn:
            df = self.apply_atl12_classifications(df, atl12_fn, laser)

        df = self.classify_nearshore_roughness(df)
        df = self.classify_inland_water_algo(
            df, max_roughness=0.45, max_reflectance=0.2, max_range=1, fill_gaps=True
        )
        df = self.classify_buildings_algo(df)

        if self.known_bathymetry or (self.use_dbscan and HAS_SKLEARN):
            df = self.classify_bathymetry_algo(df)

        return df

    def yield_chunks(self):
        """Pipeline to yield classified points."""

        # bing_geom = None
        # osm_geom = None
        # osm_lakes = None

        if self.use_external_masks:
            logger.warning(
                "External mask fetching (Bing/OSM) disabled. Need fetches.osm/bingbfp module."
            )

        # Fetch Aux ATLXX Data
        atl08_fn = self.fetch_atlxx(self.fn, "ATL08") if self.classes else None
        atl24_fn = self.fetch_atlxx(self.fn, "ATL24") if self.classes else None
        # atl12_fn = self.fetch_atlxx(self.fn, "ATL12") if self.classes else None
        # atl13_fn = self.fetch_atlxx(self.fn, "ATL13") if self.classes else None
        atl06_fn = None
        atl09_fn = None

        with h5.File(self.fn, "r") as f:
            if self.reject_failed_qa and "quality_assessment" in f:
                if f["/quality_assessment/qa_granule_pass_fail"][0] != 0:
                    logger.warning(f"Skipping failed granule {self.fn}")
                    return

            for i in range(1, 4):
                for orient in range(2):
                    dataset = self.read_atl03(
                        f,
                        str(i),
                        orientation=orient,
                        atl08_fn=atl08_fn,
                        atl09_fn=atl09_fn,
                        atl24_fn=atl24_fn,
                        atl06_fn=atl06_fn,
                    )

                    if dataset is None or dataset.empty:
                        continue

                    if self.confidence_levels:
                        dataset = dataset[
                            dataset["confidence"].isin(self.confidence_levels)
                        ]

                    if self.classes:
                        dataset = dataset[dataset["ph_h_classed"].isin(self.classes)]

                    if dataset.empty:
                        continue

                    # Rename to Standard Schema
                    dataset.rename(
                        columns={
                            "longitude": "x",
                            "latitude": "y",
                            "photon_height": "z",
                        },
                        inplace=True,
                    )

                    if self.region:
                        xmin = getattr(self.region, "xmin", self.region[0])
                        xmax = getattr(self.region, "xmax", self.region[1])
                        ymin = getattr(self.region, "ymin", self.region[2])
                        ymax = getattr(self.region, "ymax", self.region[3])

                        dataset = dataset[
                            (dataset["x"] >= xmin)
                            & (dataset["x"] <= xmax)
                            & (dataset["y"] >= ymin)
                            & (dataset["y"] <= ymax)
                        ]

                    if dataset.empty:
                        continue

                    yield dataset.to_records(index=False)


# ==============================================
# Testing reader/stream hook
# ==============================================
class ATL03Stream(FetchHook):
    name = "atl03_stream"
    meta_stage = "format"
    meta_category = "format-stream"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.params = kwargs

    def run(self, entries):
        for mod, entry in entries:
            dst_fn = entry.get("dst_fn")

            if dst_fn and dst_fn.endswith(".h5") and os.path.exists(dst_fn):
                logger.info(
                    f"[{self.name}] Initiating raw ATL03 stream for {os.path.basename(dst_fn)}"
                )

                reader = ATL03RawReader(dst_fn, **self.params)
                entry["stream"] = reader.yield_chunks()
                entry["stream_type"] = "xyz_recarray"

        return entries


class ATL03RawReader:
    """A reader for raw ICESat-2 ATL03 HDF5 files.
    Yields chunks of NumPy structured arrays containing photons across all 6 beams.

    Usage:
      --hook read_atl03
    """

    def __init__(self, src_fn, chunk_size=1000000, **kwargs):
        self.src_fn = src_fn
        self.chunk_size = int(chunk_size)
        self.beams = ["gt1l", "gt1r", "gt2l", "gt2r", "gt3l", "gt3r"]

    def yield_chunks(self):
        try:
            with h5.File(self.src_fn, "r") as f:
                for beam in self.beams:
                    if beam not in f or f"{beam}/heights" not in f:
                        continue

                    h_grp = f[f"{beam}/heights"]

                    if not all(k in h_grp for k in ["lon_ph", "lat_ph", "h_ph"]):
                        logger.warning(
                            f"[{self.src_fn}] Missing spatial arrays in beam {beam}"
                        )
                        continue

                    total_pts = h_grp["h_ph"].shape[0]
                    if total_pts == 0:
                        continue

                    logger.debug(
                        f"[{self.src_fn}] Streaming {total_pts} photons from {beam}..."
                    )

                    for i in range(0, total_pts, self.chunk_size):
                        chunk_end = min(i + self.chunk_size, total_pts)
                        n_pts = chunk_end - i

                        dt = [
                            ("x", "f8"),
                            ("y", "f8"),
                            ("z", "f4"),
                            ("w", "f4"),
                            ("delta_time", "f8"),
                            ("beam", "S4"),
                        ]

                        chunk_arr = np.zeros(n_pts, dtype=dt)
                        chunk_arr["x"] = h_grp["lon_ph"][i:chunk_end]
                        chunk_arr["y"] = h_grp["lat_ph"][i:chunk_end]
                        chunk_arr["z"] = h_grp["h_ph"][i:chunk_end]

                        if "delta_time" in h_grp:
                            chunk_arr["delta_time"] = h_grp["delta_time"][i:chunk_end]
                        chunk_arr["beam"] = beam.encode("utf-8")

                        if "signal_conf_ph" in h_grp:
                            # ATL03 confidence is an (N, 5) array for 5 surface types.
                            # Taking the max across axis 1 securely grabs the highest
                            # confidence rating this photon received across any algorithm!
                            conf_block = h_grp["signal_conf_ph"][i:chunk_end]
                            if conf_block.ndim == 2:
                                chunk_arr["w"] = np.max(conf_block, axis=1)
                            else:
                                chunk_arr["w"] = conf_block

                        yield chunk_arr

        except Exception as e:
            logger.error(f"[ATL03] Failed to parse ATL03 {self.src_fn}: {e}")
