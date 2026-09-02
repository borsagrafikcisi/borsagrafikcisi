import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import json
import os
from datetime import datetime
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
from streamlit_autorefresh import st_autorefresh
import warnings
warnings.filterwarnings("ignore")

st.cache_data.clear()

# ==================== AYARLAR & STATE DOSYASI ====================
STATE_FILE = "scan_state.json"

TELEGRAM_TOKEN = "8770184809:AAHskJ8stv-BfC9DVHuKKX-ooekSf5zskV4"
TELEGRAM_CHAT_ID = "-1003546836920"

LRC_LENGTH = 300
MAX_WORKERS = 6
INDEX_SYMBOL = "XU100.IS"

TZ = pytz.timezone("Europe/Istanbul")

TIMEFRAMES_15M = {
    "15m": None, "30m": "30min", "45m": "45min",
    "1h": "1h", "2h": "2h", "3h": "3h", "4h": "4h",
    "5h": "5h", "8h": "8h", "12h": "12h", "13h": "13h"
}

DAILY_BAR_COUNTS = {
    "1d": 1, "2d": 2, "3d": 3, "4d": 4, 
    "5d": 5, "6d": 6, "7d": 7
}

TIMEFRAMES_HIGHER = {
    "1wk": "1W", "1mo": "1ME"
}

BIST_SYMBOLS = [
    "AYEN.IS", "THYAO.IS", "GARAN.IS", "AKBNK.IS", "YKBNK.IS", "ISCTR.IS",
    "EREGL.IS", "KCHOL.IS", "SAHOL.IS", "TUPRS.IS", "SISE.IS", "ASELS.IS",
    "BIMAS.IS", "FROTO.IS", "TOASO.IS", "SASA.IS", "PGSUS.IS", "HEKTS.IS"
]

# ==================== DURUM (STATE) YÖNETİMİ ====================
def get_scan_status() -> bool:
    """Sunucudaki durum dosyasından tarama durumunu okur."""
    if not os.path.exists(STATE_FILE):
        return False
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            return data.get("is_active", False)
    except:
        return False

def set_scan_status(status: bool):
    """Sunucudaki durum dosyasına durumu kaydeder (Sekme kapansa da kaybolmaz)."""
    with open(STATE_FILE, "w") as f:
        json.dump({"is_active": status}, f)

# ==================== SEANS KONTROLÜ ====================
def is_bist_open() -> bool:
    now = datetime.now(TZ)
    if now.weekday() >= 5:
        return False
    start_time = now.replace(hour=10, minute=0, second=0, microsecond=0)
    end_time = now.replace(hour=18, minute=5, second=0, microsecond=0)
    return start_time <= now <= end_time

# ==================== YARDIMCI FONKSİYONLAR ====================
def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
        requests.post(url, json=payload, timeout=10)
    except:
        pass

def calculate_linreg_fast(series: pd.Series, length: int) -> pd.Series:
    if len(series) < length:
        return pd.Series(index=series.index, dtype=float)
    x = np.arange(length, dtype=np.float64)
    x_mean = x.mean()
    x_dev = x - x_mean
    x_var = (x_dev ** 2).sum()
    vals = series.values.astype(np.float64)
    result = np.full(len(vals), np.nan, dtype=np.float64)
    for i in range(length - 1, len(vals)):
        y_window = vals[i - length + 1 : i + 1]
        if np.isnan(y_window).any():
            continue
        y_mean = y_window.mean()
        slope = (x_dev * (y_window - y_mean)).sum() / x_var
        intercept = y_mean - slope * x_mean
        result[i] = slope * (length - 1) + intercept
    return pd.Series(result, index=series.index)

def resample_tradingview_daily_strict(df: pd.DataFrame, days: int) -> pd.DataFrame:
    if days == 1 or len(df) == 0:
        return df
    df_sorted = df.sort_index().copy()
    total_bars = len(df_sorted)
    remainder = total_bars % days
    if remainder != 0:
        df_sorted = df_sorted.iloc[remainder:]
    group_ids = np.arange(len(df_sorted)) // days
    return df_sorted.groupby(group_ids).agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last'})

def detect_lrc_cross_dynamic(close_series: pd.Series, tf_name: str, length: int = 300):
    lookback = 8 if tf_name in ["15m", "30m", "45m"] else (4 if tf_name in ["1h", "2h", "3h", "4h", "5h"] else 2)
    if len(close_series) < length + lookback + 1:
        return None
    lrc_line = calculate_linreg_fast(close_series, length)
    for i in range(1, lookback + 1):
        c_curr, lrc_curr = close_series.iloc[-i], lrc_line.iloc[-i]
        c_prev, lrc_prev = close_series.iloc[-i-1], lrc_line.iloc[-i-1]
        if pd.isna(lrc_curr) or pd.isna(lrc_prev):
            continue
        bar_diff = i - 1
        bar_label = "ANLIK CANLI BAR" if bar_diff == 0 else f"{bar_diff} Bar Önce"
        if c_prev <= lrc_prev and c_curr > lrc_curr:
            return f"YEŞİL 🟢 ({bar_label})"
        if c_prev >= lrc_prev and c_curr < lrc_curr:
            return f"TURUNCU 🟠 ({bar_label})"
    return None

def build_ratio_df(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([stock_df, index_df], axis=1, keys=['stock', 'index'], join='inner').dropna()
    if combined.empty: return pd.DataFrame()
    ratio_df = pd.DataFrame(index=combined.index)
    ratio_df['Close'] = combined['stock']['Close'] / combined['index']['Close']
    ratio_df['Open']  = combined['stock']['Open'] / combined['index']['Open']
    ratio_df['High']  = combined['stock']['High'] / combined['index']['High']
    ratio_df['Low']   = combined['stock']['Low'] / combined['index']['Low']
    return ratio_df

def scan_symbol_ratio(symbol: str, df_index_15m: pd.DataFrame, df_index_1d: pd.DataFrame):
    results = []
    try:
        clean_symbol = symbol.replace(".IS", "")
        ticker = yf.Ticker(symbol)
        
        df_stock_15m = ticker.history(period="60d", interval="15m", auto_adjust=True)
        if not df_stock_15m.empty:
            ratio_15m = build_ratio_df(df_stock_15m, df_index_15m)
            if not ratio_15m.empty:
                for tf_name, rule in TIMEFRAMES_15M.items():
                    df_tf = ratio_15m if rule is None else ratio_15m.resample(rule, origin='start_day').agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
                    signal = detect_lrc_cross_dynamic(df_tf["Close"], tf_name, LRC_LENGTH)
                    if signal:
                        tv_url = f"https://www.tradingview.com/chart/?symbol=BIST:{clean_symbol}/BIST:XU100_CFNNTLTL"
                        results.append({"Hisse": clean_symbol, "RASYON": f"{clean_symbol}/XU100", "Periyot": tf_name, "Sinyal Durumu": signal, "Rasyo Fiyat": round(df_tf["Close"].iloc[-1], 6), "Link": tv_url})

        df_stock_1d = ticker.history(period="10y", interval="1d", auto_adjust=True)
        if not df_stock_1d.empty:
            ratio_1d = build_ratio_df(df_stock_1d, df_index_1d)
            if not ratio_1d.empty:
                for tf_name, bar_count in DAILY_BAR_COUNTS.items():
                    df_tf = resample_tradingview_daily_strict(ratio_1d, bar_count)
                    signal = detect_lrc_cross_dynamic(df_tf["Close"], tf_name, LRC_LENGTH)
                    if signal:
                        tv_url = f"https://www.tradingview.com/chart/?symbol=BIST:{clean_symbol}/BIST:XU100_CFNNTLTL"
                        results.append({"Hisse": clean_symbol, "RASYON": f"{clean_symbol}/XU100", "Periyot": tf_name, "Sinyal Durumu": signal, "Rasyo Fiyat": round(df_tf["Close"].iloc[-1], 6), "Link": tv_url})

                for tf_name, rule in TIMEFRAMES_HIGHER.items():
                    df_tf = ratio_1d.resample(rule, origin='start_day').agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
                    signal = detect_lrc_cross_dynamic(df_tf["Close"], tf_name, LRC_LENGTH)
                    if signal:
                        tv_url = f"https://www.tradingview.com/chart/?symbol=BIST:{clean_symbol}/BIST:XU100_CFNNTLTL"
                        results.append({"Hisse": clean_symbol, "RASYON": f"{clean_symbol}/XU100", "Periyot": tf_name, "Sinyal Durumu": signal, "Rasyo Fiyat": round(df_tf["Close"].iloc[-1], 6), "Link": tv_url})
    except:
        pass
    return results

def run_full_scan():
    idx_ticker = yf.Ticker(INDEX_SYMBOL)
    df_index_15m = idx_ticker.history(period="60d", interval="15m", auto_adjust=True)
    df_index_1d = idx_ticker.history(period="10y", interval="1d", auto_adjust=True)

    if df_index_15m.empty or df_index_1d.empty:
        st.error("Endeks verisi alınamadı!")
        return

    all_signals = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scan_symbol_ratio, sym, df_index_15m, df_index_1d): sym for sym in BIST_SYMBOLS}
        for future in as_completed(futures):
            res = future.result()
            if res: all_signals.extend(res)
    
    if all_signals:
        df_res = pd.DataFrame(all_signals)
        st.success(f"Son Tarama ({datetime.now(TZ).strftime('%H:%M:%S')}): {len(all_signals)} adet kesişim bulundu!")
        st.dataframe(df_res, use_container_width=True)
        
        msg = f"<b>🤖 BIST RASYO LRC OTO BİLDİRİM ({datetime.now(TZ).strftime('%H:%M')})</b>\n\n"
        for sig in all_signals:
            msg += f"<b>{sig['Hisse']} / XU100</b>\n"
            msg += f"├ Periyot: <b>{sig['Periyot']}</b>\n"
            msg += f"├ Sinyal: <b>{sig['Sinyal Durumu']}</b>\n"
            msg += f"└ <a href='{sig['Link']}'>TradingView Grafiği Aç</a>\n\n"
        send_telegram(msg)
    else:
        st.info(f"Son Tarama ({datetime.now(TZ).strftime('%H:%M:%S')}): Kesişim bulunamadı.")

# ==================== STREAMLIT ARAYÜZÜ ====================
st.set_page_config(page_title="BIST Rasyo LRC Kalıcı Tarayıcı", page_icon="🟢", layout="wide")

st.title("🟢 BIST Rasyo LRC Tam Bağımsız Oto Tarayıcı")
st.caption("UptimeRobot Tetiklemeli | Sayfa Kapalı Olsa Bile Sen Durdurana Kadar Arka Planda Çalışır")

# MOUT OTO REFRESH (Açık sayfada 15 dakikada bir görsel yeniler)
st_autorefresh(interval=15 * 60 * 1000, key="screen_reload")

# MEVCUT DURUMU OKU
current_status = get_scan_status()

# PANEL
col1, col2 = st.columns([1, 1])

with col1:
    if st.button("▶️ OTOMATİK TARAMAYI BAŞLAT", type="primary", use_container_width=True):
        set_scan_status(True)
        st.rerun()

with col2:
    if st.button("⏹️ OTOMATİK TARAMAYI DURDUR", use_container_width=True):
        set_scan_status(False)
        st.rerun()

st.divider()

# DURUM BİLGİSİ EKRANI
if current_status:
    st.success("🟢 **OTOMATİK TARAMA SİSTEMİ AKTİF!** (Siteyi kapatsan bile UptimeRobot ping attıkça tarama çalışır ve Telegram'a atar.)")
else:
    st.error("🔴 **OTOMATİK TARAMA DURDURULDU.** (Arka planda hiçbir işlem yapılmıyor.)")

# OTOMATİK ÇALIŞTIRMA MANTIĞI (UptimeRobot ping attığında burası tetiklenir)
if current_status:
    if is_bist_open():
        with st.spinner("Arka Plan Taraması Yapılıyor..."):
            run_full_scan()
    else:
        st.warning("⏳ Borsa kapalı olduğu için tarama pas geçildi (Sadece Hafta İçi 10:00 - 18:05).")
