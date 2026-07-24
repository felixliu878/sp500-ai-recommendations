from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


TRADING_DAYS = 252


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 6) if math.isfinite(number) else None


def _return_over(prices: pd.Series, periods: int) -> float | None:
    clean = prices.dropna()
    if len(clean) <= periods:
        return None
    return _finite(clean.iloc[-1] / clean.iloc[-periods - 1] - 1.0)


def _rsi(prices: pd.Series, periods: int = 14) -> float | None:
    delta = prices.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / periods, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / periods, adjust=False).mean()
    if loss.empty or loss.iloc[-1] == 0:
        return 100.0 if not gain.empty and gain.iloc[-1] > 0 else None
    relative_strength = gain.iloc[-1] / loss.iloc[-1]
    return _finite(100 - 100 / (1 + relative_strength))


def compute_features(
    frame: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    sector_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    if frame.empty:
        raise ValueError("Price frame is empty")
    data = frame.sort_values("date").copy()
    price = pd.to_numeric(data["adj_close"], errors="coerce").dropna()
    volume = pd.to_numeric(data.get("volume"), errors="coerce")
    latest = float(price.iloc[-1])
    returns = price.pct_change().dropna()

    sma20 = price.rolling(20).mean().iloc[-1]
    sma50 = price.rolling(50).mean().iloc[-1]
    sma200 = price.rolling(200).mean().iloc[-1]
    ema12 = price.ewm(span=12, adjust=False).mean()
    ema26 = price.ewm(span=26, adjust=False).mean()
    macd_series = ema12 - ema26
    macd_signal = macd_series.ewm(span=9, adjust=False).mean()

    trailing = price.tail(TRADING_DAYS)
    rolling_peak = trailing.cummax()
    drawdown = trailing / rolling_peak - 1
    high_52w = trailing.max()
    low_52w = trailing.min()
    range_position = (latest - low_52w) / (high_52w - low_52w) if high_52w > low_52w else None

    volume_tail = volume.dropna().tail(20)
    volume_z = None
    if len(volume_tail) >= 5 and volume_tail.std(ddof=0) > 0:
        volume_z = (volume_tail.iloc[-1] - volume_tail.mean()) / volume_tail.std(ddof=0)

    beta = None
    relative_12m = None
    if benchmark is not None and not benchmark.empty:
        stock_returns = data.set_index("date")["adj_close"].pct_change().rename("stock")
        benchmark_returns = (
            benchmark.sort_values("date").set_index("date")["adj_close"].pct_change().rename("benchmark")
        )
        aligned = pd.concat([stock_returns, benchmark_returns], axis=1).dropna().tail(TRADING_DAYS)
        if len(aligned) >= 30 and aligned["benchmark"].var() > 0:
            beta = aligned["stock"].cov(aligned["benchmark"]) / aligned["benchmark"].var()
        stock_12m = _return_over(price, TRADING_DAYS)
        benchmark_12m = _return_over(
            benchmark.sort_values("date")["adj_close"], TRADING_DAYS
        )
        if stock_12m is not None and benchmark_12m is not None:
            relative_12m = stock_12m - benchmark_12m

    sector_relative_12m = None
    if sector_frame is not None and not sector_frame.empty:
        stock_12m = _return_over(price, TRADING_DAYS)
        sector_12m = _return_over(sector_frame.sort_values("date")["adj_close"], TRADING_DAYS)
        if stock_12m is not None and sector_12m is not None:
            sector_relative_12m = stock_12m - sector_12m

    return {
        "data_start": data["date"].min().date().isoformat(),
        "data_end": data["date"].max().date().isoformat(),
        "observations": int(len(data)),
        "latest_adjusted_close": _finite(latest),
        "return_1m": _return_over(price, 21),
        "return_3m": _return_over(price, 63),
        "return_6m": _return_over(price, 126),
        "return_12m": _return_over(price, TRADING_DAYS),
        "relative_return_vs_spy_12m": _finite(relative_12m),
        "relative_return_vs_sector_12m": _finite(sector_relative_12m),
        "volatility_20d_annualized": _finite(returns.tail(20).std() * np.sqrt(TRADING_DAYS)),
        "volatility_60d_annualized": _finite(returns.tail(60).std() * np.sqrt(TRADING_DAYS)),
        "sma20": _finite(sma20),
        "sma50": _finite(sma50),
        "sma200": _finite(sma200),
        "distance_from_sma20": _finite(latest / sma20 - 1) if sma20 else None,
        "distance_from_sma50": _finite(latest / sma50 - 1) if sma50 else None,
        "distance_from_sma200": _finite(latest / sma200 - 1) if sma200 else None,
        "rsi14": _rsi(price),
        "macd": _finite(macd_series.iloc[-1]),
        "macd_signal": _finite(macd_signal.iloc[-1]),
        "maximum_drawdown_52w": _finite(drawdown.min()),
        "position_in_52w_range": _finite(range_position),
        "volume_zscore_20d": _finite(volume_z),
        "beta_vs_spy_1y": _finite(beta),
    }


def build_price_history_packet(frame: pd.DataFrame, months: int = 24) -> list[dict[str, Any]]:
    data = frame.sort_values("date").copy()
    data["calendar_month"] = pd.to_datetime(data["date"]).dt.to_period("M").astype(str)
    monthly = (
        data.groupby("calendar_month", as_index=False)
        .agg(
            observation_date=("date", "last"),
            adj_close=("adj_close", "last"),
            volume=("volume", "sum"),
        )
        .dropna(subset=["adj_close"])
        .tail(months)
    )
    return [
        {
            "calendar_month": str(row["calendar_month"]),
            "observation_date": pd.Timestamp(row["observation_date"]).date().isoformat(),
            "adjusted_close": _finite(row["adj_close"]),
            "monthly_volume_through_observation_date": (
                int(row["volume"]) if pd.notna(row["volume"]) else None
            ),
        }
        for _, row in monthly.iterrows()
    ]
