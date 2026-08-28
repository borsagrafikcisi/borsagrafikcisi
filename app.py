import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import requests
import io

st.set_page_config(page_title="BTR/USDT 12h LRC Scanner", layout="wide")

st.title("🚀 BTR/USDT Dual LRC (300/301) Kesişim Tarayıcısı")

DEFAULT_TOKEN = "8770184809:AAHskJ8stv-BfC9DVHuKKX-ooekSf5zskV4"
DEFAULT_CHAT_ID = "-1003546836920"

st.sidebar.header("⚙️ Tarama & Telegram Ayarları")
telegram_token = st.sidebar.text_input("Telegram Bot Token", value=DEFAULT_TOKEN, type="password")
telegram_chat_id = st.sidebar.text_input("Telegram Chat ID", value=DEFAULT_CHAT_ID)

lookback_bars = st.sidebar.slider("Kesişim Kontrolü (Son Kaç Bar?)", min_value=1, max_value=100, value=35)
timeframe = st.sidebar.selectbox("Periyot", ["12h"], index=0)

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

def generate_chart(df, symbol, ratio_series, length, cross_type):
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(ratio_series.index[-100:], ratio_series.tail(100), label=f'{symbol}', color='blue')
    
    reg, upper, lower = calc_lrc(ratio_series, length)
    if reg is not None:
        idx = ratio_series.index[-length:]
        ax.plot(idx, reg, 'r--', label='Orta Kanal')
        ax.plot(idx, upper, 'g--', label='Üst Bant (Short)')
        ax.plot(idx, lower, 'g--', label='Alt Bant (Long)')
    
    ax.set_title(f"{symbol} - {cross_type} Grafiği ({timeframe})")
    ax.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close(fig)
    return buf.getvalue()

# Coingecko API üzerinden IP engeline takılmadan veri çekme
def fetch_btr_gecko():
    url = "https://api.coingecko.com/api/v3/coins/bitrue-token/market_chart?vs_currency=usd&days=180"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    res = requests.get(url, headers=headers, timeout=10)
    if res.status_code == 200:
        data = res.json()
        prices = data.get('prices', [])
        
        df = pd.DataFrame(prices, columns=['timestamp', 'close'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # 12 Saatlik periyoda resampling (Yeniden örnekleme)
        df.set_index('timestamp', inplace=True)
        df_12h = df['close'].resample('12h').last().dropna().reset_index()
        
        return df_12h
    else:
        raise Exception(f"Coingecko API Yanıtı ({res.status_code}): {res.text}")

if st.button("🔥 BTR/USDT Taramasını Başlat"):
    st.info("BTR/USDT fiyat verileri çekiliyor...")
    results = []
    
    try:
        df = fetch_btr_gecko()
        
        if len(df) < 301:
            st.warning(f"Geçmiş veri sayısı ({len(df)}) 301 barın altında. Mevcut {len(df)} bar üzerinden hesaplanıyor...")
            calc_len_300 = min(300, len(df))
            calc_len_301 = min(301, len(df))
        else:
            calc_len_300 = 300
            calc_len_301 = 301

        ratio = df['close']
        
        reg300, up300, low300 = calc_lrc(ratio, calc_len_300)
        reg301, up301, low301 = calc_lrc(ratio, calc_len_301)
        
        if up300 is not None and up301 is not None:
            hit_300_upper = any(ratio.iloc[-j] >= up300[-j] for j in range(1, min(lookback_bars + 1, len(up300))))
            hit_300_lower = any(ratio.iloc[-j] <= low300[-j] for j in range(1, min(lookback_bars + 1, len(low300))))
            
            hit_301_upper = any(ratio.iloc[-j] >= up301[-j] for j in range(1, min(lookback_bars + 1, len(up301))))
            hit_301_lower = any(ratio.iloc[-j] <= low301[-j] for j in range(1, min(lookback_bars + 1, len(low301))))
            
            status = []
            if hit_300_upper or hit_300_lower: status.append("300/300")
            if hit_301_upper or hit_301_lower: status.append("301/301")
            
            last_price = ratio.iloc[-1]
            
            if status:
                channel_str = " & ".join(status)
                signal_type = "ÜST BANT KESİŞİMİ (Short)" if (hit_300_upper or hit_301_upper) else "ALT BANT KESİŞİMİ (Long)"
                
                results.append({
                    "Sembol": "BTRUSDT",
                    "Kanal": channel_str,
                    "Sinyal Yönü": signal_type,
                    "Son Fiyat": f"{last_price:.4f}"
                })
                
                used_len = calc_len_300 if "300/300" in status else calc_len_301
                img_bytes = generate_chart(df, "BTR/USDT", ratio, used_len, f"{channel_str} - {signal_type}")
                
                caption = (
                    f"🚨 *LRC KESİŞİM SİNYALİ*\n\n"
                    f"📌 *Sembol:* `BTRUSDT`\n"
                    f"📊 *Kanal:* `{channel_str}`\n"
                    f"🎯 *Sinyal:* `{signal_type}`\n"
                    f"📈 *Fiyat:* `{last_price:.4f}`"
                )
                
                send_telegram_photo(telegram_token, telegram_chat_id, img_bytes, caption)
                st.success("Kesişim Yakalandı!")
                st.table(pd.DataFrame(results))
            else:
                st.write(f"BTR/USDT 12h periyodunda son {lookback_bars} barda 300 veya 301 kanallarına kesişim tespit edilmedi.")
                st.write(f"Son Fiyat: {last_price:.4f} | 300 Üst Bant: {up300[-1]:.4f} | 300 Alt Bant: {low300[-1]:.4f}")

    except Exception as e:
        st.error(f"Hata oluştu: {e}")
