"""
Multi-exchange USDT-M perpetual futures public data wrapper.

Some cloud hosts (Streamlit Community Cloud runs on US-based AWS
servers) get blocked by Binance's futures API for legal/regional
reasons, even though the endpoints are "public". To keep the app
working regardless of where it's hosted, this module tries Binance
first and automatically falls back to Bybit, then OKX, if a source
fails. Whichever source responds first becomes the "active source"
for the rest of the session.

No API key is required for any of these — all are public market-data
endpoints.
"""
import requests
import pandas as pd

BINANCE_BASE = "https://fapi.binance.com"
BYBIT_BASE = "https://api.bybit.com"
OKX_BASE = "https://www.okx.com"

BYBIT_INTERVAL_MAP = {"1d": "D", "4h": "240", "1h": "60"}
OKX_INTERVAL_MAP = {"1d": "1D", "4h": "4H", "1h": "1H"}

_ACTIVE_SOURCE = None
_LAST_ERRORS = {}


class DataSourceError(Exception):
    pass


def _get(url, params=None, timeout=10):
    r = requests.get(url, params=params, timeout=timeout)
    if r.status_code >= 400:
        raise DataSourceError(f"HTTP {r.status_code} from {url}: {r.text[:200]}")
    return r.json()


# ---------------------------------------------------------------- Binance --
def _binance_symbols():
    data = _get(f"{BINANCE_BASE}/fapi/v1/exchangeInfo")
    return sorted([
        s["symbol"] for s in data["symbols"]
        if s["quoteAsset"] == "USDT" and s["contractType"] == "PERPETUAL" and s["status"] == "TRADING"
    ])


def _binance_klines(symbol, interval, limit):
    data = _get(f"{BINANCE_BASE}/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(data, columns=cols)
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df[["open_time", "open", "high", "low", "close", "volume", "quote_volume"]]


def _binance_funding(symbol):
    data = _get(f"{BINANCE_BASE}/fapi/v1/premiumIndex", {"symbol": symbol})
    return {"mark_price": float(data["markPrice"]), "last_funding_rate": float(data["lastFundingRate"])}


# ------------------------------------------------------------------ Bybit --
def _bybit_symbols():
    data = _get(f"{BYBIT_BASE}/v5/market/instruments-info", {"category": "linear"})
    items = data["result"]["list"]
    return sorted([i["symbol"] for i in items if i["symbol"].endswith("USDT") and i.get("status") == "Trading"])


def _bybit_klines(symbol, interval, limit):
    biv = BYBIT_INTERVAL_MAP.get(interval, "D")
    data = _get(f"{BYBIT_BASE}/v5/market/kline",
                {"category": "linear", "symbol": symbol, "interval": biv, "limit": min(limit, 1000)})
    rows = list(reversed(data["result"]["list"]))  # bybit returns newest first
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "turnover"])
    for c in ["open", "high", "low", "close", "volume", "turnover"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms")
    df["quote_volume"] = df["turnover"]
    return df[["open_time", "open", "high", "low", "close", "volume", "quote_volume"]]


def _bybit_funding(symbol):
    data = _get(f"{BYBIT_BASE}/v5/market/tickers", {"category": "linear", "symbol": symbol})
    item = data["result"]["list"][0]
    return {"mark_price": float(item["markPrice"]), "last_funding_rate": float(item["fundingRate"])}


# -------------------------------------------------------------------- OKX --
def _okx_symbols():
    data = _get(f"{OKX_BASE}/api/v5/public/instruments", {"instType": "SWAP"})
    items = data["data"]
    return sorted([i["instId"] for i in items if i["instId"].endswith("-USDT-SWAP") and i.get("state") == "live"])


def _okx_klines(symbol, interval, limit):
    bar = OKX_INTERVAL_MAP.get(interval, "1D")
    data = _get(f"{OKX_BASE}/api/v5/market/candles", {"instId": symbol, "bar": bar, "limit": min(limit, 300)})
    rows = list(reversed(data["data"]))  # okx returns newest first
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close",
                                      "volume", "volCcy", "volCcyQuote", "confirm"])
    for c in ["open", "high", "low", "close", "volume", "volCcyQuote"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms")
    df["quote_volume"] = df["volCcyQuote"]
    return df[["open_time", "open", "high", "low", "close", "volume", "quote_volume"]]


def _okx_funding(symbol):
    fr = _get(f"{OKX_BASE}/api/v5/public/funding-rate", {"instId": symbol})
    tick = _get(f"{OKX_BASE}/api/v5/market/ticker", {"instId": symbol})
    return {
        "mark_price": float(tick["data"][0]["last"]),
        "last_funding_rate": float(fr["data"][0]["fundingRate"]),
    }


_SYMBOL_FETCHERS = {"binance": _binance_symbols, "bybit": _bybit_symbols, "okx": _okx_symbols}
_KLINE_FETCHERS = {"binance": _binance_klines, "bybit": _bybit_klines, "okx": _okx_klines}
_FUNDING_FETCHERS = {"binance": _binance_funding, "bybit": _bybit_funding, "okx": _okx_funding}


def _detect_source(force=False):
    global _ACTIVE_SOURCE
    if _ACTIVE_SOURCE and not force:
        return _ACTIVE_SOURCE

    _LAST_ERRORS.clear()
    for name in ("binance", "bybit", "okx"):
        try:
            _SYMBOL_FETCHERS[name]()
            _ACTIVE_SOURCE = name
            return name
        except Exception as e:
            _LAST_ERRORS[name] = f"{type(e).__name__}: {e}"
            continue

    detail = "\n".join(f"- {k}: {v}" for k, v in _LAST_ERRORS.items())
    raise DataSourceError(
        "Hiçbir borsa API'sine erişilemedi (barındırma sunucusu engellenmiş olabilir).\n"
        f"Denenen kaynaklar ve hatalar:\n{detail}"
    )


def get_active_source():
    """Which exchange is currently being used ('binance' / 'bybit' / 'okx')."""
    return _detect_source()


def get_usdt_perpetual_symbols():
    source = _detect_source()
    return _SYMBOL_FETCHERS[source]()


def get_klines(symbol, interval="1d", limit=500):
    source = _detect_source()
    return _KLINE_FETCHERS[source](symbol, interval, limit)


def get_mark_price_and_funding(symbol):
    source = _detect_source()
    return _FUNDING_FETCHERS[source](symbol)


def get_funding_rate_history(symbol, limit=90):
    """Only implemented for Binance; returns empty DataFrame on other sources."""
    if _detect_source() != "binance":
        return pd.DataFrame()
    data = _get(f"{BINANCE_BASE}/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit})
    df = pd.DataFrame(data)
    if df.empty:
        return df
    df["fundingRate"] = df["fundingRate"].astype(float)
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms")
    return df


def get_open_interest_hist(symbol, period="1d", limit=30):
    """Only implemented for Binance (~30 days retained); empty DataFrame otherwise."""
    if _detect_source() != "binance":
        return pd.DataFrame()
    try:
        data = _get(f"{BINANCE_BASE}/futures/data/openInterestHist",
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
