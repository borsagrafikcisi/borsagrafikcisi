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
INDEX_SYMBOL = "XU100.IS"

TZ = pytz.timezone("Europe/Istanbul")

# ==================== 20 ÖZEL PERİYOT HARİTASI ====================
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

# ==================== YARDIMCI FONKSİYONLAR ====================
def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
        requests.post(url, json=payload, timeout=10)
    except:
        pass

def calculate_linreg_fast(series: pd.Series, length: int) -> pd.Series:
    """Vektörel Hızlı Lineer Regresyon (TradingView ta.linreg birebir dengi)"""
    if len(series) < length:
        return pd.Series(index=series.index, dtype=float)
    
    x = np.arange(length)
    x_mean = x.mean()
    x_dev = x - x_mean
    x_var = (x_dev ** 2).sum()
    
    vals = series.values
    result = np.full(len(vals), np.nan)
    
    for i in range(length - 1, len(vals)):
        y_window = vals[i - length + 1 : i + 1]
        if np.isnan(y_window).any():
            continue
        y_mean = y_window.mean()
        slope = (x_dev * (y_window - y_mean)).sum() / x_var
        intercept = y_mean - slope * x_mean
        result[i] = slope * (length - 1) + intercept
        
    return pd.Series(result, index=series.index)

def resample_tradingview_daily(df: pd.DataFrame, days: int) -> pd.DataFrame:
    """
    TradingView Birebir Gruplama:
    Bugünden (son mumdan) geçmişe doğru N'lik paketler oluşturur.
    """
    if days == 1 or len(df) == 0:
        return df
    
    # 1. Veriyi tersten sırala (En yeni mum en üstte)
    df_reversed = df.iloc[::-1].reset_index()
    
    # 2. Tersten N'erli paketler oluştur (Bugün = Group 0)
    group_id = np.arange(len(df_reversed)) // days
    
    # 3. Tersten paket OHLC hesabı
    date_col = 'Date' if 'Date' in df_reversed.columns else 'index'
    df_res = df_reversed.groupby(group_id).agg({
        date_col: 'first',
        'Open': 'last',    # Grubun en eski açılışı
        'High': 'max',     # Grubun en yükseği
        'Low': 'min',      # Grubun en düşüğü
        'Close': 'first'   # Grubun en yeni kapanışı
    }).set_index(date_col)
    
    # 4. Veriyi tekrar kronolojik sıraya diz (Eskiden yeniye)
    return df_res.iloc[::-1]

def detect_cross(high_reg: pd.Series, low_reg: pd.Series, lookback: int = 31):
    """TradingView ile Birebir Bar Sayım Uyumlu Kesişim Tespiti"""
    if len(high_reg) < lookback + 2:
        return None
    
    for i in range(1, lookback + 1):
        prev_h = high_reg.iloc[-i-1]
        prev_l = low_reg.iloc[-i-1]
        curr_h = high_reg.iloc[-i]
        curr_l = low_reg.iloc[-i]
        
        if pd.isna(prev_h) or pd.isna(prev_l) or pd.isna(curr_h) or pd.isna(curr_l):
            continue
            
        bar_count = i - 1
        bar_str = "son barda" if bar_count == 0 else f"{bar_count}. mumda"
        
        # Turuncu Kesişim (High Low'u yukarı keser)
        if prev_h < prev_l and curr_h > curr_l:
            return f"TURUNCU ({bar_str})"
            
        # Yeşil Kesişim (High Low'u aşağı keser)
        if prev_h > prev_l and curr_h < curr_l:
            return f"YEŞİL ({bar_str})"
            
    return None

def build_ratio_df(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> pd.DataFrame:
    """TradingView Rasyo Mum Birebir Çapraz Bölme Mantığı"""
    combined = pd.concat([stock_df, index_df], axis=1, keys=['stock', 'index'], join='inner').dropna()
    if combined.empty:
        return pd.DataFrame()
    
    ratio_df = pd.DataFrame(index=combined.index)
    ratio_df['Open']  = combined['stock']['Open']  / combined['index']['Open']
    ratio_df['High']  = combined['stock']['High']  / combined['index']['Low']
    ratio_df['Low']   = combined['stock']['Low']   / combined['index']['High']
    ratio_df['Close'] = combined['stock']['Close'] / combined['index']['Close']
    return ratio_df

# ==================== İŞLEMCİ VE TARAMA MANTIĞI ====================
def scan_symbol_ratio(symbol: str, df_index_15m: pd.DataFrame, df_index_1d: pd.DataFrame):
    results = []
    try:
        clean_symbol = symbol.replace(".IS", "")
        ticker = yf.Ticker(symbol)
        
        # 1. Dakikalık/Saatlik Rasyolar (15m Ana Veri)
        df_stock_15m = ticker.history(period="60d", interval="15m", auto_adjust=True)
        if not df_stock_15m.empty:
            ratio_15m = build_ratio_df(df_stock_15m, df_index_15m)
            if not ratio_15m.empty:
                for tf_name, rule in TIMEFRAMES_15M.items():
                    df_tf = ratio_15m if rule is None else ratio_15m.resample(rule, origin='start').agg({
                        "Open": "first", "High": "max", "Low": "min", "Close": "last"
                    }).dropna()
                    
                    if len(df_tf) >= LRC_LENGTH:
                        h_reg = calculate_linreg_fast(df_tf["High"], LRC_LENGTH)
                        l_reg = calculate_linreg_fast(df_tf["Low"], LRC_LENGTH)
                        signal = detect_cross(h_reg, l_reg, LOOKBACK_BARS)
                        if signal:
                            tv_url = f"https://www.tradingview.com/chart/?symbol=BIST:{clean_symbol}/BIST:XU100_CFNNTLTL"
                            results.append({
                                "Hisse": clean_symbol,
                                "Rasyo": f"{clean_symbol}/XU100",
                                "Periyot": tf_name,
                                "Sinyal": signal,
                                "Rasyo Fiyat": round(df_tf["Close"].iloc[-1], 5),
                                "Link": tv_url
                            })

        # 2. Günlük Seans Bazlı Rasyolar (1d, 2d, 3d, 4d, 5d, 6d, 7d)
        df_stock_1d = ticker.history(period="10y", interval="1d", auto_adjust=True)
        if not df_stock_1d.empty:
            ratio_1d = build_ratio_df(df_stock_1d, df_index_1d)
            if not ratio_1d.empty:
                for tf_name, bar_count in DAILY_BAR_COUNTS.items():
                    df_tf = resample_tradingview_daily(ratio_1d, bar_count)
                    
                    if len(df_tf) >= LRC_LENGTH:
                        h_reg = calculate_linreg_fast(df_tf["High"], LRC_LENGTH)
                        l_reg = calculate_linreg_fast(df_tf["Low"], LRC_LENGTH)
                        signal = detect_cross(h_reg, l_reg, LOOKBACK_BARS)
                        if signal:
                            tv_url = f"https://www.tradingview.com/chart/?symbol=BIST:{clean_symbol}/BIST:XU100_CFNNTLTL"
                            results.append({
                                "Hisse": clean_symbol,
                                "Rasyo": f"{clean_symbol}/XU100",
                                "Periyot": tf_name,
                                "Sinyal": signal,
                                "Rasyo Fiyat": round(df_tf["Close"].iloc[-1], 5),
                                "Link": tv_url
                            })

                # Hafta ve Ay Taramaları
                for tf_name, rule in TIMEFRAMES_HIGHER.items():
                    df_tf = ratio_1d.resample(rule, origin='start').agg({
                        "Open": "first", "High": "max", "Low": "min", "Close": "last"
                    }).dropna()
                    
                    if len(df_tf) >= LRC_LENGTH:
                        h_reg = calculate_linreg_fast(df_tf["High"], LRC_LENGTH)
                        l_reg = calculate_linreg_fast(df_tf["Low"], LRC_LENGTH)
                        signal = detect_cross(h_reg, l_reg, LOOKBACK_BARS)
                        if signal:
                            tv_url = f"https://www.tradingview.com/chart/?symbol=BIST:{clean_symbol}/BIST:XU100_CFNNTLTL"
                            results.append({
                                "Hisse": clean_symbol,
                                "Rasyo": f"{clean_symbol}/XU100",
                                "Periyot": tf_name,
                                "Sinyal": signal,
                                "Rasyo Fiyat": round(df_tf["Close"].iloc[-1], 5),
                                "Link": tv_url
                            })
    except:
        pass
    return results

# ==================== STREAMLIT ARAYÜZÜ ====================
st.set_page_config(page_title="Rasyo LRC Tarayıcı", page_icon="📈", layout="wide")

st.title("BIST Rasyo LRC 300 Kesişim Tarayıcı")
st.caption("TradingView Uyumlu | Seans Barmatik Gruplama | 20 Özel Periyot")

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Taranacak Hisse", len(BIST_SYMBOLS))
with col2:
    st.metric("Periyot Sayısı", len(TIMEFRAMES_15M) + len(DAILY_BAR_COUNTS) + len(TIMEFRAMES_HIGHER))
with col3:
    st.metric("Saat (TR)", datetime.now(TZ).strftime("%H:%M:%S"))

if st.button("Rasyo Taramasını Başlat", type="primary", use_container_width=True):
    status = st.empty()
    status.info("Endeks verisi indiriliyor...")
    
    idx_ticker = yf.Ticker(INDEX_SYMBOL)
    df_index_15m = idx_ticker.history(period="60d", interval="15m", auto_adjust=True)
    df_index_1d = idx_ticker.history(period="10y", interval="1d", auto_adjust=True)
    
    if df_index_15m.empty or df_index_1d.empty:
        st.error("Endeks verisi alınamadı!")
    else:
        status.info("Hisse rasyoları taranıyor...")
        progress = st.progress(0)
        all_signals = []
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(scan_symbol_ratio, sym, df_index_15m, df_index_1d): sym for sym in BIST_SYMBOLS}
            total = len(futures)
            done = 0
            
            for future in as_completed(futures):
                res = future.result()
                if res:
                    all_signals.extend(res)
                done += 1
                progress.progress(done / total)
        
        progress.empty()
        status.empty()
        
        if all_signals:
            df_res = pd.DataFrame(all_signals)
            st.success(f"{len(all_signals)} adet rasyo kesişimi bulundu!")
            st.dataframe(df_res, use_container_width=True)
            
            # Telegram Bildirimi
            msg = "<b>📈 BIST RASYO LRC KESİŞİM SİNYALLERİ</b>\n\n"
            for sig in all_signals:
                emoji = "🟠" if "TURUNCU" in sig["Sinyal"] else "🟢"
                msg += f"{emoji} <b>{sig['Hisse']} / XU100</b>\n"
                msg += f"├ Periyot: <b>{sig['Periyot']}</b>\n"
                msg += f"├ Sinyal: {sig['Sinyal']}\n"
                msg += f"└ <a href='{sig['Link']}'>TradingView Grafiği Aç</a>\n\n"
            send_telegram(msg)
        else:
            st.warning("Seçilen rasyolarda kesişim bulunamadı.")
