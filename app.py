import streamlit as st
import pandas as pd
import numpy as np
import requests

st.set_page_config(page_title="LRC Scanner", layout="wide")

st.title("🚀 Çoklu Coin LRC (300 & 301) Otomatik Kesişim Tarayıcısı")

DEFAULT_TOKEN = "8770184809:AAHskJ8stv-BfC9DVHuKKX-ooekSf5zskV4"
DEFAULT_CHAT_ID = "-1003546836920"

COINS = [
    "BTR-USDT",
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT"
]

st.sidebar.header("⚙️ Tarama & Telegram Ayarları")
telegram_token = st.sidebar.text_input("Telegram Bot Token", value=DEFAULT_TOKEN, type="password")
telegram_chat_id = st.sidebar.text_input("Telegram Chat ID", value=DEFAULT_CHAT_ID)

lookback_bars = st.sidebar.slider("Kesişim Kontrolü (Son Kaç Bar Kontrol Edilsin?)", min_value=1, max_value=10, value=2)
timeframe = st.sidebar.selectbox("Periyot", ["12H"], index=0)

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

def fetch_okx_klines(symbol):
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar=12H&limit=350"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    res = requests.get(url, headers=headers, timeout=10)
    if res.status_code == 200:
        data = res.json()
        if data.get("code") == "0":
            raw_candles = data.get("data", [])
            df = pd.DataFrame(raw_candles, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'vol', 'volCcy', 'volCcyQuote', 'confirm'
            ])
            df['close'] = df['close'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
            df = df.sort_values('timestamp').reset_index(drop=True)
            return df
        else:
            raise Exception(f"OKX API Hatası: {data.get('msg')}")
    else:
        raise Exception(f"Bağlantı Hatası ({symbol} - Status: {res.status_code})")

def run_full_scan():
    st.info(f"Tarama başlatıldı ({len(COINS)} Coin taranıyor)...")
    results = []
    
    for symbol in COINS:
        try:
            df = fetch_okx_klines(symbol)
            close_prices = df['close']
            high_prices = df['high']
            low_prices = df['low']
            
            calc_len_300 = min(300, len(df))
            calc_len_301 = min(301, len(df))
            
            up2_300, low2_300 = calc_lrc(close_prices, calc_len_300)
            up2_301, low2_301 = calc_lrc(close_prices, calc_len_301)
            
            hit_300 = False
            hit_301 = False
            
            # Sadece belirlenen son N bardaki hassas teması kontrol eder
            check_depth = min(lookback_bars, len(df))
            
            if up2_300 is not None and low2_300 is not None:
                for i in range(1, check_depth + 1):
                    idx = -i
                    if high_prices.iloc[idx] >= up2_300[idx] or low_prices.iloc[idx] <= low2_300[idx]:
                        hit_300 = True
                        break

            if up2_301 is not None and low2_301 is not None:
                for i in range(1, check_depth + 1):
                    idx = -i
                    if high_prices.iloc[idx] >= up2_301[idx] or low_prices.iloc[idx] <= low2_301[idx]:
                        hit_301 = True
                        break

            clean_symbol = symbol.replace("-", "")

            if hit_300 or hit_301:
                last_price = close_prices.iloc[-1]
                
                if hit_300 and hit_301:
                    kanal_str = "300/300 ve 301/301 ( ÇİFT KANAL KESİŞİMİ )"
                elif hit_300:
                    kanal_str = "300/300"
                else:
                    kanal_str = "301/301"

                msg = (
                    f"🚨 *LRC KESİŞİM SİNYALİ*\n"
                    f"📌 *SEMBOL :* `{clean_symbol}`\n"
                    f"📊 *KANAL :*  `{kanal_str}`\n"
                    f"🎯 *SİNYAL  :* `KESİŞİM`\n"
                    f"📈 *FİYAT :* `{last_price:.4f}`"
                )
                
                send_telegram_msg(telegram_token, telegram_chat_id, msg)
                
                results.append({
                    "SEMBOL": clean_symbol,
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
        st.info("Seçilen son barlarda hiçbir coin için kanala kesişim/temas bulunamadı.")

if st.button("🔥 TÜM COINLERI TARAT", use_container_width=True):
    run_full_scan()
