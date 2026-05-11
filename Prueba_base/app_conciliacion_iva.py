"""
Conciliación IVA Compras — herramienta de reconciliación mensual de IVA
=======================================================================

Cruza dos fuentes de comprobantes:
  • Listado IVA Compras (Colppy) — libro contable interno
  • Mis Comprobantes Recibidos (ARCA/AFIP) — registro fiscal oficial

Para cada comprobante del Listado determina si existe en ARCA y si los
importes (Neto, IVA y Total) coinciden dentro de una tolerancia configurable.
Produce tres vistas: conciliación completa, solo en Listado y solo en ARCA.

Este archivo es el entry point de Streamlit. Toda la lógica de negocio vive
en el paquete `conciliacion/`.
"""

import copy
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Conciliación IVA Compras",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Importaciones del paquete ─────────────────────────────────────────────────

from conciliacion.constants import (
    ALIASES_ARCA, ALIASES_LISTADO,
    BOOL_COLS,
    CAMPOS_ARCA, CAMPOS_LISTADO,
    DATA_DIR, DB_FILE, PERSIST_DIR,
    ESTADO_ICON, ICON_ESTADO,
    MAPEOS_DEFAULT, MOTIVOS_CORRECCION,
    TOLERANCIA_DEFAULT,
)
from conciliacion.utils import (
    _agg_total, _combinar_cols, _detectar_periodo, _es_nota_credito_arca,
    _hash_s1, _normalizar_col, _origen_from_cuit_tipo, _restore_bools,
)
from conciliacion.column_mapping import (
    _ARCA_FALLBACKS, _LISTADO_FALLBACKS,
    _detectar_cols_multi, sugerir_mapeo,
)
from conciliacion.file_reader import _detectar_columnas, _detectar_formato_colppy
from conciliacion.database import (
    cargar_csv, cargar_historico, cargar_mapeos, cargar_reglas,
    eliminar_regla, guardar_csv, guardar_feedback, guardar_mapeos,
    guardar_regla, init_db, listar_historicos,
)
from conciliacion.loaders import load_arca, load_listado_iva
from conciliacion.reconciler import conciliar
from conciliacion.exporter import generar_excel
from conciliacion.ui_helpers import (
    APP_CSS, _NO_MAPEAR, _csv_download, _fmt_bool, _render_mapeo_parejas,
)
from conciliacion.session import SessionManager
from conciliacion.column_mapping import build_mapping_rules
from conciliacion.fingerprint import fingerprint_dataframe

# ── CSS ───────────────────────────────────────────────────────────────────────

st.markdown(APP_CSS, unsafe_allow_html=True)

# ── Inicialización BD (una sola vez por sesión) ───────────────────────────────

if "db_initialized" not in st.session_state:
    init_db()
    st.session_state["db_initialized"] = True

# ── Lecturas DB — una sola vez por rerun, antes del sidebar ──────────────────
# Centralizar evita llamadas duplicadas en sidebar + panel de resultados.

_reglas_cuit_global                     = cargar_reglas()
_mapeos_global                          = cargar_mapeos()
_prev_s1_g, _prev_s2_g, _prev_s3_g, _prev_meta_g = cargar_csv()
_historicos_global                      = listar_historicos()

# ── Header ────────────────────────────────────────────────────────────────────

st.markdown("""
<div class="header-bar">
    <h1><span style="color:#007acc;font-size:1.4rem;margin-right:.45rem">⬡</span>Conciliación IVA Compras</h1>
    <p>Listado IVA Compras <span style="color:#858585">(Colppy)</span>
    &nbsp;<span style="color:#3e3e42">━━</span>&nbsp;
    Mis Comprobantes Recibidos <span style="color:#858585">(ARCA)</span></p>
</div>
""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### ⚙️ Parámetros")
    tolerancia = st.number_input(
        "Tolerancia ($)", min_value=0.0, max_value=10.0,
        value=TOLERANCIA_DEFAULT, step=0.01, format="%.2f",
    )

    st.markdown("### 📁 Archivos")
    local_dir  = Path(__file__).parent
    local_xlsx = (list(local_dir.glob("*.xls"))
                  + list(local_dir.glob("*.xlsx"))
                  + list(local_dir.glob("*.csv")))
    _auto_listado_path = next(
        (str(f) for f in local_xlsx if "listado" in f.name.lower() and "iva" in f.name.lower()), None
    )
    _auto_arca_path = next(
        (str(f) for f in local_xlsx if "comprobantes" in f.name.lower() and "recibidos" in f.name.lower()), None
    )

    file_listado = st.file_uploader("Subir Listado IVA", type=["xls", "xlsx", "csv"])
    file_arca    = st.file_uploader("Subir Comprobantes ARCA", type=["xls", "xlsx", "csv"])

    auto_listado = None
    if _auto_listado_path and not file_listado and not st.session_state.get("dismiss_auto_listado"):
        _c1, _c2 = st.columns([5, 1])
        with _c1:
            st.success(f"Listado: `{Path(_auto_listado_path).name}`", icon="✅")
        with _c2:
            if st.button("✕", key="btn_dismiss_listado", help="Ignorar archivo local"):
                st.session_state["dismiss_auto_listado"] = True
                for _k in ["ml_file_id", "ml_sug", "ml_conf", "ml_cols"]:
                    st.session_state.pop(_k, None)
                st.rerun()
        auto_listado = _auto_listado_path

    auto_arca = None
    if _auto_arca_path and not file_arca and not st.session_state.get("dismiss_auto_arca"):
        _c1, _c2 = st.columns([5, 1])
        with _c1:
            st.success(f"ARCA: `{Path(_auto_arca_path).name}`", icon="✅")
        with _c2:
            if st.button("✕", key="btn_dismiss_arca", help="Ignorar archivo local"):
                st.session_state["dismiss_auto_arca"] = True
                for _k in ["ma_file_id", "ma_sug", "ma_conf", "ma_cols"]:
                    st.session_state.pop(_k, None)
                st.rerun()
        auto_arca = _auto_arca_path

    src_listado = file_listado or auto_listado
    src_arca    = file_arca    or auto_arca

    # ── Autodetección de columnas al cargar nuevo archivo ─────────────────────
    _fb_map = {"ml": _LISTADO_FALLBACKS, "ma": _ARCA_FALLBACKS}
    for _fsrc, _kp, _campos, _aliases, _hkw in [
        (src_listado, "ml", CAMPOS_LISTADO, ALIASES_LISTADO, "Comprobante"),
        (src_arca,    "ma", CAMPOS_ARCA,    ALIASES_ARCA,    "Punto de Venta"),
    ]:
        if _fsrc is None:
            continue
        _fid = (
            f"{_fsrc.name}_{_fsrc.size}" if hasattr(_fsrc, "name")
            else str(_fsrc)
        )
        if not SessionManager.file_changed(_kp, _fid):
            continue

        # Invalidar estado derivado de este lado
        SessionManager.invalidate_file(_kp)

        _label = "Listado" if _kp == "ml" else "ARCA"
        with st.spinner(f"Leyendo {_label}..."):
            _cols = _detectar_columnas(_fsrc, _hkw, _fb_map[_kp])
            if hasattr(_fsrc, "seek"):
                _fsrc.seek(0)
            if _kp == "ml":
                _fmt_ml = _detectar_formato_colppy(_fsrc)
                if hasattr(_fsrc, "seek"):
                    _fsrc.seek(0)
                st.session_state["ml_formato"] = _fmt_ml
            else:
                _fmt_ml = None

        if _cols:
            _sug, _conf = sugerir_mapeo(_cols, _campos, _aliases)
            _multi_override = {"ml": ["neto", "iva", "otros_tributos_l"], "ma": ["total_iva"]}
            for _mc in _multi_override.get(_kp, []):
                _all_mc = _detectar_cols_multi(_cols, _mc)
                if len(_all_mc) > 1:
                    _sug[_mc]  = _all_mc
                    _conf[_mc] = "multi"
            st.session_state[f"{_kp}_sug"]  = _sug
            st.session_state[f"{_kp}_conf"] = _conf
            st.session_state[f"{_kp}_cols"] = _cols

            # Fingerprinting estadístico de columnas (para detección semántica y perfiles)
            _fp_key = "left_fingerprints" if _kp == "ml" else "right_fingerprints"
            try:
                import pandas as _pd
                # Leer solo el encabezado y unas filas para fingerprint rápido
                from conciliacion.file_reader import leer_excel, _mejor_hoja, _find_header
                _raw = leer_excel(_fsrc)
                if hasattr(_fsrc, "seek"):
                    _fsrc.seek(0)
                _sheet = _mejor_hoja(_raw)
                _hr    = _find_header(_sheet, _hkw, _fb_map[_kp])
                if _hr is not None:
                    _df_sample = _sheet.iloc[_hr + 1 : _hr + 51].copy()
                    _df_sample.columns = [str(c).strip() for c in _sheet.iloc[_hr]]
                    _fps = fingerprint_dataframe(_df_sample)
                    st.session_state[_fp_key] = {
                        k: v.to_dict() for k, v in _fps.items()
                    }
                if hasattr(_fsrc, "seek"):
                    _fsrc.seek(0)
            except Exception:
                pass

            if _fmt_ml == "libro":
                st.success(
                    f"📖 Libro IVA Compras detectado — {len(_cols)} columnas. "
                    "Las columnas se mapean **automáticamente**."
                )
            else:
                st.success(f"{_label} listo — {len(_cols)} columnas detectadas.", icon="✅")
        else:
            st.session_state.pop(f"{_kp}_cols", None)
            st.session_state.pop("ml_formato", None)
            st.warning(
                f"No se detectaron columnas en el {_label}. "
                "Verificá que el archivo sea el formato correcto."
            )
        if hasattr(_fsrc, "seek"):
            _fsrc.seek(0)
        SessionManager.mark_file_processed(_kp, _fid)

    # ── Reglas de memoria por CUIT ────────────────────────────────────────────
    reglas_sidebar = _reglas_cuit_global
    if reglas_sidebar:
        with st.expander(f"📋 Reglas de memoria ({len(reglas_sidebar)})", expanded=False):
            st.caption("Se aplican automáticamente a futuros comprobantes del mismo CUIT.")
            for _cuit_k, _rule in list(reglas_sidebar.items()):
                _rs  = _rule.get("razon_social", _cuit_k)
                _est = _rule.get("estado_display", _rule.get("estado", ""))
                _mot = _rule.get("motivo", "")
                _cr1, _cr2 = st.columns([5, 1])
                with _cr1:
                    st.markdown(f"**{_rs}**")
                    st.caption(f"CUIT: {_cuit_k}  ·  {_est}  ·  {_mot}")
                with _cr2:
                    if st.button("✕", key=f"del_regla_{_cuit_k}", help="Eliminar esta regla"):
                        eliminar_regla(_cuit_k)
                        st.rerun()

    procesar = st.button("▶ Procesar", type="primary", use_container_width=True)

    st.markdown("---")
    st.markdown("### 🕓 Histórico")
    prev_s1, prev_s2, prev_s3, prev_meta = _prev_s1_g, _prev_s2_g, _prev_s3_g, _prev_meta_g

    if prev_s1 is not None:
        fecha_str = str(prev_meta.get("fecha_proceso", ""))[:16].replace("T", " ")
        st.caption(f"Último proceso: {fecha_str}")
        st.caption(f"Tolerancia: ${float(prev_meta.get('tolerancia', 0)):.2f}")
        cargar_prev = st.button("Cargar último resultado", use_container_width=True)
    else:
        cargar_prev = False

    historicos = _historicos_global
    if historicos:
        st.markdown(f"**Snapshots guardados:** {len(historicos)}")
        sel_hist = st.selectbox(
            "Comparar snapshot:",
            options=[None] + historicos,
            format_func=lambda h: "— Ninguno —" if h is None else f"{h['label']} ({h['conc']}/{h['n']} conc.)",
            key="sel_hist_key",
        )
    else:
        sel_hist = None


# ── Panel de emparejamiento de columnas ──────────────────────────────────────

_cols_l = st.session_state.get("ml_cols", [])
_cols_a = st.session_state.get("ma_cols", [])
_tiene_archivos = bool(_cols_l or _cols_a)

if _tiene_archivos:
    _map_open = not st.session_state.get("loaded", False)
    with st.expander("🗂️ Emparejamiento de columnas", expanded=_map_open):
        if _cols_l and _cols_a:
            _res_l, _res_a = _render_mapeo_parejas(_cols_l, _cols_a, mapeos_db=_mapeos_global)
            st.session_state["mapeo_listado"] = {"header_keyword": "Comprobante",    "columnas": _res_l}
            st.session_state["mapeo_arca"]    = {"header_keyword": "Punto de Venta", "columnas": _res_a}
            # Construir y guardar MappingRules formales para el motor y la persistencia
            _rules = build_mapping_rules(_res_l, _res_a, tolerancia=tolerancia)
            SessionManager.set_mapping_rules(_rules)
        elif _cols_l:
            st.info("Cargá también el archivo de ARCA para ver el emparejamiento.")
        elif _cols_a:
            st.info("Cargá también el archivo del Listado (Colppy) para ver el emparejamiento.")
        else:
            st.info("Cargá ambos archivos para ver el emparejamiento de columnas.")


# ── Lógica de procesamiento ───────────────────────────────────────────────────

if procesar:
    if not src_listado or not src_arca:
        st.error("Se necesitan ambos archivos.")
        st.stop()

    df_listado = df_arca = s1 = s2 = s3 = None
    hubo_cambio = False
    with st.status("Procesando conciliación...", expanded=True) as _proc_status:
        try:
            mapeo_l = st.session_state.get("mapeo_listado", copy.deepcopy(MAPEOS_DEFAULT["listado"]["Colppy"]))
            mapeo_a = st.session_state.get("mapeo_arca",    copy.deepcopy(MAPEOS_DEFAULT["arca"]["ARCA"]))

            _raw_extras = st.session_state.get("extra_comparaciones", [])
            _extra_cols: list[dict] = []
            for _xtra in _raw_extras:
                _xid = _xtra.get("id", 0)
                _lbl = str(st.session_state.get(f"xtra_lbl_{_xid}", "")).strip()
                _cl  = st.session_state.get(f"xtra_cl_{_xid}", _NO_MAPEAR)
                _ca  = st.session_state.get(f"xtra_ca_{_xid}", _NO_MAPEAR)
                if _cl and _cl != _NO_MAPEAR and _ca and _ca != _NO_MAPEAR:
                    _extra_cols.append({
                        "label": _lbl or f"Extra_{len(_extra_cols) + 1}",
                        "col_l": _cl,
                        "col_a": _ca,
                    })
            mapeo_l = {**mapeo_l, "extra_cols": _extra_cols}
            mapeo_a = {**mapeo_a, "extra_cols": _extra_cols}

            _proc_status.update(label="Leyendo Listado IVA Compras...")
            df_listado = load_listado_iva(src_listado, mapeo_l)

            if df_listado is not None:
                _proc_status.update(label="Leyendo Mis Comprobantes ARCA...")
                df_arca = load_arca(src_arca, mapeo_a)

            if df_listado is not None and df_arca is not None:
                _proc_status.update(label="Cruzando comprobantes...")
                s1, s2, s3 = conciliar(df_listado, df_arca, tolerancia, extra_cols=_extra_cols)
                _proc_status.update(label="Guardando resultado...")
                hubo_cambio = guardar_csv(s1, s2, s3, tolerancia)
                _proc_status.update(label="Conciliación completada", state="complete", expanded=False)
            else:
                _proc_status.update(label="No se pudo procesar — revisá los errores", state="error")
        except Exception as e:
            import traceback as _tb
            print(f"[ERROR procesamiento] {_tb.format_exc()}", file=sys.stderr)
            _proc_status.update(label="Error inesperado", state="error")
            st.error(
                f"Error al procesar los archivos: **{type(e).__name__}** — {e}\n\n"
                "Verificá que los archivos sean los formatos correctos y que el "
                "emparejamiento de columnas sea el adecuado."
            )

    if df_listado is None or df_arca is None or s1 is None:
        st.stop()

    periodo_det = _detectar_periodo(s1)
    st.session_state.update({
        "s1": s1, "s2": s2, "s3": s3,
        "tol": tolerancia, "loaded": True,
        "periodo_actual":  periodo_det,
        "correcciones":    {},
        "_hist_loaded_ts": None,
        "_excel_hash":     None,
    })
    if hubo_cambio:
        st.success("Snapshot histórico guardado.")
    else:
        st.info("Sin cambios respecto al resultado anterior (no se guardó snapshot).")

elif cargar_prev and prev_s1 is not None:
    tol_prev = float(prev_meta.get("tolerancia", TOLERANCIA_DEFAULT))
    st.session_state.update({
        "s1": prev_s1, "s2": prev_s2, "s3": prev_s3,
        "tol": tol_prev, "loaded": True,
        "correcciones":    {},
        "periodo_actual":  str(prev_meta.get("periodo", "")),
        "_hist_loaded_ts": None,
        "_excel_hash":     None,
    })

elif sel_hist is not None and sel_hist["ts"] != st.session_state.get("_hist_loaded_ts"):
    try:
        s1h, s2h, s3h = cargar_historico(sel_hist["ts"])
        if s1h is None:
            raise ValueError("Snapshot no encontrado en la base de datos.")
        st.session_state.update({
            "s1": s1h, "s2": s2h, "s3": s3h,
            "tol": sel_hist["tol"], "loaded": True,
            "correcciones":    {},
            "periodo_actual":  sel_hist.get("periodo", ""),
            "_hist_loaded_ts": sel_hist["ts"],
            "_excel_hash":     None,
        })
    except Exception as e:
        st.error(f"No se pudo cargar el snapshot: {e}")


# ── Panel de resultados ───────────────────────────────────────────────────────

if st.session_state.get("loaded"):
    s1  = st.session_state["s1"]
    s2  = st.session_state["s2"]
    s3  = st.session_state["s3"]
    tol = st.session_state["tol"]

    _reglas_cuit = _reglas_cuit_global
    _reglas_norm = {
        re.sub(r"[^0-9]", "", k): v
        for k, v in _reglas_cuit.items()
        if v.get("activo", True)
    }

    # Construir s1_export con correcciones y reglas (fuente única de verdad para el Excel)
    correcciones_xl = st.session_state.get("correcciones", {})
    s1_export = s1.copy()

    # 1. Reglas de memoria (menor precedencia)
    if _reglas_norm and "CUIT_norm" in s1_export.columns:
        for _xi in s1_export[
            s1_export["CUIT_norm"].astype(str).isin(_reglas_norm)
            & ~s1_export["Comprobante"].isin(correcciones_xl)
        ].index:
            _xrule = _reglas_norm[str(s1_export.at[_xi, "CUIT_norm"])]
            s1_export.at[_xi, "Estado"] = _xrule.get("estado", s1_export.at[_xi, "Estado"])

    # 2. Correcciones manuales de sesión (mayor precedencia)
    if correcciones_xl:
        for comp_c, corr_c in correcciones_xl.items():
            mask_c = s1_export["Comprobante"] == comp_c
            s1_export.loc[mask_c, "Estado"] = ICON_ESTADO.get(
                corr_c["estado_usuario"], corr_c["estado_usuario"]
            )

    # 3. Sincronizar flag Conciliado con el Estado final
    if "Conciliado" in s1_export.columns:
        s1_export["Conciliado"] = s1_export["Estado"] == "Conciliado"

    # KPIs
    n_l       = len(s1_export)
    n_a       = len(s3) + int(s1_export["Existe_en_ARCA"].sum())
    n_conc    = int(s1_export["Conciliado"].sum())
    n_sdes    = int((s1_export["Estado"] == "Total OK / Sin desglose").sum())
    n_dif     = int((s1_export["Estado"] == "Diferencia detectada").sum())
    n_sl      = int((~s1_export["Existe_en_ARCA"]).sum())
    n_sa      = len(s3)
    n_nc      = int(s1_export.get("ARCA_es_NC", pd.Series(dtype=bool)).sum())
    n_ext     = int((s1_export["Estado"] == "Exterior / No en ARCA").sum())
    n_rev     = int((s1_export["Estado"] == "Revisado / Aceptado").sum())
    n_cuit_dif = int((s1_export["Estado"] == "CUIT no coincide").sum())

    _kpi_cuit = (
        f'<div class="kpi or"><div class="n">{n_cuit_dif}</div>'
        f'<div class="l">CUIT difiere</div></div>'
        if n_cuit_dif else ""
    )
    st.markdown(f"""
    <div class="kpi-row">
      <div class="kpi bl"><div class="n">{n_l}</div><div class="l">Listado</div></div>
      <div class="kpi bl"><div class="n">{n_a}</div><div class="l">ARCA</div></div>
      <div class="kpi ok"><div class="n">{n_conc}</div><div class="l">Conciliados</div></div>
      <div class="kpi gr"><div class="n">{n_sdes}</div><div class="l">Total OK s/desg.</div></div>
      <div class="kpi or"><div class="n">{n_dif}</div><div class="l">Con diferencias</div></div>
      {_kpi_cuit}
      <div class="kpi re"><div class="n">{n_sl}</div><div class="l">Solo Listado</div></div>
      <div class="kpi re"><div class="n">{n_sa}</div><div class="l">Solo ARCA</div></div>
      <div class="kpi pu"><div class="n">{n_nc}</div><div class="l">NC ARCA</div></div>
      <div class="kpi or"><div class="n">{n_ext}</div><div class="l">Ext. s/ARCA</div></div>
      <div class="kpi bl"><div class="n">{n_rev}</div><div class="l">Revisados</div></div>
    </div>
    """, unsafe_allow_html=True)

    n_corr   = len(correcciones_xl)
    n_reglas = int(
        s1_export[
            s1_export["CUIT_norm"].astype(str).isin(_reglas_norm)
            & ~s1_export["Comprobante"].isin(correcciones_xl)
        ].shape[0]
    ) if _reglas_norm and "CUIT_norm" in s1_export.columns else 0
    _lbl_extras = ", ".join(filter(None, [
        f"{n_corr} corregido(s)" if n_corr else "",
        f"{n_reglas} con regla 📋" if n_reglas else "",
    ]))
    lbl_xl = "⬇ Excel completo (3 hojas)" + (f"  ·  {_lbl_extras}" if _lbl_extras else "")

    # Generar Excel una sola vez por contenido (caché en session_state)
    _excel_hash = _hash_s1(s1_export)
    if st.session_state.get("_excel_hash") != _excel_hash:
        st.session_state["_excel_bytes"] = generar_excel(s1_export, s2, s3)
        st.session_state["_excel_hash"]  = _excel_hash
    _excel_bytes = st.session_state["_excel_bytes"]

    col_xl, col_inf = st.columns([2, 5])
    with col_xl:
        st.download_button(
            lbl_xl,
            data=_excel_bytes,
            file_name=f"ConciliacionIVA_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", use_container_width=True,
        )
    with col_inf:
        st.caption(
            f"Tolerancia: ±${tol:.2f}  |  {n_conc} conciliados / {n_l} totales  "
            f"|  {n_nc} NC en ARCA  |  {n_ext} exterior sin ARCA"
        )

    st.markdown("---")

    tab1, tab2, tab3 = st.tabs([
        f"Conciliación ({n_l})",
        f"Solo en Listado ({n_sl})",
        f"Solo en ARCA ({n_sa})",
    ])

    # ── Tab 1: Conciliación ───────────────────────────────────────────────────
    with tab1:
        all_estados  = list(ESTADO_ICON.values())
        all_origenes = ["Nacional", "Exterior"] if "Origen" in s1.columns else []

        fc1, fc2, fc3, fc4 = st.columns([3, 2, 2, 2])
        with fc1:
            sel_estados = st.multiselect("Estado:", all_estados, default=all_estados, key="sel_estados")
        with fc2:
            sel_orig = st.multiselect("Origen:", all_origenes, default=all_origenes, key="sel_orig") if all_origenes else []
        with fc3:
            solo_nc = st.checkbox("Solo NC (ARCA)", key="solo_nc")
        with fc4:
            solo_memoria = st.checkbox("Solo memoria 📋", key="solo_memoria",
                                       help="Muestra solo filas donde se aplicó una regla guardada")

        correcciones = st.session_state.get("correcciones", {})

        d1 = s1.copy()
        d1["Estado"] = d1["Estado"].map(ESTADO_ICON).fillna(d1["Estado"])

        if correcciones:
            d1["Estado"] = d1.apply(
                lambda r: correcciones[r["Comprobante"]]["estado_usuario"]
                          if r["Comprobante"] in correcciones else r["Estado"],
                axis=1,
            )

        d1["Memoria"] = False
        if _reglas_norm and "CUIT_norm" in d1.columns:
            for _idx in d1[
                d1["CUIT_norm"].astype(str).isin(_reglas_norm)
                & ~d1["Comprobante"].isin(correcciones)
            ].index:
                _rule = _reglas_norm[str(d1.at[_idx, "CUIT_norm"])]
                d1.at[_idx, "Estado"]  = _rule.get("estado_display",
                                            ESTADO_ICON.get(_rule.get("estado", ""), ""))
                d1.at[_idx, "Memoria"] = True

        if sel_estados:
            d1 = d1[d1["Estado"].isin(sel_estados)]
        if sel_orig and "Origen" in d1.columns:
            d1 = d1[d1["Origen"].isin(sel_orig)]
        if solo_nc and "ARCA_es_NC" in d1.columns:
            d1 = d1[d1["ARCA_es_NC"] == True]
        if solo_memoria:
            d1 = d1[d1["Memoria"] == True]

        d1_disp = _fmt_bool(d1, ["Existe_en_ARCA", "Match_Neto", "Match_IVA", "Match_Total", "Conciliado", "ARCA_es_NC", "Match_OtrosTrib"])
        if "Memoria" in d1_disp.columns:
            d1_disp["Memoria"] = d1_disp["Memoria"].map({True: "📋", False: ""})

        COL_CFG = {
            "Estado":      st.column_config.TextColumn("Estado", width="medium"),
            "Memoria":     st.column_config.TextColumn("📋", width="small"),
            "Tipo_Doc":    st.column_config.TextColumn("Tipo"),
            "Origen":      st.column_config.TextColumn("Origen", width="small"),
            "ARCA_Tipo":   st.column_config.TextColumn("Tipo ARCA"),
            "ARCA_es_NC":  st.column_config.TextColumn("NC", width="small"),
            "Neto":        st.column_config.NumberColumn("Neto Listado",  format="$ %.2f"),
            "IVA":         st.column_config.NumberColumn("IVA Listado",   format="$ %.2f"),
            "Total":       st.column_config.NumberColumn("Total Listado", format="$ %.2f"),
            "ARCA_Neto":          st.column_config.NumberColumn("Neto ARCA",       format="$ %.2f"),
            "ARCA_IVA":           st.column_config.NumberColumn("IVA ARCA",        format="$ %.2f"),
            "ARCA_OtrosTrib":     st.column_config.NumberColumn("Otros Trib ARCA", format="$ %.2f"),
            "ARCA_Total":         st.column_config.NumberColumn("Total ARCA",      format="$ %.2f"),
            "ARCA_Neto_Derivado": st.column_config.TextColumn("Neto Der.", width="small"),
            "Dif_Neto":       st.column_config.NumberColumn("Δ Neto",       format="$ %.2f"),
            "Dif_IVA":        st.column_config.NumberColumn("Δ IVA",        format="$ %.2f"),
            "Dif_Total":      st.column_config.NumberColumn("Δ Total",       format="$ %.2f"),
            "Dif_OtrosTrib":  st.column_config.NumberColumn("Δ Otros Trib.", format="$ %.2f"),
            "Match_OtrosTrib": st.column_config.TextColumn("M.OT", width="small"),
            "Existe_en_ARCA": st.column_config.TextColumn("En ARCA", width="small"),
            "Conciliado":  st.column_config.TextColumn("OK",  width="small"),
            "Match_Neto":  st.column_config.TextColumn("M.N", width="small"),
            "Match_IVA":   st.column_config.TextColumn("M.I", width="small"),
            "Match_Total": st.column_config.TextColumn("M.T", width="small"),
        }

        evento = st.dataframe(
            d1_disp, use_container_width=True, height=400, hide_index=True,
            on_select="rerun", selection_mode="single-row",
            column_config=COL_CFG,
        )

        tc1, tc2 = st.columns([2, 6])
        with tc1:
            _csv_download(
                d1_disp, f"⬇ CSV filtrado ({len(d1_disp)} filas)",
                f"conciliacion_filtrada_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            )
        with tc2:
            st.caption(
                f"{len(d1_disp)} filas  |  "
                f"Filtros: estado={len(sel_estados)}/{len(all_estados)}"
                + (f", origen={len(sel_orig)}/{len(all_origenes)}" if all_origenes else "")
                + (", solo NC" if solo_nc else "")
                + (", solo memoria 📋" if solo_memoria else "")
                + "  — Hacé clic en una fila para corregirla"
            )

        # ── Panel de corrección (aparece al seleccionar una fila) ─────────────
        filas_sel = evento.selection.rows if evento.selection else []
        if filas_sel:
            pos      = filas_sel[0]
            idx      = d1_disp.index[pos]
            fila_orig = s1.loc[idx]

            comp           = fila_orig.get("Comprobante", "")
            razon          = fila_orig.get("Razon_Social", "")
            est_algo       = ESTADO_ICON.get(fila_orig.get("Estado", ""), fila_orig.get("Estado", ""))
            est_tabla      = d1_disp.iloc[pos].get("Estado", est_algo)
            periodo_actual = st.session_state.get("periodo_actual", "")

            st.markdown("---")
            st.markdown(f"#### ✏️ Corrección — `{comp}` &nbsp; {razon}")

            pc1, pc2 = st.columns(2)
            with pc1:
                if est_tabla != est_algo:
                    st.markdown(f"**Estado algoritmo:** {est_algo}  ·  **En tabla:** {est_tabla}")
                else:
                    st.markdown(f"**Estado actual:** {est_algo}")

                meta_items = [
                    ("Fecha",         "Fecha_Factura"),
                    ("Tipo",          "Tipo_Doc"),
                    ("Origen",        "Origen"),
                    ("Condición IVA", "Condicion_IVA"),
                    ("CUIT/DNI",      "CUIT_DNI"),
                    ("ARCA Emisor",   "ARCA_Denominacion"),
                    ("Tipo ARCA",     "ARCA_Tipo"),
                    ("Fecha ARCA",    "ARCA_Fecha"),
                    ("NC ARCA",       "ARCA_es_NC"),
                    ("Neto derivado", "ARCA_Neto_Derivado"),
                ]
                meta_data = [
                    {"Campo": lbl, "Valor": str(fila_orig.get(col, ""))}
                    for lbl, col in meta_items
                    if col in fila_orig.index
                    and str(fila_orig.get(col, "")) not in ("", "nan", "None", "False")
                ]
                if meta_data:
                    st.dataframe(pd.DataFrame(meta_data), hide_index=True, use_container_width=True)

                cmp_rows = [
                    {
                        "Campo":   lbl,
                        "Listado": fila_orig.get(campo),
                        "ARCA":    fila_orig.get(f"ARCA_{campo}"),
                        "Δ":       fila_orig.get(f"Dif_{campo}"),
                    }
                    for campo, lbl in [("Neto", "Neto"), ("IVA", "IVA"), ("Total", "Total")]
                    if campo in fila_orig.index
                ]
                if cmp_rows:
                    st.dataframe(
                        pd.DataFrame(cmp_rows), hide_index=True, use_container_width=True,
                        column_config={
                            "Listado": st.column_config.NumberColumn("Listado", format="$ %.2f"),
                            "ARCA":    st.column_config.NumberColumn("ARCA",    format="$ %.2f"),
                            "Δ":       st.column_config.NumberColumn("Δ",       format="$ %.2f"),
                        },
                    )

            with pc2:
                _wkey = comp

                estados_opciones = list(ESTADO_ICON.values())
                _idx_estado      = estados_opciones.index(est_algo) if est_algo in estados_opciones else 0
                nuevo_estado = st.selectbox(
                    "Corregir estado a:", estados_opciones,
                    index=_idx_estado, key=f"corr_estado_{_wkey}",
                )
                motivo_sel = st.selectbox("Motivo:", MOTIVOS_CORRECCION, key=f"corr_motivo_{_wkey}")
                if motivo_sel == "Otro (ver nota)":
                    motivo_custom = st.text_input(
                        "Describí el motivo:", key=f"corr_motivo_custom_{_wkey}",
                        placeholder="Ej: retención IIBB no contemplada en el sistema",
                    )
                    motivo = f"Otro: {motivo_custom}" if motivo_custom.strip() else "Otro (ver nota)"
                else:
                    motivo = motivo_sel
                nota = st.text_area(
                    "Nota adicional (opcional):", height=68, key=f"corr_nota_{_wkey}",
                    placeholder="Ej: diferencia de $0.12 por redondeo en alícuota 10.5%",
                )

                cuit_corr      = str(fila_orig.get("CUIT_norm", ""))
                ya_tiene_regla = cuit_corr in _reglas_cuit

                _key_regla = f"corr_regla_{_wkey}"
                if _key_regla not in st.session_state:
                    st.session_state[_key_regla] = ya_tiene_regla

                guardar_como_regla = st.checkbox(
                    "📋 Guardar como regla para este proveedor"
                    + (" *(ya existe — se actualizará)*" if ya_tiene_regla else ""),
                    key=_key_regla,
                    help="Futuros comprobantes de este CUIT se corregirán automáticamente.",
                )

                btn_cols = st.columns(2)
                with btn_cols[0]:
                    if st.button("💾 Guardar corrección", type="primary", use_container_width=True,
                                 key=f"corr_save_{_wkey}"):
                        ok_fb = guardar_feedback(fila_orig, nuevo_estado, motivo, nota, periodo_actual)
                        if not ok_fb:
                            st.error("No se pudo guardar el feedback en la base de datos.")
                        if "correcciones" not in st.session_state:
                            st.session_state["correcciones"] = {}
                        st.session_state["correcciones"][comp] = {
                            "estado_usuario": nuevo_estado,
                            "motivo":         motivo,
                            "nota":           nota,
                        }
                        if guardar_como_regla and cuit_corr:
                            ok_r = guardar_regla(
                                cuit_corr, nuevo_estado, motivo,
                                str(fila_orig.get("Razon_Social", ""))
                            )
                            if not ok_r:
                                st.error("No se pudo guardar la regla en la base de datos.")
                        for _wk in [f"corr_estado_{_wkey}", f"corr_motivo_{_wkey}",
                                    f"corr_motivo_custom_{_wkey}", f"corr_nota_{_wkey}", _key_regla]:
                            st.session_state.pop(_wk, None)
                        st.rerun()
                with btn_cols[1]:
                    if st.button("✕ Cancelar", use_container_width=True, key=f"corr_cancel_{_wkey}"):
                        for _wk in [f"corr_estado_{_wkey}", f"corr_motivo_{_wkey}",
                                    f"corr_motivo_custom_{_wkey}", f"corr_nota_{_wkey}", _key_regla]:
                            st.session_state.pop(_wk, None)
                        st.rerun()

    # ── Tab 2: Solo en Listado ────────────────────────────────────────────────
    with tab2:
        all_orig2  = ["Nacional", "Exterior"] if "Origen" in s2.columns else []
        all_tipos2 = sorted(s2["Tipo_Doc"].dropna().unique().tolist()) if "Tipo_Doc" in s2.columns else []

        f2c1, f2c2 = st.columns(2)
        with f2c1:
            sel_orig2 = st.multiselect("Origen:", all_orig2, default=all_orig2, key="sel_orig2") if all_orig2 else []
        with f2c2:
            sel_tipo2 = st.multiselect("Tipo:", all_tipos2, default=all_tipos2, key="sel_tipo2") if all_tipos2 else []

        d2 = s2.copy()
        if sel_orig2 and "Origen" in d2.columns:
            d2 = d2[d2["Origen"].isin(sel_orig2)]
        if sel_tipo2 and "Tipo_Doc" in d2.columns:
            d2 = d2[d2["Tipo_Doc"].isin(sel_tipo2)]

        if "Origen" in d2.columns:
            resumen   = d2["Origen"].value_counts()
            cols_res  = st.columns(len(resumen))
            for i, (orig, cnt) in enumerate(resumen.items()):
                cls = "or" if orig == "Exterior" else "bl"
                cols_res[i].markdown(f"""
                <div class="kpi {cls}" style="margin:0">
                  <div class="n">{cnt}</div><div class="l">{orig}</div>
                </div>""", unsafe_allow_html=True)

        st.dataframe(
            d2, use_container_width=True, height=400, hide_index=True,
            column_config={
                "Tipo_Doc": st.column_config.TextColumn("Tipo"),
                "Origen":   st.column_config.TextColumn("Origen", width="small"),
                "Neto":     st.column_config.NumberColumn("Neto",  format="$ %.2f"),
                "IVA":      st.column_config.NumberColumn("IVA",   format="$ %.2f"),
                "Total":    st.column_config.NumberColumn("Total", format="$ %.2f"),
            },
        )

        cc1, _ = st.columns([2, 5])
        with cc1:
            _csv_download(
                d2, f"⬇ CSV filtrado ({len(d2)} filas)",
                f"solo_listado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            )

    # ── Tab 3: Solo en ARCA ───────────────────────────────────────────────────
    with tab3:
        all_tipos3 = sorted(s3["Tipo_Doc_ARCA"].dropna().unique().tolist()) if "Tipo_Doc_ARCA" in s3.columns else []
        col_nc3, col_t3 = st.columns(2)
        with col_nc3:
            solo_nc3 = st.checkbox("Solo NC", key="solo_nc3")
        with col_t3:
            sel_tipo3 = st.multiselect("Tipo:", all_tipos3, default=all_tipos3, key="sel_tipo3") if all_tipos3 else []

        d3 = s3.copy()
        if solo_nc3 and "es_NC" in d3.columns:
            d3 = d3[d3["es_NC"] == True]
        if sel_tipo3 and "Tipo_Doc_ARCA" in d3.columns:
            d3 = d3[d3["Tipo_Doc_ARCA"].isin(sel_tipo3)]

        d3_disp = _fmt_bool(d3, ["es_NC"])
        st.dataframe(
            d3_disp, use_container_width=True, height=400, hide_index=True,
            column_config={
                "es_NC":              st.column_config.TextColumn("NC", width="small"),
                "Neto Gravado Total": st.column_config.NumberColumn("Neto Gravado", format="$ %.2f"),
                "Total IVA":          st.column_config.NumberColumn("IVA",          format="$ %.2f"),
                "Imp. Total":         st.column_config.NumberColumn("Total",        format="$ %.2f"),
            },
        )

        cc1, _ = st.columns([2, 5])
        with cc1:
            _csv_download(
                d3_disp, f"⬇ CSV filtrado ({len(d3)} filas)",
                f"solo_arca_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            )

else:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Pasos:**")
        st.markdown("""
1. Subí el **Listado IVA Compras** de Colppy (`.xls` / `.xlsx`)
2. Subí **Mis Comprobantes Recibidos** de ARCA (`.xlsx`)
3. Ajustá la tolerancia (por defecto ±$0.07)
4. **▶ Procesar**
        """)
    with c2:
        st.markdown("**Funcionalidades:**")
        st.markdown("""
- Notas de Crédito en ARCA → montos en **negativo**
- Facturas del exterior (**FCC-A/B/C**) discriminadas por Origen
- Filtros **combinables** por Estado, Origen y Tipo
- **Descarga CSV** de la vista filtrada en cada pestaña
- **Persistencia histórica**: snapshot guardado solo si hay cambios
        """)
    if prev_s1 is not None:
        st.info("Hay un resultado previo disponible. Usá **Cargar último resultado** en el sidebar.")
    if historicos:
        st.info(f"Hay {len(historicos)} snapshot(s) histórico(s) disponibles para comparar.")
