import os
import logging
import rasterio
from fetchez.hooks import FetchHook

logger = logging.getLogger(__name__)


class RasterWrite(FetchHook):
    """Universal sink that writes a TIFF. Can act as a terminal sink or an inline tap."""

    name = "raster_write"
    meta_stage = "post"
    meta_category = "sink"

    def __init__(self, suffix="_final", artifact_id=None, inline=False, **kwargs):
        super().__init__(**kwargs)
        self.suffix = suffix
        self.artifact_id = artifact_id or self.name
        # Catch YAML booleans gracefully
        self.inline = str(inline).lower() in ["true", "1", "t", "yes"]

    def _write_stream(self, stream, dst_fn):
        """Generator that intercepts the stream, writes to disk, and yields onward."""
        # Pop and yield the profile
        profile = next(stream)
        yield profile

        # Open the file and process the stream
        with rasterio.open(dst_fn, 'w', **profile) as dst:
            for window, buff_win, data, ndv, transform in stream:

                # Write the chunk to disk
                y_off = window.row_off - buff_win.row_off
                x_off = window.col_off - buff_win.col_off

                if data.ndim == 3:
                    final_chunk = data[:, y_off : y_off + window.height, x_off : x_off + window.width]
                    dst.write(final_chunk, window=window)
                else:
                    final_chunk = data[y_off : y_off + window.height, x_off : x_off + window.width]
                    dst.write(final_chunk, 1, window=window)

                # YIELD THE CHUNK ONWARD to keep the stream alive!
                yield window, buff_win, data, ndv, transform

    def run(self, entries):
        # Do a pre-pass to auto-grid point streams
        for mod, entry in entries:
            if entry.get("stream_type") == "xyz_recarray":
                from globato.hooks.transforms.point_pixels import Point2PixelStream
                gridder = Point2PixelStream()
                gridder.run([(mod, entry)])

        # Wrap the raster streams!
        for mod, entry in entries:
            stream = entry.get("raster_stream")
            if not stream:
                continue

            src_fn = entry.get("dst_fn")
            base = os.path.splitext(src_fn)[0]
            dst_fn = f"{base}{self.suffix}.tif"

            # Create the generator
            writer_generator = self._write_stream(stream, dst_fn)

            if self.inline:
                # Keep stream alive for downstream streaming hooks
                entry["raster_stream"] = writer_generator
            else:
                # 🛑 EAGER EVALUATION: Force the stream to drain and write to disk!
                logger.info(f"Draining raster stream to disk: {dst_fn}")
                for _ in writer_generator:
                    pass
                # Clean up the exhausted stream
                del entry["raster_stream"]

            # Save the artifact target for focus_sink
            entry.setdefault("artifacts", {})[self.artifact_id] = dst_fn

        return entries
# #!/usr/bin/env python
# # -*- coding: utf-8 -*-

# """
# globato.hooks.sinks.raster_writer
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Writes the stream to a raster

# :copyright: (c) 2016 - 2026 Regents of the University of Colorado
# :license: MIT, see LICENSE for more details.
# """

# import os
# import logging
# import rasterio
# from fetchez.hooks import FetchHook

# logger = logging.getLogger(__name__)


# class RasterWrite(FetchHook):
#     """Universal sink that writes a TIFF. Acts as an inline tap on the stream."""

#     name = "raster_write"
#     meta_stage = "post"
#     meta_category = "sink"

#     def __init__(self, suffix="_final", artifact_id=None, **kwargs):
#         super().__init__(**kwargs)
#         self.suffix = suffix
#         self.artifact_id = artifact_id or self.name

#     def _write_stream(self, stream, dst_fn):
#         """Generator that intercepts the stream, writes to disk, and yields onward."""
#         # Pop and yield the profile
#         profile = next(stream)
#         yield profile

#         # Open the file and process the stream
#         with rasterio.open(dst_fn, 'w', **profile) as dst:
#             for window, buff_win, data, ndv, transform in stream:

#                 # Write the chunk to disk
#                 y_off = window.row_off - buff_win.row_off
#                 x_off = window.col_off - buff_win.col_off

#                 if data.ndim == 3:
#                     final_chunk = data[:, y_off : y_off + window.height, x_off : x_off + window.width]
#                     dst.write(final_chunk, window=window)
#                 else:
#                     final_chunk = data[y_off : y_off + window.height, x_off : x_off + window.width]
#                     dst.write(final_chunk, 1, window=window)

#                 # YIELD THE CHUNK ONWARD to keep the stream alive!
#                 yield window, buff_win, data, ndv, transform

#     def run(self, entries):
#         # Do a pre-pass to auto-grid point streams
#         for mod, entry in entries:
#             if entry.get("stream_type") == "xyz_recarray":
#                 from globato.hooks.transforms.point_pixels import PointPixelsHook
#                 gridder = PointPixelsHook()
#                 gridder.run([(mod, entry)])

#         # Wrap the raster streams!
#         for mod, entry in entries:
#             stream = entry.get("raster_stream")
#             if not stream:
#                 continue

#             src_fn = entry.get("dst_fn")
#             base = os.path.splitext(src_fn)[0]
#             dst_fn = f"{base}{self.suffix}.tif"

#             # Wrap the stream generator.
#             # We DO NOT delete the stream or update dst_fn!
#             entry["raster_stream"] = self._write_stream(stream, dst_fn)

#             # Save it under the custom ID so focus_sink can easily find it!
#             entry.setdefault("artifacts", {})[self.artifact_id] = dst_fn
#             print(entry)
#         return entries
