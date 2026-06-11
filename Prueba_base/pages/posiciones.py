"""
Posiciones IVA
==============
Flujo:
  1. Cargar WPP → detecta empresa y períodos disponibles
  2. Elegir el mes a analizar
  3. Ver comparación WPP ↔ Posiciones para ese mes
  4. Seleccionar meses a exportar y generar el archivo
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
    extract_empresa_wpp,
    read_posiciones_iva,
    read_posiciones_pdf,
    read_wpp,
)

# ── Constantes ────────────────────────────────────────────────────────────────

DATA_DIR    = Path("data/posiciones")
DEFAULT_WPP = DATA_DIR / "3- 2026_ WPP.Beta_IVA_Paggunix.xlsx"
DEFAULT_POS = DATA_DIR / "Posiciones impuestos(1).xlsx"
DEFAULT_PDF = next(DATA_DIR.glob("*.pdf"), None) if DATA_DIR.exists() else None
_TOL_CMP    = 0.05

# Campos de comparación según fuente
_CAMPOS_CMP_XLSX = {
    "debito_fiscal":     "Débito Fiscal",
    "credito_fiscal":    "Crédito Fiscal",
    "retenciones_iva":   "Retenciones IVA",
    "percepciones_iva":  "Percepciones IVA",
    "saldo_ld_anterior": "S/F L.D. Anterior",
    "saldo_final":       "A Pagar / (A Favor)",
}
# F.2051: ret y perc vienen combinadas con pagos a cuenta
_CAMPOS_CMP_PDF = {
    "debito_fiscal":     "Débito Fiscal",
    "credito_fiscal":    "Crédito Fiscal",
    "ret_perc_total":    "Ret.+Perc. (total ARCA)",
    "saldo_ld_anterior": "S/F L.D. Anterior",
    "saldo_final":       "A Pagar / (A Favor)",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_bytes(src) -> bytes:
    if hasattr(src, "read"):
        b = src.read()
        if hasattr(src, "seek"):
            src.seek(0)
        return b
    return open(src, "rb").read()


@st.cache_data(show_spinner="Leyendo WPP…")
def _load_wpp(b: bytes, _name: str) -> DDJJData:
    return read_wpp(b)


@st.cache_data(show_spinner=False)
def _load_empresa(b: bytes, _name: str) -> str:
    return extract_empresa_wpp(b)


@st.cache_data(show_spinner="Leyendo Posiciones actuales…")
def _load_pos(b: bytes, ext: str) -> dict:
    # ext SIN prefijo "_": debe formar parte del hash de caché — los mismos
    # bytes con extensión distinta requieren un parser distinto.
    if ext == "pdf":
        return read_posiciones_pdf(b)
    return read_posiciones_iva(b)


# ── Sidebar: archivos ─────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("##### 📁 Archivos")

    wpp_upload = st.file_uploader(
        "WPP IVA (.xlsx)",
        type=["xlsx"],
        key="wpp_upload",
        help="Working Papers IVA. Un sheet por mes (MM-YYYY).",
    )
    pos_upload = st.file_uploader(
        "Posiciones — template (.xlsx)",
        type=["xlsx"],
        key="pos_upload",
        help="Archivo base de Posiciones. Se genera una copia actualizada.",
    )
    cmp_upload = st.file_uploader(
        "Comparar con (.xlsx o PDF ARCA)",
        type=["xlsx", "pdf"],
        key="cmp_upload",
        help="Excel de Posiciones existente o PDF de DDJJ descargado de ARCA.",
    )

    if wpp_upload is None and DEFAULT_WPP.exists():
        st.caption(f"WPP default: `{DEFAULT_WPP.name}`")
        wpp_source: Path | object = DEFAULT_WPP
        wpp_name = DEFAULT_WPP.name
    elif wpp_upload is not None:
        wpp_source = wpp_upload
        wpp_name = wpp_upload.name
    else:
        wpp_source = None
        wpp_name = ""

    if pos_upload is None and DEFAULT_POS.exists():
        st.caption(f"Template default: `{DEFAULT_POS.name}`")
        pos_source: Path | object = DEFAULT_POS
    elif pos_upload is not None:
        pos_source = pos_upload
    else:
        pos_source = None

    # Source for comparison (separate from template)
    if cmp_upload is not None:
        cmp_source: Path | object | None = cmp_upload
        cmp_ext = cmp_upload.name.rsplit(".", 1)[-1].lower()
    elif DEFAULT_PDF is not None and DEFAULT_PDF.exists():
        st.caption(f"Comparar default: `{DEFAULT_PDF.name}`")
        cmp_source = DEFAULT_PDF
        cmp_ext = "pdf"
    elif DEFAULT_POS.exists():
        cmp_source = DEFAULT_POS
        cmp_ext = "xlsx"
    else:
        cmp_source = None
        cmp_ext = ""

    st.divider()

    # Empresa (se llena una vez que carguemos el WPP)
    empresa_sidebar = st.empty()

# ── Guardia: WPP obligatorio ──────────────────────────────────────────────────

if wpp_source is None:
    st.info("Cargá un archivo WPP en el panel izquierdo para comenzar.")
    st.stop()

wpp_bytes = _read_bytes(wpp_source)
empresa   = _load_empresa(wpp_bytes, wpp_name)

try:
    wpp_data: DDJJData = _load_wpp(wpp_bytes, wpp_name)
except Exception as e:
    st.error(f"Error leyendo WPP: {e}")
    st.stop()

if not wpp_data:
    st.error("No se encontraron hojas de meses (MM-YYYY) en el WPP.")
    st.stop()

# Empresa en sidebar
with empresa_sidebar:
    if empresa:
        st.markdown(
            f"<div style='background:#1e3a2f;border-left:3px solid #4ec9b0;"
            f"border-radius:4px;padding:.5rem .8rem;font-size:.9rem;"
            f"color:#4ec9b0;font-weight:600'>{empresa}</div>",
            unsafe_allow_html=True,
        )

# ── Header con empresa ────────────────────────────────────────────────────────

empresa_tag = (
    f"<span style='color:#4ec9b0;font-weight:600'>{empresa}</span>"
    if empresa else ""
)
st.markdown(f"""
<div class="header-bar">
    <h1>
        <span style="color:#4ec9b0;font-size:1.4rem;margin-right:.45rem">⬡</span>
        Posiciones IVA
        {"&nbsp;<span style='color:#3e3e42;font-size:1rem'>·</span>&nbsp;" + empresa_tag if empresa else ""}
    </h1>
    <p>Determinación IVA mensual extraída del WPP · comparación con Posiciones contables</p>
</div>
""", unsafe_allow_html=True)

# ── Posiciones actuales (lectura silenciosa) ──────────────────────────────────

pos_current: dict = {}
if cmp_source is not None:
    try:
        cmp_bytes = _read_bytes(cmp_source)
        pos_current = _load_pos(cmp_bytes, cmp_ext)
    except Exception:
        pos_current = {}

# ─────────────────────────────────────────────────────────────────────────────
# PASO 1 · Elegir el período
# ─────────────────────────────────────────────────────────────────────────────

periodos       = sorted(wpp_data.keys())
periodo_labels = {(m, a): f"{MESES_ES[m]} {a}" for m, a in periodos}

st.markdown(
    "<div style='font-size:.75rem;color:#858585;text-transform:uppercase;"
    "letter-spacing:.08em;margin-bottom:.4rem'>PASO 1 · Período</div>",
    unsafe_allow_html=True,
)

# Si hay un F.2051 cargado, sugerir su período; si no, el último del WPP
_pdf_period = next(iter(pos_current), None) if cmp_ext == "pdf" and pos_current else None
_default_idx = (
    periodos.index(_pdf_period)
    if _pdf_period and _pdf_period in periodos
    else len(periodos) - 1
)

sel_mes = st.selectbox(
    "Mes a analizar:",
    options=periodos,
    index=_default_idx,
    format_func=lambda k: periodo_labels[k],
    label_visibility="collapsed",
)

# Indicador visual del período seleccionado
m_sel, a_sel = sel_mes
st.markdown(
    f"<div style='background:#252526;border:1px solid #3e3e42;border-left:4px solid #569cd6;"
    f"border-radius:4px;padding:.6rem 1rem;margin:.4rem 0 1rem;"
    f"font-size:1.05rem;color:#9cdcfe;font-weight:600'>"
    f"{MESES_ES[m_sel]} {a_sel}"
    + (f"<span style='font-size:.8rem;color:#858585;font-weight:400;margin-left:.8rem'>"
       f"{empresa}</span>" if empresa else "")
    + "</div>",
    unsafe_allow_html=True,
)

vals_mes = wpp_data[sel_mes]
vals_pos_mes = pos_current.get(sel_mes, {})

# ─────────────────────────────────────────────────────────────────────────────
# PASO 2 · Comparación del período seleccionado
# ─────────────────────────────────────────────────────────────────────────────

_cmp_label = (
    "PDF ARCA" if cmp_ext == "pdf"
    else ("Excel Posiciones" if cmp_source is not None else "sin fuente")
)
_cmp_badge = (
    f"<span style='background:#1e3a2f;color:#4ec9b0;font-size:.7rem;"
    f"border-radius:3px;padding:.15rem .4rem;margin-left:.6rem'>{_cmp_label}</span>"
    if cmp_source is not None else ""
)
st.markdown(
    f"<div style='font-size:.75rem;color:#858585;text-transform:uppercase;"
    f"letter-spacing:.08em;margin-bottom:.6rem'>PASO 2 · Comparación WPP ↔ Posiciones"
    f"{_cmp_badge}</div>",
    unsafe_allow_html=True,
)

_campos_cmp = _CAMPOS_CMP_PDF if cmp_ext == "pdf" else _CAMPOS_CMP_XLSX
_col_cmp_label = "ARCA (F.2051)" if cmp_ext == "pdf" else "Posiciones actual"

cmp_rows = []
for campo, label in _campos_cmp.items():
    if campo == "ret_perc_total":
        # WPP: suma de retenciones + percepciones (ambos positivos en WPP)
        r = vals_mes.get("retenciones_iva") or 0
        p = vals_mes.get("percepciones_iva") or 0
        v_wpp_raw = (r + p) if (r or p) else None
    else:
        v_wpp_raw = vals_mes.get(campo)

    v_pos     = vals_pos_mes.get(campo)
    v_wpp_pos = abs(v_wpp_raw) if v_wpp_raw is not None else None

    if v_wpp_pos is not None and v_pos is not None:
        diff   = v_wpp_pos - v_pos
        status = "✓" if abs(diff) <= _TOL_CMP else "≠"
    elif v_wpp_pos is not None:
        diff, status = None, "nuevo"
    else:
        diff, status = None, "—"

    cmp_rows.append({
        "Concepto":        label,
        "WPP (valor)":     v_wpp_raw,
        "WPP → Cmp":       v_wpp_pos,
        _col_cmp_label:    v_pos,
        "Diferencia":      diff,
        "Estado":          status,
    })

df_cmp = pd.DataFrame(cmp_rows)


def _color_estado(val):
    if val == "✓":    return "color:#4ec9b0;font-weight:bold"
    if val == "≠":    return "color:#f14c4c;font-weight:bold"
    if val == "nuevo": return "color:#569cd6;font-weight:bold"
    return "color:#858585"


def _color_diff(val):
    if pd.isna(val) or val is None:
        return ""
    if abs(val) <= _TOL_CMP:
        return "color:#4ec9b0"
    return "color:#f14c4c;font-weight:bold"


st.dataframe(
    df_cmp.style
        .map(_color_estado, subset=["Estado"])
        .map(_color_diff, subset=["Diferencia"])
        .format(
            {
                "WPP (valor)": "{:,.2f}",
                "WPP → Cmp":   "{:,.2f}",
                _col_cmp_label: "{:,.2f}",
                "Diferencia":  "{:+,.2f}",
            },
            na_rep="—",
        ),
    use_container_width=True,
    hide_index=True,
    height=min(340, 45 + 38 * len(df_cmp)),
    column_config={
        "WPP (valor)":   st.column_config.NumberColumn("WPP (original)", format="$ %.2f"),
        "WPP → Cmp":     st.column_config.NumberColumn("WPP (abs)", format="$ %.2f"),
        _col_cmp_label:  st.column_config.NumberColumn(_col_cmp_label, format="$ %.2f"),
        "Diferencia":    st.column_config.NumberColumn("Diferencia", format="$ %.2f"),
        "Estado":      st.column_config.TextColumn("", width="small"),
    },
)

# KPIs del período
n_ok    = (df_cmp["Estado"] == "✓").sum()
n_diff  = (df_cmp["Estado"] == "≠").sum()
n_nuevo = (df_cmp["Estado"] == "nuevo").sum()

ck1, ck2, ck3 = st.columns(3)
ck1.metric("Coinciden", n_ok)
ck2.metric("Difieren",  n_diff)
ck3.metric("Sin datos previos", n_nuevo)

# ─────────────────────────────────────────────────────────────────────────────
# PASO 3 · Generar Posiciones actualizado
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("---")
st.markdown(
    "<div style='font-size:.75rem;color:#858585;text-transform:uppercase;"
    "letter-spacing:.08em;margin-bottom:.6rem'>PASO 3 · Generar Posiciones</div>",
    unsafe_allow_html=True,
)

if pos_source is None:
    st.warning("Cargá el archivo de Posiciones (template) en el panel izquierdo.")
    st.stop()

# Selector de meses a incluir en el archivo generado
sel_exportar = st.multiselect(
    "Meses a incluir en el archivo generado:",
    options=periodos,
    default=periodos,
    format_func=lambda k: periodo_labels[k],
    help="Por defecto se incluyen todos los períodos del WPP.",
)

if not sel_exportar:
    st.warning("Seleccioná al menos un período para generar.")
    st.stop()

col_btn, col_info = st.columns([1, 3])
with col_btn:
    generar = st.button("⚙️ Generar", type="primary", use_container_width=True)
with col_info:
    st.caption(
        f"{len(sel_exportar)} mes(es) seleccionado(s)"
        + (f" · {empresa}" if empresa else "")
        + " · El archivo original no se modifica."
    )

if generar:
    wpp_filtrado = {k: wpp_data[k] for k in sel_exportar}
    with st.spinner("Actualizando Posiciones…"):
        buf = io.BytesIO()
        pos_bytes_gen = _read_bytes(pos_source)
        try:
            warns = build_posiciones(pos_bytes_gen, wpp_filtrado, buf)
            st.session_state["pos_bytes"] = buf.getvalue()
            st.session_state["pos_warns"] = warns
            st.session_state["pos_empresa"] = empresa
            st.session_state["pos_gen_key"] = (wpp_name, tuple(sorted(sel_exportar)))
        except Exception as e:
            st.error(f"Error generando Posiciones: {e}")
            st.stop()

# Mostrar la descarga solo si lo generado corresponde al WPP y a los meses
# actualmente seleccionados — evita entregar un archivo de otra selección.
_gen_key_actual = (wpp_name, tuple(sorted(sel_exportar)))
if (
    "pos_bytes" in st.session_state
    and st.session_state.get("pos_gen_key") == _gen_key_actual
):
    warns = st.session_state.get("pos_warns", {})
    _empresa_dl = st.session_state.get("pos_empresa", empresa)

    if warns:
        # Solo mostrar advertencias de concepto no encontrado (las de fuera de rango son esperadas)
        warns_real = {k: v for k, v in warns.items()
                      if any("no encontrado" in m for m in v)}
        fuera_rango = len(warns) - len(warns_real)
        if warns_real:
            with st.expander(f"⚠️ {len(warns_real)} período(s) con advertencias de mapeo", expanded=True):
                for period, msgs in sorted(warns_real.items()):
                    for msg in msgs:
                        st.warning(f"**{period}:** {msg}")
        if fuera_rango:
            st.caption(f"ℹ️ {fuera_rango} período(s) fuera del rango del template (ignorados).")
    else:
        st.success("Posiciones generado sin advertencias.")

    nombre_dl = (
        f"Posiciones_IVA_{_empresa_dl}_{pd.Timestamp.now().strftime('%Y%m')}.xlsx"
        if _empresa_dl
        else "Posiciones_IVA_actualizado.xlsx"
    )
    st.download_button(
        label=f"📥 Descargar {nombre_dl}",
        data=st.session_state["pos_bytes"],
        file_name=nombre_dl,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )
