"""
Binance USDT-M Futures public API wrapper.
No API key required — uses public market data endpoints only.
"""
import requests
import pandas as pd

BASE_URL = "https://fapi.binance.com"


def _get(path, params=None, timeout=10):
    r = requests.get(f"{BASE_URL}{path}", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_usdt_perpetual_symbols():
    """Return list of active USDT-margined perpetual futures symbols."""
    data = _get("/fapi/v1/exchangeInfo")
    symbols = [
        s["symbol"] for s in data["symbols"]
        if s["quoteAsset"] == "USDT"
        and s["contractType"] == "PERPETUAL"
        and s["status"] == "TRADING"
    ]
    return sorted(symbols)


def get_klines(symbol, interval="1d", limit=1000):
    """Historical OHLCV. Binance futures klines max limit = 1500."""
    data = _get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(data, columns=cols)
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    return df


def get_mark_price_and_funding(symbol):
    """Current mark price + current funding rate."""
    data = _get("/fapi/v1/premiumIndex", {"symbol": symbol})
    return {
        "mark_price": float(data["markPrice"]),
        "last_funding_rate": float(data["lastFundingRate"]),
        "next_funding_time": data["nextFundingTime"],
    }


def get_funding_rate_history(symbol, limit=90):
    data = _get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit})
    df = pd.DataFrame(data)
    if df.empty:
        return df
    df["fundingRate"] = df["fundingRate"].astype(float)
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms")
    return df


def get_open_interest_hist(symbol, period="1d", limit=30):
    """Binance only retains ~30 days of history on this endpoint."""
    try:
        data = _get("/futures/data/openInterestHist",
                     {"symbol": symbol, "period": period, "limit": limit})
        df = pd.DataFrame(data)
        if df.empty:
            return df
        df["sumOpenInterest"] = df["sumOpenInterest"].astype(float)
        df["sumOpenInterestValue"] = df["sumOpenInterestValue"].astype(float)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except Exception:
        return pd.DataFrame()


def get_24h_ticker(symbol):
    data = _get("/fapi/v1/ticker/24hr", {"symbol": symbol})
    return {
        "price_change_percent": float(data["priceChangePercent"]),
        "last_price": float(data["lastPrice"]),
        "quote_volume": float(data["quoteVolume"]),
    }
