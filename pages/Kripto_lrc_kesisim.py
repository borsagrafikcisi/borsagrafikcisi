LRC KESİŞİM TEST SCRIPTI (tek sembol / oran)
----------------------------------------------------------
Amaç: CRYPTOCAP:TOTAL3 / BINANCE:BTCUSDT.P oranını çekip, orijinal
Pine Script'teki LRC kesişim mantığını uygulayıp doğru sonuç verip
vermediğini kontrol etmek. Bunu aynı sembolü TradingView'de açıp
LRC kesişim (KESİŞME) etiketleriyle karşılaştırarak doğrulayabilirsin.

Gereken kütüphaneler:
    pip install ccxt --break-system-packages
    pip install --upgrade --no-cache-dir git+https://github.com/rongardF/tvdatafeed.git
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import ccxt
from tvDatafeed import TvDatafeed, Interval


# ==================== AYARLAR ====================
# TradingView'deki "CRYPTOCAP:TOTAL3 / BINANCE:BTCUSDT.P" oranını test ediyoruz
TV_SEMBOL = "TOTAL3"
TV_EXCHANGE = "CRYPTOCAP"
TV_INTERVAL = Interval.in_daily     # Pine'daki per = 'D' ile eşleşiyor

BINANCE_SEMBOL = "BTC/USDT:USDT"    # ccxt formatı - BTCUSDT.P (perpetual futures)
BINANCE_ZAMAN_DILIMI = "1d"

BAR_SAYISI = 500

# Pine Script'teki girdilerle birebir aynı
LRC_LEN_HIGH = 300
LRC_LEN_LOW = 300
GERIKONTROL = 31                    # "TARANACAK BAR SAYISI"


# ==================== TA FONKSİYONLARI (Pine karşılıkları) ====================

def ta_linreg(series: pd.Series, length: int, offset: int = 0) -> pd.Series:
    result = pd.Series(index=series.index, dtype=float)
    x = np.arange(length)
    for i in range(length - 1, len(series)):
        y = series.iloc[i - length + 1 : i + 1].values
        if np.isnan(y).any():
            continue
        slope, intercept = np.polyfit(x, y, 1)
        result.iloc[i] = intercept + slope * (length - 1 - offset)
    return result


def ta_dev(series: pd.Series, length: int) -> pd.Series:
    def mad(window):
        return np.mean(np.abs(window - window.mean()))
    return series.rolling(length).apply(mad, raw=True)


def ta_crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def ta_crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


# ==================== VERİ ÇEKME ====================

def total3_veri_cek():
    tv = TvDatafeed()  # girişsiz; sorun olursa TvDatafeed(kullanici, sifre) kullan
    df = tv.get_hist(symbol=TV_SEMBOL, exchange=TV_EXCHANGE, interval=TV_INTERVAL, n_bars=BAR_SAYISI)
    return df[["open", "high", "low", "close"]]


def btc_veri_cek():
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    ohlcv = exchange.fetch_ohlcv(BINANCE_SEMBOL, timeframe=BINANCE_ZAMAN_DILIMI, limit=BAR_SAYISI)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df[["open", "high", "low", "close"]]


def oran_serisi_olustur(total3_df: pd.DataFrame, btc_df: pd.DataFrame) -> pd.DataFrame:
    """TradingView'in sembol oranı (A/B) hesaplama mantığıyla aynı:
    high = highA/lowB, low = lowA/highB, close = closeA/closeB, open = openA/openB"""
    ortak = total3_df.join(btc_df, how="inner", lsuffix="_total3", rsuffix="_btc")
    oran = pd.DataFrame(index=ortak.index)
    oran["open"] = ortak["open_total3"] / ortak["open_btc"]
    oran["high"] = ortak["high_total3"] / ortak["low_btc"]
    oran["low"] = ortak["low_total3"] / ortak["high_btc"]
    oran["close"] = ortak["close_total3"] / ortak["close_btc"]
    oran.dropna(inplace=True)
    return oran


# ==================== LRC KESİŞİM HESABI ====================

def lrc_kesisim_hesapla(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["lrcHighReg"] = ta_linreg(out["high"], LRC_LEN_HIGH, 0)
    out["lrcLowReg"] = ta_linreg(out["low"], LRC_LEN_LOW, 0)
    out["lrcUpperReg"] = ta_dev(out["high"], LRC_LEN_HIGH) + out["lrcHighReg"]
    out["lrcLowerDev"] = -ta_dev(out["low"], LRC_LEN_LOW) + out["lrcLowReg"]
    out["crossover"] = ta_crossover(out["lrcHighReg"], out["lrcLowReg"])
    out["crossunder"] = ta_crossunder(out["lrcHighReg"], out["lrcLowReg"])
    return out


# ==================== ÇALIŞTIR ====================

if __name__ == "__main__":
    print("TOTAL3 verisi çekiliyor (TradingView)...")
    total3_df = total3_veri_cek()
    print(f"  {len(total3_df)} bar alındı. Son tarih: {total3_df.index[-1]}")

    print("BTCUSDT.P verisi çekiliyor (Binance Futures)...")
    btc_df = btc_veri_cek()
    print(f"  {len(btc_df)} bar alındı. Son tarih: {btc_df.index[-1]}")

    print("Oran serisi oluşturuluyor: TOTAL3 / BTCUSDT.P ...")
    oran_df = oran_serisi_olustur(total3_df, btc_df)
    print(f"  {len(oran_df)} ortak bar bulundu.")

    print("LRC kesişim hesaplanıyor...")
    sonuc = lrc_kesisim_hesapla(oran_df)

    # Son GERIKONTROL bar içindeki kesişimleri listele
    son_barlar = sonuc.tail(GERIKONTROL)
    kesisimler = son_barlar[son_barlar["crossover"] | son_barlar["crossunder"]]

    print("\n" + "=" * 60)
    if kesisimler.empty:
        print(f"Son {GERIKONTROL} barda kesişim bulunamadı.")
    else:
        print(f"Son {GERIKONTROL} barda bulunan kesişimler:")
        for tarih, satir in kesisimler.iterrows():
            tur = "YUKARI (turuncu)" if satir["crossover"] else "AŞAĞI (yeşil)"
            print(f"  {tarih.date()}  ->  KESİŞME {tur}   (LRC High: {satir['lrcHighReg']:.6f} | LRC Low: {satir['lrcLowReg']:.6f})")

    # Son 5 barın LRC değerlerini de göster (TradingView ile karşılaştırma için)
    print("\nSon 5 barın LRC değerleri (TradingView'deki LRC High/Low çizgileriyle karşılaştır):")
    print(sonuc[["close", "lrcHighReg", "lrcLowReg"]].tail(5).to_string())

    # Grafik
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(sonuc.index, sonuc["lrcHighReg"], color="blue", linewidth=1.5, label="LRC High")
    ax.plot(sonuc.index, sonuc["lrcLowReg"], color="red", linewidth=1.5, label="LRC Low")
    up = sonuc[sonuc["crossover"]]
    down = sonuc[sonuc["crossunder"]]
    ax.scatter(up.index, up["lrcHighReg"], color="orange", marker="^", s=100, zorder=5, label="KESİŞME (Yukarı)")
    ax.scatter(down.index, down["lrcHighReg"], color="green", marker="v", s=100, zorder=5, label="KESİŞME (Aşağı)")
    ax.set_title("TOTAL3 / BTCUSDT.P - LRC Kesişim Testi")
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig("/mnt/user-data/outputs/kesisim_test_grafik.png", dpi=150)
    print("\nGrafik kaydedildi: kesisim_test_grafik.png")
