"""Statistical liquidation-cluster estimator.

This module deliberately does NOT claim to reproduce Coinglass. It estimates
where leveraged positions could be liquidated from OHLCV/quote-volume data.
The V10 model improves the old version by:
- using recency decay so old volume has less influence;
- weighting exchanges by their relative quote volume instead of giving every
  exchange identical voting power;
- using robust price-range bounds around the observed market;
- exposing the nearest/strongest remaining short cluster for the screener.
"""
import numpy as np
import pandas as pd

LEVERAGE_TIERS = [5, 10, 20, 25, 50, 75, 100, 125]
MAINTENANCE_MARGIN = 0.005
RECENCY_HALF_LIFE_DAYS = 45


def _liq_price_long(entry, leverage):
    return entry * (1.0 - 1.0 / leverage + MAINTENANCE_MARGIN)


def _liq_price_short(entry, leverage):
    return entry * (1.0 + 1.0 / leverage - MAINTENANCE_MARGIN)


def _prepare_weights(df):
    """Return normalized volume weights with a conservative recency bias."""
    x = df.copy()
    required = {"open_time", "close", "quote_volume"}
    if not required.issubset(x.columns):
        raise ValueError(f"Missing columns: {required - set(x.columns)}")
    x = x.dropna(subset=["open_time", "close", "quote_volume"]).copy()
    x = x[(x["close"] > 0) & (x["quote_volume"] >= 0)]
    if x.empty:
        return x, np.array([], dtype=float)

    t = pd.to_datetime(x["open_time"])
    age_days = (t.max() - t).dt.total_seconds().clip(lower=0) / 86400.0
    recency = np.power(0.5, age_days / RECENCY_HALF_LIFE_DAYS)
    raw = np.asarray(x["quote_volume"], dtype=float) * np.asarray(recency, dtype=float)
    raw = np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0)
    if raw.sum() <= 0:
        raw = np.ones(len(x), dtype=float)
    return x, raw / raw.sum()


def _weighted_liq_points(df):
    """Build long/short liquidation candidates and statistical weights."""
    x, weights = _prepare_weights(df)
    if x.empty:
        return [], [], [], []

    entries = x["close"].to_numpy(dtype=float)
    long_liqs, long_w, short_liqs, short_w = [], [], [], []
    lev_raw = np.asarray([1.0 / np.sqrt(lev) for lev in LEVERAGE_TIERS])
    lev_weights = lev_raw / lev_raw.sum()

    for entry, w in zip(entries, weights):
        for lev, lw in zip(LEVERAGE_TIERS, lev_weights):
            weight = w * lw
            long_liqs.append(_liq_price_long(entry, lev))
            long_w.append(weight)
            short_liqs.append(_liq_price_short(entry, lev))
            short_w.append(weight)
    return long_liqs, long_w, short_liqs, short_w


def _price_edges(dfs, bins):
    lows = [float(df["low"].min()) for df in dfs if not df.empty]
    highs = [float(df["high"].max()) for df in dfs if not df.empty]
    if not lows or not highs:
        raise ValueError("No usable OHLCV data")
    lo, hi = min(lows), max(highs)
    if lo <= 0 or hi <= 0 or hi <= lo:
        raise ValueError("Invalid price range")
    # Keep enough room for liquidation levels without creating a huge empty map.
    return np.linspace(max(lo * 0.55, 1e-12), hi * 1.45, bins + 1)


def estimate_liquidation_clusters(df: pd.DataFrame, bins: int = 60):
    return estimate_liquidation_clusters_multi([df], bins=bins)


def estimate_liquidation_clusters_multi(dfs: list, bins: int = 60):
    """Aggregate several exchanges using relative quote-volume weights."""
    dfs = [df for df in dfs if df is not None and not df.empty]
    if not dfs:
        raise ValueError("estimate_liquidation_clusters_multi needs at least one dataframe")

    edges = _price_edges(dfs, bins)
    long_total = np.zeros(bins, dtype=float)
    short_total = np.zeros(bins, dtype=float)

    source_volumes = []
    prepared = []
    for df in dfs:
        clean, _ = _prepare_weights(df)
        if clean.empty:
            continue
        prepared.append(df)
        source_volumes.append(float(clean["quote_volume"].sum()))

    if not prepared:
        raise ValueError("No exchange has usable volume data")

    # Square-root weighting prevents one exchange from completely dominating,
    # while still giving a high-volume venue more influence than a tiny venue.
    source_w = np.sqrt(np.maximum(source_volumes, 0.0))
    source_w /= source_w.sum() if source_w.sum() > 0 else len(source_w)

    for df, sw in zip(prepared, source_w):
        ll, lw, sl, sws = _weighted_liq_points(df)
        lh, _ = np.histogram(ll, bins=edges, weights=np.asarray(lw) * sw)
        sh, _ = np.histogram(sl, bins=edges, weights=np.asarray(sws) * sw)
        long_total += lh
        short_total += sh

    # Histogram tails can fall outside the synthetic map. Re-normalize so all
    # percentages in the score are percentages of what the map actually shows.
    if long_total.sum() > 0:
        long_total /= long_total.sum()
    if short_total.sum() > 0:
        short_total /= short_total.sum()

    common = {"price_low": edges[:-1], "price_high": edges[1:]}
    long_df = pd.DataFrame({**common, "weight": long_total})
    short_df = pd.DataFrame({**common, "weight": short_total})
    return long_df, short_df


def squeeze_exhaustion_score(short_clusters: pd.DataFrame, current_price: float,
                              upper_band_pct: float = 0.08):
    """Score the user's 'most shorts consumed, final cluster remains' setup."""
    if current_price <= 0 or short_clusters.empty:
        return {"consumed_pct": 0, "remaining_near_pct": 0,
                "remaining_total_pct": 0, "score": 0,
                "nearest_cluster_price": None, "nearest_cluster_pct": None,
                "largest_remaining_pct": 0}

    total = float(short_clusters["weight"].sum())
    if total <= 0:
        return {"consumed_pct": 0, "remaining_near_pct": 0,
                "remaining_total_pct": 0, "score": 0,
                "nearest_cluster_price": None, "nearest_cluster_pct": None,
                "largest_remaining_pct": 0}

    consumed = short_clusters.loc[short_clusters["price_high"] <= current_price, "weight"].sum()
    upper_bound = current_price * (1 + upper_band_pct)
    near_mask = ((short_clusters["price_low"] > current_price) &
                 (short_clusters["price_low"] <= upper_bound))
    remaining_near = short_clusters.loc[near_mask, "weight"].sum()
    above = short_clusters[short_clusters["price_low"] > current_price].copy()
    remaining_total = above["weight"].sum()

    consumed_pct = 100 * consumed / total
    near_pct = 100 * remaining_near / total
    remaining_pct = 100 * remaining_total / total

    nearest_price = None
    nearest_pct = None
    largest_remaining_pct = 0.0
    if not above.empty:
        nearest = above.sort_values("price_low").iloc[0]
        nearest_price = float((nearest["price_low"] + nearest["price_high"]) / 2)
        nearest_pct = 100 * float(nearest_price / current_price - 1)
        largest_remaining_pct = 100 * float(above["weight"].max()) / total

    # High consumption is good for the requested short setup; a very large
    # amount of short-liq still overhead is a penalty. A nearby final cluster
    # is rewarded, but not enough to overpower an unconsumed map.
    score = 0.72 * consumed_pct + 0.18 * max(0.0, 15.0 - remaining_pct)
    if nearest_pct is not None and 0 <= nearest_pct <= upper_band_pct * 100:
        score += min(10.0, largest_remaining_pct * 0.35)
    score = float(np.clip(score, 0, 100))

    return {
        "consumed_pct": round(float(consumed_pct), 1),
        "remaining_near_pct": round(float(near_pct), 1),
        "remaining_total_pct": round(float(remaining_pct), 1),
        "score": round(score, 1),
        "nearest_cluster_price": round(nearest_price, 8) if nearest_price is not None else None,
        "nearest_cluster_pct": round(nearest_pct, 2) if nearest_pct is not None else None,
        "largest_remaining_pct": round(float(largest_remaining_pct), 1),
    }
