"""
Componentes de UI reutilizables para Streamlit.

Incluye:
  - APP_CSS: estilos del tema dark con chips de fórmula y badges semánticos
  - _csv_download: botón de descarga CSV de la vista filtrada
  - _fmt_bool: reemplaza True/False por ✓/✗ para visualización
  - _NO_MAPEAR: constante centinela para campos opcionales no mapeados
  - _selectbox_col: dropdown de columna con soporte para opcional
  - _render_formula_chips: preview visual de fórmula N:M con chips
  - _render_mapeo_parejas: panel completo de emparejamiento con UI de fórmulas,
                           badges semánticos y generación de MappingRules formales

Cambios arquitectónicos en _render_mapeo_parejas:
  - Genera MappingRules explícitas al retornar (no solo dicts de columnas)
  - Muestra preview de fórmula para cada regla multi-columna
  - Badges semánticos basados en fingerprints (cuando disponibles)
  - Guarda perfiles como MappingProfile completos (con fingerprints y rules)
  - Compatible con perfiles legacy (columnas_l/columnas_a)
"""
import streamlit as st
import pandas as pd

from .constants import (
    _ARCA_INTERNOS, _CONF_BADGE, _CONF_RANK,
    CAMPOS_ARCA, GRUPOS_PAREJAS,
)
from .database import cargar_mapeos, guardar_mapeos, guardar_perfil, cargar_perfiles
from .models import (
    MappingProfile, OPERATION_SYMBOLS, COMPARISON_SYMBOLS,
    SEMANTIC_ICONS, OPERATION_LABELS,
)

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

    /* ── Chips de fórmula ────────────────────────────────────── */
    .formula-preview {
        display: flex; align-items: center; flex-wrap: wrap;
        gap: 4px; padding: 4px 0 2px; font-size: .82rem;
        color: #9cdcfe; font-family: monospace;
    }
    .chip-col {
        background: #1e3a5f; color: #9cdcfe;
        border: 1px solid #2d5f8a; border-radius: 3px;
        padding: 1px 6px; font-size: .78rem; white-space: nowrap;
    }
    .chip-col-r {
        background: #1e3a2f; color: #4ec9b0;
        border: 1px solid #2d6b50; border-radius: 3px;
        padding: 1px 6px; font-size: .78rem; white-space: nowrap;
    }
    .chip-op {
        color: #ce9178; font-weight: 700; padding: 0 2px;
        font-size: .82rem;
    }
    .chip-cmp {
        color: #858585; padding: 0 4px; font-size: .88rem;
    }
    .chip-sem {
        font-size: .72rem; color: #569cd6; padding-left: 6px;
    }

    /* ── Badge de perfil sugerido ────────────────────────────── */
    .profile-suggestion {
        background: #252526; border: 1px solid #569cd6;
        border-radius: 4px; padding: 6px 10px;
        font-size: .82rem; color: #9cdcfe;
        margin-bottom: 6px;
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
        # Con sugerencia confiable, esa columna queda como default y
        # "sin mapear" pasa a segunda opción. Si el campo opcional arranca
        # sin mapear, p.ej. Tipo, la app pierde la detección de NC aunque
        # la columna exista en el archivo. (Los callers ya vacían col_sug
        # cuando la confianza es fuzzy.)
        if col_sug and col_sug in cols:
            opts = [col_sug, _NO_MAPEAR] + [c for c in base if c != _NO_MAPEAR]
        else:
            opts = [_NO_MAPEAR] + [c for c in opts if c != _NO_MAPEAR]
    sel = st.selectbox(label, opts or cols, index=0, key=key,
                       label_visibility="collapsed")
    return "" if sel == _NO_MAPEAR else sel


def _render_formula_chips(
    left_cols: list[str],
    right_cols: list[str],
    operation: str,
    comparison: str,
    semantic_type: str,
    right_operation: str | None = None,
) -> str:
    """Genera HTML de preview de fórmula con chips coloreados.

    Ejemplo:
      [IVA 21%] + [IVA 10.5%]  ≈  [Total IVA]  💰

    right_operation: operador usado para separar las columnas del lado derecho.
    Si es None, usa el mismo operador que el lado izquierdo (operation).
    Útil cuando el campo es identity (=) pero las columnas derechas son implícitas
    y siempre se concatenan con + (ej: Punto de Venta + Número Desde).
    """
    if not left_cols and not right_cols:
        return ""

    op_sym  = OPERATION_SYMBOLS.get(operation,  "+")
    r_op_sym = OPERATION_SYMBOLS.get(right_operation, op_sym) if right_operation else op_sym
    cmp_sym = COMPARISON_SYMBOLS.get(comparison, "≈")
    sem_ico = SEMANTIC_ICONS.get(semantic_type,  "")

    left_html = f" <span class='chip-op'>{op_sym}</span> ".join(
        f"<span class='chip-col'>{c}</span>" for c in left_cols
    ) if left_cols else "<span style='color:#585858'>—</span>"

    right_html = f" <span class='chip-op'>{r_op_sym}</span> ".join(
        f"<span class='chip-col-r'>{c}</span>" for c in right_cols
    ) if right_cols else "<span style='color:#585858'>—</span>"

    sem_html = f"<span class='chip-sem'>{sem_ico}</span>" if sem_ico else ""

    return (
        f"<div class='formula-preview'>"
        f"{left_html}"
        f" <span class='chip-cmp'>{cmp_sym}</span> "
        f"{right_html}"
        f"{sem_html}"
        f"</div>"
    )


def _semantic_hint_for_col(col_name: str, side: str) -> str:
    """Retorna el hint semántico de una columna desde los fingerprints en session_state."""
    fp_key = "left_fingerprints" if side == "ml" else "right_fingerprints"
    fps = st.session_state.get(fp_key, {})
    fp  = fps.get(col_name)
    if fp is None:
        return ""
    hint = fp.get("semantic_hint", "") if isinstance(fp, dict) else getattr(fp, "semantic_hint", "")
    return SEMANTIC_ICONS.get(hint, "")


def _render_mapeo_parejas(cols_l: list, cols_a: list,
                          mapeos_db: dict | None = None) -> tuple[dict, dict]:
    """Panel central de emparejamiento columna-a-columna entre Listado y ARCA.

    Cambios respecto a la versión anterior:
      - Preview de fórmula N:M para campos multi-columna (chips coloreados)
      - Badges semánticos derivados de fingerprints estadísticos
      - Sugerencia automática del mejor perfil guardado
      - Guarda perfiles como MappingProfile completo (con fingerprints y rules)
      - Retorna (res_l, res_a) igual que antes para compatibilidad total

    Si cols_l es [] (Libro IVA mode), solo muestra el lado ARCA.
    * = campo requerido para la conciliación.
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

    # ── Sugerencia automática de perfil ──────────────────────────────────────
    _perfiles_completos = []
    try:
        _perfiles_completos = cargar_perfiles()
    except Exception:
        pass

    if _perfiles_completos:
        from .fingerprint import suggest_best_profile
        _left_fps  = st.session_state.get("left_fingerprints",  {})
        _right_fps = st.session_state.get("right_fingerprints", {})
        if _left_fps or _right_fps:
            _suggestion = suggest_best_profile(_left_fps, _right_fps, _perfiles_completos)
            if _suggestion:
                _sugg_profile, _sugg_score = _suggestion
                pct = int(_sugg_score * 100)
                st.markdown(
                    f"<div class='profile-suggestion'>"
                    f"🔍 Perfil sugerido: <strong>{_sugg_profile.name}</strong> "
                    f"— similitud estructural {pct}%"
                    f"</div>",
                    unsafe_allow_html=True,
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
        operation     = campo.get("operation",     "identity")
        comparison    = campo.get("comparison",    "approx")
        semantic_type = campo.get("semantic_type", "texto")

        cl = conf_l.get(lc, "none") if lc else "exact"
        ca = conf_a.get(ac, "none") if ac else "exact"

        sel_l_cols: list[str] = []
        sel_a_cols: list[str] = []

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
        sem_icon = SEMANTIC_ICONS.get(semantic_type, "")
        if required:
            lbl_html = (
                f"<div style='padding:6px 0;font-size:.96rem;line-height:1.4'>"
                f"<span style='color:#cccccc'>{lbl_base}</span>"
                f"<span style='color:#f14c4c;font-weight:700;font-size:.85rem'> *</span>"
                f"<span style='color:#569cd6;font-size:.72rem;margin-left:4px'>{sem_icon}</span>"
                f"</div>"
            )
        else:
            lbl_html = (
                f"<div style='padding:6px 0;font-size:.92rem;line-height:1.4;"
                f"color:#858585'>{lbl_base} {sem_icon}</div>"
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
                sel_l_cols = _sel
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
            elif lc and cols_l:
                _sug_l = sug_l.get(lc, "")
                if not required and conf_l.get(lc, "none") == "fuzzy":
                    _sug_l = ""
                chosen = _selectbox_col(lbl_base, _sug_l, cols_l, _key_l, required=required)
                res_l[lc] = chosen
                sel_l_cols = [chosen] if chosen else []
            elif lc:
                v = sug_l.get(lc, "")
                res_l[lc] = v
                sel_l_cols = [v] if v else []

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
                    v = sug_a.get(impl, CAMPOS_ARCA.get(impl, ""))
                    res_a[impl] = v
                    sel_a_cols.append(v)
            elif campo.get("multi_a") and ac and cols_a:
                _sug     = sug_a.get(ac, "")
                _default = _sug if isinstance(_sug, list) else ([_sug] if _sug else [])
                _default = [c for c in _default if c in cols_a]
                _sel = st.multiselect(
                    lbl_base, cols_a, default=_default,
                    key=_key_a, label_visibility="collapsed",
                    placeholder="Seleccioná una o más columnas…",
                )
                sel_a_cols = _sel
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
            elif ac and cols_a:
                _sug_a = sug_a.get(ac, "")
                if not required and conf_a.get(ac, "none") == "fuzzy":
                    _sug_a = ""
                chosen = _selectbox_col(lbl_base, _sug_a, cols_a, _key_a, required=required)
                res_a[ac] = chosen
                sel_a_cols = [chosen] if chosen else []
            elif ac:
                v = sug_a.get(ac, "")
                res_a[ac] = v
                sel_a_cols = [v] if v else []

        # ── Preview de fórmula (solo para multi-columna o suma explícita) ─────
        is_multi = len(sel_l_cols) > 1 or len(sel_a_cols) > 1
        is_sum   = operation == "sum" and (len(sel_l_cols) > 0 or len(sel_a_cols) > 0)
        has_impl = bool(campo.get("a_implícitos"))
        if is_multi or (is_sum and (sel_l_cols or sel_a_cols)) or has_impl:
            # Columnas implícitas (ej: Pto. Venta + Número) siempre se unen con "+"
            r_op = "concat" if (has_impl and len(sel_a_cols) > 1) else None
            chips_html = _render_formula_chips(
                sel_l_cols, sel_a_cols, operation, comparison, semantic_type,
                right_operation=r_op,
            )
            if chips_html:
                st.markdown(chips_html, unsafe_allow_html=True)

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

    # ── Guardar perfil como MappingProfile completo ───────────────────────────
    if _guardar_ok:
        _nom = (_perfil_nom or _perfil_sel or "").strip()
        if _nom:
            _now = __import__("datetime").datetime.now().isoformat()
            _left_fps  = st.session_state.get("left_fingerprints",  {})
            _right_fps = st.session_state.get("right_fingerprints", {})
            # Construir MappingRules formales
            from .column_mapping import build_mapping_rules
            _rules = build_mapping_rules(res_l, res_a)
            _profile = MappingProfile(
                id=_nom,
                name=_nom,
                rules=_rules,
                left_fingerprint={
                    k: (v.to_dict() if hasattr(v, "to_dict") else v)
                    for k, v in _left_fps.items()
                },
                right_fingerprint={
                    k: (v.to_dict() if hasattr(v, "to_dict") else v)
                    for k, v in _right_fps.items()
                },
                aliases={},
                created_at=_now,
                updated_at=_now,
            )
            guardar_perfil(_profile)
            # Compat: también en mapeos legacy
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
                    icon = _semantic_hint_for_col(c, "ml")
                    st.markdown(
                        f"<span style='color:#858585;font-size:.88rem'>· {c} {icon}</span>",
                        unsafe_allow_html=True,
                    )
                if not ignoradas_l:
                    st.caption("—")
            with ic2:
                st.markdown("<span style='color:#9cdcfe;font-weight:600'>ARCA</span>", unsafe_allow_html=True)
                for c in ignoradas_a:
                    icon = _semantic_hint_for_col(c, "ma")
                    st.markdown(
                        f"<span style='color:#858585;font-size:.88rem'>· {c} {icon}</span>",
                        unsafe_allow_html=True,
                    )
                if not ignoradas_a:
                    st.caption("—")

    return res_l, res_a
