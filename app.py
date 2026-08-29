import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
from datetime import datetime
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings("ignore")

# ==================== AYARLAR ====================
TELEGRAM_TOKEN = "8770184809:AAHskJ8stv-BfC9DVHuKKX-ooekSf5zskV4"
TELEGRAM_CHAT_ID = "-1003546836920"

LRC_LENGTH = 300
LOOKBACK_BARS = 31
MAX_WORKERS = 6

TZ = pytz.timezone("Europe/Istanbul")

# ==================== PERİYOTLAR ====================
TIMEFRAMES = {
    # Dakika
    "15m":  {"interval": "15m", "period": "60d"},
    "30m":  {"interval": "30m", "period": "60d"},
    "45m":  {"interval": "15m", "period": "60d", "resample": "45min"},

    # Saat
    "1h":   {"interval": "1h",  "period": "730d"},
    "2h":   {"interval": "1h",  "period": "730d", "resample": "2h"},
    "3h":   {"interval": "1h",  "period": "730d", "resample": "3h"},
    "4h":   {"interval": "1h",  "period": "730d", "resample": "4h"},
    "5h":   {"interval": "1h",  "period": "730d", "resample": "5h"},
    "6h":   {"interval": "1h",  "period": "730d", "resample": "6h"},
    "7h":   {"interval": "1h",  "period": "730d", "resample": "7h"},
    "8h":   {"interval": "1h",  "period": "730d", "resample": "8h"},

    # Gün
    "1d":   {"interval": "1d",  "period": "5y"},
    "2d":   {"interval": "1d",  "period": "5y", "resample": "2D"},
    "3d":   {"interval": "1d",  "period": "5y", "resample": "3D"},
    "4d":   {"interval": "1d",  "period": "5y", "resample": "4D"},
    "5d":   {"interval": "1d",  "period": "5y", "resample": "5D"},

    # Hafta & Ay
    "1wk":  {"interval": "1wk", "period": "10y"},
    "1mo":  {"interval": "1mo", "period": "max"},
}

# Şimdilik örnek hisse listesi (sonra tüm BIST ekleyeceğiz)
BIST_SYMBOLS = [
    "THYAO.IS", "GARAN.IS", "AKBNK.IS", "YKBNK.IS", "ISCTR.IS", "HALKB.IS", "VAKBN.IS",
    "EREGL.IS", "KCHOL.IS", "SAHOL.IS", "TUPRS.IS", "SISE.IS", "ASELS.IS", "TCELL.IS",
    "BIMAS.IS", "FROTO.IS", "TOASO.IS", "ARCLK.IS", "CCOLA.IS", "PETKM.IS", "SASA.IS",
    "ENKAI.IS", "PGSUS.IS", "AEFES.IS", "DOAS.IS", "GUBRF.IS", "ENJSA.IS", "AKSEN.IS",
    "ALARK.IS", "BRSAN.IS", "CIMSA.IS", "HEKTS.IS", "KOZAL.IS", "KOZAA.IS", "AYEN.IS",
    "ODAS.IS", "ZOREN.IS", "AYDEM.IS", "BIOEN.IS", "SMRTG.IS", "DERHL.IS"
]

def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except:
        pass

def calculate_linreg(series: pd.Series, length: int) -> pd.Series:
    if len(series) < length:
        return pd.Series(index=series.index, dtype=float)
    result = np.full(len(series), np.nan)
    x = np.arange(length)
    for i in range(length - 1, len(series)):
        y = series.iloc[i - length + 1 : i + 1].values
        if np.isnan(y).any():
            continue
        coef = np.polyfit(x, y, 1)
        result[i] = coef[0] * (length - 1) + coef[1]
    return pd.Series(result, index=series.index)

def detect_cross(high_reg: pd.Series, low_reg: pd.Series, lookback: int = 5):
    if len(high_reg) < lookback + 2:
        return None
    for i in range(1, lookback + 1):
        prev_h = high_reg.iloc[-i-1]
        prev_l = low_reg.iloc[-i-1]
        curr_h = high_reg.iloc[-i]
        curr_l = low_reg.iloc[-i]
        if pd.isna(prev_h) or pd.isna(prev_l) or pd.isna(curr_h) or pd.isna(curr_l):
            continue
        if prev_h < prev_l and curr_h > curr_l:
            return "TURUNCU"
        if prev_h > prev_l and curr_h < curr_l:
            return "YEŞİL"
    return None

def process_symbol(symbol: str):
    results = []
    try:
        ticker = yf.Ticker(symbol)
        for tf_name, tf_config in TIMEFRAMES.items():
            try:
                df = ticker.history(period=tf_config["period"], interval=tf_config["interval"], auto_adjust=True)
                if df.empty or len(df) < LRC_LENGTH:
                    continue
                if "resample" in tf_config:
                    df = df.resample(tf_config["resample"]).agg({
                        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
                    }).dropna()
                if len(df) < LRC_LENGTH:
                    continue
                high_reg = calculate_linreg(df["High"], LRC_LENGTH)
                low_reg = calculate_linreg(df["Low"], LRC_LENGTH)
                cross_type = detect_cross(high_reg, low_reg, LOOKBACK_BARS)
                if cross_type:
                    results.append({
                        "Hisse": symbol.replace(".IS", ""),
                        "Periyot": tf_name,
                        "Sinyal": cross_type,
                        "Fiyat": round(df["Close"].iloc[-1], 2)
                    })
            except:
                continue
    except:
        pass
    return results

# ==================== STREAMLIT ARAYÜZÜ ====================
st.set_page_config(page_title="LRC Kesişim Tarayıcı", page_icon="📊", layout="wide")

st.title("LRC Kesişim Tarayıcı (BIST)")
st.caption("Yahoo Finance | LRC 300/300 | Sadece kesişimler")

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Taranacak Hisse", len(BIST_SYMBOLS))
with col2:
    st.metric("Periyot Sayısı", len(TIMEFRAMES))
with col3:
    now = datetime.now(TZ).strftime("%H:%M:%S")
    st.metric("Saat (TR)", now)

if st.button("Tarama Başlat", type="primary", use_container_width=True):
    send_telegram("LRC Tarama başlatıldı (Streamlit)")
    
    progress = st.progress(0)
    status = st.empty()
    all_signals = []
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_symbol, sym): sym for sym in BIST_SYMBOLS}
        total = len(futures)
        done = 0
        
        for future in as_completed(futures):
            res = future.result()
            if res:
                all_signals.extend(res)
            done += 1
            progress.progress(done / total)
            status.text(f"Taranıyor... {done}/{total}")
    
    progress.empty()
    status.empty()
    
    if all_signals:
        df_result = pd.DataFrame(all_signals)
        st.success(f"{len(all_signals)} adet kesişim bulundu!")
        st.dataframe(df_result, use_container_width=True)
        
        msg = "<b>LRC KESİŞME SİNYALLERİ</b>\n\n"
        for sig in all_signals:
            emoji = "🟠" if sig["Sinyal"] == "TURUNCU" else "🟢"
            msg += f"{emoji} <b>{sig['Hisse']}</b> | {sig['Periyot']} | {sig['Fiyat']} ₺\n"
        send_telegram(msg)
    else:
        st.warning("Bu taramada kesişim bulunamadı.")
        send_telegram("Tarama tamamlandı. Kesişim bulunamadı.")

st.markdown("---")
st.info("Not: Şu an örnek hisse listesi kullanılıyor. İstersen tüm BIST listesini ekleyebiliriz.")
