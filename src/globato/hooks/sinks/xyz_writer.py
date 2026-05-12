#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.hooks.sinks.xyz_writer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Writes the point stream to an ASCII XYZ file inline.

:copyright: (c) 2016 - 2026 Regents of the University of Colorado
:license: MIT, see LICENSE for more details.
"""

import os
import sys
import logging
import numpy as np
from fetchez.hooks import FetchHook

logger = logging.getLogger(__name__)


class WriteXYZ(FetchHook):
    """Saves stream to an ASCII XYZ file and passes the stream onward.

    Args:
        output (str): Filename template (default: '{base}_out.xyz')
        fmt (str): Float formatting (default: '%.6f')
    """

    name = "write_xyz"
    meta_stage = "stream"
    meta_category = "point-stream"
    meta_desc = "Write a point stream to xyz"
    meta_requires = "point-stream"

    def __init__(self, output="{base}_out.xyz", fmt="%.6f", artifact_id=None, **kwargs):
        super().__init__(**kwargs)
        self.output = output
        self.fmt = fmt
        self.artifact_id = artifact_id or self.name

    def run(self, entries):
        for mod, entry in entries:
            if self.is_raster_stream(entry):
                from globato.hooks.transforms.point_pixels import PixelsToPoints

                p2p = PixelsToPoints()
                p2p.run([(mod, entry)])

            if not self.has_stream(entry):
                continue

            stream = entry.get("stream")
            src_fn = entry.get("dst_fn", "unknown")
            base = os.path.splitext(os.path.basename(src_fn))[0]
            out_fn = self.output.format(base=base, name=mod.name)

            if not os.path.isabs(out_fn):
                out_dir = (
                    os.path.dirname(src_fn) if src_fn != "unknown" else os.getcwd()
                )
                out_fn = os.path.join(out_dir, out_fn)

            entry["stream"] = self._write_stream(stream, out_fn)
            # entry.setdefault('artifacts', {})[self.name] = out_fn
            entry.setdefault("artifacts", {})[self.artifact_id] = out_fn

        return entries

    def _write_stream(self, stream, out_fn):
        logger.info(f"Tapping stream to XYZ: {out_fn}")
        total_pts = 0

        with open(out_fn, "w") as f:
            for chunk in stream:
                if len(chunk) == 0:
                    yield chunk
                    continue

                total_pts += len(chunk)

                cols = [chunk["x"], chunk["y"], chunk["z"]]
                if "w" in chunk.dtype.names:
                    cols.append(chunk["w"])

                if "u" in chunk.dtype.names:
                    cols.append(chunk["u"])

                data = np.column_stack(cols)
                np.savetxt(f, data, fmt=self.fmt, delimiter=" ")

                yield chunk

        logger.info(f"Finished writing {total_pts} points to {out_fn}")


class XYZWrite(FetchHook):
    name = "xyz_write"
    meta_stage = "stream"
    meta_category = "stream-sink"

    def __init__(self, output_path=None, **kwargs):
        super().__init__(**kwargs)
        self.output_path = output_path

    def run(self, entries):
        out_port = open(self.output_path, "w") if self.output_path else sys.stdout

        try:
            for mod, entry in entries:
                if not self.has_stream(entry):
                    continue

                stream = entry["stream"]
                for chunk in stream:
                    # Filter out NaN/invalid geometries if necessary
                    np.savetxt(
                        out_port,
                        chunk[["x", "y", "z", "w", "u"]],
                        fmt="%.6f",
                        delimiter=" ",
                    )
        finally:
            if self.output_path:
                out_port.close()

        return entries
