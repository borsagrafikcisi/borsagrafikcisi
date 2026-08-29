import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, time as dt_time
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings("ignore")

# ==================== AYARLAR ====================
TELEGRAM_TOKEN = "8770184809:AAHskJ8stv-BfC9DVHuKKX-ooekSf5zskV4"
TELEGRAM_CHAT_ID = "-1003546836920"

LRC_LENGTH = 300
LOOKBACK_BARS = 31          # Son kaç bar içinde kesişim aransın
MAX_WORKERS = 8            # Aynı anda kaç hisse taransın (çok yükseltme)
WAIT_AFTER_CYCLE = 15 * 60 # Döngü bitince bekleme (saniye)

# Türkiye saati
TZ = pytz.timezone("Europe/Istanbul")

# ==================== PERİYOTLAR ====================
# yfinance native + resample edilecekler
TIMEFRAMES = {
    "15m":  {"interval": "15m", "period": "60d"},
    "30m":  {"interval": "30m", "period": "60d"},
    "45m":  {"interval": "15m", "period": "60d", "resample": "45T"},   # 15m'den üret
    "1h":   {"interval": "1h",  "period": "730d"},
    "2h":   {"interval": "1h",  "period": "730d", "resample": "2H"},
    "3h":   {"interval": "1h",  "period": "730d", "resample": "3H"},
    "4h":   {"interval": "1h",  "period": "730d", "resample": "4H"},
    "5h":   {"interval": "1h",  "period": "730d", "resample": "5H"},
    "6h":   {"interval": "1h",  "period": "730d", "resample": "6H"},
    "8h":   {"interval": "1h",  "period": "730d", "resample": "8H"},
    "12h":  {"interval": "1h",  "period": "730d", "resample": "12H"},
    "1d":   {"interval": "1d",  "period": "5y"},
    "2d":   {"interval": "1d",  "period": "5y", "resample": "2D"},
    "3d":   {"interval": "1d",  "period": "5y", "resample": "3D"},
    "4d":   {"interval": "1d",  "period": "5y", "resample": "4D"},
    "5d":   {"interval": "1d",  "period": "5y", "resample": "5D"},
    "1wk":  {"interval": "1wk", "period": "10y"},
    "1mo":  {"interval": "1mo", "period": "max"},
}

# ==================== HİSSE LİSTESİ (İlk versiyon - genişletilebilir) ====================
# Şimdilik güçlü bir başlangıç listesi. Sonra tüm BIST'i ekleyeceğiz.
BIST_SYMBOLS = [
    "THYAO.IS", "GARAN.IS", "AKBNK.IS", "YKBNK.IS", "ISCTR.IS", "HALKB.IS", "VAKBN.IS",
    "EREGL.IS", "KCHOL.IS", "SAHOL.IS", "TUPRS.IS", "SISE.IS", "ASELS.IS", "TCELL.IS",
    "BIMAS.IS", "FROTO.IS", "TOASO.IS", "ARCLK.IS", "CCOLA.IS", "MGROS.IS", "ULKER.IS",
    "PETKM.IS", "SASA.IS", "KOZAL.IS", "KOZAA.IS", "ENKAI.IS", "TAVHL.IS", "PGSUS.IS",
    "AEFES.IS", "DOAS.IS", "VESBE.IS", "VESTL.IS", "GUBRF.IS", "TKFEN.IS", "ENJSA.IS",
    "AYDEM.IS", "ZOREN.IS", "AKSEN.IS", "ODAS.IS", "ALARK.IS", "BRSAN.IS", "CIMSA.IS",
    "AKCNS.IS", "BUCIM.IS", "KONYA.IS", "OYAKC.IS", "HEKTS.IS", "EGEEN.IS", "BRISA.IS",
    "GOODY.IS", "MAVI.IS", "TKNSA.IS", "SOKM.IS", "BIZIM.IS", "MIGRS.IS", "TSKB.IS",
    "SKBNK.IS", "ALBRK.IS", "KLNMA.IS", "LOGO.IS", "NETAS.IS", "INDES.IS", "ARENA.IS",
    "DESPC.IS", "KRDMD.IS", "KRDMA.IS", "KRDMB.IS", "CEMAS.IS", "CELHA.IS", "ISDMR.IS",
    "AYEN.IS", "AKFYE.IS", "CWENE.IS", "GWIND.IS", "BIOEN.IS", "NATEN.IS", "SMRTG.IS",
    # ... buraya daha fazla eklenecek
]

def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram hatası: {e}")

def is_market_open() -> bool:
    now = datetime.now(TZ)
    if now.weekday() >= 5:  # Cumartesi & Pazar
        return False
    market_open = dt_time(10, 0)
    market_close = dt_time(18, 10)
    current = now.time()
    return market_open <= current <= market_close

def calculate_linreg(series: pd.Series, length: int) -> pd.Series:
    """Pine Script ta.linreg benzeri"""
    if len(series) < length:
        return pd.Series(index=series.index, dtype=float)
    
    result = np.full(len(series), np.nan)
    x = np.arange(length)
    
    for i in range(length - 1, len(series)):
        y = series.iloc[i - length + 1 : i + 1].values
        if np.isnan(y).any():
            continue
        coef = np.polyfit(x, y, 1)
        result[i] = coef[0] * (length - 1) + coef[1]  # son nokta
    
    return pd.Series(result, index=series.index)

def detect_cross(high_reg: pd.Series, low_reg: pd.Series, lookback: int = 5):
    """Son lookback bar içinde crossover / crossunder var mı?"""
    if len(high_reg) < lookback + 2:
        return None
    
    for i in range(1, lookback + 1):
        prev_h = high_reg.iloc[-i-1]
        prev_l = low_reg.iloc[-i-1]
        curr_h = high_reg.iloc[-i]
        curr_l = low_reg.iloc[-i]
        
        if pd.isna(prev_h) or pd.isna(prev_l) or pd.isna(curr_h) or pd.isna(curr_l):
            continue
            
        # Crossover (High, Low'u yukarı kesti) → Turuncu
        if prev_h < prev_l and curr_h > curr_l:
            return "TURUNCU"
        # Crossunder (High, Low'u aşağı kesti) → Yeşil
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
                
                # Resample gerekiyorsa
                if "resample" in tf_config:
                    df = df.resample(tf_config["resample"]).agg({
                        "Open": "first",
                        "High": "max",
                        "Low": "min",
                        "Close": "last",
                        "Volume": "sum"
                    }).dropna()
                
                if len(df) < LRC_LENGTH:
                    continue
                
                high_reg = calculate_linreg(df["High"], LRC_LENGTH)
                low_reg = calculate_linreg(df["Low"], LRC_LENGTH)
                
                cross_type = detect_cross(high_reg, low_reg, LOOKBACK_BARS)
                
                if cross_type:
                    last_close = df["Close"].iloc[-1]
                    results.append({
                        "symbol": symbol.replace(".IS", ""),
                        "timeframe": tf_name,
                        "type": cross_type,
                        "price": round(last_close, 2)
                    })
                    
            except Exception:
                continue
                
    except Exception as e:
        print(f"{symbol} genel hata: {e}")
    
    return results

def run_scan():
    print(f"\n[{datetime.now(TZ).strftime('%H:%M:%S')}] Tarama başlıyor... ({len(BIST_SYMBOLS)} hisse)")
    all_signals = []
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_symbol, sym): sym for sym in BIST_SYMBOLS}
        
        for future in as_completed(futures):
            res = future.result()
            if res:
                all_signals.extend(res)
    
    if all_signals:
        message = "<b>LRC KESİŞME SİNYALLERİ</b>\n\n"
        for sig in all_signals:
            color = "🟠" if sig["type"] == "TURUNCU" else "🟢"
            message += f"{color} <b>{sig['symbol']}</b> | {sig['timeframe']} | {sig['price']} ₺\n"
        
        send_telegram(message)
        print(f"{len(all_signals)} sinyal bulundu ve Telegram'a gönderildi.")
    else:
        print("Bu turda sinyal yok.")
    
    return len(all_signals)

def main():
    send_telegram("LRC Tarama Botu başlatıldı.")
    print("Bot başlatıldı. Piyasa açıkken tarama yapacak...")
    
    while True:
        try:
            if is_market_open():
                run_scan()
                print(f"Döngü bitti. {WAIT_AFTER_CYCLE // 60} dakika bekleniyor...")
                time.sleep(WAIT_AFTER_CYCLE)
            else:
                now = datetime.now(TZ)
                print(f"Piyasa kapalı ({now.strftime('%H:%M')}). 5 dk sonra tekrar kontrol edilecek.")
                time.sleep(300)
        except KeyboardInterrupt:
            print("Bot durduruldu.")
            break
        except Exception as e:
            print(f"Ana döngü hatası: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
