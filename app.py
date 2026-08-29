import streamlit as st
import pandas as pd
import numpy as np
import requests

st.set_page_config(page_title="Binance LRC Scanner", layout="wide")

st.title("🚀 Binance Çoklu Coin LRC (300 & 301) Otomatik Kesişim Tarayıcısı")

DEFAULT_TOKEN = "8770184809:AAHskJ8stv-BfC9DVHuKKX-ooekSf5zskV4"
DEFAULT_CHAT_ID = "-1003546836920"

# Taranacak Binance Çiftleri Listesi
COINS = [
    "BTRUSDT",
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT"
]

st.sidebar.header("⚙️ Tarama & Telegram Ayarları")
telegram_token = st.sidebar.text_input("Telegram Bot Token", value=DEFAULT_TOKEN, type="password")
telegram_chat_id = st.sidebar.text_input("Telegram Chat ID", value=DEFAULT_CHAT_ID)

lookback_bars = st.sidebar.slider("Kesişim Kontrolü (Son Kaç Bar?)", min_value=1, max_value=100, value=35)
timeframe = st.sidebar.selectbox("Periyot", ["12h"], index=0)

def send_telegram_msg(bot_token, chat_id, text):
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        st.error(f"Telegram hatası: {e}")

def calc_lrc(series, length):
    if len(series) < length:
        return None, None
    y = series.tail(length).values
    x = np.arange(length)
    
    poly = np.polyfit(x, y, 2)
    reg_line = np.polyval(poly, x)
    std_dev = np.std(y - reg_line)
    
    up2 = reg_line + (std_dev * 2.0)
    low2 = reg_line - (std_dev * 2.0)
    
    return up2, low2

def fetch_binance_klines(symbol):
    # Binance Public API üzerinden 12h periyodunda son 350 mumu çeker (API Key Gerekmez)
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=12h&limit=350"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    res = requests.get(url, headers=headers, timeout=10)
    if res.status_code == 200:
        data = res.json()
        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])
        df['close'] = df['close'].astype(float)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    else:
        raise Exception(f"Binance API Hatası ({symbol} - Status: {res.status_code})")

def run_full_scan():
    st.info(f"Binance üzerinden tarama başlatıldı ({len(COINS)} Coin 300 ve 301 kanalları için taranıyor)...")
    results = []
    
    for symbol in COINS:
        try:
            df = fetch_binance_klines(symbol)
            ratio = df['close']
            
            calc_len_300 = min(300, len(df))
            calc_len_301 = min(301, len(df))
            
            up2_300, low2_300 = calc_lrc(ratio, calc_len_300)
            up2_301, low2_301 = calc_lrc(ratio, calc_len_301)
            
            hit_300 = False
            hit_301 = False
            
            if up2_300 is not None and low2_300 is not None:
                hit_300_up = any(ratio.iloc[-j] >= up2_300[-j] for j in range(1, min(lookback_bars + 1, len(up2_300))))
                hit_300_low = any(ratio.iloc[-j] <= low2_300[-j] for j in range(1, min(lookback_bars + 1, len(low2_300))))
                hit_300 = hit_300_up or hit_300_low

            if up2_301 is not None and low2_301 is not None:
                hit_301_up = any(ratio.iloc[-j] >= up2_301[-j] for j in range(1, min(lookback_bars + 1, len(up2_301))))
                hit_301_low = any(ratio.iloc[-j] <= low2_301[-j] for j in range(1, min(lookback_bars + 1, len(low2_301))))
                hit_301 = hit_301_up or hit_301_low

            if hit_300 or hit_301:
                last_price = ratio.iloc[-1]
                
                if hit_300 and hit_301:
                    kanal_str = "300/300 ve 301/301 ( ÇİFT KANAL KESİŞİMİ )"
                elif hit_300:
                    kanal_str = "300/300"
                else:
                    kanal_str = "301/301"

                msg = (
                    f"🚨 *LRC KESİŞİM SİNYALİ*\n"
                    f"📌 *SEMBOL :* `{symbol}`\n"
                    f"📊 *KANAL :*  `{kanal_str}`\n"
                    f"🎯 *SİNYAL  :* `KESİŞİM`\n"
                    f"📈 *FİYAT :* `{last_price:.4f}`"
                )
                
                send_telegram_msg(telegram_token, telegram_chat_id, msg)
                
                results.append({
                    "SEMBOL": symbol,
                    "KANAL": kanal_str,
                    "SİNYAL": "KESİŞİM",
                    "FİYAT": f"{last_price:.4f}"
                })
        except Exception as e:
            st.warning(f"{symbol} taranırken hata: {e}")

    if results:
        st.success(f"Tarama Tamamlandı! {len(results)} Adet Kesişim Sinyali Telegram'a Gönderildi.")
        st.table(pd.DataFrame(results))
    else:
        st.write("Listedeki coinlerde seçilen barlarda 300 veya 301 kesişimi bulunamadı.")

if st.button("🔥 BINANCE İLE TÜM COINLERI TARAT", use_container_width=True):
    run_full_scan()
