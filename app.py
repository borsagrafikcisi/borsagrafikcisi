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

# TradingView Çoklu Bant Hesaplaması (1.0 SD ve 2.0 SD)
def calc_lrc(series, length):
    if len(series) < length:
        return None, None, None, None, None
    y = series.tail(length).values
    x = np.arange(length)
    slope, intercept = np.polyfit(x, y, 1)
    reg_line = intercept + slope * x
    std_dev = np.std(y - reg_line)
    
    up2 = reg_line + (std_dev * 2.0)
    up1 = reg_line + (std_dev * 1.0)
    low1 = reg_line - (std_dev * 1.0)
    low2 = reg_line - (std_dev * 2.0)
    
    return reg_line, up2, up1, low1, low2

# TradingView Siyah Arka Plan & Noktalı Çizgi Stili
def generate_chart(df, symbol, ratio_series, length, cross_type):
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 6), facecolor='black')
    ax.set_facecolor('black')
    
    df_plot = df.tail(100).copy().reset_index(drop=True)
    
    # Mum Grafiği (Yeşil/Kırmızı)
    for i, row in df_plot.iterrows():
        color = '#00FF7F' if row['close'] >= row['open'] else '#FF3333'
        ax.plot([i, i], [row['open'], row['close']], color=color, linewidth=3)
        ax.plot([i, i], [row['low'], row['high']], color=color, linewidth=1)

    # 5 Bantlı LRC Çizimi (Artı / Noktalı Stilde)
    reg, up2, up1, low1, low2 = calc_lrc(ratio_series, length)
    if reg is not None:
        idx_start = max(0, len(df_plot) - length)
        x_axis = np.arange(idx_start, len(df_plot))
        
        offset = len(x_axis)
        reg_sub = reg[-offset:]
        up2_sub = up2[-offset:]
        up1_sub = up1[-offset:]
        low1_sub = low1[-offset:]
        low2_sub = low2[-offset:]

        # Ekran görüntüsündeki görsel renk paleti
        ax.plot(x_axis, up2_sub, color='#00FF00', linestyle='None', marker='+', markersize=4, label='Upper 2.0')
        ax.plot(x_axis, up1_sub, color='#0088FF', linestyle='None', marker='+', markersize=4, label='Upper 1.0')
        ax.plot(x_axis, reg_sub, color='#FF0044', linestyle='None', marker='+', markersize=4, label='Middle')
        ax.plot(x_axis, low1_sub, color='#AA00FF', linestyle='None', marker='+', markersize=4, label='Lower 1.0')
        ax.plot(x_axis, low2_sub, color='#00FF00', linestyle='None', marker='+', markersize=4, label='Lower 2.0')

    ax.set_title(f"{symbol} - {cross_type} (12h)", fontsize=11, fontweight='bold', color='white')
    ax.grid(False)
    
    step = max(1, len(df_plot) // 6)
    ticks = np.arange(0, len(df_plot), step)
    labels = [df_plot['timestamp'].iloc[t].strftime('%d %b') for t in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, color='white', fontsize=9)
    ax.tick_params(colors='white')

    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, facecolor='black', edgecolor='none')
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
        
        # Mum çizimi için Open, High, Low türetme
        df['open'] = df['close'].shift(1).fillna(df['close'])
        df['high'] = df[['open', 'close']].max(axis=1)
        df['low'] = df[['open', 'close']].min(axis=1)
        
        df.set_index('timestamp', inplace=True)
        df_12h = df.resample('12h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna().reset_index()
        
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
        
        reg300, up2_300, up1_300, low1_300, low2_300 = calc_lrc(ratio, calc_len_300)
        reg301, up2_301, up1_301, low1_301, low2_301 = calc_lrc(ratio, calc_len_301)
        
        if up2_300 is not None and up2_301 is not None:
            # 300/300 Kesişim Kontrolü
            hit_300_upper = any(ratio.iloc[-j] >= up2_300[-j] for j in range(1, min(lookback_bars + 1, len(up2_300))))
            hit_300_lower = any(ratio.iloc[-j] <= low2_300[-j] for j in range(1, min(lookback_bars + 1, len(low2_300))))
            
            # 301/301 Kesişim Kontrolü
            hit_301_upper = any(ratio.iloc[-j] >= up2_301[-j] for j in range(1, min(lookback_bars + 1, len(up2_301))))
            hit_301_lower = any(ratio.iloc[-j] <= low2_301[-j] for j in range(1, min(lookback_bars + 1, len(low2_301))))
            
            has_300 = hit_300_upper or hit_300_lower
            has_301 = hit_301_upper or hit_301_lower
            
            if has_300 or has_301:
                # 300/300 vs 301/301 Ayrımı
                if has_300 and has_301:
                    channel_str = "300/300 & 301/301 (ÇİFT KESİŞİM)"
                    used_len = calc_len_300
                elif has_300:
                    channel_str = "300/300"
                    used_len = calc_len_300
                else:
                    channel_str = "301/301"
                    used_len = calc_len_301

                if (hit_300_upper or hit_301_upper):
                    signal_type = "ÜST BANT KESİŞİMİ (Short)"
                else:
                    signal_type = "ALT BANT KESİŞİMİ (Long)"
                
                last_price = ratio.iloc[-1]
                
                results.append({
                    "Sembol": "BTRUSDT",
                    "Kanal": channel_str,
                    "Sinyal Yönü": signal_type,
                    "Son Fiyat": f"{last_price:.4f}"
                })
                
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
                last_price = ratio.iloc[-1]
                st.write(f"BTR/USDT 12h periyodunda son {lookback_bars} barda 300/300 veya 301/301 kanallarına kesişim tespit edilmedi.")
                st.write(f"Son Fiyat: {last_price:.4f} | 300 Üst Bant: {up2_300[-1]:.4f} | 300 Alt Bant: {low2_300[-1]:.4f}")

    except Exception as e:
        st.error(f"Hata oluştu: {e}")
