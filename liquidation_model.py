"""
Approximate liquidation-cluster estimation from public OHLCV + volume data.

IMPORTANT: This is NOT the same data Coinglass shows. Coinglass aggregates
real order-flow / open-interest data across every exchange, some of which
is behind a paid API tier. This module builds an ESTIMATE by assuming a
distribution of common leverage levels and treating historical volume at
each price as a proxy for the size of positions opened at that price.
It's a best-effort approximation meant for screening / idea generation,
not a precise reproduction of the real liquidation map.
"""
import numpy as np
import pandas as pd

# Common leverage tiers offered on major exchanges
LEVERAGE_TIERS = [5, 10, 20, 25, 50, 75, 100, 125]
MAINTENANCE_MARGIN = 0.005  # rough approximation, varies by tier/exchange


def _liq_price_long(entry, leverage):
    return entry * (1 - (1 / leverage) + MAINTENANCE_MARGIN)


def _liq_price_short(entry, leverage):
    return entry * (1 + (1 / leverage) - MAINTENANCE_MARGIN)


def estimate_liquidation_clusters(df: pd.DataFrame, bins: int = 60):
    """
    df: OHLCV daily dataframe with columns open/high/low/close/quote_volume
    Returns two DataFrames (long_clusters, short_clusters) with columns
    [price_low, price_high, weight] representing estimated liquidation
    pressure at that price zone.
    """
    entries = df["close"].values
    weights = df["quote_volume"].values
    weights = weights / (weights.sum() + 1e-9)

    long_liqs, long_w = [], []
    short_liqs, short_w = [], []

    for entry, w in zip(entries, weights):
        for lev in LEVERAGE_TIERS:
            # weight decays for very high leverage tiers (fewer traders use 100x+)
            lev_weight = w * (1.0 / np.sqrt(lev))
            long_liqs.append(_liq_price_long(entry, lev))
            long_w.append(lev_weight)
            short_liqs.append(_liq_price_short(entry, lev))
            short_w.append(lev_weight)

    price_min = df["low"].min() * 0.5
    price_max = df["high"].max() * 1.5
    bin_edges = np.linspace(price_min, price_max, bins + 1)

    long_hist, _ = np.histogram(long_liqs, bins=bin_edges, weights=long_w)
    short_hist, _ = np.histogram(short_liqs, bins=bin_edges, weights=short_w)

    long_df = pd.DataFrame({
        "price_low": bin_edges[:-1], "price_high": bin_edges[1:], "weight": long_hist
    })
    short_df = pd.DataFrame({
        "price_low": bin_edges[:-1], "price_high": bin_edges[1:], "weight": short_hist
    })
    return long_df, short_df


def squeeze_exhaustion_score(short_clusters: pd.DataFrame, current_price: float,
                              upper_band_pct: float = 0.08):
    """
    Measures how much of the historical SHORT-liquidation weight sits
    ABOVE current price (still ahead of us) vs how much has already been
    "consumed" by price moving up through it — i.e. the setup you
    described: most short-liq clusters cleared, only a thin one left
    just above current price.

    Returns dict with:
      consumed_pct        -> % of total short-liq weight already below price
      remaining_near_pct   -> % of weight remaining within upper_band_pct above price
      remaining_total_pct  -> % of weight remaining anywhere above price
      score                -> 0-100 composite (higher = more exhausted squeeze)
    """
    total = short_clusters["weight"].sum()
    if total <= 0:
        return {"consumed_pct": 0, "remaining_near_pct": 0, "remaining_total_pct": 0, "score": 0}

    below_mask = short_clusters["price_high"] <= current_price
    consumed = short_clusters.loc[below_mask, "weight"].sum()

    upper_bound = current_price * (1 + upper_band_pct)
    near_mask = (short_clusters["price_low"] > current_price) & (short_clusters["price_low"] <= upper_bound)
    remaining_near = short_clusters.loc[near_mask, "weight"].sum()

    above_mask = short_clusters["price_low"] > current_price
    remaining_total = short_clusters.loc[above_mask, "weight"].sum()

    consumed_pct = 100 * consumed / total
    remaining_near_pct = 100 * remaining_near / total
    remaining_total_pct = 100 * remaining_total / total

    # High score = most historical short-liq weight already behind us,
    # AND what's left ahead is small (not a big wall further up).
    score = consumed_pct * 0.7 + max(0, (15 - remaining_total_pct)) * 2
    score = float(np.clip(score, 0, 100))

    return {
        "consumed_pct": round(consumed_pct, 1),
        "remaining_near_pct": round(remaining_near_pct, 1),
        "remaining_total_pct": round(remaining_total_pct, 1),
        "score": round(score, 1),
    }
