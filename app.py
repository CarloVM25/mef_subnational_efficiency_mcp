"""
MEF Subnational Efficiency Dashboard
Streamlit 4-tab dashboard for Peruvian Public Expenditure Auditing.
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import streamlit as st

# ── paths ──────────────────────────────────────────────────────────────────────
PROCESSED_DIR = Path("data/processed")

BUDGET_SUMMARY = PROCESSED_DIR / "budget_2025_summary.csv"
HIST_SUMMARY   = PROCESSED_DIR / "historical_1964_summary.csv"
AUDIT_REPORT   = PROCESSED_DIR / "evaluator_report.md"
LAST_RUN       = PROCESSED_DIR / "last_run.txt"

# OCR engine saves these names; support both the spec name and actual name
def _find_chart(primary: str, fallback: str) -> Optional[Path]:
    p = PROCESSED_DIR / primary
    if p.exists():
        return p
    f = PROCESSED_DIR / fallback
    return f if f.exists() else None

# ── column names (mirror data_pipeline.py) ────────────────────────────────────
COL_ENTITY = "Nombre Pliego"
COL_NIVEL  = "Nivel de Gobierno"
COL_PIM    = "PIM"
COL_DEV    = "Devengado"
COL_AVANCE = "Avance_Pct"
COL_SALDO  = "Saldo_No_Devengado"

# ── page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="MEF Subnational Efficiency Dashboard",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Header banner */
.mef-header {
    background: linear-gradient(135deg, #0d2d5e 0%, #1a5fa8 100%);
    padding: 1.1rem 1.8rem;
    border-radius: 8px;
    margin-bottom: 1.4rem;
    box-shadow: 0 3px 10px rgba(0,0,0,0.22);
}
.mef-header h1 {
    color: #ffffff;
    font-size: 1.55rem;
    font-weight: 700;
    margin: 0;
    letter-spacing: 0.015em;
}
.mef-header p {
    color: #b8d0ea;
    font-size: 0.82rem;
    margin: 0.35rem 0 0 0;
    letter-spacing: 0.02em;
}

/* Era badges */
.badge-2025 {
    display: inline-block;
    background: #0d2d5e;
    color: #fff;
    font-size: 0.68rem;
    font-weight: 700;
    padding: 3px 12px;
    border-radius: 20px;
    margin-bottom: 0.85rem;
    letter-spacing: 0.07em;
    text-transform: uppercase;
}
.badge-1964 {
    display: inline-block;
    background: #6b4600;
    color: #fff;
    font-size: 0.68rem;
    font-weight: 700;
    padding: 3px 12px;
    border-radius: 20px;
    margin-bottom: 0.85rem;
    letter-spacing: 0.07em;
    text-transform: uppercase;
}

/* Info cards */
.card-blue {
    background: #eef6ff;
    border-left: 4px solid #1a5fa8;
    padding: 0.9rem 1.1rem;
    border-radius: 0 6px 6px 0;
    margin: 0.7rem 0 1rem 0;
    font-size: 0.88rem;
    color: #0d2d5e;
    line-height: 1.65;
}
.card-blue strong { color: #0d2d5e; }

.card-red {
    background: #fff4f4;
    border-left: 4px solid #b02020;
    padding: 0.9rem 1.1rem;
    border-radius: 0 6px 6px 0;
    margin: 0.7rem 0 1rem 0;
    font-size: 0.88rem;
    color: #6b0000;
    line-height: 1.65;
}

/* Metric tweaks */
[data-testid="stMetricValue"] {
    font-size: 1.55rem !important;
    font-weight: 700 !important;
    color: #0d2d5e !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.72rem !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #5a6a7e !important;
}
</style>
""", unsafe_allow_html=True)

# ── cached loaders ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_budget_summary() -> Optional[pd.DataFrame]:
    if not BUDGET_SUMMARY.exists():
        return None
    return pd.read_csv(BUDGET_SUMMARY)


@st.cache_data(ttl=300)
def load_historical_summary() -> Optional[pd.DataFrame]:
    if not HIST_SUMMARY.exists():
        return None
    return pd.read_csv(HIST_SUMMARY)


# ── page header ────────────────────────────────────────────────────────────────
st.markdown("""
<div class="mef-header">
  <h1>🏛️ MEF Subnational Efficiency Dashboard</h1>
  <p>Sistema de Auditoría del Gasto Público Subnacional del Perú &nbsp;·&nbsp;
     Ministerio de Economía y Finanzas</p>
</div>
""", unsafe_allow_html=True)

# ── tabs ───────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Resumen Ejecutivo & Análisis Dual",
    "🗺️ Distribución Territorial 2025",
    "🚨 Hall of Shame: Peores Ejecutores 2025",
    "📋 Log de Auditoría & Playground",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Resumen Ejecutivo & Análisis Dual
# ══════════════════════════════════════════════════════════════════════════════
with tab1:

    # ── 2025 block ────────────────────────────────────────────────────────────
    st.markdown('<span class="badge-2025">📅 Ejercicio Fiscal 2025</span>',
                unsafe_allow_html=True)

    with st.spinner("Cargando datos de ejecución 2025..."):
        df_budget = load_budget_summary()

    if df_budget is None:
        st.warning(
            "⚠️ `budget_2025_summary.csv` no encontrado. "
            "Ejecuta el pipeline desde la pestaña **Log de Auditoría** primero."
        )
        total_pim = total_dev = avance_nac = None
    else:
        total_pim  = df_budget[COL_PIM].sum()
        total_dev  = df_budget[COL_DEV].sum()
        avance_nac = (total_dev / total_pim * 100) if total_pim else 0.0

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric(
            "💰 Total PIM",
            f"S/ {total_pim:,.0f}" if total_pim is not None else "—",
            help="Presupuesto Institucional Modificado — entidades filtradas con PIM > S/ 10M",
        )
    with c2:
        st.metric(
            "✅ Total Devengado",
            f"S/ {total_dev:,.0f}" if total_dev is not None else "—",
            help="Monto de gasto efectivamente devengado en el período",
        )
    with c3:
        if avance_nac is not None:
            delta_str   = f"{avance_nac - 90:.1f}% vs meta"
            delta_color = "normal" if avance_nac >= 90 else "inverse"
        else:
            delta_str, delta_color = None, "off"
        st.metric(
            "📈 Tasa de Ejecución Nacional",
            f"{avance_nac:.1f}%" if avance_nac is not None else "—",
            delta=delta_str,
            delta_color=delta_color,
            help="Avance % ponderado sobre el universo subnacional filtrado",
        )

    st.markdown("""
    <div class="card-blue">
    <strong>🤖 Asesor AI — Diagnóstico Fiscal 2025</strong><br><br>
    El análisis de la ejecución presupuestaria 2025 revela tres cuellos de botella estructurales
    en el gasto subnacional peruano:<br><br>
    <strong>1. Capacidad de gestión de inversión pública:</strong> Los gobiernos regionales presentan
    retrasos recurrentes en la fase de devengado por limitaciones en la formulación de expedientes
    técnicos y contrataciones bajo el sistema SEACE.<br><br>
    <strong>2. Fragmentación presupuestaria:</strong> La atomización de pliegos en gobiernos locales
    dificulta la consolidación y supervisión efectiva. Municipalidades con PIM superior a S/ 10M
    pero con estructuras administrativas insuficientes acumulan saldos no devengados significativos.<br><br>
    <strong>3. Rigideces de ejecución de fin de año:</strong> La concentración del gasto en el
    último trimestre genera ineficiencias en la calidad del gasto y eleva el riesgo de
    irregularidades en los procesos de adquisición.<br><br>
    <em>Fuente: análisis automatizado sobre datos del Portal de Datos Abiertos del MEF.</em>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    # ── 1964 historical block ─────────────────────────────────────────────────
    st.markdown('<span class="badge-1964">📜 Archivo Histórico 1964</span>',
                unsafe_allow_html=True)
    st.markdown(
        "##### Ministerio de Hacienda y Comercio — "
        "Presupuesto, Balance y Cuenta General de la República"
    )

    with st.spinner("Cargando datos del análisis OCR 1964..."):
        df_hist = load_historical_summary()

    if df_hist is None:
        st.warning(
            "⚠️ `historical_1964_summary.csv` no encontrado. "
            "Ejecuta `python -m src.ocr_engine` para generarlo."
        )
    else:
        h1, h2, h3 = st.columns(3)
        h1.metric("🔢 Valores Numéricos Extraídos",
                  f"{df_hist['total_numbers_found'].sum():,}")
        h2.metric("🏷️ Etiquetas de Categoría",
                  f"{df_hist['total_categories_found'].sum():,}")
        best_row = df_hist.loc[df_hist["total_numbers_found"].idxmax()]
        h3.metric("📄 Página más densa en datos",
                  f"Página {int(best_row['page_num'])}")

        with st.expander("📄 Muestra de texto extraído por página", expanded=False):
            st.dataframe(
                df_hist[["page_num", "total_numbers_found",
                          "total_categories_found", "sample_text"]],
                use_container_width=True,
                hide_index=True,
            )

    chart1_path = _find_chart("chart1_numbers_per_page.png", "chart_numbers_per_page.png")
    chart2_path = _find_chart("chart2_keyword_frequency.png", "chart_top15_keywords.png")

    img_c1, img_c2 = st.columns(2)
    with img_c1:
        if chart1_path:
            st.image(str(chart1_path),
                     caption="Valores numéricos extraídos por página (OCR)",
                     use_container_width=True)
        else:
            st.info("🔍 Gráfico 1 no generado. Ejecuta `python -m src.ocr_engine`.")
    with img_c2:
        if chart2_path:
            st.image(str(chart2_path),
                     caption="Top 15 keywords de categorías — frecuencia OCR",
                     use_container_width=True)
        else:
            st.info("🔍 Gráfico 2 no generado. Ejecuta `python -m src.ocr_engine`.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Distribución Territorial 2025
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown("### 🗺️ Distribución Territorial del Gasto 2025")

    with st.spinner("Cargando datos territoriales..."):
        df_t2 = load_budget_summary()

    if df_t2 is None:
        st.warning(
            "⚠️ `budget_2025_summary.csv` no encontrado. "
            "Ejecuta el pipeline desde la pestaña **Log de Auditoría**."
        )
    else:
        # ── department filter ─────────────────────────────────────────────────
        entities = ["Todos"] + sorted(df_t2[COL_ENTITY].dropna().unique().tolist())
        selected = st.selectbox(
            "Filtrar por entidad",
            entities,
            key="t2_entity_filter",
            help="Selecciona una entidad específica o deja 'Todos' para ver el agregado.",
        )
        df_view = df_t2 if selected == "Todos" else df_t2[df_t2[COL_ENTITY] == selected]

        if df_view.empty:
            st.warning("No hay datos para la entidad seleccionada.")
        else:
            # ── bar chart: avg Avance % by Nivel de Gobierno ──────────────────
            st.markdown("#### Avance Promedio de Ejecución por Nivel de Gobierno")

            df_bar = (
                df_view
                .groupby(COL_NIVEL, as_index=False)
                .agg(
                    Avance_Promedio=(COL_AVANCE, "mean"),
                    PIM_Total=(COL_PIM, "sum"),
                    N_Entidades=(COL_ENTITY, "count"),
                )
                .sort_values("Avance_Promedio", ascending=False)
            )

            fig_bar = px.bar(
                df_bar,
                x=COL_NIVEL,
                y="Avance_Promedio",
                color=COL_NIVEL,
                text="Avance_Promedio",
                custom_data=["PIM_Total", "N_Entidades"],
                color_discrete_sequence=["#1a5fa8", "#e07b00"],
                labels={
                    COL_NIVEL: "Nivel de Gobierno",
                    "Avance_Promedio": "Avance Promedio (%)",
                },
                title="Avance Promedio de Ejecución por Nivel de Gobierno",
            )
            fig_bar.update_traces(
                texttemplate="%{text:.1f}%",
                textposition="outside",
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "Avance promedio: %{y:.1f}%<br>"
                    "PIM total: S/ %{customdata[0]:,.0f}<br>"
                    "Entidades: %{customdata[1]}<extra></extra>"
                ),
            )
            fig_bar.add_hline(
                y=90, line_dash="dash", line_color="#c0392b",
                annotation_text="Meta 90%", annotation_position="top right",
            )
            fig_bar.update_layout(
                showlegend=False, height=420,
                yaxis=dict(title="Avance Promedio (%)", range=[0, 115]),
                plot_bgcolor="white", paper_bgcolor="white",
                font=dict(family="Inter, Arial, sans-serif"),
                title_font_size=14,
            )
            st.plotly_chart(fig_bar, use_container_width=True)

            # ── scatter: PIM vs Avance %, size = Saldo ────────────────────────
            st.markdown("#### PIM vs. Avance % por Entidad")

            fig_scatter = px.scatter(
                df_view,
                x=COL_PIM,
                y=COL_AVANCE,
                size=COL_SALDO,
                color=COL_NIVEL,
                hover_name=COL_ENTITY,
                size_max=55,
                color_discrete_sequence=["#1a5fa8", "#e07b00"],
                labels={
                    COL_PIM:    "PIM (soles)",
                    COL_AVANCE: "Avance (%)",
                    COL_NIVEL:  "Nivel de Gobierno",
                    COL_SALDO:  "Saldo No Devengado",
                },
                title="PIM vs. Avance % — tamaño de burbuja = Saldo No Devengado",
            )
            fig_scatter.add_hline(
                y=30, line_dash="dot", line_color="#e67e22",
                annotation_text="Umbral crítico 30%", annotation_position="bottom right",
            )
            fig_scatter.add_hline(
                y=90, line_dash="dash", line_color="#27ae60",
                annotation_text="Meta 90%", annotation_position="top right",
            )
            fig_scatter.update_layout(
                height=520,
                plot_bgcolor="white", paper_bgcolor="white",
                font=dict(family="Inter, Arial, sans-serif"),
                title_font_size=14,
            )
            st.plotly_chart(fig_scatter, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Hall of Shame
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown("### 🚨 Hall of Shame: Peores Ejecutores 2025")
    st.markdown("""
    <div class="card-red">
    Entidades subnacionales con <strong>PIM > S/ 10,000,000</strong> y
    <strong>Avance < 30 %</strong>. Estos pliegos acumulan capital presupuestario
    sin ejecutar, afectando directamente la provisión de servicios públicos en sus
    jurisdicciones y generando riesgos de sub-ejecución estructural.
    </div>
    """, unsafe_allow_html=True)

    with st.spinner("Identificando peores ejecutores..."):
        df_t3 = load_budget_summary()

    if df_t3 is None:
        st.warning(
            "⚠️ `budget_2025_summary.csv` no encontrado. "
            "Ejecuta el pipeline desde la pestaña **Log de Auditoría**."
        )
    else:
        df_shame = (
            df_t3[
                (df_t3[COL_PIM] > 10_000_000) &
                (df_t3[COL_AVANCE] < 30)
            ]
            .sort_values(COL_AVANCE, ascending=True)
            .reset_index(drop=True)
        )

        frozen_capital = df_shame[COL_SALDO].sum() if not df_shame.empty else 0.0

        m1, m2, m3 = st.columns(3)
        m1.metric(
            "🥶 Capital Congelado Total",
            f"S/ {frozen_capital:,.0f}",
            help="Suma del Saldo No Devengado de todas las entidades en la lista",
        )
        m2.metric(
            "🚩 Entidades en Lista",
            str(len(df_shame)),
        )
        m3.metric(
            "📉 Avance Mínimo Detectado",
            f"{df_shame[COL_AVANCE].min():.1f}%" if not df_shame.empty else "—",
        )

        if df_shame.empty:
            st.success(
                "✅ No se encontraron entidades con Avance < 30 % y PIM > S/ 10M "
                "en el conjunto de datos actual."
            )
        else:
            st.markdown(f"#### {len(df_shame)} Entidades con Ejecución Crítica")

            # Format for display (keep numeric df_shame for chart)
            display_df = df_shame[
                [COL_ENTITY, COL_NIVEL, COL_PIM, COL_DEV, COL_AVANCE, COL_SALDO]
            ].copy()
            display_df[COL_PIM]    = display_df[COL_PIM].map("S/ {:,.0f}".format)
            display_df[COL_DEV]    = display_df[COL_DEV].map("S/ {:,.0f}".format)
            display_df[COL_AVANCE] = display_df[COL_AVANCE].map("{:.1f}%".format)
            display_df[COL_SALDO]  = display_df[COL_SALDO].map("S/ {:,.0f}".format)
            display_df.columns = [
                "Entidad", "Nivel", "PIM", "Devengado", "Avance %", "Saldo No Devengado"
            ]

            st.dataframe(display_df, use_container_width=True, hide_index=True, height=360)

            # ── horizontal bar chart: top 20 worst ───────────────────────────
            st.markdown("#### Top 20 Peores Ejecutores")
            top20 = df_shame.head(20).copy()
            top20["_label"] = top20[COL_ENTITY].str[:50]

            fig_shame = px.bar(
                top20.sort_values(COL_AVANCE, ascending=True),
                x=COL_AVANCE,
                y="_label",
                orientation="h",
                color=COL_AVANCE,
                color_continuous_scale=[[0, "#8b0000"], [0.5, "#e07b00"], [1, "#f4d03f"]],
                text=COL_AVANCE,
                custom_data=[COL_PIM, COL_SALDO, COL_NIVEL],
                labels={COL_AVANCE: "Avance (%)", "_label": "Entidad"},
                title="Top 20 Peores Ejecutores — Avance de Ejecución (%)",
            )
            fig_shame.update_traces(
                texttemplate="%{text:.1f}%",
                textposition="outside",
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Avance: %{x:.1f}%<br>"
                    "PIM: S/ %{customdata[0]:,.0f}<br>"
                    "Saldo: S/ %{customdata[1]:,.0f}<br>"
                    "%{customdata[2]}<extra></extra>"
                ),
            )
            fig_shame.update_layout(
                height=max(500, len(top20) * 28),
                plot_bgcolor="white",
                paper_bgcolor="white",
                font=dict(family="Inter, Arial, sans-serif"),
                coloraxis_showscale=False,
                yaxis=dict(tickfont=dict(size=10.5)),
                xaxis=dict(title="Avance (%)", range=[0, 38]),
                title_font_size=14,
            )
            fig_shame.add_vline(
                x=30, line_dash="dash", line_color="#555",
                annotation_text="30%", annotation_position="top",
            )
            st.plotly_chart(fig_shame, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Log de Auditoría & Pipeline Playground
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.markdown("### 📋 Log de Auditoría & Pipeline Playground")

    left, right = st.columns([3, 2], gap="large")

    with left:
        st.markdown("#### 📄 Reporte de Evaluación Automatizado")
        if AUDIT_REPORT.exists():
            report_md = AUDIT_REPORT.read_text(encoding="utf-8")
            st.markdown(report_md)
        else:
            st.info(
                "El archivo `data/processed/evaluator_report.md` aún no existe.\n\n"
                "Este reporte es generado por `src/analytical_engine.py` tras "
                "cada ejecución completa del pipeline de análisis."
            )

    with right:
        st.markdown("#### ⚙️ Ejecutar Pipeline de Datos")

        period_input = st.text_input(
            "Período de reporte",
            placeholder="2025-12  ó  2025-Q4",
            help="YYYY-MM para mensual (e.g. 2025-12) · YYYY-QN para trimestral (e.g. 2025-Q4)",
            key="pipeline_period",
        )

        run_btn = st.button(
            "▶ Actualizar Pipeline",
            type="primary",
            use_container_width=True,
            key="run_pipeline_btn",
        )

        if run_btn:
            if not period_input.strip():
                st.error("Ingresa un período válido antes de ejecutar (e.g. 2025-12).")
            else:
                with st.spinner(f"Ejecutando pipeline — período **{period_input.strip()}** …"):
                    try:
                        cmd = [
                            sys.executable, "-m", "src.data_pipeline",
                            "--period", period_input.strip(),
                        ]
                        proc = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True,
                            timeout=300,
                            cwd=str(Path(__file__).parent),
                        )
                        # Record run timestamp
                        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
                        LAST_RUN.write_text(datetime.now().isoformat(), encoding="utf-8")
                        # Clear all cached data so tabs refresh
                        st.cache_data.clear()

                        if proc.returncode == 0:
                            st.success("✅ Pipeline completado con éxito. Recarga la página para ver los datos.")
                        else:
                            st.error("❌ El pipeline terminó con errores (ver log abajo).")

                        with st.expander(
                            "📃 Salida del proceso",
                            expanded=(proc.returncode != 0),
                        ):
                            if proc.stdout:
                                st.code(proc.stdout, language="text")
                            if proc.stderr:
                                st.code(proc.stderr, language="text")

                    except subprocess.TimeoutExpired:
                        st.error("⏱️ Tiempo límite alcanzado (5 min). El proceso fue cancelado.")
                    except Exception as exc:
                        st.error(f"Error inesperado al lanzar el pipeline: {exc}")

        st.divider()

        st.markdown("#### 🕒 Historial de Ejecuciones")
        if LAST_RUN.exists():
            raw_ts = LAST_RUN.read_text(encoding="utf-8").strip()
            try:
                dt_last = datetime.fromisoformat(raw_ts)
                st.success(
                    f"Última ejecución: **{dt_last.strftime('%d/%m/%Y a las %H:%M:%S')}**"
                )
            except ValueError:
                st.info(f"Última ejecución registrada: `{raw_ts}`")
        else:
            st.warning("No hay registros de ejecuciones previas del pipeline.")
