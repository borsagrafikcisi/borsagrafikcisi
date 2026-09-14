"""
LRC Korelasyon Tarama - Streamlit Web Sayfası (Senkron + Gerçek Paralel Tarama)
==================================================================================
Bu sürüm SADE tutuldu: arka plan thread / session_state kalıcılık mimarisi
kaldırıldı. Tarama, sayfa açıkken CANLI ilerleme göstererek çalışır (senkron).
Hız için: her hisseye AYNI ANDA (ThreadPoolExecutor ile paralel) istek atılır.

KURULUM: Bu dosyayı 'pages/2_LRC_Korelasyon_Tarama.py' olarak kaydet.
requirements.txt: streamlit, borsapy, pandas, numpy
"""

import concurrent.futures as cf

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


def compute_lrc_cross(high, low, lrc_len=300):
    lrc_high_reg = linreg(high, lrc_len)
    lrc_low_reg = linreg(low, lrc_len)
    cross_flag = cross_series(lrc_high_reg, lrc_low_reg)
    bars_since = barssince(cross_flag)
    return bars_since


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
    # Kısa ama 300 barlık LRC için yeterli geçmiş periyotlar - hem hız hem
    # bellek için 'max' yerine sınırlı aralıklar kullanılıyor.
    "15dk":  {"base_interval": "15m", "bars_per_group": 1,  "bp_period": "max"},
    "30dk":  {"base_interval": "30m", "bars_per_group": 1,  "bp_period": "max"},
    "45dk":  {"base_interval": "45m", "bars_per_group": 1,  "bp_period": "max"},
    "1sa":   {"base_interval": "1h",  "bars_per_group": 1,  "bp_period": "2y"},
    "2sa":   {"base_interval": "1h",  "bars_per_group": 2,  "bp_period": "2y"},
    "3sa":   {"base_interval": "1h",  "bars_per_group": 3,  "bp_period": "2y"},
    "4sa":   {"base_interval": "1h",  "bars_per_group": 4,  "bp_period": "2y"},
    "5sa":   {"base_interval": "1h",  "bars_per_group": 5,  "bp_period": "2y"},
    "6sa":   {"base_interval": "1h",  "bars_per_group": 6,  "bp_period": "2y"},
    "8sa":   {"base_interval": "1h",  "bars_per_group": 8,  "bp_period": "3y"},
    "12sa":  {"base_interval": "1h",  "bars_per_group": 12, "bp_period": "3y"},
    "13sa":  {"base_interval": "1h",  "bars_per_group": 13, "bp_period": "3y"},
    "1gun":  {"base_interval": "1d",  "bars_per_group": 1,  "bp_period": "3y"},
    "2gun":  {"base_interval": "1d",  "bars_per_group": 2,  "bp_period": "5y"},
    "3gun":  {"base_interval": "1d",  "bars_per_group": 3,  "bp_period": "5y"},
    "4gun":  {"base_interval": "1d",  "bars_per_group": 4,  "bp_period": "6y"},
    "5gun":  {"base_interval": "1d",  "bars_per_group": 5,  "bp_period": "7y"},
    "1hafta": {"base_interval": "1wk", "bars_per_group": 1, "bp_period": "max"},
    "1ay":    {"base_interval": "1mo", "bars_per_group": 1, "bp_period": "max"},
}


def fetch_single_ohlc(symbol: str, base_interval: str, bp_period: str, is_index: bool) -> pd.DataFrame:
    obj = bp.Index(symbol) if is_index else bp.Ticker(symbol)
    data = obj.history(period=bp_period, interval=base_interval)
    if data is None or data.empty:
        raise ValueError(f"'{symbol}' için veri bulunamadı ({base_interval}).")
    return data.rename(columns={"High": "high", "Low": "low", "Close": "close"})[
        ["high", "low", "close"]
    ]


def get_ratio_series(stock_ohlc: pd.DataFrame, index_ohlc: pd.DataFrame, tf_key: str) -> pd.DataFrame:
    cfg = TIMEFRAMES[tf_key]
    n = cfg["bars_per_group"]
    if cfg["base_interval"] == "1h" and n > 1:
        stock_grp = group_n_bars_intraday(stock_ohlc, n)
        index_grp = group_n_bars_intraday(index_ohlc, n)
    else:
        stock_grp = group_n_bars(stock_ohlc, n)
        index_grp = group_n_bars(index_ohlc, n)
    return build_ratio_ohlc(stock_grp, index_grp)


def scan_one_stock(stock: str, index_ohlc: pd.DataFrame, tf: str, lrc_len: int, gerikontrol: int):
    """Tek hisse için: veri çek + oran hesapla + LRC kesişimini kontrol et."""
    cfg = TIMEFRAMES[tf]
    stock_ohlc = fetch_single_ohlc(stock, cfg["base_interval"], cfg["bp_period"], is_index=False)
    ratio = get_ratio_series(stock_ohlc, index_ohlc, tf)
    n_bars = len(ratio)

    if n_bars < lrc_len:
        return {"not_enough_data": True, "available_bars": n_bars}

    bars_since = compute_lrc_cross(ratio["high"], ratio["low"], lrc_len)
    last_bars_since = bars_since.iloc[-1] if len(bars_since) else np.nan
    condition = (not np.isnan(last_bars_since)) and (last_bars_since < gerikontrol)

    return {
        "not_enough_data": False,
        "available_bars": n_bars,
        "bars_since_cross": last_bars_since,
        "condition": condition,
    }


# ==================== STREAMLIT ARAYÜZÜ ====================

st.set_page_config(page_title="LRC Korelasyon Tarama", layout="wide")
st.title("📈 LRC Korelasyon Tarama (Orta Üst / Orta Alt Bant Kesişimi)")
st.caption(
    "Veri kaynağı: borsapy (TradingView WebSocket, ~15 dk gecikmeli). "
    "HİSSE/ENDEKS oranı üzerinde LRC orta üst - orta alt bant kesişimi taranır. "
    "Tarama sayfa açıkken çalışır ve canlı ilerleme gösterir."
)

if not BORSAPY_AVAILABLE:
    st.error("`borsapy` kurulu değil. `requirements.txt` dosyana `borsapy` ekle.")
    st.stop()

with st.sidebar:
    st.header("Tarama Ayarları")
    index_symbol = st.text_input("Endeks (GETİRİ endeksi - oranın paydası)", value="XU100_CFNNTLTL")

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

    selected_timeframes = st.multiselect("Periyotlar", options=list(TIMEFRAMES.keys()), default=["1gun"])

    lrc_len = 300
    st.caption("LRC Uzunluk (High/Low): **300 / 300** (sabit)")
    gerikontrol = st.number_input("Taranacak Bar Sayısı (gerikontrol)", min_value=5, max_value=200, value=31, step=1)

    max_workers = st.slider(
        "Eşzamanlı İstek Sayısı (Hız)",
        min_value=5, max_value=40, value=25,
        help="Yüksek değer hızlandırır ama çok yüksek olursa hata oranı artabilir. 20-30 önerilir.",
    )

    run_scan = st.button("🔍 Taramayı Başlat", type="primary", use_container_width=True)

if run_scan:
    stock_symbols = stock_symbols_input
    if stock_symbols is None:
        with st.spinner("BIST TÜM (XUTUM) hisse listesi çekiliyor..."):
            try:
                xutum = bp.Index("XUTUM")
                stock_symbols = list(xutum.component_symbols)
            except Exception as e:
                st.error(f"Tüm hisse listesi çekilemedi: {e}")
                st.stop()
        st.write(f"**Çekilen toplam hisse sayısı:** {len(stock_symbols)}")

    if not stock_symbols:
        st.warning("Taranacak hisse bulunamadı.")
        st.stop()
    if not selected_timeframes:
        st.warning("En az bir periyot seçmelisin.")
        st.stop()

    kesisim_bulunanlar = []
    basarisiz_semboller = []
    yetersiz_veri_sayisi = 0

    total = len(stock_symbols) * len(selected_timeframes)
    done = 0

    progress_bar = st.progress(0.0)
    progress_text = st.empty()
    sonuc_placeholder = st.empty()

    import time as _time
    t0 = _time.time()

    for tf in selected_timeframes:
        cfg = TIMEFRAMES[tf]

        # Endeks verisi TEK sefer çekilir (tüm hisseler için ortak kullanılır)
        try:
            index_ohlc = fetch_single_ohlc(index_symbol, cfg["base_interval"], cfg["bp_period"], is_index=True)
        except Exception as e:
            st.error(f"Endeks verisi çekilemedi ({tf}): {e}")
            for s in stock_symbols:
                basarisiz_semboller.append((s, tf, "Endeks verisi çekilemedi"))
                done += 1
            continue

        # Hisseler PARALEL taranır
        with cf.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(scan_one_stock, stock, index_ohlc, tf, lrc_len, gerikontrol): stock
                for stock in stock_symbols
            }
            for future in cf.as_completed(futures):
                stock = futures[future]
                try:
                    r = future.result()
                    if r["not_enough_data"]:
                        yetersiz_veri_sayisi += 1
                    elif r["condition"]:
                        kesisim_bulunanlar.append({
                            "Hisse": stock,
                            "Periyot": tf,
                            "Son Kesişimden Bu Yana Bar": int(r["bars_since_cross"]),
                            "Toplam Bar (Çekilen Veri)": r["available_bars"],
                        })
                except Exception as e:
                    basarisiz_semboller.append((stock, tf, str(e)))

                done += 1
                if done % 3 == 0 or done == total:
                    elapsed = _time.time() - t0
                    hiz = done / elapsed if elapsed > 0 else 0
                    kalan_sn = (total - done) / hiz if hiz > 0 else 0
                    progress_bar.progress(done / total)
                    progress_text.text(
                        f"Taranıyor... {done}/{total} | "
                        f"Geçen süre: {elapsed:.0f}sn | Tahmini kalan: {kalan_sn:.0f}sn"
                    )
                    if kesisim_bulunanlar:
                        with sonuc_placeholder.container():
                            st.write(f"🎯 **Şimdiye kadar bulunan kesişim: {len(kesisim_bulunanlar)}**")
                            st.dataframe(pd.DataFrame(kesisim_bulunanlar), use_container_width=True, hide_index=True)

    progress_bar.empty()
    progress_text.empty()
    sonuc_placeholder.empty()

    toplam_sure = _time.time() - t0
    st.caption(f"Toplam tarama süresi: {toplam_sure:.0f} saniye ({toplam_sure/60:.1f} dakika)")

    st.subheader(f"🎯 Kesişim Bulunan Sonuçlar ({index_symbol})")
    if kesisim_bulunanlar:
        sonuc_df = pd.DataFrame(kesisim_bulunanlar).sort_values(["Periyot", "Son Kesişimden Bu Yana Bar"])
        st.dataframe(sonuc_df, use_container_width=True, hide_index=True)
        st.success(f"Toplam {len(sonuc_df)} adet kesişim sinyali bulundu.")
    else:
        st.info("Seçilen kriterlerde kesişim sinyali bulunamadı.")

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
