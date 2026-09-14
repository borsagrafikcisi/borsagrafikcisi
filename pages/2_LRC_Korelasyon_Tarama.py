"""
LRC Korelasyon Tarama - Streamlit Web Sayfası (Arka Plan + Toplu Veri Çekme)
================================================================================
Bu sürümde iki büyük mimari değişiklik var:

1) ARKA PLAN TARAMA: Tarama artık gerçek bir arka plan thread'inde (iş
   parçacığında) çalışıyor. Sayfadan çıkıp geri dönsen bile tarama SUNUCU
   TARAFINDA devam eder, çünkü tarayıcı bağlantısına bağlı değildir.
   st.cache_resource ile process ömrü boyunca kalıcı, paylaşılan bir durum
   (scan_state) nesnesi tutulur.

2) TOPLU (BULK) VERİ ÇEKME: Her hisse için ayrı ayrı ağ isteği atmak yerine,
   bp.download() ile TÜM hisseler TEK/birkaç istekte çekilir. Ardından LRC
   hesaplaması tamamen yerel (ağ gerektirmeyen) pandas işlemleriyle yapılır,
   bu da süreci saniyeler/dakikalar seviyesine indirir.

KURULUM: Bu dosyayı 'pages/2_LRC_Korelasyon_Tarama.py' olarak kaydet.
requirements.txt: streamlit, borsapy, pandas, numpy
"""

import threading
import time

import numpy as np
import pandas as pd
import streamlit as st

try:
    import borsapy as bp
    BORSAPY_AVAILABLE = True
except ImportError:
    BORSAPY_AVAILABLE = False


# ==================== TEMEL LRC FONKSİYONLARI ====================

def linreg(series: pd.Series, length: int) -> pd.Series:
    n = length
    x = np.arange(n)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    result = pd.Series(index=series.index, dtype=float)
    values = series.values
    for i in range(n - 1, len(values)):
        window = values[i - n + 1: i + 1]
        if np.isnan(window).any():
            result.iloc[i] = np.nan
            continue
        y_mean = window.mean()
        slope = ((x - x_mean) * (window - y_mean)).sum() / x_var
        intercept = y_mean - slope * x_mean
        result.iloc[i] = intercept + slope * (n - 1)
    return result


def ta_dev(series: pd.Series, length: int) -> pd.Series:
    sma = series.rolling(length).mean()
    return (series - sma).abs().rolling(length).mean()


def cross_series(a: pd.Series, b: pd.Series) -> pd.Series:
    diff = a - b
    prev_diff = diff.shift(1)
    crossed = (prev_diff > 0) & (diff <= 0) | (prev_diff < 0) & (diff >= 0)
    return crossed.fillna(False)


def barssince(cond: pd.Series) -> pd.Series:
    result = pd.Series(index=cond.index, dtype=float)
    last_true_idx = None
    for i, val in enumerate(cond.values):
        if val:
            last_true_idx = i
            result.iloc[i] = 0
        elif last_true_idx is not None:
            result.iloc[i] = i - last_true_idx
        else:
            result.iloc[i] = np.nan
    return result


def compute_lrc(high, low, lrc_len_high=300, lrc_len_low=300) -> pd.DataFrame:
    lrc_high_reg = linreg(high, lrc_len_high)
    lrc_low_reg = linreg(low, lrc_len_low)
    cross_flag = cross_series(lrc_high_reg, lrc_low_reg)
    bars_since = barssince(cross_flag)
    return pd.DataFrame({
        "lrc_high_reg": lrc_high_reg, "lrc_low_reg": lrc_low_reg,
        "cross_flag": cross_flag, "bars_since_cross": bars_since,
    })


def build_ratio_ohlc(stock_df, index_df) -> pd.DataFrame:
    aligned = stock_df.join(index_df, how="inner", lsuffix="_stk", rsuffix="_idx")
    df = pd.DataFrame(index=aligned.index)
    df["high"] = aligned["high_stk"] / aligned["high_idx"]
    df["low"] = aligned["low_stk"] / aligned["low_idx"]
    df["close"] = aligned["close_stk"] / aligned["close_idx"]
    return df


def group_n_bars(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Günlük+ periyotlar: BAŞTAN say, fazlalığı SONDAN at (stabil hizalama)."""
    if n <= 1:
        return df.copy()
    n_full_groups = len(df) // n
    keep = n_full_groups * n
    trimmed = df.iloc[:keep]
    if len(trimmed) == 0:
        return trimmed[["high", "low", "close"]]
    group_id = np.arange(len(trimmed)) // n
    grouped = pd.DataFrame({
        "high": trimmed["high"].groupby(group_id).max().values,
        "low": trimmed["low"].groupby(group_id).min().values,
        "close": trimmed["close"].groupby(group_id).last().values,
    }, index=trimmed.index[n - 1::n][:n_full_groups])
    return grouped


def group_n_bars_intraday(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Gün-içi çoklu-saat periyotları: her gün kendi içinde, seans açılışından
    itibaren gruplanır (TradingView hizalamasına en yakın, stabil yöntem)."""
    if n <= 1:
        return df.copy()
    work = df.copy()
    work["_date"] = work.index.date
    work["_bar_no"] = work.groupby("_date").cumcount()
    work["_block"] = work["_bar_no"] // n
    grouped = work.groupby(["_date", "_block"]).agg(
        high=("high", "max"), low=("low", "min"), close=("close", "last"),
    )
    last_ts = work.groupby(["_date", "_block"]).apply(lambda g: g.index[-1])
    grouped = grouped.reset_index(drop=True)
    grouped.index = last_ts.values
    grouped = grouped.sort_index()
    return grouped[["high", "low", "close"]]


TIMEFRAMES = {
    "15dk":  {"base_interval": "15m", "bars_per_group": 1,  "bp_period": "max"},
    "30dk":  {"base_interval": "30m", "bars_per_group": 1,  "bp_period": "max"},
    "45dk":  {"base_interval": "45m", "bars_per_group": 1,  "bp_period": "max"},
    "1sa":   {"base_interval": "1h",  "bars_per_group": 1,  "bp_period": "max"},
    "2sa":   {"base_interval": "1h",  "bars_per_group": 2,  "bp_period": "max"},
    "3sa":   {"base_interval": "1h",  "bars_per_group": 3,  "bp_period": "max"},
    "4sa":   {"base_interval": "1h",  "bars_per_group": 4,  "bp_period": "max"},
    "5sa":   {"base_interval": "1h",  "bars_per_group": 5,  "bp_period": "max"},
    "6sa":   {"base_interval": "1h",  "bars_per_group": 6,  "bp_period": "max"},
    "8sa":   {"base_interval": "1h",  "bars_per_group": 8,  "bp_period": "max"},
    "12sa":  {"base_interval": "1h",  "bars_per_group": 12, "bp_period": "max"},
    "13sa":  {"base_interval": "1h",  "bars_per_group": 13, "bp_period": "max"},
    "1gun":  {"base_interval": "1d",  "bars_per_group": 1,  "bp_period": "max"},
    "2gun":  {"base_interval": "1d",  "bars_per_group": 2,  "bp_period": "max"},
    "3gun":  {"base_interval": "1d",  "bars_per_group": 3,  "bp_period": "max"},
    "4gun":  {"base_interval": "1d",  "bars_per_group": 4,  "bp_period": "max"},
    "5gun":  {"base_interval": "1d",  "bars_per_group": 5,  "bp_period": "max"},
    "1hafta": {"base_interval": "1wk", "bars_per_group": 1, "bp_period": "max"},
    "1ay":    {"base_interval": "1mo", "bars_per_group": 1, "bp_period": "max"},
}


def fetch_single_ohlc(symbol: str, base_interval: str, bp_period: str, is_index: bool) -> pd.DataFrame:
    """Tek sembol (endeks için kullanılır) - küçük/az sayıda çağrı olduğu için
    toplu çekime gerek yok."""
    obj = bp.Index(symbol) if is_index else bp.Ticker(symbol)
    data = obj.history(period=bp_period, interval=base_interval)
    if data is None or data.empty:
        raise ValueError(f"'{symbol}' için veri bulunamadı ({base_interval}).")
    return data.rename(columns={"High": "high", "Low": "low", "Close": "close"})[
        ["high", "low", "close"]
    ]


def fetch_bulk_ohlc(symbols: list, base_interval: str, bp_period: str) -> dict:
    """
    TÜM hisseleri TEK/birkaç istekte çeker (borsapy'nin bp.download() toplu
    fonksiyonu ile). Dönen sözlük: {sembol: DataFrame(high, low, close)}.
    Bulk çekim herhangi bir sebeple başarısız olursa (örn. borsapy sürümü
    'interval' param'ını desteklemiyorsa), sembol sembol (yavaş ama çalışan)
    yönteme otomatik geri döner.
    """
    result = {}
    try:
        bulk = bp.download(symbols, period=bp_period, interval=base_interval, group_by="ticker")
        for sym in symbols:
            try:
                sub = bulk[sym]
                sub = sub.rename(columns={"High": "high", "Low": "low", "Close": "close"})
                sub = sub[["high", "low", "close"]].dropna(how="all")
                if not sub.empty:
                    result[sym] = sub
            except Exception:
                continue
        return result
    except Exception:
        # Toplu çekim desteklenmiyor/başarısız oldu -> tek tek dene (yavaş yol)
        for sym in symbols:
            try:
                result[sym] = fetch_single_ohlc(sym, base_interval, bp_period, is_index=False)
            except Exception:
                continue
        return result


def get_ratio_series_for_timeframe(stock_ohlc: pd.DataFrame, index_ohlc: pd.DataFrame,
                                    tf_key: str) -> pd.DataFrame:
    cfg = TIMEFRAMES[tf_key]
    n = cfg["bars_per_group"]
    if cfg["base_interval"] == "1h" and n > 1:
        stock_grp = group_n_bars_intraday(stock_ohlc, n)
        index_grp = group_n_bars_intraday(index_ohlc, n)
    else:
        stock_grp = group_n_bars(stock_ohlc, n)
        index_grp = group_n_bars(index_ohlc, n)
    return build_ratio_ohlc(stock_grp, index_grp)


# ==================== ARKA PLAN TARAMA DURUMU (process ömrü boyunca kalıcı) ====================

@st.cache_resource
def get_scan_state():
    return {
        "running": False,
        "done": 0,
        "total": 0,
        "results": None,       # {"kesisim_bulunanlar":..., "basarisiz":..., "yetersiz":..., "total":...}
        "log": [],
        "lock": threading.Lock(),
    }


def _run_scan_worker(stock_symbols, selected_timeframes, index_symbol,
                      lrc_len, gerikontrol, state):
    try:
        kesisim_bulunanlar = []
        yetersiz_veri_sayisi = 0
        basarisiz_semboller = []
        done = 0

        for tf in selected_timeframes:
            cfg = TIMEFRAMES[tf]

            # Endeks verisi (tek sembol, hafif)
            try:
                index_ohlc = fetch_single_ohlc(index_symbol, cfg["base_interval"], cfg["bp_period"], is_index=True)
            except Exception as e:
                for s in stock_symbols:
                    basarisiz_semboller.append((s, tf, f"Endeks verisi çekilemedi: {e}"))
                    done += 1
                with state["lock"]:
                    state["done"] = done
                continue

            # Hisselerin verisi TOPLU çekiliyor (tek/birkaç istek)
            bulk_data = fetch_bulk_ohlc(stock_symbols, cfg["base_interval"], cfg["bp_period"])

            for stock in stock_symbols:
                try:
                    stock_ohlc = bulk_data.get(stock)
                    if stock_ohlc is None or stock_ohlc.empty:
                        raise ValueError("Toplu çekimde veri bulunamadı.")

                    ratio = get_ratio_series_for_timeframe(stock_ohlc, index_ohlc, tf)
                    n_bars = len(ratio)

                    if n_bars < lrc_len:
                        yetersiz_veri_sayisi += 1
                    else:
                        lrc = compute_lrc(ratio["high"], ratio["low"], lrc_len, lrc_len)
                        last_bars_since = lrc["bars_since_cross"].iloc[-1] if len(lrc) else np.nan
                        if (not np.isnan(last_bars_since)) and (last_bars_since < gerikontrol):
                            kesisim_bulunanlar.append({
                                "Hisse": stock,
                                "Periyot": tf,
                                "Son Kesişimden Bu Yana Bar": int(last_bars_since),
                                "Toplam Bar (Çekilen Veri)": n_bars,
                            })
                except Exception as e:
                    basarisiz_semboller.append((stock, tf, str(e)))

                done += 1
                with state["lock"]:
                    state["done"] = done

        with state["lock"]:
            state["results"] = {
                "index_symbol": index_symbol,
                "kesisim_bulunanlar": kesisim_bulunanlar,
                "basarisiz_semboller": basarisiz_semboller,
                "yetersiz_veri_sayisi": yetersiz_veri_sayisi,
                "total": state["total"],
            }
            state["running"] = False
    except Exception as e:
        with state["lock"]:
            state["results"] = {
                "index_symbol": index_symbol,
                "kesisim_bulunanlar": [],
                "basarisiz_semboller": [("GENEL_HATA", "-", str(e))],
                "yetersiz_veri_sayisi": 0,
                "total": state["total"],
            }
            state["running"] = False


# ==================== STREAMLIT ARAYÜZÜ ====================

st.set_page_config(page_title="LRC Korelasyon Tarama", layout="wide")
st.title("📈 LRC Korelasyon Tarama (Orta Üst / Orta Alt Bant Kesişimi)")
st.caption(
    "Veri kaynağı: borsapy (TradingView WebSocket, ~15 dk gecikmeli). "
    "HİSSE/ENDEKS oranı üzerinde LRC orta üst - orta alt bant kesişimi taranır. "
    "Tarama arka planda çalışır; sayfadan çıkıp geri dönsen bile devam eder."
)

if not BORSAPY_AVAILABLE:
    st.error("`borsapy` kurulu değil. `requirements.txt` dosyana `borsapy` ekle.")
    st.stop()

scan_state = get_scan_state()

with st.sidebar:
    st.header("Tarama Ayarları")
    index_symbol = st.text_input(
        "Endeks (GETİRİ endeksi - oranın paydası)",
        value="XU100_CFNNTLTL",
    )

    tarama_kapsami = st.radio(
        "Taranacak Hisseler",
        options=["Tüm BIST Hisseleri (XUTUM)", "Manuel Liste Gir"],
        index=0,
    )

    if tarama_kapsami == "Manuel Liste Gir":
        stock_input = st.text_area("Hisseler (virgülle ayır)", value="AYEN, AKBNK, THYAO, GARAN")
        stock_symbols_input = [s.strip().upper() for s in stock_input.split(",") if s.strip()]
    else:
        stock_symbols_input = None
        st.caption("Tüm BIST'te işlem gören hisseler taranacak (XUTUM bileşenleri).")

    selected_timeframes = st.multiselect(
        "Periyotlar", options=list(TIMEFRAMES.keys()), default=["1gun"],
    )

    lrc_len = 300
    st.caption("LRC Uzunluk (High/Low): **300 / 300** (sabit)")
    gerikontrol = st.number_input("Taranacak Bar Sayısı (gerikontrol)", min_value=5, max_value=200, value=31, step=1)

    disabled = scan_state["running"]
    run_scan = st.button(
        "🔍 Taramayı Başlat" if not disabled else "⏳ Tarama Sürüyor...",
        type="primary", use_container_width=True, disabled=disabled,
    )

if run_scan and not scan_state["running"]:
    stock_symbols = stock_symbols_input
    if stock_symbols is None:
        with st.spinner("BIST TÜM (XUTUM) hisse listesi çekiliyor..."):
            try:
                xutum = bp.Index("XUTUM")
                stock_symbols = list(xutum.component_symbols)
            except Exception as e:
                st.error(f"Tüm hisse listesi çekilemedi: {e}")
                st.stop()

    if not stock_symbols:
        st.warning("Taranacak hisse bulunamadı.")
    elif not selected_timeframes:
        st.warning("En az bir periyot seçmelisin.")
    else:
        total = len(stock_symbols) * len(selected_timeframes)
        with scan_state["lock"]:
            scan_state["running"] = True
            scan_state["done"] = 0
            scan_state["total"] = total
            scan_state["results"] = None

        thread = threading.Thread(
            target=_run_scan_worker,
            args=(stock_symbols, selected_timeframes, index_symbol, lrc_len, gerikontrol, scan_state),
            daemon=True,
        )
        thread.start()
        st.rerun()

# --- DURUM GÖSTERİMİ ---
if scan_state["running"]:
    done = scan_state["done"]
    total = scan_state["total"] or 1
    st.progress(done / total, text=f"Taranıyor (arka planda)... {done}/{total}")
    st.caption("Bu sayfadan çıkıp geri dönebilirsin, tarama sunucu tarafında devam eder.")
    time.sleep(2)
    st.rerun()

elif scan_state["results"] is not None:
    results = scan_state["results"]
    st.subheader(f"🎯 Kesişim Bulunan Sonuçlar ({results['index_symbol']})")
    if results["kesisim_bulunanlar"]:
        sonuc_df = pd.DataFrame(results["kesisim_bulunanlar"]).sort_values(
            ["Periyot", "Son Kesişimden Bu Yana Bar"]
        )
        st.dataframe(sonuc_df, use_container_width=True, hide_index=True)
        st.success(f"Toplam {len(sonuc_df)} adet kesişim sinyali bulundu.")
    else:
        st.info("Seçilen kriterlerde kesişim sinyali bulunamadı.")

    basarisiz_semboller = results["basarisiz_semboller"]
    yetersiz_veri_sayisi = results["yetersiz_veri_sayisi"]
    total = results["total"]
    basarili = total - len(basarisiz_semboller) - yetersiz_veri_sayisi

    st.divider()
    st.caption(
        f"Toplam denenen: {total} | Başarılı: {basarili} | "
        f"Yetersiz veri: {yetersiz_veri_sayisi} | Hatalı: {len(basarisiz_semboller)}"
    )
    if basarisiz_semboller:
        with st.expander(f"⚠️ Hatalı/çekilemeyen {len(basarisiz_semboller)} sorgu"):
            hata_df = pd.DataFrame(basarisiz_semboller, columns=["Hisse", "Periyot", "Hata Mesajı"])
            st.dataframe(hata_df, use_container_width=True, hide_index=True)
else:
    st.info("Soldaki ayarları yapıp 'Taramayı Başlat' butonuna bas.")
