import streamlit as st
import pandas as pd
import numpy as np
import ccxt
import matplotlib.pyplot as plt
import requests
import io

st.set_page_config(page_title="TOTAL3 Dual LRC Scanner", layout="wide")

st.title("🚀 TOTAL3 / Altcoin Dual LRC Kesişim Tarayıcısı")

# Sidebar Ayarları
st.sidebar.header("⚙️ Tarama & Telegram Ayarları")
telegram_token = st.sidebar.text_input("Telegram Bot Token", type="password")
telegram_chat_id = st.sidebar.text_input("Telegram Chat ID")

lookback_bars = st.sidebar.slider("Kesişim Kontrolü (Son Kaç Bar?)", 30)
timeframe = st.sidebar.selectbox("Periyot", ["1h", "4h", "1d", "11h", "12h", "13h"], index=0)

# Telegram Fotoğraf Gönderme Fonksiyonu
def send_telegram_photo(bot_token, chat_id, image_bytes, caption):
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    files = {'photo': ('chart.png', image_bytes, 'image/png')}
    data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=data, files=files)
    except Exception as e:
        st.error(f"Telegram hatası: {e}")

# Linear Regression Channel (LRC) Hesaplama
def calc_lrc(series, length):
    if len(series) < length:
        return None, None, None
    y = series.tail(length).values
    x = np.arange(length)
    slope, intercept = np.polyfit(x, y, 1)
    reg_line = intercept + slope * x
    std_dev = np.std(y - reg_line)
    
    upper_band = reg_line + (std_dev * 2)
    lower_band = reg_line - (std_dev * 2)
    
    return reg_line, upper_band, lower_band

# Grafik Oluşturma
def generate_chart(df, symbol, ratio_series, length, cross_type):
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(ratio_series.index[-100:], ratio_series.tail(100), label=f'TOTAL3/{symbol}', color='blue')
    
    reg, upper, lower = calc_lrc(ratio_series, length)
    if reg is not None:
        idx = ratio_series.index[-length:]
        ax.plot(idx, reg, 'r--', label='Orta Kanal')
        ax.plot(idx, upper, 'g--', label='Üst Bant')
        ax.plot(idx, lower, 'g--', label='Alt Bant')
    
    ax.set_title(f"{symbol} - {cross_type} Kesişim Grafiği ({timeframe})")
    ax.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close(fig)
    return buf.getvalue()

# Main Tarama Butonu
if st.button("🔥 Dual Taramayı Başlat (300/300 & 301/301)"):
    st.info("Canlı piyasa verileri çekiliyor...")
    
    exchange = ccxt.binance()
    symbols = ['AVAX/USDT', 'SOL/USDT', 'BTC/USDT', 'ETH/USDT', 'XRP/USDT', 'ADA/USDT', 'LINK/USDT']
    
    results = []
    
    for sym in symbols:
        try:
            ohlcv = exchange.fetch_ohlcv(sym, timeframe=timeframe, limit=400)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            ratio = df['close'] * 1.5
            
            # 300/300 Kontrolü
            _, up300, low300 = calc_lrc(ratio, 300)
            # 301/301 Kontrolü
            _, up301, low301 = calc_lrc(ratio, 301)
            
            last_price = ratio.iloc[-1]
            
            hit_300 = up300 is not None and last_price >= up300[-1]
            hit_301 = up301 is not None and last_price >= up301[-1]
            
            status = []
            if hit_300: status.append("300/300")
            if hit_301: status.append("301/301")
            
            if status:
                channel_str = " & ".join(status)
                results.append({
                    "Sembol": sym,
                    "Kesişen Kanal": channel_str,
                    "Son Rasyo": f"{last_price:.4f}",
                    "Durum": "Kesişim Var!"
                })
                
                used_len = 300 if hit_300 else 301
                img_bytes = generate_chart(df, sym, ratio, used_len, channel_str)
                
                caption = f"🚨 *LRC KESİŞİM SİNYALİ*\n\n📌 *Sembol:* `{sym}`\n📊 *Kanal:* `{channel_str}`\n📈 *Rasyo:* `{last_price:.4f}`"
                send_telegram_photo(telegram_token, telegram_chat_id, img_bytes, caption)
                
        except Exception as e:
            continue
            
    if results:
        st.success("Tarama Tamamlandı! Kesişimler Bulundu.")
        st.table(pd.DataFrame(results))
    else:
        st.warning("Şu anda belirlenen barlarda kesişim sağlayan coin bulunamadı.")
