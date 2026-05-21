"""
Posiciones IVA
==============
Genera el archivo de Posiciones contables a partir del WPP mensual de IVA.

Flujo:
  1. Cargar el WPP (Working Papers IVA, con hojas MM-YYYY)
  2. Ver los valores extraídos por período
  3. Comparar con el archivo Posiciones actual
  4. Generar y descargar el Posiciones actualizado
"""

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from conciliacion.posiciones import (
    CAMPOS_LABELS,
    MESES_ES,
    DDJJData,
    build_posiciones,
    read_wpp,
)

# ── Configuración ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Posiciones IVA",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

DATA_DIR = Path("data/posiciones")
DEFAULT_WPP = DATA_DIR / "3- 2026_ WPP.Beta_IVA_Paggunix.xlsx"
DEFAULT_POS = DATA_DIR / "Posiciones impuestos(1).xlsx"

# ── CSS mínimo ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
  .metric-ok   { color: #15803d; font-weight: bold; }
  .metric-warn { color: #b45309; font-weight: bold; }
  .metric-err  { color: #b91c1c; font-weight: bold; }
  thead th { background-color: #1e3a5f !important; color: white !important; }
</style>
""", unsafe_allow_html=True)

# ── Título ────────────────────────────────────────────────────────────────────

st.title("📋 Posiciones IVA")
st.caption(
    "Extrae los datos DDJJ de cada período del WPP y actualiza el archivo de Posiciones."
)

# ── Sidebar: carga de archivos ────────────────────────────────────────────────

with st.sidebar:
    st.header("Archivos")

    wpp_upload = st.file_uploader(
        "WPP IVA (.xlsx)",
        type=["xlsx"],
        key="wpp_upload",
        help="Working Papers del IVA. Un sheet por mes (MM-YYYY).",
    )
    pos_upload = st.file_uploader(
        "Posiciones — template (.xlsx)",
        type=["xlsx"],
        key="pos_upload",
        help="Archivo de Posiciones contables. Se actualiza con los datos del WPP.",
    )

    # Defaults
    if wpp_upload is None and DEFAULT_WPP.exists():
        st.info(f"WPP default:\n`{DEFAULT_WPP.name}`")
        wpp_source = DEFAULT_WPP
        wpp_name = DEFAULT_WPP.name
    elif wpp_upload is not None:
        wpp_source = wpp_upload
        wpp_name = wpp_upload.name
    else:
        wpp_source = None
        wpp_name = ""

    if pos_upload is None and DEFAULT_POS.exists():
        st.info(f"Posiciones default:\n`{DEFAULT_POS.name}`")
        pos_source = DEFAULT_POS
    elif pos_upload is not None:
        pos_source = pos_upload
    else:
        pos_source = None

    st.divider()
    st.caption("Módulo independiente del Conciliador IVA.")

# ── Leer WPP ─────────────────────────────────────────────────────────────────

if wpp_source is None:
    st.info("Cargá un archivo WPP en el sidebar para comenzar.")
    st.stop()

@st.cache_data(show_spinner="Leyendo WPP…")
def _load_wpp(source_bytes: bytes, _name: str) -> DDJJData:
    return read_wpp(source_bytes)

wpp_bytes = (
    wpp_source.read() if hasattr(wpp_source, "read") else open(wpp_source, "rb").read()
)
# Re-open si se leyó el upload (los file_uploader se pueden leer una sola vez)
if hasattr(wpp_source, "seek"):
    wpp_source.seek(0)

try:
    wpp_data: DDJJData = _load_wpp(wpp_bytes, wpp_name)
except Exception as e:
    st.error(f"Error leyendo WPP: {e}")
    st.stop()

if not wpp_data:
    st.error("No se encontraron hojas de meses (MM-YYYY) en el WPP.")
    st.stop()

# ── Métricas de resumen ───────────────────────────────────────────────────────

periodos_ok = sorted(wpp_data.keys())
col1, col2, col3 = st.columns(3)
col1.metric("Períodos en WPP", len(periodos_ok))
col2.metric("Primer período", f"{MESES_ES[periodos_ok[0][0]]} {periodos_ok[0][1]}")
col3.metric("Último período",  f"{MESES_ES[periodos_ok[-1][0]]} {periodos_ok[-1][1]}")

# ── Tabla de valores extraídos ────────────────────────────────────────────────

st.subheader("Valores extraídos del WPP por período")

rows = []
for (mes, anio), vals in sorted(wpp_data.items()):
    row: dict = {"Período": f"{MESES_ES[mes]} {anio}"}
    for campo, label in CAMPOS_LABELS.items():
        v = vals.get(campo)
        row[label] = v
    rows.append(row)

df_wpp = pd.DataFrame(rows).set_index("Período")

num_cols = [c for c in df_wpp.columns]
fmt = {c: "{:,.2f}" for c in num_cols}

st.dataframe(
    df_wpp.style.format(fmt, na_rep="-"),
    use_container_width=True,
    height=min(400, 45 + 35 * len(df_wpp)),
)

with st.expander("Ver campos faltantes por período"):
    missing_rows = []
    for (mes, anio), vals in sorted(wpp_data.items()):
        faltantes = [label for campo, label in CAMPOS_LABELS.items() if campo not in vals]
        if faltantes:
            missing_rows.append({
                "Período": f"{MESES_ES[mes]} {anio}",
                "Campos faltantes": ", ".join(faltantes),
            })
    if missing_rows:
        st.dataframe(pd.DataFrame(missing_rows), use_container_width=True)
    else:
        st.success("Todos los campos encontrados en todos los períodos.")

# ── Generar Posiciones actualizado ────────────────────────────────────────────

st.divider()
st.subheader("Generar Posiciones actualizado")

if pos_source is None:
    st.warning("Cargá el archivo de Posiciones (template) en el sidebar.")
    st.stop()

col_btn, col_info = st.columns([1, 3])
with col_btn:
    generar = st.button("⚙️ Generar", type="primary", use_container_width=True)
with col_info:
    st.caption(
        "Escribe los valores del WPP en la copia del template "
        "sin modificar el archivo original."
    )

if generar:
    with st.spinner("Actualizando Posiciones…"):
        buf = io.BytesIO()
        pos_bytes = (
            pos_source.read() if hasattr(pos_source, "read")
            else open(pos_source, "rb").read()
        )
        if hasattr(pos_source, "seek"):
            pos_source.seek(0)
        try:
            warns = build_posiciones(pos_bytes, wpp_data, buf)
            st.session_state["pos_bytes"] = buf.getvalue()
            st.session_state["pos_warns"] = warns
        except Exception as e:
            st.error(f"Error generando Posiciones: {e}")
            st.stop()

if "pos_bytes" in st.session_state:
    warns = st.session_state.get("pos_warns", {})

    if warns:
        with st.expander(f"⚠️ {len(warns)} períodos con advertencias", expanded=True):
            for period, msgs in sorted(warns.items()):
                for msg in msgs:
                    st.warning(f"**{period}:** {msg}")
    else:
        st.success("Posiciones generado sin advertencias.")

    st.download_button(
        label="📥 Descargar Posiciones_IVA.xlsx",
        data=st.session_state["pos_bytes"],
        file_name="Posiciones_IVA_actualizado.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=False,
    )
