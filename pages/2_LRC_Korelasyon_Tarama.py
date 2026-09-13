"""
LRC Korelasyon Tarama - Streamlit Web Sayfası
================================================
Bu dosya mevcut Streamlit uygulamana YENİ BİR SAYFA olarak eklenmek üzere
tasarlandı.

KURULUM (mevcut Streamlit projene ekleme):
  1) Projenin içinde 'pages' klasörü yoksa oluştur.
  2) Bu dosyayı 'pages/2_LRC_Korelasyon_Tarama.py' olarak kaydet
     (Streamlit multi-page app'lerde dosya adının başındaki sayı, menüdeki
     sırayı belirler; adını istediğin gibi değiştirebilirsin).
  3) requirements.txt dosyana şu satırları ekle (yoksa):
         streamlit
         borsapy
         pandas
         numpy
  4) GitHub'a push et.
  5) Render otomatik deploy tetikleniyorsa (Auto-Deploy: Yes) birkaç dakika
     içinde yeni sayfa canlıya çıkar. Otomatik değilse Render panelinden
     "Manual Deploy" > "Deploy latest commit" ile tetikle.

BAĞIMSIZ (TEK BAŞINA) BİR RENDER SERVİSİ OLARAK ÇALIŞTIRMAK İSTERSEN:
  Render'da "New Web Service" oluştururken:
    Build Command : pip install -r requirements.txt
    Start Command : streamlit run streamlit_app.py --server.port=$PORT --server.address=0.0.0.0
  (Bu durumda dosyayı 'pages/' altına değil, repo kök dizinine
   'streamlit_app.py' adıyla koy.)
"""

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
    lrc_upper = ta_dev(high, lrc_len_high) + lrc_high_reg
    lrc_lower = -ta_dev(low, lrc_len_low) + lrc_low_reg
    cross_flag = cross_series(lrc_high_reg, lrc_low_reg)
    bars_since = barssince(cross_flag)
    return pd.DataFrame({
        "lrc_high_reg": lrc_high_reg, "lrc_low_reg": lrc_low_reg,
        "lrc_upper": lrc_upper, "lrc_lower": lrc_lower,
        "cross_flag": cross_flag, "bars_since_cross": bars_since,
    })


def build_ratio_ohlc(stock_df, index_df) -> pd.DataFrame:
    aligned = stock_df.join(index_df, how="inner", lsuffix="_stk", rsuffix="_idx")
    df = pd.DataFrame(index=aligned.index)
    df["high"] = aligned["High_stk"] / aligned["High_idx"]
    df["low"] = aligned["Low_stk"] / aligned["Low_idx"]
    df["close"] = aligned["Close_stk"] / aligned["Close_idx"]
    return df


def group_n_bars(df: pd.DataFrame, n: int) -> pd.DataFrame:
    if n <= 1:
        return df.copy()
    n_full_groups = len(df) // n
    trimmed = df.iloc[len(df) - n_full_groups * n:]
    group_id = np.arange(len(trimmed)) // n
    grouped = pd.DataFrame({
        "high": trimmed["high"].groupby(group_id).max().values,
        "low": trimmed["low"].groupby(group_id).min().values,
        "close": trimmed["close"].groupby(group_id).last().values,
    }, index=trimmed.index[n - 1::n][:n_full_groups])
    return grouped


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
# NOT: bp_period="max" -> borsapy/TradingView'dan alınabilecek EN FAZLA geçmiş
# veri istenir. Gün-içi periyotlarda (15dk-13sa) TradingView'ın sağlayabildiği
# geçmiş zaten sınırlıdır (genelde birkaç hafta/ay); "max" istemek o sınıra
# kadar ne varsa hepsini getirir, sınırı aşmaya çalışmaz. Günlük ve üzeri
# periyotlarda ise gerçekten yıllar boyu geriye gidebilir.


@st.cache_data(ttl=300, show_spinner=False)
def fetch_base_ohlc(symbol: str, base_interval: str, bp_period: str, is_index: bool):
    """
    NOT: 'XU100_CFNNTLTL' gibi 'GETİRİ' (return/total-return) endeksleri,
    borsapy'nin bp.indices()/bp.all_indices() listesindeki standart 79
    endeksin İÇİNDE OLMAYABİLİR (bunlar ayrı ISIN'e sahip, temettü dahil
    edilmiş resmi Borsa İstanbul serileridir). bp.Index(symbol) sınıfı
    sembolü doğrudan TradingView'a ilettiği için genelde çalışır, ama
    resmi olarak dokümante edilmemiştir. Çalışmazsa aşağıdaki except bloğu
    açık bir hata mesajı verir; o durumda TradingViewStream üzerinden
    ham sembol çekmeye geçmemiz gerekir.
    """
    try:
        obj = bp.Index(symbol) if is_index else bp.Ticker(symbol)
        data = obj.history(period=bp_period, interval=base_interval)
    except Exception as e:
        raise ValueError(
            f"'{symbol}' için veri çekilemedi ({base_interval}, {bp_period}). "
            f"Hata: {e}. "
            f"'{symbol}' borsapy'nin desteklediği bir sembol olmayabilir "
            f"('GETİRİ' endeksleri için TradingViewStream üzerinden manuel "
            f"çekim gerekebilir)."
        )

    if data is None or data.empty:
        raise ValueError(
            f"'{symbol}' için '{base_interval}' periyodunda veri bulunamadı "
            f"(boş sonuç döndü)."
        )
    return data.rename(columns={"High": "high", "Low": "low", "Close": "close"})[
        ["high", "low", "close"]
    ]


def get_ohlc_for_timeframe(symbol: str, tf_key: str, is_index: bool = False):
    cfg = TIMEFRAMES[tf_key]
    base_df = fetch_base_ohlc(symbol, cfg["base_interval"], cfg["bp_period"], is_index)
    return group_n_bars(base_df, cfg["bars_per_group"])


def scan_pair_on_timeframe(stock_symbol, index_symbol, tf_key,
                            lrc_len_high=300, lrc_len_low=300, gerikontrol=31):
    stock_df = get_ohlc_for_timeframe(stock_symbol, tf_key, is_index=False)
    index_df = get_ohlc_for_timeframe(index_symbol, tf_key, is_index=True)
    ratio = build_ratio_ohlc(stock_df, index_df)

    n_bars = len(ratio)
    if n_bars < lrc_len_high:
        return {"bars_since_cross": None, "condition": None,
                "not_enough_data": True, "available_bars": n_bars}

    lrc = compute_lrc(ratio["high"], ratio["low"], lrc_len_high, lrc_len_low)
    last_bars_since = lrc["bars_since_cross"].iloc[-1] if len(lrc) else np.nan
    condition = (not np.isnan(last_bars_since)) and (last_bars_since < gerikontrol)
    return {"bars_since_cross": last_bars_since, "condition": condition,
            "not_enough_data": False, "available_bars": n_bars}


# ==================== STREAMLIT ARAYÜZÜ ====================

st.set_page_config(page_title="LRC Korelasyon Tarama", layout="wide")
st.title("📈 LRC Korelasyon Tarama (Orta Üst / Orta Alt Bant Kesişimi)")
st.caption(
    "Veri kaynağı: borsapy (TradingView WebSocket, ~15 dk gecikmeli). "
    "HİSSE/ENDEKS oranı üzerinde LRC orta üst - orta alt bant kesişimi taranır."
)

if not BORSAPY_AVAILABLE:
    st.error(
        "`borsapy` kurulu değil. Render'da `requirements.txt` dosyana "
        "`borsapy` satırını eklediğinden emin ol."
    )
    st.stop()

with st.sidebar:
    st.header("Tarama Ayarları")
    index_symbol = st.text_input(
        "Endeks (GETİRİ endeksi)",
        value="XU100_CFNNTLTL",
        help=(
            "Orijinal TradingView taramasındaki gibi BIST 100 GETİRİ endeksi "
            "kullanılır (düz XU100 fiyat endeksi DEĞİL)."
        ),
    )
    stock_input = st.text_area(
        "Hisseler (virgülle ayır)",
        value="AYEN, AKBNK, THYAO, GARAN",
    )
    stock_symbols = [s.strip().upper() for s in stock_input.split(",") if s.strip()]

    selected_timeframes = st.multiselect(
        "Periyotlar",
        options=list(TIMEFRAMES.keys()),
        default=["1gun", "2gun", "3gun", "1hafta", "1ay"],
    )

    # LRC uzunluğu sabit 300/300 (orijinal Pine Script ile birebir aynı)
    lrc_len = 300
    st.caption("LRC Uzunluk (High/Low): **300 / 300** (sabit, orijinal ayarla aynı)")
    gerikontrol = st.number_input("Taranacak Bar Sayısı (gerikontrol)", min_value=5, max_value=200, value=31, step=1)

    run_scan = st.button("🔍 Taramayı Başlat", type="primary", use_container_width=True)

if run_scan:
    if not stock_symbols:
        st.warning("En az bir hisse girmelisin.")
        st.stop()
    if not selected_timeframes:
        st.warning("En az bir periyot seçmelisin.")
        st.stop()

    progress = st.progress(0.0, text="Taranıyor...")
    total = len(stock_symbols) * len(selected_timeframes)
    done = 0

    matrix = pd.DataFrame(index=stock_symbols, columns=selected_timeframes, dtype=object)
    detail_rows = []

    for stock in stock_symbols:
        for tf in selected_timeframes:
            try:
                r = scan_pair_on_timeframe(stock, index_symbol, tf,
                                            lrc_len_high=lrc_len, lrc_len_low=lrc_len,
                                            gerikontrol=gerikontrol)
                if r["not_enough_data"]:
                    cell = f"Yetersiz veri ({r['available_bars']}/{lrc_len})"
                else:
                    cell = "✅ KESİŞİM" if r["condition"] else "—"
                matrix.loc[stock, tf] = cell
                detail_rows.append({
                    "Hisse": stock, "Periyot": tf,
                    "Toplam Bar (Çekilen Veri)": r["available_bars"],
                    "Son Kesişimden Bu Yana Bar": r["bars_since_cross"],
                    "Sonuç": cell,
                })
            except Exception as e:
                matrix.loc[stock, tf] = f"Hata: {e}"
                detail_rows.append({
                    "Hisse": stock, "Periyot": tf,
                    "Son Kesişimden Bu Yana Bar": None, "Sonuç": f"Hata: {e}",
                })
            done += 1
            progress.progress(done / total, text=f"Taranıyor... ({done}/{total})")

    progress.empty()

    st.subheader("Hisse × Periyot Matrisi")
    st.dataframe(matrix, use_container_width=True)

    st.subheader("Detaylı Sonuçlar")
    detail_df = pd.DataFrame(detail_rows)
    st.dataframe(detail_df, use_container_width=True)

    kesisim_var = detail_df[detail_df["Sonuç"] == "✅ KESİŞİM"]
    if not kesisim_var.empty:
        st.success(f"{len(kesisim_var)} adet kesişim sinyali bulundu.")
    else:
        st.info("Seçilen kriterlerde kesişim sinyali bulunamadı.")
else:
    st.info("Soldaki ayarları yapıp 'Taramayı Başlat' butonuna bas.")
