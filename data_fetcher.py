"""
5-exchange USDT-M perpetual futures public data wrapper.

Exchanges: Binance, Bybit, OKX, Bitget, Gate.io — all public market-data
endpoints, no API key needed. Used to approximate a Coinglass-style
multi-exchange liquidation cluster map by pulling the same coin's data
from as many of these 5 as are reachable/listed, and letting the caller
(liquidation_model.estimate_liquidation_clusters_multi) sum them.

NOTE: Bitget and Gate.io wrappers were written from memory of their public
API shapes and could NOT be live-tested in this environment (no outbound
network access here). If they error out, use the diagnostic
`debug_fetch_klines()` function from the app's test panel to see the raw
error and we can patch field names quickly.
"""
import requests
import pandas as pd
import re

BINANCE_BASE = "https://fapi.binance.com"
BYBIT_BASE = "https://api.bybit.com"
OKX_BASE = "https://www.okx.com"
BITGET_BASE = "https://api.bitget.com"
GATEIO_BASE = "https://api.gateio.ws"

EXCHANGES = ["binance", "bybit", "okx", "bitget", "gateio"]
MODULE_VERSION = "data_fetcher-v8-symbol-prefix-resolution"

BYBIT_INTERVAL_MAP = {"1d": "D", "4h": "240", "1h": "60"}
OKX_INTERVAL_MAP = {"1d": "1D", "4h": "4H", "1h": "1H"}
BITGET_INTERVAL_MAP = {"1d": "1D", "4h": "4H", "1h": "1H"}
GATEIO_INTERVAL_MAP = {"1d": "1d", "4h": "4h", "1h": "1h"}

_LAST_ERRORS = {}
_SYMBOL_CACHE = {}  # exchange -> set of that exchange's raw perpetual symbol strings


class DataSourceError(Exception):
    pass


def _get(url, params=None, timeout=10):
    r = requests.get(url, params=params, timeout=timeout)
    if r.status_code >= 400:
        raise DataSourceError(f"HTTP {r.status_code} from {url}: {r.text[:200]}")
    return r.json()


def _format_symbol(exchange, base):
    """Builds the raw exchange symbol string for a given base ticker,
    WITHOUT trying to guess prefixes — see resolve_symbol() for that."""
    if exchange in ("binance", "bybit", "bitget"):
        return f"{base}USDT"
    if exchange == "okx":
        return f"{base}-USDT-SWAP"
    if exchange == "gateio":
        return f"{base}_USDT"
    raise ValueError(f"Unknown exchange: {exchange}")


def _get_all_symbols(exchange):
    """Full raw perpetual symbol list for one exchange, cached per scan.
    Needed because low-price coins get different '1000x' style prefixes
    on different exchanges (e.g. Binance's 1000PEPEUSDT vs OKX's
    PEPE-USDT-SWAP) — we can't just guess the format blindly."""
    if exchange in _SYMBOL_CACHE:
        return _SYMBOL_CACHE[exchange]

    syms = set()
    try:
        if exchange == "binance":
            data = _get(f"{BINANCE_BASE}/fapi/v1/exchangeInfo")
            syms = {s["symbol"] for s in data["symbols"]
                    if s["quoteAsset"] == "USDT" and s["contractType"] == "PERPETUAL" and s["status"] == "TRADING"}
        elif exchange == "bybit":
            data = _get(f"{BYBIT_BASE}/v5/market/instruments-info", {"category": "linear"})["result"]["list"]
            syms = {i["symbol"] for i in data if i["symbol"].endswith("USDT") and i.get("status") == "Trading"}
        elif exchange == "okx":
            data = _get(f"{OKX_BASE}/api/v5/public/instruments", {"instType": "SWAP"})["data"]
            syms = {i["instId"] for i in data if i["instId"].endswith("-USDT-SWAP") and i.get("state") == "live"}
        elif exchange == "bitget":
            data = _get(f"{BITGET_BASE}/api/v2/mix/market/contracts", {"productType": "usdt-futures"})["data"]
            syms = {i["symbol"] for i in data if i.get("symbol", "").endswith("USDT")}
        elif exchange == "gateio":
            data = _get(f"{GATEIO_BASE}/api/v4/futures/usdt/contracts")
            syms = {i["name"] for i in data if i.get("name", "").endswith("_USDT")}
    except Exception:
        syms = set()

    _SYMBOL_CACHE[exchange] = syms
    return syms


def clear_symbol_cache():
    _SYMBOL_CACHE.clear()


_PREFIX_VARIANTS = ["", "1000", "10000", "100000", "1000000", "100"]
_PREFIX_RE = re.compile(r"^(10{2,6})([A-Z].*)$")  # matches 100/1000/10000/... + letters


def resolve_symbol(exchange, base):
    """Finds the ACTUAL symbol string this exchange uses for a coin,
    trying common '1000x' style prefix variants against that exchange's
    real symbol list. Returns None if not listed there at all."""
    base = base.upper()
    m = _PREFIX_RE.match(base)
    core = m.group(2) if m else base

    all_syms = _get_all_symbols(exchange)
    if not all_syms:
        # symbol list unavailable (e.g. exchange down) — fall back to a
        # blind guess so we at least try the plain, unprefixed form
        return _format_symbol(exchange, core)

    for prefix in _PREFIX_VARIANTS:
        candidate = _format_symbol(exchange, f"{prefix}{core}")
        if candidate in all_syms:
            return candidate
    return None


def symbol_for(exchange, base):
    """Kept for compatibility — blind format without prefix resolution."""
    return _format_symbol(exchange, base)


# OKX also lists tokenized US-stock perpetuals under the same naming —
# excluded so the screener stays crypto-only.
_STOCK_TICKER_DENYLIST = {
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "TSLA", "META", "NVDA", "NFLX",
    "AVGO", "COST", "ADBE", "INTC", "AMD", "CSCO", "PEP", "TXN", "QCOM",
    "CMCSA", "HON", "INTU", "AMAT", "BKNG", "ISRG", "VRTX", "GILD", "ADP",
    "MDLZ", "REGN", "LRCX", "PANW", "SNPS", "CDNS", "ORLY", "MU", "KLAC",
    "MAR", "CTAS", "PYPL", "MRVL", "ABNB", "WDAY", "CRWD", "FTNT", "DXCM",
    "PCAR", "ODFL", "ROST", "KDP", "EXC", "XEL", "CSGP", "IDXX", "BIIB",
    "ILMN", "MNST", "DLTR", "WBA", "LULU", "EA", "VRSK", "ANSS", "CTSH",
    "FAST", "PAYX", "CPRT", "VRSN", "SIRI", "MTCH", "ENPH", "ALGN", "MELI",
    "JPM", "V", "MA", "WMT", "KO", "MCD", "NKE", "HD", "UNH", "JNJ", "PG",
    "XOM", "CVX", "BAC", "WFC", "GS", "MS", "C", "T", "VZ", "PFE", "ABBV",
    "TMO", "IBM", "UPS", "LMT", "RTX", "SPGI", "BLK", "SCHW", "AXP", "GM",
    "F", "UBER", "LYFT", "SNAP", "PINS", "SQ", "SHOP", "ROKU", "ZM",
    "DOCU", "SNOW", "PLTR", "COIN", "RIVN", "LCID", "NIO", "BABA", "DIS",
    "BA", "GE", "MMM", "CAT", "ORCL", "CRM", "BE",
}


# ---------------------------------------------------------------- Binance --
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


def _binance_top_symbols(n):
    data = _get(f"{BINANCE_BASE}/fapi/v1/ticker/24hr")
    data = [d for d in data if d["symbol"].endswith("USDT")]
    data.sort(key=lambda d: float(d.get("quoteVolume", 0) or 0), reverse=True)
    return [d["symbol"][:-4] for d in data[: n * 2]]


# ------------------------------------------------------------------ Bybit --
def _bybit_klines(symbol, interval, limit):
    biv = BYBIT_INTERVAL_MAP.get(interval, "D")
    data = _get(f"{BYBIT_BASE}/v5/market/kline",
                {"category": "linear", "symbol": symbol, "interval": biv, "limit": min(limit, 1000)})
    rows = list(reversed(data["result"]["list"]))
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


def _bybit_top_symbols(n):
    data = _get(f"{BYBIT_BASE}/v5/market/tickers", {"category": "linear"})["result"]["list"]
    data = [d for d in data if d["symbol"].endswith("USDT")]
    data.sort(key=lambda d: float(d.get("turnover24h", 0) or 0), reverse=True)
    return [d["symbol"][:-4] for d in data[: n * 2]]


# -------------------------------------------------------------------- OKX --
def _okx_klines(symbol, interval, limit):
    bar = OKX_INTERVAL_MAP.get(interval, "1D")
    data = _get(f"{OKX_BASE}/api/v5/market/candles", {"instId": symbol, "bar": bar, "limit": min(limit, 300)})
    rows = list(reversed(data["data"]))
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
    return {"mark_price": float(tick["data"][0]["last"]), "last_funding_rate": float(fr["data"][0]["fundingRate"])}


def _okx_top_symbols(n):
    data = _get(f"{OKX_BASE}/api/v5/market/tickers", {"instType": "SWAP"})["data"]
    data = [d for d in data if d["instId"].endswith("-USDT-SWAP")]
    data.sort(key=lambda d: float(d.get("volCcy24h", 0) or 0), reverse=True)
    bases = [d["instId"].split("-")[0] for d in data]
    bases = [b for b in bases if b not in _STOCK_TICKER_DENYLIST]
    return bases[: n * 2]


# ------------------------------------------------------------------ Bitget --
def _bitget_klines(symbol, interval, limit):
    gran = BITGET_INTERVAL_MAP.get(interval, "1D")
    data = _get(f"{BITGET_BASE}/api/v2/mix/market/candles",
                {"symbol": symbol, "productType": "usdt-futures", "granularity": gran, "limit": min(limit, 1000)})
    rows = data.get("data", [])
    rows = sorted(rows, key=lambda r: int(r[0]))
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "base_vol", "quote_volume"])
    for c in ["open", "high", "low", "close", "base_vol", "quote_volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms")
    df["volume"] = df["base_vol"]
    return df[["open_time", "open", "high", "low", "close", "volume", "quote_volume"]]


def _bitget_funding(symbol):
    data = _get(f"{BITGET_BASE}/api/v2/mix/market/ticker", {"symbol": symbol, "productType": "usdt-futures"})
    raw = data.get("data")
    item = raw[0] if isinstance(raw, list) else (raw or {})
    return {
        "mark_price": float(item.get("markPrice", item.get("lastPr", 0)) or 0),
        "last_funding_rate": float(item.get("fundingRate", 0) or 0),
    }


def _bitget_top_symbols(n):
    data = _get(f"{BITGET_BASE}/api/v2/mix/market/tickers", {"productType": "usdt-futures"})["data"]
    data = [d for d in data if d.get("symbol", "").endswith("USDT")]
    data.sort(key=lambda d: float(d.get("usdtVolume", d.get("quoteVolume", 0)) or 0), reverse=True)
    return [d["symbol"][:-4] for d in data[: n * 2]]


# ----------------------------------------------------------------- Gate.io --
def _gateio_klines(symbol, interval, limit):
    iv = GATEIO_INTERVAL_MAP.get(interval, "1d")
    data = _get(f"{GATEIO_BASE}/api/v4/futures/usdt/candlesticks",
                {"contract": symbol, "interval": iv, "limit": min(limit, 2000)})
    rows = sorted(data, key=lambda r: r["t"])
    df = pd.DataFrame(rows)
    df = df.rename(columns={"t": "open_time", "o": "open", "h": "high", "l": "low", "c": "close",
                             "v": "volume", "sum": "quote_volume"})
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="s")
    return df[["open_time", "open", "high", "low", "close", "volume", "quote_volume"]]


def _gateio_funding(symbol):
    data = _get(f"{GATEIO_BASE}/api/v4/futures/usdt/tickers", {"contract": symbol})
    item = data[0] if isinstance(data, list) and data else {}
    return {
        "mark_price": float(item.get("mark_price", item.get("last", 0)) or 0),
        "last_funding_rate": float(item.get("funding_rate", 0) or 0),
    }


def _gateio_top_symbols(n):
    data = _get(f"{GATEIO_BASE}/api/v4/futures/usdt/tickers")
    data = [d for d in data if d.get("contract", "").endswith("_USDT")]
    data.sort(key=lambda d: float(d.get("volume_24h_quote", d.get("volume_24h", 0)) or 0), reverse=True)
    return [d["contract"][:-5] for d in data[: n * 2]]


_KLINE_FETCHERS = {
    "binance": _binance_klines, "bybit": _bybit_klines, "okx": _okx_klines,
    "bitget": _bitget_klines, "gateio": _gateio_klines,
}
_FUNDING_FETCHERS = {
    "binance": _binance_funding, "bybit": _bybit_funding, "okx": _okx_funding,
    "bitget": _bitget_funding, "gateio": _gateio_funding,
}
_TOP_SYMBOL_FETCHERS = {
    "binance": _binance_top_symbols, "bybit": _bybit_top_symbols, "okx": _okx_top_symbols,
    "bitget": _bitget_top_symbols, "gateio": _gateio_top_symbols,
}


def get_klines_from(exchange, base_symbol, interval="1d", limit=500):
    """Returns None (does not raise) if this exchange doesn't list the
    symbol or the request fails — callers treat a missing exchange as
    'not available there' and just use whichever DO respond."""
    try:
        sym = resolve_symbol(exchange, base_symbol)
        if sym is None:
            return None
        return _KLINE_FETCHERS[exchange](sym, interval, limit)
    except Exception:
        return None


def get_funding_from(exchange, base_symbol):
    try:
        sym = resolve_symbol(exchange, base_symbol)
        if sym is None:
            return None
        return _FUNDING_FETCHERS[exchange](sym)
    except Exception:
        return None


def debug_fetch_klines(exchange, base_symbol, limit=10):
    """Diagnostic helper — unlike get_klines_from, this RAISES so you can
    see the exact error when testing a single exchange/symbol combo."""
    sym = resolve_symbol(exchange, base_symbol)
    if sym is None:
        raise DataSourceError(
            f"'{base_symbol}' bu borsanın sembol listesinde bulunamadı "
            f"(denenen önekler: {_PREFIX_VARIANTS})"
        )
    return _KLINE_FETCHERS[exchange](sym, "1d", limit)


def get_top_symbols_by_volume(n=30, exchanges=None):
    """
    Builds a volume-ranked universe of base-asset symbols (e.g. 'BTC',
    'ETH', ...) using whichever exchange responds first. This list is
    just used to decide WHICH coins to scan — the actual per-coin
    analysis still queries all 5 exchanges for each one.
    """
    exchanges = exchanges or EXCHANGES
    _LAST_ERRORS.clear()
    for ex in exchanges:
        try:
            bases = _TOP_SYMBOL_FETCHERS[ex](n)
            seen, out = set(), []
            for b in bases:
                if b not in seen:
                    seen.add(b)
                    out.append(b)
                if len(out) >= n:
                    break
            return out, ex
        except Exception as e:
            _LAST_ERRORS[ex] = f"{type(e).__name__}: {e}"
            continue

    detail = "\n".join(f"- {k}: {v}" for k, v in _LAST_ERRORS.items())
    raise DataSourceError(
        "Hiçbir borsa API'sinden coin listesi alınamadı.\n"
        f"Denenen kaynaklar ve hatalar:\n{detail}"
    )


def get_last_errors():
    return dict(_LAST_ERRORS)
