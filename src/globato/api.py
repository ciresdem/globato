#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
globato.api
~~~~~~~~~~~

High-level Python API for Globato.
Provides interface for streaming, processing, and accessing geospatial data.

"""

import os
import logging
import numpy as np
import pandas as pd
from typing import Union, List, Iterator, Optional, cast

from fetchez.modules import FetchModule
from fetchez.registry import ReaderRegistry, ProfileRegistry

logger = logging.getLogger(__name__)


class GlobatoStream:
    """A wrapper around a data stream generator.
    Allows for chaining processing steps.
    """

    def __init__(self, iterator: Iterator[np.ndarray], src_srs: str = "EPSG:4326"):
        self._iterator = iterator
        self.src_srs = src_srs

    def __iter__(self):
        yield from self._iterator

    def map(self, func, **kwargs):
        def _wrapper():
            for chunk in self._iterator:
                if chunk is not None and len(chunk) > 0:
                    yield func(chunk, **kwargs)

        self._iterator = _wrapper()
        return self

    # def reproject(self, dst_srs: str):
    #     from globato.hooks.transforms.reproject import stream_reproject_chunk

    #     def _repro_func(chunk):
    #         return stream_reproject_chunk(chunk, self.src_srs, dst_srs)

    #     return self.map(_repro_func)

    def crop(self, region: List[float]):
        from globato.hooks.transforms.crop import stream_crop_chunk

        return self.map(stream_crop_chunk, region=region)

    def to_dataframe(self, limit: Optional[int] = None) -> pd.DataFrame:
        chunks = []
        count = 0
        for chunk in self._iterator:
            if chunk is None or len(chunk) == 0:
                continue
            chunks.append(pd.DataFrame(chunk))
            count += len(chunk)
            if limit and count >= limit:
                break
        if not chunks:
            return pd.DataFrame()
        df = pd.concat(chunks, ignore_index=True)
        if limit:
            df = df.head(limit)
        return df

    def to_polars(self):
        import polars as pl

        chunks = list(self._iterator)
        if not chunks:
            return pl.DataFrame()
        stacked_array = np.concatenate(chunks)
        return pl.from_numpy(stacked_array)

    def to_numpy(self) -> np.recarray:
        chunks = list(self._iterator)
        if not chunks:
            return cast(
                np.recarray, np.array([], dtype=[("x", "f8"), ("y", "f8"), ("z", "f4")])
            )
        return cast(np.recarray, np.concatenate(chunks))


def read(
    source: Union[str, FetchModule], data_type: Optional[str] = None, **kwargs
) -> GlobatoStream:
    """The entry point for the Globato API.

    Args:
        source: A file path (str) OR a generic Fetchez Module instance.
        data_type: Explicitly set the format profile (e.g., 'nos_xyz', 'copernicus_sdb').
        **kwargs: Arguments passed to the Reader (e.g. chunk_size, weight, uncertainty).

    Returns:
        GlobatoStream: An iterable stream object.
    """

    ReaderRegistry.load_fast()
    ProfileRegistry.load_fast()

    if isinstance(source, str):
        if not os.path.exists(source):
            raise FileNotFoundError(f"Source not found: {source}")

        term = data_type or source.split(".")[-1]
        reader = ReaderRegistry.get_reader(source, term, **kwargs)

        if not reader:
            raise ValueError(f"No valid reader found for {source} (term: {term})")

        schema_gen = reader.yield_chunks()
        src_srs = getattr(reader, "get_srs", lambda: "EPSG:4326")()

        return GlobatoStream(schema_gen, src_srs=src_srs)

    elif isinstance(source, FetchModule):

        def _module_chain_gen():
            for entry in source.results:
                fn = entry.get("dst_fn")
                entry_data_type = data_type or entry.get("data_type")

                if fn and os.path.exists(fn):
                    try:
                        sub_stream = read(fn, data_type=entry_data_type, **kwargs)
                        yield from sub_stream
                    except Exception as e:
                        logger.warning(f"Failed to stream {fn}: {e}")

        return GlobatoStream(_module_chain_gen(), src_srs="EPSG:4326")

    else:
        raise TypeError(f"Unknown source type: {type(source)}")
