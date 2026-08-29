import streamlit as st
import pandas as pd
import numpy as np
import requests

st.set_page_config(page_title="BTR/USDT LRC Scanner", layout="wide")

st.title("🚀 BTR/USDT LRC (300 & 301) Kesişim Tarayıcısı")

DEFAULT_TOKEN = "8770184809:AAHskJ8stv-BfC9DVHuKKX-ooekSf5zskV4"
DEFAULT_CHAT_ID = "-1003546836920"

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

def fetch_btr_gecko():
    url = "https://api.coingecko.com/api/v3/coins/bitrue-token/market_chart?vs_currency=usd&days=180"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    res = requests.get(url, headers=headers, timeout=10)
    if res.status_code == 200:
        data = res.json()
        prices = data.get('prices', [])
        
        df = pd.DataFrame(prices, columns=['timestamp', 'close'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        df_12h = df.resample('12h').agg({'close': 'last'}).dropna().reset_index()
        return df_12h
    else:
        raise Exception(f"Coingecko API Yanıtı ({res.status_code}): {res.text}")

def run_scan(target_length, label_name):
    st.info(f"{label_name} için BTR/USDT fiyat verileri çekiliyor...")
    try:
        df = fetch_btr_gecko()
        calc_len = min(target_length, len(df))
        ratio = df['close']
        
        up2, low2 = calc_lrc(ratio, calc_len)
        
        if up2 is not None and low2 is not None:
            hit_f"📌 *SEMBOL:* `BTRUSDT`\n"g_type = "ÜST BANT KESİŞİMİ (Short)" if hit_up else "ALT BANT KESİŞİMİ (Long)"
                
                msg = (
                    f"🚨 *LRC KESİŞİM SİNYALİ*\n\n"
                    f"📌 *SEMBOL:* `BTRUSDT`\n"
                    f"📊 *KANAL:* `{label_name}`\n"
                    f"🎯 *SİNYAL:* `{sig_type}`\n"
                    f"📈 *FİYAT:* `{last_price:.4f}`"
                )

                
                send_telegram_msg(telegram_token, telegram_chat_id, msg)
                st.success(f"{label_name} Kesişimi Yakalandı ve Telegram'a Bildirildi!")
                
                st.table(pd.DataFrame([{
                    "Sembol": "BTRUSDT",
                    "Kanal": label_name,
                    "Sinyal Yönü": sig_type,
                    "Son Fiyat": f"{last_price:.4f}"
                }]))
            else:
                st.write(f"BTR/USDT 12h periyodunda {label_name} için kesişim bulunamadı.")
                st.write(f"Son Fiyat: {last_price:.4f}")
    except Exception as e:
        st.error(f"Hata oluştu: {e}")

col1, col2 = st.columns(2)

with col1:
    if st.button("🔥 300 / 300 Taramasını Başlat", use_container_width=True):
        run_scan(300, "300/300")

with col2:
    if st.button("⚡ 301 / 301 Taramasını Başlat", use_container_width=True):
        run_scan(301, "301/301")
