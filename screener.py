"""Multi-exchange scanner for statistical liquidation-cluster setups."""
import logging
import time
import pandas as pd

import data_fetcher as api
import liquidation_model as liq

MODULE_VERSION = "screener-v10-robust-multi-exchange"
logger = logging.getLogger(__name__)


def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100).clip(0, 100)


def _weighted_current_price(dfs):
    """Volume-weighted current price across exchanges, with equalized caps."""
    rows = []
    for ex, df in dfs.items():
        if df is None or df.empty:
            continue
        price = float(df["close"].iloc[-1])
        vol = float(df["quote_volume"].tail(30).sum())
        if price > 0:
            rows.append((ex, price, max(vol, 1.0)))
    if not rows:
        raise ValueError("No valid current prices")
    # Use square-root volume weighting: robust against one venue dominating.
    weights = pd.Series([x[2] for x in rows], dtype=float).pow(0.5)
    weights = weights / weights.sum()
    return float(sum(w * price for w, (_, price, _) in zip(weights, rows)))


def analyze_symbol_multi(base_symbol, kline_limit=500, cluster_window=90, min_sources=1):
    dfs = {}
    errors = {}
    for ex in api.EXCHANGES:
        try:
            df = api.get_klines_from(ex, base_symbol, interval="1d", limit=kline_limit)
            if df is not None and len(df) >= 30:
                dfs[ex] = df
            else:
                errors[ex] = "yetersiz veri / sembol yok"
        except Exception as exc:
            errors[ex] = f"{type(exc).__name__}: {exc}"
        time.sleep(0.05)

    if len(dfs) < min_sources:
        return None

    current_price = _weighted_current_price(dfs)
    cluster_dfs = [d.tail(min(cluster_window, len(d))) for d in dfs.values()]
    long_clusters, short_clusters = liq.estimate_liquidation_clusters_multi(cluster_dfs)
    exhaustion = liq.squeeze_exhaustion_score(short_clusters, current_price)

    # Funding from every reachable exchange; display a volume-neutral median.
    funding_values = []
    funding_by_exchange = {}
    for ex in dfs:
        fr = api.get_funding_from(ex, base_symbol)
        if fr and fr.get("last_funding_rate") is not None:
            value = float(fr["last_funding_rate"])
            funding_values.append(value)
            funding_by_exchange[ex] = value
    funding_rate = float(pd.Series(funding_values).median()) if funding_values else None

    primary_ex = max(dfs, key=lambda e: len(dfs[e]))
    primary_df = dfs[primary_ex]
    rsi = compute_rsi(primary_df["close"]).iloc[-1]
    lookback = min(90, len(primary_df))
    low_90 = float(primary_df["close"].tail(lookback).min())
    pct_from_low = 100 * (current_price - low_90) / low_90 if low_90 > 0 else 0

    # A compact quality score: more independent sources and fresher data are better.
    source_quality = round(100 * len(dfs) / len(api.EXCHANGES), 1)
    newest_age_days = max(
        (pd.Timestamp.utcnow().tz_localize(None) - pd.to_datetime(d["open_time"].iloc[-1])).total_seconds() / 86400
        for d in dfs.values()
    )

    return {
        "symbol": base_symbol,
        "price": round(current_price, 8),
        "kaynaklar": ", ".join(sorted(dfs.keys())),
        "kaynak_sayisi": len(dfs),
        "kaynak_kalitesi_pct": source_quality,
        "veri_yasi_saat": round(newest_age_days * 24, 1),
        "rsi_14d": round(float(rsi), 1) if pd.notna(rsi) else None,
        "funding_rate_pct": round(funding_rate * 100, 4) if funding_rate is not None else None,
        "funding_borsalar": ", ".join(sorted(funding_by_exchange)),
        "pct_from_90d_low": round(pct_from_low, 1),
        "short_liq_consumed_pct": exhaustion["consumed_pct"],
        "short_liq_remaining_near_pct": exhaustion["remaining_near_pct"],
        "short_liq_remaining_total_pct": exhaustion["remaining_total_pct"],
        "nearest_short_cluster_price": exhaustion["nearest_cluster_price"],
        "nearest_short_cluster_pct": exhaustion["nearest_cluster_pct"],
        "largest_remaining_short_pct": exhaustion["largest_remaining_pct"],
        "exhaustion_score": exhaustion["score"],
        "long_clusters": long_clusters,
        "short_clusters": short_clusters,
        "ohlcv": primary_df,
        "exchange_errors": errors,
    }


def run_scan_multi(base_symbols, kline_limit=500, cluster_window=90, min_sources=1, progress_callback=None):
    api.clear_symbol_cache()
    results = []
    failures = []
    total = len(base_symbols)
    for i, sym in enumerate(base_symbols):
        try:
            r = analyze_symbol_multi(sym, kline_limit=kline_limit, cluster_window=cluster_window,
                                     min_sources=min_sources)
            if r:
                results.append(r)
            else:
                failures.append({"symbol": sym, "error": "minimum source requirement not met"})
        except Exception as exc:
            logger.exception("Scan failed for %s", sym)
            failures.append({"symbol": sym, "error": f"{type(exc).__name__}: {exc}"})
        if progress_callback:
            progress_callback(i + 1, total, sym)
    return results
