import streamlit as st
import pandas as pd
import plotly.graph_objects as go

import data_fetcher as api
import screener

st.set_page_config(page_title="Şort Sıkışması Tarayıcı", layout="wide")

st.title("📉 Tahmini Şort Likidasyon Kümesi Tarayıcısı")
st.caption("🔧 Kod sürümü: v7-multiexchange (bu satırı görüyorsanız güncel kod çalışıyor demektir)")
st.caption(f"📦 Modül sürümleri — app: v7 | {api.MODULE_VERSION} | {screener.MODULE_VERSION}")

st.markdown("""
Bu araç, Coinglass'ın **ücretli** likidasyon haritası verisi yerine, **5 borsanın
ücretsiz herkese açık verilerini** (Binance, Bybit, OKX, Bitget, Gate.io — fiyat +
hacim) birleştirerek şort likidasyon kümelerini **tahmin eder**. Coinglass ile
birebir aynı sonucu vermez — amaç, "şort sıkışması tükeniyor mu?" sorusuna dair
bir ön filtre / fikir üretme aracı sağlamaktır.

**Mantık:** Fiyat yükselirken art arda şort likidasyonlarını temizliyorsa ve
geride, mevcut fiyatın hemen üstünde küçük/ince bir küme kalmışsa, "tükenme
skoru" yükselir — CAP örneğinde anlattığınız senaryo budur. Her coin için
verisi olan borsalar toplanıp tek bir kümede birleştirilir.
""")

with st.sidebar:
    st.header("Ayarlar")
    max_symbols = st.slider(
        "Taranacak maksimum coin sayısı", 10, 100, 25, step=5,
        help="Her coin 5 borsadan sorgulandığı için yüksek değerler taramayı belirgin şekilde yavaşlatır."
    )
    kline_limit = st.select_slider("Geçmiş veri uzunluğu (gün)", options=[200, 365, 500, 1000], value=500)
    cluster_window = st.slider(
        "Küme analizi penceresi (gün)", 30, 200, 90, step=10,
        help="Şort likidasyon kümeleri bu son N günlük hareketten hesaplanır."
    )
    min_sources = st.slider(
        "Minimum kaç borsadan veri gelsin", 1, 5, 2,
        help="Bir coin en az bu kadar borsada işlem görmüyorsa sonuçlara dahil edilmez."
    )
    min_score = st.slider("Minimum tükenme skoru", 0, 100, 30)
    run_button = st.button("🔍 Taramayı Başlat", type="primary")

    with st.expander("🔧 Tek borsa/coin testi (teşhis)"):
        st.caption("Tüm taramayı beklemeden tek bir borsa+coin kombinasyonunu test edin.")
        test_ex = st.selectbox("Borsa", api.EXCHANGES, key="test_ex")
        test_symbol = st.text_input("Coin (örn. BTC)", value="BTC", key="test_symbol")
        if st.button("Test Et"):
            try:
                df = api.debug_fetch_klines(test_ex, test_symbol.upper(), limit=5)
                st.success(f"{test_ex} / {test_symbol.upper()} — {len(df)} satır alındı")
                st.dataframe(df, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")

if "scan_results" not in st.session_state:
    st.session_state.scan_results = []

if run_button:
    try:
        with st.spinner("Hacme göre coin evreni alınıyor..."):
            base_symbols, universe_source = api.get_top_symbols_by_volume(max_symbols)
    except Exception as e:
        st.error("Hiçbir borsadan coin listesi alınamadı.")
        st.code(str(e))
        st.stop()

    st.info(f"Coin evreni **{universe_source.upper()}** hacim sıralamasından alındı "
            f"({len(base_symbols)} coin). Her coin ayrıca 5 borsadan da sorgulanacak.")

    progress_bar = st.progress(0, text="Taranıyor...")

    def _progress(i, total, sym):
        progress_bar.progress(i / total, text=f"Taranıyor: {sym} ({i}/{total})")

    results = screener.run_scan_multi(
        base_symbols, kline_limit=kline_limit, cluster_window=cluster_window,
        min_sources=min_sources, progress_callback=_progress
    )
    st.session_state.scan_results = results
    progress_bar.empty()
    st.success(f"Tarama tamamlandı. {len(results)} coin analiz edildi.")

results = st.session_state.scan_results

if results:
    table_rows = [{k: v for k, v in r.items() if k not in ("long_clusters", "short_clusters", "ohlcv")}
                   for r in results]
    df_table = pd.DataFrame(table_rows).sort_values("exhaustion_score", ascending=False)

    st.subheader("Tüm taranan coinler (skora göre sıralı — eşik uygulanmadan)")
    st.caption("Eşiği doğru kalibre edebilmeniz için skor dağılımının tamamı burada. "
               "'kaynaklar' sütunu her coin için hangi borsalardan veri alındığını gösterir.")
    st.dataframe(df_table.head(25), use_container_width=True, hide_index=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("En yüksek skor", df_table["exhaustion_score"].max())
    c2.metric("Ortalama skor", round(df_table["exhaustion_score"].mean(), 1))
    c3.metric("En düşük skor", df_table["exhaustion_score"].min())

    avg_sources = df_table["kaynak_sayisi"].mean()
    st.caption(f"Ortalama kaynak sayısı: {avg_sources:.1f} / 5 "
               f"(düşükse Bitget/Gate.io muhtemelen çoğu coin için veri döndürmüyor — "
               f"soldaki 'Tek borsa/coin testi' ile kontrol edin)")

    df_filtered = df_table[df_table["exhaustion_score"] >= min_score]

    st.subheader(f"Eşiği geçenler (skor ≥ {min_score})")
    st.dataframe(df_filtered, use_container_width=True, hide_index=True)

    if not df_filtered.empty:
        chosen = st.selectbox("Detay grafiği görmek için coin seçin:", df_filtered["symbol"].tolist())
        r = next(x for x in results if x["symbol"] == chosen)

        df = r["ohlcv"]
        short_clusters = r["short_clusters"]

        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=df["open_time"], open=df["open"], high=df["high"],
            low=df["low"], close=df["close"], name=chosen
        ))
        max_w = short_clusters["weight"].max() or 1
        for _, row in short_clusters.iterrows():
            if row["weight"] <= 0:
                continue
            opacity = min(0.9, 0.15 + 0.75 * (row["weight"] / max_w))
            fig.add_hrect(
                y0=row["price_low"], y1=row["price_high"],
                fillcolor="orange", opacity=opacity, line_width=0
            )
        fig.add_hline(y=r["price"], line_dash="dash", line_color="white",
                       annotation_text=f"Güncel Fiyat: {r['price']}")

        fig.update_layout(
            title=f"{chosen} — Tahmini Şort Likidasyon Kümeleri ({r['kaynaklar']} birleşimi)",
            height=650, xaxis_rangeslider_visible=False
        )
        st.plotly_chart(fig, use_container_width=True)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Tükenme Skoru", r["exhaustion_score"])
        c2.metric("Temizlenen Şort Likidasyon %", f"{r['short_liq_consumed_pct']}%")
        c3.metric("Yakında Kalan Şort Likidasyon %", f"{r['short_liq_remaining_near_pct']}%")
        c4.metric("Funding Rate", f"%{r['funding_rate_pct']}" if r['funding_rate_pct'] is not None else "N/A")
        c5.metric("Kaynak Sayısı", f"{r['kaynak_sayisi']}/5")
else:
    st.info("Taramayı başlatmak için soldaki 'Taramayı Başlat' butonuna basın.")

st.markdown("---")
st.caption(
    "⚠️ Bu araç yatırım tavsiyesi değildir. Likidasyon kümeleri gerçek OI/order-flow "
    "verisi yerine fiyat+hacim üzerinden istatistiksel bir TAHMİNDİR. Coinglass'ın "
    "gösterdiği haritalarla birebir örtüşmeyebilir. Kaldıraçlı işlemler yüksek risk "
    "içerir, kendi araştırmanızı yapın."
)
