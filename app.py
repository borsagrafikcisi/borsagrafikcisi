import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import data_fetcher as api
import screener

st.set_page_config(page_title="Şort Sıkışması Tarayıcı", layout="wide")

st.title("📉 Tahmini Şort Likidasyon Kümesi Tarayıcısı")
st.caption("🔧 Kod sürümü: v13-selectable-10-exchanges (bu satırı görüyorsanız güncel kod çalışıyor demektir)")
st.caption(f"📦 Modül sürümleri — app: v13 | {api.MODULE_VERSION} | {screener.MODULE_VERSION}")

st.markdown("""
Bu araç, Coinglass'ın **ücretli** likidasyon haritası verisi yerine, **seçtiğiniz borsaların
ücretsiz herkese açık verilerini** (fiyat + hacim) birleştirerek şort likidasyon kümelerini
**tahmin eder**. 10 borsa arasından (Binance, Bybit, OKX, Bitget, Gate.io, KuCoin, MEXC, HTX,
CoinEx, BingX) istediğinizi soldan seçip birleştirebilirsiniz. Coinglass ile birebir aynı
sonucu vermez — amaç, "şort sıkışması tükeniyor mu?" sorusuna dair bir ön filtre / fikir
üretme aracı sağlamaktır.

**Mantık:** Fiyat yükselirken art arda şort likidasyonlarını temizliyorsa ve
geride, mevcut fiyatın hemen üstünde küçük/ince bir küme kalmışsa, "tükenme
skoru" yükselir — CAP örneğinde anlattığınız senaryo budur. Seçtiğiniz borsalardan
o coin için veri olanlar toplanıp tek bir kümede birleştirilir.
""")

with st.sidebar:
    st.header("Ayarlar")

    st.subheader("Borsa Seçimi")
    selected_exchanges = st.multiselect(
        "Hangi borsaların verisi birleştirilsin?",
        options=api.EXCHANGES,
        default=api.EXCHANGES[:5],  # Binance, Bybit, OKX, Bitget, Gate.io — en test edilmiş 5'i
        help="Örn. sadece 'binance' ve 'kucoin' seçerseniz, kümeler sadece bu ikisinden "
             "birleştirilir. KuCoin/MEXC/HTX/CoinEx/BingX henüz canlı test edilmedi — "
             "önce aşağıdaki test panelinden deneyin."
    )
    if not selected_exchanges:
        st.warning("En az bir borsa seçmelisiniz.")

    st.subheader("Coin Seçimi")
    selection_mode = st.radio(
        "Coin seçim modu", ["Hacim sıralaması (sayfalı)", "Manuel liste"],
        help="Sayfalı mod: Coinglass'ın sayfaları gibi, hacim sırasına göre "
             "belirli bir aralığı tararsınız (ör. 51-100. coinler). Manuel: "
             "istediğiniz coinleri kendiniz yazarsınız."
    )
    if selection_mode == "Hacim sıralaması (sayfalı)":
        page_size = st.slider("Sayfa başına coin sayısı", 10, 100, 50, step=10)
        page_number = st.number_input(
            "Sayfa numarası (1 = en yüksek hacimliler)", min_value=1, value=1, step=1
        )
        st.caption(f"Bu tarama, hacme göre {(page_number-1)*page_size + 1}. ile "
                   f"{page_number*page_size}. sıradaki coinleri kapsayacak.")
        manual_symbols = None
    else:
        manual_text = st.text_area(
            "Coin listesi (virgülle ayırın)", value="BTC, ETH, SOL, XRP, DOGE",
            help="Örnek: BTC, ETH, SOL, XRP, DOGE"
        )
        manual_symbols = [s.strip().upper() for s in manual_text.split(",") if s.strip()]
        page_size = page_number = None

    st.subheader("Analiz Ayarları")
    kline_limit = st.select_slider("Geçmiş veri uzunluğu (gün)", options=[200, 365, 500, 1000], value=365)
    cluster_window = st.slider(
        "Küme analizi penceresi (gün)", 30, 200, 90, step=10,
        help="Şort likidasyon kümeleri bu son N günlük hareketten hesaplanır."
    )
    min_sources = st.slider(
        "Minimum kaç borsadan veri gelsin", 1, max(len(selected_exchanges), 1),
        min(2, max(len(selected_exchanges), 1)),
        help="Önerilen: seçtiğiniz borsa sayısının yarısı kadar. Seçtiğiniz TÜM borsaları "
             "şart koşarsanız (max değer), bir coin sadece o borsaların hepsinde birden "
             "bulunursa sonuçlara girer — bu genelde sonuç sayısını çok azaltır."
    )
    min_score = st.slider("Minimum tükenme skoru", 0, 100, 30)

    with st.expander("⚙️ Gelişmiş: istek gruplama ayarları"):
        st.caption("Bu ayar YUKARIDAKİ sayfa/coin seçiminden farklı — sadece seçilen coinlerin "
                   "borsalara ne hızda sorgulanacağını kontrol eder (rate-limit koruması).")
        batch_size = st.slider(
            "Grup büyüklüğü (kaç coin)", 5, 100, 25, step=5,
            help="Seçtiğiniz coinler bu büyüklükte gruplara bölünüp sırayla taranır."
        )
        batch_pause = st.slider(
            "Gruplar arası bekleme (saniye)", 0.0, 10.0, 3.0, step=0.5,
            help="Rate-limit banına takılıyorsanız bu süreyi artırın."
        )

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
    if not selected_exchanges:
        st.error("Lütfen soldan en az bir borsa seçin.")
        st.stop()

    if selection_mode == "Manuel liste":
        if not manual_symbols:
            st.error("Lütfen en az bir coin girin (örn: BTC, ETH, SOL).")
            st.stop()
        base_symbols = manual_symbols
        universe_source = "manuel liste"
        st.info(f"Manuel liste kullanılıyor: {len(base_symbols)} coin ({', '.join(base_symbols)}). "
                f"Kaynak borsalar: {', '.join(selected_exchanges)}.")
    else:
        needed = page_number * page_size
        try:
            with st.spinner("Hacme göre coin evreni alınıyor..."):
                full_universe, vol_source = api.get_top_symbols_by_volume(needed, exchanges=selected_exchanges)
        except Exception as e:
            st.error("Seçtiğiniz borsalardan coin listesi alınamadı.")
            st.code(str(e))
            st.stop()

        start = (page_number - 1) * page_size
        base_symbols = full_universe[start:start + page_size]
        universe_source = f"{vol_source} hacim sıralaması — sayfa {page_number}"

        if not base_symbols:
            st.warning(
                f"Bu sayfada ({page_number}. sayfa, {start+1}-{start+page_size} arası) hiç coin "
                f"bulunamadı — evren sadece {len(full_universe)} coin içeriyor. Daha düşük bir "
                f"sayfa numarası deneyin."
            )
            st.stop()
        elif len(base_symbols) < page_size:
            st.warning(f"Bu sayfada sadece {len(base_symbols)} coin bulunabildi (evrenin sonuna gelindi).")

        st.info(f"Coin evreni **{vol_source.upper()}** hacim sıralamasından alındı — "
                f"{page_number}. sayfa ({start+1}-{start+len(base_symbols)}. sıradaki "
                f"{len(base_symbols)} coin). Kaynak borsalar: {', '.join(selected_exchanges)}.")

    progress_bar = st.progress(0, text="Taranıyor...")
    batch_status = st.empty()

    def _progress(i, total, sym, batch_idx, total_batches):
        batch_status.caption(f"Grup {batch_idx}/{total_batches}")
        progress_bar.progress(i / total, text=f"Taranıyor: {sym} ({i}/{total})")

    results = screener.run_scan_multi(
        base_symbols, kline_limit=kline_limit, cluster_window=cluster_window,
        min_sources=min_sources, batch_size=batch_size, batch_pause=batch_pause,
        exchanges=selected_exchanges, progress_callback=_progress
    )
    st.session_state.scan_results = results
    st.session_state.scan_exchange_count = len(selected_exchanges)
    progress_bar.empty()
    batch_status.empty()
    st.success(f"Tarama tamamlandı. {len(results)} coin analiz edildi.")

results = st.session_state.scan_results
scan_exchange_count = st.session_state.get("scan_exchange_count", len(api.EXCHANGES))

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
    st.caption(f"Ortalama kaynak sayısı: {avg_sources:.1f} / {scan_exchange_count} "
               f"(düşükse yeni eklenen borsalardan biri çoğu coin için veri döndürmüyor olabilir — "
               f"soldaki 'Tek borsa/coin testi' ile kontrol edin)")

    df_filtered = df_table[df_table["exhaustion_score"] >= min_score]

    st.subheader(f"Eşiği geçenler (skor ≥ {min_score})")
    st.dataframe(df_filtered, use_container_width=True, hide_index=True)

    if not df_filtered.empty:
        chosen = st.selectbox("Detay grafiği görmek için coin seçin:", df_filtered["symbol"].tolist())
        r = next(x for x in results if x["symbol"] == chosen)

        df = r["ohlcv"]
        short_clusters = r["short_clusters"]
        long_clusters = r["long_clusters"]

        # Only keep bins that are actually meaningful clusters (top ~35%
        # by weight) so the chart shows distinct bars with gaps between
        # them — like Coinglass — instead of a uniform painted ladder.
        nonzero_short = short_clusters[short_clusters["weight"] > 0]["weight"]
        nonzero_long = long_clusters[long_clusters["weight"] > 0]["weight"]
        short_threshold = nonzero_short.quantile(0.65) if len(nonzero_short) else 0
        long_threshold = nonzero_long.quantile(0.65) if len(nonzero_long) else 0

        short_display = short_clusters[short_clusters["weight"] >= short_threshold].copy()
        long_display = long_clusters[long_clusters["weight"] >= long_threshold].copy()
        short_display["mid"] = (short_display["price_low"] + short_display["price_high"]) / 2
        long_display["mid"] = (long_display["price_low"] + long_display["price_high"]) / 2
        bin_height = (short_clusters["price_high"].iloc[0] - short_clusters["price_low"].iloc[0])

        # zoom the y-axis to where the actual clusters + recent price live,
        # so we're not stretched out to the full synthetic bin range
        price_lo = min(df["low"].tail(120).min(), short_display["mid"].min() if len(short_display) else df["low"].min())
        price_hi = max(df["high"].tail(120).max(), short_display["mid"].max() if len(short_display) else df["high"].max())
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
            title=f"{chosen} — Tahmini Likidasyon Kümeleri ({r['kaynaklar']} birleşimi)",
            height=650, barmode="overlay", showlegend=True,
            bargap=0.15,
        )
        st.plotly_chart(fig, use_container_width=True)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Tükenme Skoru", r["exhaustion_score"])
        c2.metric("Temizlenen Şort Likidasyon %", f"{r['short_liq_consumed_pct']}%")
        c3.metric("Yakında Kalan Şort Likidasyon %", f"{r['short_liq_remaining_near_pct']}%")
        c4.metric("Funding Rate", f"%{r['funding_rate_pct']}" if r['funding_rate_pct'] is not None else "N/A")
        c5.metric("Kaynak Sayısı", f"{r['kaynak_sayisi']}/{scan_exchange_count}")
else:
    st.info("Taramayı başlatmak için soldaki 'Taramayı Başlat' butonuna basın.")

st.markdown("---")
st.caption(
    "⚠️ Bu araç yatırım tavsiyesi değildir. Likidasyon kümeleri gerçek OI/order-flow "
    "verisi yerine fiyat+hacim üzerinden istatistiksel bir TAHMİNDİR. Coinglass'ın "
    "gösterdiği haritalarla birebir örtüşmeyebilir. Kaldıraçlı işlemler yüksek risk "
    "içerir, kendi araştırmanızı yapın."
)
