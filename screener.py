"""
Runs the full scan across USDT-M perpetual symbols and ranks them
by "short-squeeze exhaustion" score.
"""
import time
import pandas as pd

import data_fetcher as api
import liquidation_model as liq


def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def analyze_symbol(symbol, kline_limit=500):
    df = api.get_klines(symbol, interval="1d", limit=kline_limit)
    if df.empty or len(df) < 30:
        return None

    current_price = df["close"].iloc[-1]

    long_clusters, short_clusters = liq.estimate_liquidation_clusters(df)
    exhaustion = liq.squeeze_exhaustion_score(short_clusters, current_price)

    try:
        funding = api.get_mark_price_and_funding(symbol)
        funding_rate = funding["last_funding_rate"]
    except Exception:
        funding_rate = None

    rsi = compute_rsi(df["close"]).iloc[-1]

    lookback = min(90, len(df))
    low_90 = df["close"].tail(lookback).min()
    pct_from_low = 100 * (current_price - low_90) / low_90

    return {
        "symbol": symbol,
        "price": current_price,
        "rsi_14d": round(rsi, 1) if pd.notna(rsi) else None,
        "funding_rate_pct": round(funding_rate * 100, 4) if funding_rate is not None else None,
        "pct_from_90d_low": round(pct_from_low, 1),
        "short_liq_consumed_pct": exhaustion["consumed_pct"],
        "short_liq_remaining_near_pct": exhaustion["remaining_near_pct"],
        "short_liq_remaining_total_pct": exhaustion["remaining_total_pct"],
        "exhaustion_score": exhaustion["score"],
        "long_clusters": long_clusters,
        "short_clusters": short_clusters,
        "ohlcv": df,
    }


def run_scan(symbols, kline_limit=500, progress_callback=None):
    results = []
    for i, sym in enumerate(symbols):
        try:
            r = analyze_symbol(sym, kline_limit=kline_limit)
            if r:
                results.append(r)
        except Exception:
            pass
        if progress_callback:
            progress_callback(i + 1, len(symbols), sym)
        time.sleep(0.05)  # stay comfortably under Binance rate limits
    return results
