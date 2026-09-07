import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import data_fetcher as api
import screener

st.set_page_config(page_title="Şort Sıkışması Tarayıcı", layout="wide")

# Fixed internal parameters — not exposed in the UI, kept simple on purpose.
KLINE_LIMIT = 365       # one year of daily candles
CLUSTER_WINDOW = 90     # liquidation clusters computed from the last ~90 days
BATCH_SIZE = 25
BATCH_PAUSE = 3.0

EXCHANGE_OPTIONS = ["binance", "bybit", "okx"]

st.title("📉 Şort Likidasyon Tarayıcısı")
st.caption("🔧 Kod sürümü: v16-simplified")

st.markdown("""
Seçtiğiniz borsadaki **tüm futures coinlerini tek seferde** tarar ve yıllık likidasyon
hareketinde şort kümelerinin **sonuna yaklaşan** coinleri listeler — CAP örneğinde
anlattığınız senaryo gibi: fiyat art arda şort kümelerini temizlemiş, geride sadece
ince bir küme kalmış.

⚠️ Bu, Coinglass'ın gerçek verisi değil — o borsanın herkese açık fiyat+hacim
verisinden üretilen istatistiksel bir **tahmindir**. Bir ön filtre olarak kullanın,
son kararı Coinglass'ta kendiniz doğrulayın.
""")

with st.sidebar:
    st.header("Ayarlar")
    exchange = st.radio("Borsa", EXCHANGE_OPTIONS, format_func=lambda x: x.upper())
    min_score = st.slider("Minimum tükenme skoru", 0, 100, 30)
    run_button = st.button("🔍 Taramayı Başlat", type="primary")

    with st.expander("🔧 Tek coin testi (teşhis)"):
        st.caption("Tüm taramayı beklemeden tek bir coin'i test edin.")
        test_symbol = st.text_input("Coin (örn. BTC)", value="BTC")
        if st.button("Test Et"):
            try:
                df = api.debug_fetch_klines(exchange, test_symbol.upper(), limit=5)
                st.success(f"{exchange.upper()} / {test_symbol.upper()} — {len(df)} satır alındı")
                st.dataframe(df, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")

if "scan_results" not in st.session_state:
    st.session_state.scan_results = []
if "scan_exchange" not in st.session_state:
    st.session_state.scan_exchange = None

if run_button:
    try:
        with st.spinner(f"{exchange.upper()} coin listesi alınıyor..."):
            base_symbols = api.get_all_base_symbols(exchange)
    except Exception as e:
        st.error(f"{exchange.upper()} borsasından coin listesi alınamadı.")
        st.code(str(e))
        st.stop()

    if not base_symbols:
        st.error(f"{exchange.upper()} borsasından hiç coin bulunamadı.")
        st.stop()

    st.info(f"**{exchange.upper()}**: {len(base_symbols)} coin taranacak.")

    progress_bar = st.progress(0, text="Taranıyor...")
    batch_status = st.empty()

    def _progress(i, total, sym, batch_idx, total_batches):
        batch_status.caption(f"Grup {batch_idx}/{total_batches}")
        progress_bar.progress(min(i / total, 1.0), text=f"Taranıyor: {sym} ({i}/{total})")

    results = screener.run_scan_multi(
        base_symbols, kline_limit=KLINE_LIMIT, cluster_window=CLUSTER_WINDOW,
        min_sources=1, batch_size=BATCH_SIZE, batch_pause=BATCH_PAUSE,
        exchanges=[exchange], progress_callback=_progress
    )
    progress_bar.empty()
    batch_status.empty()

    st.session_state.scan_results = results
    st.session_state.scan_exchange = exchange
    st.success(f"Tarama tamamlandı. {len(results)} coin analiz edildi.")

results = st.session_state.scan_results
scan_exchange = st.session_state.scan_exchange or exchange

DISPLAY_COLS = ["symbol", "price", "exhaustion_score", "short_liq_consumed_pct",
                "short_liq_remaining_near_pct", "rsi_14d", "funding_rate_pct"]

if results:
    table_rows = [{k: v for k, v in r.items()
                   if k not in ("long_clusters", "short_clusters", "ohlcv", "kaynaklar", "kaynak_sayisi")}
                  for r in results]
    df_table = pd.DataFrame(table_rows).sort_values("exhaustion_score", ascending=False)

    st.subheader(f"Tüm taranan coinler ({scan_exchange.upper()}, skora göre sıralı)")
    st.dataframe(df_table[DISPLAY_COLS].head(30), use_container_width=True, hide_index=True)

    c1, c2, c3 = st.columns(3)
    c1.metric("En yüksek skor", df_table["exhaustion_score"].max())
    c2.metric("Ortalama skor", round(df_table["exhaustion_score"].mean(), 1))
    c3.metric("En düşük skor", df_table["exhaustion_score"].min())

    df_filtered = df_table[df_table["exhaustion_score"] >= min_score]

    st.subheader(f"Sona yaklaşan coinler (skor ≥ {min_score})")
    if df_filtered.empty:
        st.write("Hiçbir coin bu eşiği geçmedi. Soldan skoru düşürmeyi deneyin.")
    else:
        name_list = ", ".join(df_filtered["symbol"].tolist())
        st.text_area("📋 İsim listesi (kopyalayıp Coinglass'ta kontrol edin):", value=name_list, height=80)
        st.dataframe(df_filtered[DISPLAY_COLS], use_container_width=True, hide_index=True)

        chosen = st.selectbox("Detay grafiği görmek için coin seçin:", df_filtered["symbol"].tolist())
        r = next(x for x in results if x["symbol"] == chosen)

        df = r["ohlcv"]
        short_clusters = r["short_clusters"]
        long_clusters = r["long_clusters"]

        nonzero_short = short_clusters[short_clusters["weight"] > 0]["weight"]
        nonzero_long = long_clusters[long_clusters["weight"] > 0]["weight"]
        short_threshold = nonzero_short.quantile(0.65) if len(nonzero_short) else 0
        long_threshold = nonzero_long.quantile(0.65) if len(nonzero_long) else 0

        short_display = short_clusters[short_clusters["weight"] >= short_threshold].copy()
        long_display = long_clusters[long_clusters["weight"] >= long_threshold].copy()
        short_display["mid"] = (short_display["price_low"] + short_display["price_high"]) / 2
        long_display["mid"] = (long_display["price_low"] + long_display["price_high"]) / 2
        bin_height = (short_clusters["price_high"].iloc[0] - short_clusters["price_low"].iloc[0])

        price_lo = min(df["low"].tail(120).min(),
                        short_display["mid"].min() if len(short_display) else df["low"].min())
        price_hi = max(df["high"].tail(120).max(),
                        short_display["mid"].max() if len(short_display) else df["high"].max())
        pad = (price_hi - price_lo) * 0.08
        y_range = [price_lo - pad, price_hi + pad]

        fig = make_subplots(
            rows=1, cols=2, shared_yaxes=True,
            column_widths=[0.22, 0.78], horizontal_spacing=0.01,
        )
        fig.add_trace(go.Bar(
            y=short_display["mid"], x=short_display["weight"], orientation="h",
            marker_color="orange", name="Şort kümeleri", width=bin_height * 0.9,
        ), row=1, col=1)
        fig.add_trace(go.Bar(
            y=long_display["mid"], x=-long_display["weight"], orientation="h",
            marker_color="mediumseagreen", name="Long kümeleri", width=bin_height * 0.9,
        ), row=1, col=1)
        fig.add_trace(go.Candlestick(
            x=df["open_time"], open=df["open"], high=df["high"],
            low=df["low"], close=df["close"], name=chosen
        ), row=1, col=2)
        fig.add_hline(y=r["price"], line_dash="dash", line_color="white",
                       annotation_text=f"Güncel Fiyat: {r['price']}", row=1, col=2)

        fig.update_xaxes(autorange="reversed", showticklabels=False, row=1, col=1)
        fig.update_yaxes(range=y_range, row=1, col=1)
        fig.update_yaxes(range=y_range, row=1, col=2)
        fig.update_xaxes(rangeslider_visible=False, row=1, col=2)
        fig.update_layout(
            title=f"{chosen} — Tahmini Likidasyon Kümeleri ({scan_exchange.upper()})",
            height=650, barmode="overlay", showlegend=True, bargap=0.15,
        )
        st.plotly_chart(fig, use_container_width=True)

        c1, c2, c3 = st.columns(3)
        c1.metric("Tükenme Skoru", r["exhaustion_score"])
        c2.metric("Temizlenen Şort Likidasyon %", f"{r['short_liq_consumed_pct']}%")
        c3.metric("Yakında Kalan Şort Likidasyon %", f"{r['short_liq_remaining_near_pct']}%")
else:
    st.info("Taramayı başlatmak için soldaki 'Taramayı Başlat' butonuna basın.")

st.markdown("---")
st.caption(
    "⚠️ Bu araç yatırım tavsiyesi değildir. Likidasyon kümeleri gerçek OI/order-flow "
    "verisi yerine fiyat+hacim üzerinden istatistiksel bir TAHMİNDİR. Kaldıraçlı işlemler "
    "yüksek risk içerir, kendi araştırmanızı yapın."
)
