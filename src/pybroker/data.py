r"""Contains :class:`.DataSource`\ s used to fetch external data."""

"""Copyright (C) 2023 Edward West. All rights reserved.

This code is licensed under Apache 2.0 with Commons Clause license
(see LICENSE for details).
"""

import itertools
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Final, Iterable, Optional, Union

#import alpaca.data.historical.crypto as alpaca_crypto
#import alpaca.data.historical.stock as alpaca_stock

import numpy as np
import pandas as pd
#import yfinance

#from alpaca.data.enums import Adjustment
#from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
#from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from pybroker.cache import DataSourceCacheKey
from pybroker.common import (
    DataCol,
    parse_timeframe,
    to_datetime,
    to_seconds,
    verify_data_source_columns,
    verify_date_range,
)
from pybroker.scope import StaticScope


class DataSourceCacheMixin:
    """Mixin that implements fetching and storing cached :class:`.DataSource`
    data.
    """

    def get_cached(
        self,
        symbols: Iterable[str],
        timeframe: str,
        start_date: Union[str, datetime, pd.Timestamp, np.datetime64],
        end_date: Union[str, datetime, pd.Timestamp, np.datetime64],
        adjust: Optional[Any],
    ) -> tuple[pd.DataFrame, Iterable[str]]:
        """Retrieves cached data from disk when caching is enabled with
        :meth:`pybroker.cache.enable_data_source_cache`.

        Args:
            symbols: :class:`Iterable` of symbols for fetching cached data.
            timeframe: Formatted string that specifies the timeframe
                resolution of the cached data. The timeframe string supports
                the following units:

                - ``"s"``/``"sec"``: seconds
                - ``"m"``/``"min"``: minutes
                - ``"h"``/``"hour"``: hours
                - ``"d"``/``"day"``: days
                - ``"w"``/``"week"``: weeks


                An example timeframe string is ``1h 30m``.
            start_date: Starting date of the cached data (inclusive).
            end_date: Ending date of the cached data (inclusive).
            adjust: The type of adjustment to make.

        Returns:
            ``tuple[pandas.DataFrame, Iterable[str]]`` containing a
            :class:`pandas.DataFrame` with the cached data, and an
            ``Iterable[str]`` of symbols for which no cached data was
            found.
        """
        df = pd.DataFrame()
        scope = StaticScope.instance()
        cache = scope.data_source_cache
        if cache is None:
            return df, symbols
        start_date = to_datetime(start_date)
        end_date = to_datetime(end_date)
        tf_seconds = to_seconds(timeframe)
        uncached_syms = []
        cached_syms = []
        for sym in symbols:
            cache_key = DataSourceCacheKey(
                symbol=sym,
                tf_seconds=tf_seconds,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            cached = cache.get(repr(cache_key))
            scope.logger.debug_get_data_source_cache(cache_key)
            if cached is None:
                uncached_syms.append(sym)
            else:
                cached_syms.append(sym)
                df = pd.concat([df, cached])
        if not uncached_syms:
            scope.logger.loaded_bar_data()
        scope.logger.info_loaded_bar_data(
            symbols=cached_syms,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )
        return df, uncached_syms

    def set_cached(
        self,
        timeframe: str,
        start_date: Union[str, datetime, pd.Timestamp, np.datetime64],
        end_date: Union[str, datetime, pd.Timestamp, np.datetime64],
        adjust: Optional[Any],
        data: pd.DataFrame,
    ):
        """Stores data to disk cache when caching is enabled with
        :meth:`pybroker.cache.enable_data_source_cache`.

        Args:
            timeframe: Formatted string that specifies the timeframe
                resolution of the data to cache. The timeframe string supports
                the following units:

                - ``"s"``/``"sec"``: seconds
                - ``"m"``/``"min"``: minutes
                - ``"h"``/``"hour"``: hours
                - ``"d"``/``"day"``: days
                - ``"w"``/``"week"``: weeks

                An example timeframe string would be ``1h 30m``.
            start_date: Starting date of the data to cache (inclusive).
            end_date: Ending date of the data to cache (inclusive).
            adjust: The type of adjustment to make.
            data: :class:`pandas.DataFrame` containing the data to cache.
        """
        if data.empty:
            return
        scope = StaticScope.instance()
        cache = scope.data_source_cache
        if cache is None:
            return
        start_date = to_datetime(start_date)
        end_date = to_datetime(end_date)
        tf_seconds = to_seconds(timeframe)
        for sym in data[DataCol.SYMBOL.value].unique():
            df = data[data[DataCol.SYMBOL.value] == sym]
            cache_key = DataSourceCacheKey(
                symbol=sym,
                tf_seconds=tf_seconds,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            cache.set(repr(cache_key), df)
            scope.logger.debug_set_data_source_cache(cache_key)


class DataSource(ABC, DataSourceCacheMixin):
    """Base class for querying data from an external source. Extend this class
    and override :meth:`._fetch_data` to implement a custom
    :class:`.DataSource` that can be used with
    :class:`pybroker.strategy.Strategy`.
    """

    def __init__(self):
        self._scope = StaticScope.instance()
        self._logger = self._scope.logger

    def query(
        self,
        symbols: Union[str, Iterable[str]],
        start_date: Union[str, datetime],
        end_date: Union[str, datetime],
        timeframe: Optional[str] = "",
        adjust: Optional[Any] = None,
    ) -> pd.DataFrame:
        """Queries data. Cached data is returned if caching is enabled by
        calling :meth:`pybroker.cache.enable_data_source_cache`.

        Args:
            symbols: Symbols of the data to query.
            start_date: Start date of the data to query (inclusive).
            end_date: End date of the data to query (inclusive).
            timeframe: Formatted string that specifies the timeframe
                resolution to query. The timeframe string supports the
                following units:

                - ``"s"``/``"sec"``: seconds
                - ``"m"``/``"min"``: minutes
                - ``"h"``/``"hour"``: hours
                - ``"d"``/``"day"``: days
                - ``"w"``/``"week"``: weeks

                An example timeframe string is ``1h 30m``.
            adjust: The type of adjustment to make.

        Returns:
            :class:`pandas.DataFrame` containing the queried data.
        """
        start_date = to_datetime(start_date)
        end_date = to_datetime(end_date)
        verify_date_range(start_date, end_date)
        if isinstance(symbols, str) and not symbols:
            raise ValueError("Symbols cannot be empty.")
        unique_syms = (
            frozenset((symbols,))
            if isinstance(symbols, str)
            else frozenset(symbols)
        )
        if not unique_syms:
            raise ValueError("Symbols cannot be empty.")
        timeframe = self._format_timeframe(timeframe)
        cached_df, uncached_syms = self.get_cached(
            symbols=unique_syms,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
        if not uncached_syms:
            return cached_df
        self._logger.download_bar_data_start()
        self._logger.info_download_bar_data_start(
            symbols=uncached_syms,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )
        df = self._fetch_data(
            frozenset(uncached_syms), start_date, end_date, timeframe, adjust
        )
        if (
            self._scope.data_source_cache is not None
            and not cached_df.columns.empty
            and set(cached_df.columns) != set(df.columns)
        ):
            self._logger.info_invalidate_data_source_cache()
            self._scope.data_source_cache.clear()
            return self.query(symbols, start_date, end_date, timeframe)
        verify_data_source_columns(df)
        self.set_cached(timeframe, start_date, end_date, adjust, df)
        df = pd.concat((cached_df, df))
        if not df.empty:
            df = df.sort_values(by=[DataCol.DATE.value, DataCol.SYMBOL.value])
        self._logger.download_bar_data_completed()
        return df.reset_index(drop=True)

    @abstractmethod
    def _fetch_data(
        self,
        symbols: frozenset[str],
        start_date: datetime,
        end_date: datetime,
        timeframe: Optional[str],
        adjust: Optional[Any],
    ) -> pd.DataFrame:
        """:meta public:
        Override this method to return data from a custom
        source. The returned :class:`pandas.DataFrame` must contain the
        following columns: ``symbol``, ``date``, ``open``, ``high``, ``low``,
        and ``close``.

        Args:
            symbols: Ticker symbols of the data to query.
            start_date: Start date of the data to query (inclusive).
            end_date: End date of the data to query (inclusive).
            timeframe: Formatted string that specifies the timeframe
                resolution to query. The timeframe string supports the
                following units:

                - ``"s"``/``"sec"``: seconds
                - ``"m"``/``"min"``: minutes
                - ``"h"``/``"hour"``: hours
                - ``"d"``/``"day"``: days
                - ``"w"``/``"week"``: weeks

                An example timeframe string is ``1h 30m``.
            adjust: The type of adjustment to make.

        Returns:
            :class:`pandas.DataFrame` containing the queried data.
        """

    def _format_timeframe(self, timeframe: Optional[str]) -> str:
        if not timeframe:
            return ""
        return " ".join(
            f"{part[0]}{part[1]}" for part in parse_timeframe(timeframe)
        )
