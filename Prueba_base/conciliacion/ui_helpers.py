"""
Componentes de UI reutilizables para Streamlit.

Incluye:
  - APP_CSS: estilos CSS del tema dark (VS Code) para inyectar con st.markdown
  - _csv_download: botón de descarga CSV de la vista filtrada
  - _fmt_bool: reemplaza True/False por ✓/✗ para visualización
  - _NO_MAPEAR: constante centinela para campos opcionales no mapeados
  - _selectbox_col: dropdown de columna con soporte para opcional
  - _render_mapeo_parejas: panel completo de emparejamiento columna-a-columna

_render_mapeo_parejas es el componente más complejo de la UI. Muestra un grid
de pares [columna Listado ↔ columna ARCA] con badges de confianza. Permite:
  - Cargar y guardar perfiles de mapeo con nombre
  - Selección múltiple de columnas para campos de alícuota (Neto, IVA, Total)
  - Agregar comparaciones adicionales de alícuotas (informativas)
  - Ver columnas no utilizadas de cada archivo
"""
import streamlit as st
import pandas as pd

from .constants import (
    _ARCA_INTERNOS, _CONF_BADGE, _CONF_RANK,
    CAMPOS_ARCA, GRUPOS_PAREJAS,
)
from .database import cargar_mapeos, guardar_mapeos

# ── CSS del tema dark ─────────────────────────────────────────────────────────

APP_CSS = """
<style>
    /* ── Header ──────────────────────────────────────────────── */
    .header-bar {
        background: #252526;
        border-left: 4px solid #007acc;
        border-bottom: 1px solid #3e3e42;
        padding: 1.1rem 1.8rem;
        border-radius: 4px;
        margin-bottom: 1.1rem;
    }
    .header-bar h1 {
        margin: 0; font-size: 1.55rem; font-weight: 600;
        color: #ffffff; letter-spacing: -0.01em;
    }
    .header-bar p {
        margin: 0.3rem 0 0; font-size: 0.86rem; color: #9cdcfe;
    }

    /* ── KPI cards ───────────────────────────────────────────── */
    .kpi-row { display: flex; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 0.9rem; }
    .kpi {
        flex: 1; min-width: 96px; text-align: center;
        padding: 0.75rem 0.4rem; border-radius: 4px;
        border: 1px solid #3e3e42; background: #252526;
        transition: border-color 0.15s;
    }
    .kpi:hover { border-color: #007acc; }
    .kpi .n { font-size: 1.95rem; font-weight: 700; line-height: 1.1; }
    .kpi .l {
        font-size: 0.72rem; color: #858585;
        text-transform: uppercase; letter-spacing: .05em; margin-top: 0.2rem;
    }
    .kpi.ok .n { color: #4ec9b0; }
    .kpi.bl .n { color: #569cd6; }
    .kpi.or .n { color: #ce9178; }
    .kpi.re .n { color: #f14c4c; }
    .kpi.gr .n { color: #858585; }
    .kpi.pu .n { color: #c586c0; }

    /* ── Saved badge ─────────────────────────────────────────── */
    .saved-badge {
        background: #1e3a2f; color: #4ec9b0;
        font-size: 0.82rem; padding: 0.25rem 0.7rem;
        border-radius: 3px; font-weight: 600;
        border: 1px solid #2d6b50;
    }

    /* ── Dataframe — filas y texto más grandes ───────────────── */
    [data-testid="stDataFrame"] td,
    [data-testid="stDataFrame"] th {
        font-size: 1.0rem !important;
        padding-top: 0.45rem !important;
        padding-bottom: 0.45rem !important;
    }
    [data-testid="stDataFrame"] td:first-child {
        font-size: 1.05rem !important;
    }

    /* ── Sidebar: separadores y subtítulos estilo panel VS Code ─ */
    [data-testid="stSidebar"] h3 {
        color: #9cdcfe !important;
        font-size: 0.78rem !important;
        text-transform: uppercase;
        letter-spacing: .1em;
        border-bottom: 1px solid #3e3e42;
        padding-bottom: 4px;
        margin-bottom: 6px;
    }

    /* ── Botones primarios con acento VS Code blue ───────────── */
    [data-testid="stBaseButton-primary"] > div {
        font-size: 1.0rem !important;
        letter-spacing: 0.01em;
    }

    /* ── Expander headers más grandes ───────────────────────── */
    [data-testid="stExpander"] summary {
        font-size: 1.0rem !important;
    }
    [data-testid="stExpander"] summary span {
        font-size: 1.05rem !important;
    }

    /* ── Ocultar chrome de Streamlit ─────────────────────────── */
    #MainMenu { visibility: hidden; }
    footer     { visibility: hidden; }
</style>
"""


# ── Helpers simples ───────────────────────────────────────────────────────────

def _csv_download(df: pd.DataFrame, label: str, filename: str):
    """Botón de descarga CSV de la vista actualmente filtrada."""
    st.download_button(
        label=label,
        data=df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
        file_name=filename,
        mime="text/csv",
    )


def _fmt_bool(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    """Reemplaza True/False por ✓/✗ para visualización en la tabla."""
    df = df.copy()
    for col in cols:
        if col in df.columns:
            df[col] = df[col].map({True: "✓", False: "✗"})
    return df


# ── Panel de emparejamiento ───────────────────────────────────────────────────

_NO_MAPEAR = "— sin mapear —"


def _selectbox_col(label: str, col_sug: str, cols: list, key: str,
                   required: bool = True) -> str:
    """Dropdown de columna. Opcionales incluyen '— sin mapear —' como primera opción."""
    if not cols:
        return col_sug
    base = [c for c in cols if c != col_sug]
    if col_sug and col_sug in cols:
        opts = [col_sug] + base
    else:
        opts = ([col_sug] if col_sug else []) + base
    if not required:
        opts = [_NO_MAPEAR] + [c for c in opts if c != _NO_MAPEAR]
    sel = st.selectbox(label, opts or cols, index=0, key=key,
                       label_visibility="collapsed")
    return "" if sel == _NO_MAPEAR else sel


def _render_mapeo_parejas(cols_l: list, cols_a: list,
                          mapeos_db: dict | None = None) -> tuple[dict, dict]:
    """Panel central de emparejamiento columna-a-columna entre Listado y ARCA.

    Muestra los campos agrupados por propósito semántico. Cada fila tiene:
      [Nombre del campo] | [columna Listado ▾] | [badge confianza] | [columna ARCA ▾]

    Si cols_l es [] (Libro IVA mode), solo muestra el lado ARCA.
    Al pie muestra columnas sin usar de cada archivo.
    * = campo requerido para la conciliación; los demás son opcionales.
    Retorna (columnas_listado, columnas_arca) listos para pasar a load_*().
    """
    sug_l  = st.session_state.get("ml_sug",  {})
    sug_a  = st.session_state.get("ma_sug",  {})
    conf_l = st.session_state.get("ml_conf", {})
    conf_a = st.session_state.get("ma_conf", {})
    es_libro = st.session_state.get("ml_formato") == "libro"

    res_l: dict[str, str] = {}
    res_a: dict[str, str] = {}

    # ── Banner Libro IVA Compras ──────────────────────────────────────────────
    if es_libro and cols_l:
        st.info(
            "📖 **Libro IVA Compras** — el Comprobante se construye automáticamente "
            "desde las columnas **Suc. + Letra + Número**.  \n"
            "El resto de columnas puedes ajustar manualmente si tu archivo tiene "
            "nombres distintos.",
        )

    # ── Perfiles guardados ────────────────────────────────────────────────────
    _mapeos_db  = mapeos_db if mapeos_db is not None else cargar_mapeos()
    _perfiles   = [k for k in _mapeos_db if k not in ("listado", "arca")]
    _pc1, _pc2, _pc3 = st.columns([3, 2, 1])
    with _pc1:
        _perfil_sel = st.selectbox(
            "Cargar perfil guardado",
            options=[""] + _perfiles,
            format_func=lambda x: "— sin perfil —" if x == "" else x,
            key="perfil_sel",
            label_visibility="collapsed",
        )
    with _pc2:
        _perfil_nom = st.text_input(
            "Nombre perfil", placeholder="Nombre para guardar…",
            key="perfil_nom", label_visibility="collapsed",
        )
    with _pc3:
        _guardar_ok = st.button("💾 Guardar", key="btn_guardar_perfil",
                                use_container_width=True)

    if _perfil_sel and _perfil_sel in _mapeos_db:
        _cfg = _mapeos_db[_perfil_sel]
        if "columnas_l" in _cfg:
            sug_l  = _cfg["columnas_l"]
            conf_l = {k: "exact" for k in sug_l}
        if "columnas_a" in _cfg:
            sug_a  = _cfg["columnas_a"]
            conf_a = {k: "exact" for k in sug_a}

    st.markdown("<hr style='margin:4px 0 6px;border-color:#3e3e42'>", unsafe_allow_html=True)

    h0, h1, h2, h3 = st.columns([2.2, 3.5, 0.7, 3.5])
    h1.markdown(
        "<div style='font-size:.78rem;font-weight:700;color:#9cdcfe;"
        "text-transform:uppercase;letter-spacing:.09em;padding-bottom:3px'>"
        "Listado / Colppy</div>", unsafe_allow_html=True)
    h3.markdown(
        "<div style='font-size:.78rem;font-weight:700;color:#9cdcfe;"
        "text-transform:uppercase;letter-spacing:.09em;padding-bottom:3px'>"
        "ARCA</div>", unsafe_allow_html=True)
    st.markdown("<hr style='margin:0 0 6px;border-color:#3e3e42'>", unsafe_allow_html=True)

    n_warn_req_box = [0]
    n_warn_opt_box = [0]

    def _render_campo(campo: dict, key_prefix: str = ""):
        lc       = campo.get("l_campo")
        ac       = campo.get("a_campo")
        required = campo.get("required", True)

        cl = conf_l.get(lc, "none") if lc else "exact"
        ca = conf_a.get(ac, "none") if ac else "exact"

        if campo.get("multi_l") and lc:
            _cur = st.session_state.get(f"{key_prefix}ml_{lc}")
            if isinstance(_cur, list) and len(_cur) > 1:
                cl = "multi"
        if campo.get("multi_a") and ac:
            _cur = st.session_state.get(f"{key_prefix}ma_{ac}")
            if isinstance(_cur, list) and len(_cur) > 1:
                ca = "multi"

        worst = max(cl, ca, key=lambda x: _CONF_RANK.get(x, 4))
        badge, color, tooltip = _CONF_BADGE.get(worst, _CONF_BADGE["none"])

        if worst not in ("exact", "norm", "multi"):
            if required:
                n_warn_req_box[0] += 1
            else:
                n_warn_opt_box[0] += 1

        lbl_base = campo["label"].rstrip(" *")
        if required:
            lbl_html = (
                f"<div style='padding:6px 0;font-size:.96rem;line-height:1.4'>"
                f"<span style='color:#cccccc'>{lbl_base}</span>"
                f"<span style='color:#f14c4c;font-weight:700;font-size:.85rem'> *</span>"
                f"</div>"
            )
        else:
            lbl_html = (
                f"<div style='padding:6px 0;font-size:.92rem;line-height:1.4;"
                f"color:#858585'>{lbl_base}</div>"
            )

        c0, c1, c2, c3 = st.columns([2.2, 3.5, 0.7, 3.5])
        with c0:
            st.markdown(lbl_html, unsafe_allow_html=True)
        with c1:
            _key_l = f"{key_prefix}ml_{lc}" if lc else f"{key_prefix}ml_none"
            if campo.get("multi_l") and lc and cols_l:
                _sug     = sug_l.get(lc, "")
                _default = _sug if isinstance(_sug, list) else ([_sug] if _sug else [])
                _default = [c for c in _default if c in cols_l]
                _sel = st.multiselect(
                    lbl_base, cols_l, default=_default,
                    key=_key_l, label_visibility="collapsed",
                    placeholder="Seleccioná una o más columnas…",
                )
                if len(_sel) == 0:
                    res_l[lc] = ""
                    if required:
                        st.caption(
                            "<span style='color:#f14c4c;font-size:.8rem'>⚠ Requerido</span>",
                            unsafe_allow_html=True,
                        )
                elif len(_sel) == 1:
                    res_l[lc] = _sel[0]
                else:
                    res_l[lc] = _sel
                    st.caption(
                        f"<span style='color:#569cd6;font-size:.82rem'>"
                        f"Σ {' + '.join(_sel)}</span>",
                        unsafe_allow_html=True,
                    )
            elif lc and cols_l:
                _sug_l = sug_l.get(lc, "")
                if not required and conf_l.get(lc, "none") == "fuzzy":
                    _sug_l = ""
                res_l[lc] = _selectbox_col(lbl_base, _sug_l, cols_l, _key_l, required=required)
            elif lc:
                res_l[lc] = sug_l.get(lc, "")

        with c2:
            st.markdown(
                f"<div style='text-align:center;padding:4px 0;font-size:1.5rem;"
                f"line-height:1' title='{tooltip}'>{badge}</div>",
                unsafe_allow_html=True)

        with c3:
            _key_a = f"{key_prefix}ma_{ac}" if ac else f"{key_prefix}ma_none"
            if campo.get("a_fijo"):
                st.markdown(
                    f"<div style='padding:6px 0;font-size:.88rem;"
                    f"color:#858585;font-style:italic'>{campo['a_fijo']}</div>",
                    unsafe_allow_html=True)
                for impl in campo.get("a_implícitos", []):
                    res_a[impl] = sug_a.get(impl, CAMPOS_ARCA.get(impl, ""))
            elif campo.get("multi_a") and ac and cols_a:
                _sug     = sug_a.get(ac, "")
                _default = _sug if isinstance(_sug, list) else ([_sug] if _sug else [])
                _default = [c for c in _default if c in cols_a]
                _sel = st.multiselect(
                    lbl_base, cols_a, default=_default,
                    key=_key_a, label_visibility="collapsed",
                    placeholder="Seleccioná una o más columnas…",
                )
                if len(_sel) == 0:
                    res_a[ac] = ""
                    if required:
                        st.caption(
                            "<span style='color:#f14c4c;font-size:.8rem'>⚠ Requerido</span>",
                            unsafe_allow_html=True,
                        )
                elif len(_sel) == 1:
                    res_a[ac] = _sel[0]
                else:
                    res_a[ac] = _sel
                    st.caption(
                        f"<span style='color:#569cd6;font-size:.82rem'>"
                        f"Σ {' + '.join(_sel)}</span>",
                        unsafe_allow_html=True,
                    )
            elif ac and cols_a:
                _sug_a = sug_a.get(ac, "")
                if not required and conf_a.get(ac, "none") == "fuzzy":
                    _sug_a = ""
                res_a[ac] = _selectbox_col(lbl_base, _sug_a, cols_a, _key_a, required=required)
            elif ac:
                res_a[ac] = sug_a.get(ac, "")

    # ── Campos requeridos ─────────────────────────────────────────────────────
    for grupo in GRUPOS_PAREJAS:
        campos_req = [c for c in grupo["campos"] if c.get("required", True)]
        if not campos_req:
            continue
        st.markdown(
            f"<div style='font-size:.76rem;font-weight:700;color:#569cd6;"
            f"text-transform:uppercase;letter-spacing:.08em;"
            f"margin:12px 0 2px'>{grupo['titulo']}</div>",
            unsafe_allow_html=True)
        for campo in campos_req:
            _render_campo(campo)

        # ── Comparaciones adicionales de alícuotas (solo en grupo Importes) ──
        if "💰" in grupo.get("titulo", ""):
            if "extra_comparaciones" not in st.session_state:
                st.session_state["extra_comparaciones"] = []
            _extras  = st.session_state["extra_comparaciones"]
            _to_delete = None
            for _ei, _xtra in enumerate(_extras):
                _xid = _xtra.get("id", _ei)
                _c0, _c1, _c2, _c3, _c4 = st.columns([2.2, 3.5, 0.7, 3.5, 0.5])
                with _c0:
                    st.text_input(
                        "Etiqueta", key=f"xtra_lbl_{_xid}",
                        label_visibility="collapsed", placeholder="Etiqueta…",
                    )
                with _c1:
                    _opts_l = [_NO_MAPEAR] + (cols_l or [])
                    _prev_l = _xtra.get("col_l", "")
                    _idx_l  = _opts_l.index(_prev_l) if _prev_l in _opts_l else 0
                    st.selectbox(
                        "Listado col", _opts_l, index=_idx_l,
                        key=f"xtra_cl_{_xid}", label_visibility="collapsed",
                    )
                with _c2:
                    st.markdown(
                        "<div style='text-align:center;padding:7px 0;font-size:1.1rem;"
                        "color:#858585'>≈</div>", unsafe_allow_html=True,
                    )
                with _c3:
                    _opts_a = [_NO_MAPEAR] + (cols_a or [])
                    _prev_a = _xtra.get("col_a", "")
                    _idx_a  = _opts_a.index(_prev_a) if _prev_a in _opts_a else 0
                    st.selectbox(
                        "ARCA col", _opts_a, index=_idx_a,
                        key=f"xtra_ca_{_xid}", label_visibility="collapsed",
                    )
                with _c4:
                    if st.button("✕", key=f"xtra_del_{_xid}", help="Quitar esta comparación"):
                        _to_delete = _ei
            if _to_delete is not None:
                _extras.pop(_to_delete)
                st.session_state["extra_comparaciones"] = _extras
                st.rerun()
            _ba, _bb = st.columns([1.3, 9])
            with _ba:
                if st.button("➕ Agregar", key="btn_add_xtra",
                             help="Comparación adicional — informativa, no afecta Conciliado"):
                    _next_id = max((_x.get("id", -1) for _x in _extras), default=-1) + 1
                    _extras.append({"id": _next_id, "label": "", "col_l": "", "col_a": ""})
                    st.session_state["extra_comparaciones"] = _extras
                    st.rerun()
            with _bb:
                st.caption(
                    "<span style='color:#858585;font-size:.78rem'>"
                    "comparaciones de alícuotas — Dif_ y Match_ aparecen en la tabla, "
                    "no modifican el estado Conciliado"
                    "</span>", unsafe_allow_html=True,
                )

        st.markdown("<hr style='margin:4px 0 2px;border-color:#3e3e42'>", unsafe_allow_html=True)

    # ── Campos opcionales (colapsados) ────────────────────────────────────────
    campos_opt = [c for g in GRUPOS_PAREJAS for c in g["campos"] if not c.get("required", True)]
    if campos_opt:
        with st.expander(
            f"⚙️ Campos opcionales ({len(campos_opt)}) — fecha, tipo, razón social",
            expanded=False,
        ):
            st.caption("No afectan la conciliación. Se muestran en la tabla de resultados.")
            for campo in campos_opt:
                _render_campo(campo, key_prefix="opt_")

    # Campos ARCA internos — auto-asignados sin mostrar como pareja
    for _ic in _ARCA_INTERNOS:
        if _ic not in res_a:
            res_a[_ic] = sug_a.get(_ic, CAMPOS_ARCA.get(_ic, ""))

    n_warn_req = n_warn_req_box[0]
    n_warn_opt = n_warn_opt_box[0]

    # ── Resumen de confianza ──────────────────────────────────────────────────
    if n_warn_req:
        st.warning(
            f"⚠️ **{n_warn_req} campo(s) requerido(s)** sin coincidencia exacta — "
            "ajustá los desplegables antes de procesar.",
        )
    elif n_warn_opt:
        st.info(
            f"ℹ️ {n_warn_opt} campo(s) opcional(es) con coincidencia aproximada — "
            "podés ajustar o ignorar.",
        )
    else:
        st.success("Todos los campos requeridos emparejados con alta confianza.", icon="✅")

    st.caption(
        "<span style='color:#f14c4c;font-weight:700'>*</span>"
        " campo requerido para la conciliación",
        unsafe_allow_html=True,
    )

    # ── Guardar perfil ────────────────────────────────────────────────────────
    if _guardar_ok:
        _nom = (_perfil_nom or _perfil_sel or "").strip()
        if _nom:
            guardar_mapeos({_nom: {"columnas_l": res_l, "columnas_a": res_a}})
            st.success(f"Perfil **{_nom}** guardado.", icon="💾")
        else:
            st.warning("Ingresá un nombre para guardar el perfil.")

    # ── Columnas no utilizadas ────────────────────────────────────────────────
    def _flatten_used(d: dict) -> set:
        used: set[str] = set()
        for v in d.values():
            if isinstance(v, list):
                used.update(v)
            elif v:
                used.add(v)
        return used

    cols_l_usadas = _flatten_used(res_l)
    cols_a_usadas = _flatten_used(res_a)
    ignoradas_l   = [c for c in cols_l if c not in cols_l_usadas]
    ignoradas_a   = [c for c in cols_a if c not in cols_a_usadas]

    if ignoradas_l or ignoradas_a:
        with st.expander(
            f"Columnas sin usar — "
            f"Listado: {len(ignoradas_l)}  ·  ARCA: {len(ignoradas_a)}",
            expanded=False,
        ):
            ic1, ic2 = st.columns(2)
            with ic1:
                st.markdown("<span style='color:#9cdcfe;font-weight:600'>Listado</span>", unsafe_allow_html=True)
                for c in ignoradas_l:
                    st.markdown(f"<span style='color:#858585;font-size:.88rem'>· {c}</span>", unsafe_allow_html=True)
                if not ignoradas_l:
                    st.caption("—")
            with ic2:
                st.markdown("<span style='color:#9cdcfe;font-weight:600'>ARCA</span>", unsafe_allow_html=True)
                for c in ignoradas_a:
                    st.markdown(f"<span style='color:#858585;font-size:.88rem'>· {c}</span>", unsafe_allow_html=True)
                if not ignoradas_a:
                    st.caption("—")

    return res_l, res_a
