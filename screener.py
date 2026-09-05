"""
Runs the full scan across a volume-ranked universe of coins, pulling each
coin's data from up to 5 exchanges (Binance, Bybit, OKX, Bitget, Gate.io)
and aggregating the liquidation cluster estimate across whichever of
those actually have that coin listed.
"""
import time
import pandas as pd

import data_fetcher as api
import liquidation_model as liq

MODULE_VERSION = "screener-v10-selectable-exchanges"


def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def analyze_symbol_multi(base_symbol, kline_limit=500, cluster_window=90, min_sources=1, exchanges=None):
    exchanges = exchanges or api.EXCHANGES
    dfs = {}
    for ex in exchanges:
        df = api.get_klines_from(ex, base_symbol, interval="1d", limit=kline_limit)
        if df is not None and len(df) >= 30:
            dfs[ex] = df
        time.sleep(0.08)

    if len(dfs) < min_sources:
        return None

    # Exchange with the longest history becomes "primary" for price/RSI/chart display
    primary_ex = max(dfs, key=lambda e: len(dfs[e]))
    primary_df = dfs[primary_ex]
    current_price = primary_df["close"].iloc[-1]

    cluster_dfs = [d.tail(min(cluster_window, len(d))) for d in dfs.values()]
    long_clusters, short_clusters = liq.estimate_liquidation_clusters_multi(cluster_dfs)
    exhaustion = liq.squeeze_exhaustion_score(short_clusters, current_price)

    funding_rate = None
    fr = api.get_funding_from(primary_ex, base_symbol)
    if fr:
        funding_rate = fr["last_funding_rate"]

    rsi = compute_rsi(primary_df["close"]).iloc[-1]
    lookback = min(90, len(primary_df))
    low_90 = primary_df["close"].tail(lookback).min()
    pct_from_low = 100 * (current_price - low_90) / low_90

    return {
        "symbol": base_symbol,
        "price": current_price,
        "kaynaklar": ", ".join(sorted(dfs.keys())),
        "kaynak_sayisi": len(dfs),
        "rsi_14d": round(rsi, 1) if pd.notna(rsi) else None,
        "funding_rate_pct": round(funding_rate * 100, 4) if funding_rate is not None else None,
        "pct_from_90d_low": round(pct_from_low, 1),
        "short_liq_consumed_pct": exhaustion["consumed_pct"],
        "short_liq_remaining_near_pct": exhaustion["remaining_near_pct"],
        "short_liq_remaining_total_pct": exhaustion["remaining_total_pct"],
        "exhaustion_score": exhaustion["score"],
        "long_clusters": long_clusters,
        "short_clusters": short_clusters,
        "ohlcv": primary_df,
    }


def run_scan_multi(base_symbols, kline_limit=500, cluster_window=90, min_sources=1,
                    batch_size=50, batch_pause=3.0, exchanges=None, progress_callback=None):
    """
    Scans in batches (like Coinglass's paginated coin lists) with a short
    pause between batches — this spreads out the request load over time,
    which meaningfully reduces the odds of hitting an exchange's shared-IP
    rate-limit ban when scanning large coin counts.
    """
    exchanges = exchanges or api.EXCHANGES
    api.clear_symbol_cache()  # refresh each scan in case listings changed
    results = []
    total = len(base_symbols)
    batches = [base_symbols[i:i + batch_size] for i in range(0, total, batch_size)]

    done = 0
    for batch_idx, batch in enumerate(batches, start=1):
        for sym in batch:
            try:
                r = analyze_symbol_multi(sym, kline_limit=kline_limit, cluster_window=cluster_window,
                                          min_sources=min_sources, exchanges=exchanges)
                if r:
                    results.append(r)
            except Exception:
                pass
            done += 1
            if progress_callback:
                progress_callback(done, total, sym, batch_idx, len(batches))

        if batch_idx < len(batches):
            time.sleep(batch_pause)

    return results
