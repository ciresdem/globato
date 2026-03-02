#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.processors.formats.multibeam
~~~~~~~~~~~~~~~~~~~

Multibeam Reader.
Provides a native Python reader for MB-System Format 71 (.fbt).
Falls back to MB-System subprocess calls for raw vendor formats.

:copyright: (c) 2010-2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import io
import json
import struct
import subprocess
import logging
import requests
import numpy as np
import pandas as pd
from shapely.geometry import box, mapping

from fetchez.hooks import FetchHook
from fetchez.core import FetchModule
from fetchez.utils import int_or, float_or
from fetchez.presets import register_global_preset
from fetchez.presets import register_module_preset

from ...utils import yield_cmd

logger = logging.getLogger(__name__)


class MBSReader:
    """Providing an mbsystem parser.

    Process MB-System supported multibeam data files.
    Prefers native python struct parsing for .fbt files.
    """

    def __init__(self,
                 src_fn: str,
                 region=None,
                 mb_fmt=None,
                 mb_exclude="A",
                 want_mbgrid=False,
                 want_binned=False,
                 min_year=None,
                 auto_weight=True,
                 auto_uncertainty=True,
                 want_filtered=False,
                 want_flagged=False,
                 **kwargs):

        self.src_fn = src_fn

        self.region = region
        self.mb_fmt = mb_fmt
        self.mb_exclude = mb_exclude
        self.want_mbgrid = want_mbgrid
        self.want_binned = want_binned
        self.min_year = min_year
        self.auto_weight = auto_weight
        self.auto_uncertainty = auto_uncertainty
        self.want_filtered = want_filtered
        self.want_flagged = want_flagged

        self.weight = 1
        # if self.src_srs is None:
        #     self.src_srs = 'epsg:4326'

    def _get_mbs_meta(self, src_inf):
        """Extract metadata from mbsystem inf file."""

        meta = {"format": None, "date": None, "perc_good": None}

        if not os.path.exists(src_inf):
            return meta

        try:
            with open(src_inf, errors="ignore") as f:
                for line in f:
                    parts = line.split()
                    if not parts: continue

                    if parts[0].strip() == "MBIO":
                        meta["format"] = parts[4]
                    elif parts[0] == "Time:":
                        meta["date"] = parts[3]

                    if ':' in line:
                        p = line.split(":")
                        if p[0].strip() == "Number of Good Beams":
                            meta["perc_good"] = p[1].split()[-1].split("%")[0]
        except Exception:
            pass

        return meta

    def read_native_fbt(self):
        """Natively reads MB-System Format 71 (.fbt) binary files.
        If it encounters unsupported legacy versions or corruption, it returns None
        to trigger the mblist fallback.

        https://github.com/dwcaress/MB-System/blob/master/src/mbio/mbsys_ldeoih.h
        """

        logger.debug(f"Attempting native Python binary read for {self.src_fn}")

        all_x, all_y, all_z, all_flags, all_xtrack = [], [], [], [], []

        # MB-System format ID constants
        ID_V4 = 22068
        ID_V5 = 22069
        ID_COMMENT2 = 25443

        try:
            with open(self.src_fn, 'rb') as f:
                while True:
                    flag_bytes = f.read(2)
                    if not flag_bytes or len(flag_bytes) < 2:
                        break # EOF

                    # Determine Endianness by testing both unpacks
                    le_flag = struct.unpack('<h', flag_bytes)[0]
                    be_flag = struct.unpack('>h', flag_bytes)[0]

                    endian = None
                    if le_flag in [ID_V4, ID_V5, ID_COMMENT2]:
                        endian = '<'
                        flag = le_flag
                    elif be_flag in [ID_V4, ID_V5, ID_COMMENT2]:
                        endian = '>'
                        flag = be_flag
                    elif le_flag in [8995, 25700, 28270, 17476] or be_flag in [8995, 25700, 28270, 17476]:
                        # These are V1, V2, and V3 formats.
                        logger.info("Legacy FBT version detected. Falling back to mblist.")
                        return None
                    else:
                        logger.warning(f"Unknown FBT flag. Falling back to mblist.")
                        return None

                    # Process based on Record Type
                    if flag == ID_COMMENT2:
                        f.read(128) # Comment payload is always 128 bytes
                        continue

                    # Read V4 or V5 Data Header
                    is_v5 = (flag == ID_V5)
                    # Use 'b' (signed char) for ss_scalepower, 'B' for the rest
                    header_fmt = endian + ('dddddfffffffiiiiffbBBB' if is_v5 else 'dddddfffffffhhhhffbBBB')
                    header_size = 96 if is_v5 else 88

                    header_bytes = f.read(header_size)
                    if len(header_bytes) < header_size:
                        break

                    header_data = struct.unpack(header_fmt, header_bytes)

                    lon = header_data[1]
                    lat = header_data[2]
                    sensor_depth = header_data[3]
                    beams_bath = header_data[12]
                    beams_amp = header_data[13]
                    pixels_ss = header_data[14]
                    depth_scale = header_data[16]
                    distance_scale = header_data[17]

                    # Safety check to prevent negative read lengths
                    if beams_bath < 0 or beams_amp < 0 or pixels_ss < 0:
                        logger.error(
                            f"Corrupt array lengths parsed (bath={beams_bath}). Falling back to mblist."
                        )
                        return None

                    # Read Data Arrays
                    beamflags = np.frombuffer(f.read(beams_bath), dtype=np.uint8)
                    bath_raw = np.frombuffer(f.read(beams_bath * 2), dtype=f'{endian}i2')
                    acrosstrack_raw = np.frombuffer(f.read(beams_bath * 2), dtype=f'{endian}i2')

                    # We skip alongtrack as we don't need it for standard XYZ
                    f.read(beams_bath * 2)

                    # Skip Amplitude
                    if beams_amp > 0:
                        f.read(beams_amp * 2)

                    # Skip Sidescan (ss, ss_across, ss_along)
                    if pixels_ss > 0:
                        f.read(pixels_ss * 2 * 3)

                    # Apply Scaling
                    # We want heights, not depths
                    bath = ((bath_raw * depth_scale) + sensor_depth) * -1
                    xtrack = acrosstrack_raw * distance_scale

                    # Position approximation
                    x_pos = lon + (xtrack / (111111.0 * np.cos(np.radians(lat))))
                    y_pos = np.full(beams_bath, lat)

                    all_x.append(x_pos)
                    all_y.append(y_pos)
                    all_z.append(bath)
                    all_flags.append(beamflags)
                    all_xtrack.append(xtrack)

        except Exception as e:
             logger.error(f"Native Python FBT read failed: {e}. Falling back to mblist.")
             return None

        if not all_x:
            return None

        df = pd.DataFrame({
            'x': np.concatenate(all_x),
            'y': np.concatenate(all_y),
            'z': np.concatenate(all_z),
            'beamflag': np.concatenate(all_flags),
            'crosstrack_distance': np.concatenate(all_xtrack)
        })

        if not self.want_flagged:
            df = df[df['beamflag'] == 0]

        # # Apply Filters & Weights
        # if self.want_filtered:
        #     df = df[df['beamflag'] == 0]

        if self.auto_weight:
            src_inf = f"{self.src_fn}.inf"
            meta = self._get_mbs_meta(src_inf)
            age_weight = 1.0
            if meta.get("date"):
                age_weight = min(0.99, max(0.01, 1 - ((2024 - int(meta["date"])) / (2024 - 1980))))

            df["u"] = 0.25 + (0.01 * df['z'].abs())
            df["w"] = np.where(df["u"] > 0, 1.0 / df["u"], 1.0) * age_weight
        else:
            df["u"] = 0.0
            df["w"] = self.weight

        return df

    def yield_chunks(self):
        """Yield data, attempting native reader for .fbt files with a subprocess fallback."""
        dataset = None

        if self.src_fn.lower().endswith('.fbt'):
            dataset = self.read_native_fbt()

        if dataset is None:
            dataset = self.read_mblist_ds()

        if dataset is not None and not dataset.empty:
            yield dataset.to_records(index=False)

    def read_mblist_ds(self):
        """Reads mblist data into a DataFrame, calculates uncertainty/weights,
        and filters noise.
        """

        src_inf = f"{self.src_fn}.inf"
        meta = self._get_mbs_meta(src_inf)

        mb_format = meta["format"]
        if self.src_fn.endswith(".fbt"):
            mb_format = None

        # Base Weight Calculation (Age Decay)
        age_weight = 1.0
        if self.auto_weight:
            if meta["date"]:
                # Decay weight based on age (1980 baseline)
                age_weight = min(0.99, max(0.01, 1 - ((2024 - int(meta["date"])) / (2024 - 1980))))

            if self.weight is not None:
                self.weight *= age_weight

        # Build mblist Command
        if self.region is not None:
            w, e, s, n = self.region
            region_arg = f" -R{w}/{e}/{s}/{n}"
        else:
            region_arg = ""

        fmt_arg = f" -F{mb_format}" if mb_format else ""

        # O-flags: XYZ, Distance(D), Angle(A), Grazing(G), Flag(g),
        # Pitch(P), p(draft), Roll(R), r(heave), Speed(S), Course(C), c(headings), etc.
        cmd_full = f"mblist -M{self.mb_exclude} -OXYZDAGgFPpRrSCcELH#{region_arg} -I{self.src_fn}{fmt_arg}"

        column_names = ["x", "y", "z", "crosstrack_distance", "crosstrack_slope",
                        "flat_bottom_grazing_angle", "seafloor_grazing_angle",
                        "beamflag", "pitch", "draft", "roll", "heave", "speed",
                        "sonar_alt", "sonar_depth", "alongtrack_distance", "cumulative_alongtrack_distance",
                        "heading", "beam_number", "w", "u"]

        # Execute mblist and Parse
        try:
            raw_data = [
                [float(x) for x in line.strip().split("\t")]
                for line in yield_cmd(cmd_full, verbose=False)
            ]
        except ValueError:
            logger.info("Parsed invalid data in mblist output.")
            return pd.DataFrame(columns=column_names)

        if not raw_data:
            return pd.DataFrame(columns=column_names)

        df = pd.DataFrame(raw_data)

        rename_map = {
            0: "x", 1: "y", 2: "z", 3: "crosstrack_distance", 4: "crosstrack_slope",
            5: "flat_bottom_grazing_angle", 6: "seafloor_grazing_angle", 7: "beamflag",
            8: "'pitch", 9: "draft", 10: "roll", 11: "heave", 12: "speed",
            13: "sonar_alt", 14: "sonar_depth", 15: "alongtrack_distance",
            16: "cumulative_alongtrack_distance", 17: "heading", 18: "beam_number"
        }
        df.rename(columns=rename_map, inplace=True)
        df = df[rename_map.values()]

        # Calculate Uncertainty and Weight
        if self.auto_weight:
            u_depth = (0.25 + (0.02 * df['z'].abs())) * 0.51
            u_xtrack = 0.005 * df["crosstrack_distance"].abs()
            speed_diff = (df["speed"] - 14).abs()
            tmp_speed = np.minimum(14, speed_diff)
            u_speed = tmp_speed * 0.51

            df["u"] = np.sqrt(u_depth**2 + u_xtrack**2 + u_speed**2)
            df["w"] = np.where(df["u"] > 0, 1.0 / df["u"], 1.0)
            df["w"] *= age_weight
        else:
            df["u"] = 0.0
            df["w"] = self.weight if self.weight else 1.0

        if self.want_filtered:
            df = self._filter_mbs_data(df)

        return df

    def _filter_mbs_data(self, df):
        """Internal filter to clean noise based on aux columns."""

        initial_count = len(df)

        if "beamflag" in df.columns:
            df = df[df["beamflag"] == 0]
        if "speed" in df.columns:
             df = df[df["speed"] > 2.0]
        if "roll" in df.columns:
            df = df[df["roll"].abs() < 10.0]
        if "seafloor_grazing_angle" in df.columns:
            df = df[df["seafloor_grazing_angle"].abs() > 20.0]
        if "crosstrack_slope" in df.columns:
            df = df[df["crosstrack_slope"].abs() < 50.0]

        removed_count = initial_count - len(df)
        if hasattr(self, 'verbose') and getattr(self, 'verbose', False) and initial_count > 0:
            perc_removed = (removed_count / initial_count) * 100
            logger.info(
                f"Removed {removed_count} of {initial_count} points "
                f"({perc_removed:.2f}%) based on quality metrics."
            )

        return df

    def yield_points(self):
        dataset = self.read_mblist_ds()
        yield dataset
