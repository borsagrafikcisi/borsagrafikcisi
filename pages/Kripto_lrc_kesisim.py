import streamlit as st
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import ccxt
from tvDatafeed import TvDatafeed, Interval


# ==================== AYARLAR ====================
TV_SEMBOL = "TOTAL3"
TV_EXCHANGE = "CRYPTOCAP"
TV_INTERVAL = Interval.in_daily

BINANCE_SEMBOL = "BTC/USDT:USDT"
BINANCE_ZAMAN_DILIMI = "1d"

BAR_SAYISI = 500

LRC_LEN_HIGH = 300
LRC_LEN_LOW = 300
GERIKONTROL = 31


# ==================== TA FONKSIYONLARI ====================

def ta_linreg(series, length, offset=0):
    result = pd.Series(index=series.index, dtype=float)
    x = np.arange(length)
    for i in range(length - 1, len(series)):
        y = series.iloc[i - length + 1: i + 1].values
        if np.isnan(y).any():
            continue
        slope, intercept = np.polyfit(x, y, 1)
        result.iloc[i] = intercept + slope * (length - 1 - offset)
    return result


def ta_dev(series, length):
    def mad(window):
        return np.mean(np.abs(window - window.mean()))
    return series.rolling(length).apply(mad, raw=True)


def ta_crossover(a, b):
    return (a > b) & (a.shift(1) <= b.shift(1))


def ta_crossunder(a, b):
    return (a < b) & (a.shift(1) >= b.shift(1))


# ==================== VERI CEKME ====================

def total3_veri_cek():
    tv = TvDatafeed()
    df = tv.get_hist(symbol=TV_SEMBOL, exchange=TV_EXCHANGE, interval=TV_INTERVAL, n_bars=BAR_SAYISI)
    return df[["open", "high", "low", "close"]]


def btc_veri_cek():
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    ohlcv = exchange.fetch_ohlcv(BINANCE_SEMBOL, timeframe=BINANCE_ZAMAN_DILIMI, limit=BAR_SAYISI)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df[["open", "high", "low", "close"]]


def oran_serisi_olustur(total3_df, btc_df):
    ortak = total3_df.join(btc_df, how="inner", lsuffix="_total3", rsuffix="_btc")
    oran = pd.DataFrame(index=ortak.index)
    oran["open"] = ortak["open_total3"] / ortak["open_btc"]
    oran["high"] = ortak["high_total3"] / ortak["low_btc"]
    oran["low"] = ortak["low_total3"] / ortak["high_btc"]
    oran["close"] = ortak["close_total3"] / ortak["close_btc"]
    oran.dropna(inplace=True)
    return oran


def lrc_kesisim_hesapla(df):
    out = df.copy()
    out["lrcHighReg"] = ta_linreg(out["high"], LRC_LEN_HIGH, 0)
    out["lrcLowReg"] = ta_linreg(out["low"], LRC_LEN_LOW, 0)
    out["lrcUpperReg"] = ta_dev(out["high"], LRC_LEN_HIGH) + out["lrcHighReg"]
    out["lrcLowerDev"] = -ta_dev(out["low"], LRC_LEN_LOW) + out["lrcLowReg"]
    out["crossover"] = ta_crossover(out["lrcHighReg"], out["lrcLowReg"])
    out["crossunder"] = ta_crossunder(out["lrcHighReg"], out["lrcLowReg"])
    return out


# ==================== STREAMLIT SAYFASI ====================

st.title("LRC Kesisim Test - TOTAL3 / BTCUSDT.P")
st.caption("Pine Script'teki LRC kesisim mantiginin Python dogrulama testi")

if st.button("Taramayi Calistir"):
    with st.spinner("TOTAL3 verisi cekiliyor (TradingView)..."):
        try:
            total3_df = total3_veri_cek()
            st.success(f"{len(total3_df)} bar alindi. Son tarih: {total3_df.index[-1]}")
        except Exception as e:
            st.error(f"TOTAL3 verisi cekilemedi: {e}")
            st.stop()

    with st.spinner("BTCUSDT.P verisi cekiliyor (Binance Futures)..."):
        try:
            btc_df = btc_veri_cek()
            st.success(f"{len(btc_df)} bar alindi. Son tarih: {btc_df.index[-1]}")
        except Exception as e:
            st.error(f"BTC verisi cekilemedi: {e}")
            st.stop()

    oran_df = oran_serisi_olustur(total3_df, btc_df)
    st.write(f"Ortak bar sayisi: {len(oran_df)}")

    sonuc = lrc_kesisim_hesapla(oran_df)

    son_barlar = sonuc.tail(GERIKONTROL)
    kesisimler = son_barlar[son_barlar["crossover"] | son_barlar["crossunder"]]

    st.subheader("Kesisim Sonuclari")
    if kesisimler.empty:
        st.info(f"Son {GERIKONTROL} barda kesisim bulunamadi.")
    else:
        gosterim = kesisimler.copy()
        gosterim["yon"] = gosterim["crossover"].apply(lambda x: "YUKARI" if x else "ASAGI")
        st.dataframe(gosterim[["yon", "lrcHighReg", "lrcLowReg"]])

    st.subheader("Son 5 Barin LRC Degerleri")
    st.dataframe(sonuc[["close", "lrcHighReg", "lrcLowReg"]].tail(5))

    st.subheader("Grafik")
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(sonuc.index, sonuc["lrcHighReg"], color="blue", linewidth=1.5, label="LRC High")
    ax.plot(sonuc.index, sonuc["lrcLowReg"], color="red", linewidth=1.5, label="LRC Low")
    up = sonuc[sonuc["crossover"]]
    down = sonuc[sonuc["crossunder"]]
    ax.scatter(up.index, up["lrcHighReg"], color="orange", marker="^", s=100, zorder=5, label="Kesisim Yukari")
    ax.scatter(down.index, down["lrcHighReg"], color="green", marker="v", s=100, zorder=5, label="Kesisim Asagi")
    ax.set_title("TOTAL3 / BTCUSDT.P - LRC Kesisim Testi")
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    st.pyplot(fig)
else:
    st.write("Taramayi baslatmak icin yukaridaki butona bas.")
