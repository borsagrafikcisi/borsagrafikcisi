import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import data_fetcher as api
import screener

st.set_page_config(page_title="Şort Sıkışması Tarayıcı V10", layout="wide")

st.title("📉 Tahmini Şort Likidasyon Kümesi Tarayıcısı — V10")
st.caption(f"📦 Modüller — app: v10 | {api.MODULE_VERSION} | {screener.MODULE_VERSION}")
st.markdown("""
Bu sistem, ücretsiz borsa piyasa verilerinden **istatistiksel şort likidasyon bölgeleri** tahmin eder.
Gerçek Coinglass likidasyon haritası değildir; fakat 5 borsayı birleştirir, güncel fiyatı çapraz-borsa
olarak hesaplar ve sizin kullandığınız **'şort kümeleri büyük ölçüde temizlendi, yukarıda son güçlü
küme kaldı'** senaryosunu puanlar.

**V10 iyileştirmeleri:** daha yeni hacme ağırlık verme, borsaları hacimlerine göre ağırlıklandırma,
çapraz-borsa fiyat/funding doğrulaması, en yakın kalan şort kümesini gösterme ve sessiz hataları
ortadan kaldırma.
""")

with st.sidebar:
    st.header("⚙️ Tarama Ayarları")
    max_symbols = st.slider("Taranacak maksimum coin", 10, 100, 25, step=5)
    kline_limit = st.select_slider(
        "Geçmiş veri (gün)", options=[90, 200, 300, 500, 1000], value=300,
        help="Borsaların tek istekteki limitleri nedeniyle çok uzun geçmiş bazı kaynaklarda kısalabilir."
    )
    cluster_window = st.slider("Küme penceresi (gün)", 30, 200, 90, step=10)
    min_sources = st.slider("Minimum borsa sayısı", 1, 5, 2)
    min_score = st.slider("Minimum tükenme skoru", 0, 100, 30)
    run_button = st.button("🔍 Taramayı Başlat", type="primary", use_container_width=True)

    with st.expander("🔧 Borsa / coin teşhis testi"):
        test_ex = st.selectbox("Borsa", api.EXCHANGES, key="test_ex")
        test_symbol = st.text_input("Coin", value="BTC", key="test_symbol")
        if st.button("Test Et", use_container_width=True):
            try:
                df = api.debug_fetch_klines(test_ex, test_symbol.upper(), limit=5)
                st.success(f"{test_ex} / {test_symbol.upper()} — {len(df)} satır")
                st.dataframe(df, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")

if "scan_results" not in st.session_state:
    st.session_state.scan_results = []

if run_button:
    try:
        with st.spinner("Coin evreni hazırlanıyor..."):
            base_symbols, universe_source = api.get_top_symbols_by_volume(max_symbols)
    except Exception as e:
        st.error("Coin listesi alınamadı.")
        st.exception(e)
        st.stop()

    st.info(f"Coin evreni: **{universe_source.upper()}** hacim sıralaması — {len(base_symbols)} coin.")
    progress_bar = st.progress(0, text="Taranıyor...")

    def _progress(i, total, sym):
        progress_bar.progress(i / max(total, 1), text=f"Taranıyor: {sym} ({i}/{total})")

    results = screener.run_scan_multi(
        base_symbols, kline_limit=kline_limit, cluster_window=cluster_window,
        min_sources=min_sources, progress_callback=_progress
    )
    st.session_state.scan_results = results
    progress_bar.empty()
    st.success(f"Tarama tamamlandı. {len(results)} coin analiz edildi.")

results = st.session_state.scan_results

if results:
    table_rows = [{k: v for k, v in r.items() if k not in ("long_clusters", "short_clusters", "ohlcv", "exchange_errors")}
                  for r in results]
    df_table = pd.DataFrame(table_rows).sort_values("exhaustion_score", ascending=False)

    st.subheader("🏆 Skor sıralaması")
    st.dataframe(df_table.head(50), use_container_width=True, hide_index=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("En yüksek skor", df_table["exhaustion_score"].max())
    c2.metric("Ortalama skor", round(df_table["exhaustion_score"].mean(), 1))
    c3.metric("Ortalama kaynak", f"{df_table['kaynak_sayisi'].mean():.1f}/5")
    c4.metric("En yakın kalan küme", "Detaydan seç")

    df_filtered = df_table[df_table["exhaustion_score"] >= min_score]
    st.subheader(f"🎯 Eşiği geçenler — skor ≥ {min_score}")
    st.dataframe(df_filtered, use_container_width=True, hide_index=True)

    if not df_filtered.empty:
        chosen = st.selectbox("Detay grafiği", df_filtered["symbol"].tolist())
        r = next(x for x in results if x["symbol"] == chosen)
        df = r["ohlcv"]
        short_clusters = r["short_clusters"]
        long_clusters = r["long_clusters"]

        nonzero_short = short_clusters.loc[short_clusters["weight"] > 0, "weight"]
        nonzero_long = long_clusters.loc[long_clusters["weight"] > 0, "weight"]
        short_threshold = nonzero_short.quantile(0.65) if len(nonzero_short) else 0
        long_threshold = nonzero_long.quantile(0.65) if len(nonzero_long) else 0
        short_display = short_clusters[short_clusters["weight"] >= short_threshold].copy()
        long_display = long_clusters[long_clusters["weight"] >= long_threshold].copy()
        short_display["mid"] = (short_display["price_low"] + short_display["price_high"]) / 2
        long_display["mid"] = (long_display["price_low"] + long_display["price_high"]) / 2
        bin_height = short_clusters["price_high"].iloc[0] - short_clusters["price_low"].iloc[0]

        cluster_prices = pd.concat([
            short_display[["mid"]].rename(columns={"mid": "price"}),
            long_display[["mid"]].rename(columns={"mid": "price"}),
        ])
        price_lo = min(float(df["low"].tail(120).min()), float(cluster_prices["price"].min()) if not cluster_prices.empty else float(df["low"].min()))
        price_hi = max(float(df["high"].tail(120).max()), float(cluster_prices["price"].max()) if not cluster_prices.empty else float(df["high"].max()))
        pad = max((price_hi - price_lo) * 0.08, price_hi * 0.005)

        fig = make_subplots(rows=1, cols=2, shared_yaxes=True,
                            column_widths=[0.22, 0.78], horizontal_spacing=0.01)
        fig.add_trace(go.Bar(y=short_display["mid"], x=short_display["weight"], orientation="h",
                             name="Şort kümeleri", width=bin_height * 0.9), row=1, col=1)
        fig.add_trace(go.Bar(y=long_display["mid"], x=-long_display["weight"], orientation="h",
                             name="Long kümeleri", width=bin_height * 0.9), row=1, col=1)
        fig.add_trace(go.Candlestick(x=df["open_time"], open=df["open"], high=df["high"],
                                     low=df["low"], close=df["close"], name=chosen), row=1, col=2)
        fig.add_hline(y=r["price"], line_dash="dash", annotation_text=f"Fiyat: {r['price']}", row=1, col=2)

        fig.update_xaxes(autorange="reversed", showticklabels=False, row=1, col=1)
        fig.update_yaxes(range=[price_lo - pad, price_hi + pad], row=1, col=1)
        fig.update_yaxes(range=[price_lo - pad, price_hi + pad], row=1, col=2)
        fig.update_xaxes(rangeslider_visible=False, row=1, col=2)
        fig.update_layout(title=f"{chosen} — Tahmini Şort/Long Kümeleri", height=650,
                          barmode="overlay", bargap=0.15)
        st.plotly_chart(fig, use_container_width=True)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Tükenme skoru", r["exhaustion_score"])
        c2.metric("Temizlenen şort %", f"{r['short_liq_consumed_pct']}%")
        c3.metric("Yakın kalan şort %", f"{r['short_liq_remaining_near_pct']}%")
        c4.metric("Toplam kalan şort %", f"{r['short_liq_remaining_total_pct']}%")
        c5.metric("En yakın küme", f"{r['nearest_short_cluster_pct']}%" if r["nearest_short_cluster_pct"] is not None else "Yok")

        st.subheader("🔎 Sinyal özeti")
        nearest = r["nearest_short_cluster_price"]
        if nearest is not None:
            st.write(f"**Mevcut fiyat:** {r['price']}  |  **En yakın tahmini şort kümesi:** {nearest}  |  **Mesafe:** {r['nearest_short_cluster_pct']}%")
        st.write(f"**Funding medyanı:** %{r['funding_rate_pct']}  |  **RSI:** {r['rsi_14d']}  |  **Veri yaşı:** {r['veri_yasi_saat']} saat")

        with st.expander("🧪 Borsa veri durumu"):
            if r["exchange_errors"]:
                st.warning(r["exchange_errors"])
            else:
                st.success("Analize giren tüm borsalar başarılı.")
else:
    st.info("Taramayı başlatmak için soldaki butona basın.")

st.markdown("---")
st.caption("⚠️ Bu bir yatırım tavsiyesi değildir. Kümeler istatistiksel tahmindir; gerçek açık pozisyon (OI), pozisyon yönü, kaldıraç dağılımı ve gerçekleşmiş likidasyon verilerinin yerine geçmez. Kaldıraçlı işlemler yüksek risklidir.")
