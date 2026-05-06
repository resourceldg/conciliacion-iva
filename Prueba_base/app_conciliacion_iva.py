"""
Conciliación IVA Compras — herramienta de reconciliación mensual de IVA
=======================================================================

Cruza dos fuentes de comprobantes:
  • Listado IVA Compras (Colppy) — libro contable interno
  • Mis Comprobantes Recibidos (ARCA/AFIP) — registro fiscal oficial

Para cada comprobante del Listado determina si existe en ARCA y si los
importes (Neto, IVA y Total) coinciden dentro de una tolerancia configurable.
Produce tres vistas: conciliación completa, solo en Listado y solo en ARCA.
"""

import streamlit as st
import pandas as pd
from io import BytesIO, StringIO
import os, sys, subprocess, tempfile, shutil, json, difflib, copy, re, unicodedata, sqlite3
from datetime import datetime
from pathlib import Path

st.set_page_config(
    page_title="Conciliación IVA Compras",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Constantes ────────────────────────────────────────────────────────────────

TOLERANCIA_DEFAULT = 0.07

DATA_DIR    = Path(__file__).parent / "data"
PERSIST_DIR = DATA_DIR / "persistencia"
DATA_DIR.mkdir(exist_ok=True)
PERSIST_DIR.mkdir(exist_ok=True)

DB_FILE = DATA_DIR / "conciliacion_iva.db"

# Rutas legacy — solo usadas por _migrar_legacy() en la primera ejecución
CSV_CONC    = DATA_DIR / "conciliacion_last.csv"
CSV_LIST    = DATA_DIR / "solo_listado_last.csv"
CSV_ARCA    = DATA_DIR / "solo_arca_last.csv"
CSV_META    = DATA_DIR / "meta_last.csv"
REGLAS_FILE = DATA_DIR / "reglas_cuit.json"

# Columnas booleanas: necesitan conversión explícita al releer desde CSV
BOOL_COLS = {"Existe_en_ARCA", "Match_Neto", "Match_IVA", "Match_Total", "Conciliado", "es_NC"}

# Tipos de comprobante que indican Nota de Crédito
NC_ARCA    = {"nota de crédito", "nota de credito"}   # ARCA (campo "Tipo")
NC_LISTADO = {"ncc-a", "ncc-b", "ncc-c", "nc-a", "nc-b", "nc-c"}  # Colppy

# Tipos de Colppy que corresponden a facturas del exterior (FCC = Factura Compra Exterior)
EXT_PREFIXES = {"fcc-a", "fcc-b", "fcc-c", "fce-a", "fce-b", "fce-c"}

# Mapa Estado → etiqueta con ícono para la UI; ICON_ESTADO es el inverso
ESTADO_ICON = {
    "Conciliado":              "✅ Conciliado",
    "Total OK / Sin desglose": "🟡 Total OK s/desglose",
    "Diferencia detectada":    "🔴 Diferencia",
    "Sin match en ARCA":       "⬜ Sin match",
    "Revisado / Aceptado":     "✔️ Revisado / Aceptado",
}
ICON_ESTADO = {v: k for k, v in ESTADO_ICON.items()}

# ── Mapeo de columnas ─────────────────────────────────────────────────────────

MAPEOS_FILE   = DATA_DIR / "mapeos.json"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"

MOTIVOS_CORRECCION = [
    "Factura de exterior (no registrada en ARCA)",
    "Neto mal imputado en el sistema contable",
    "Diferencia de tipo de cambio",
    "Percepción no registrada / mal imputada",
    "IVA de alícuota diferente",
    "Error en punto de venta o número de comprobante",
    "Comprobante duplicado",
    "Diferencia de redondeo aceptable",
    "Retención / deducción no contabilizada",
    "Otro (ver nota)",
]

# Campos semánticos → nombre de columna por defecto en cada fuente
CAMPOS_LISTADO = {
    "comprobante":   "Comprobante",
    "fecha":         "Fecha Factura",
    "tipo":          "Tipo",
    "cuit":          "CUIT/DNI",
    "razon_social":  "Razón Social",
    "condicion_iva": "Condición IVA",
    "neto":          "Neto",
    "iva":           "IVA",
    "total":         "Total",
}
CAMPOS_ARCA = {
    "punto_venta":     "Punto de Venta",
    "numero":          "Número Desde",
    "fecha":           "Fecha",
    "tipo":            "Tipo",
    "cuit_emisor":     "Nro. Doc. Emisor",
    "denominacion":    "Denominación Emisor",
    "neto_gravado":    "Neto Gravado Total",
    "neto_no_gravado": "Neto No Gravado",
    "op_exentas":      "Op. Exentas",
    "otros_tributos":  "Otros Tributos",
    "total_iva":       "Total IVA",
    "total":           "Imp. Total",
}

# Etiquetas legibles para la UI de mapeo
LABEL_LISTADO = {
    "comprobante": "Comprobante", "fecha": "Fecha", "tipo": "Tipo",
    "cuit": "CUIT/DNI", "razon_social": "Razón Social",
    "condicion_iva": "Condición IVA", "neto": "Neto", "iva": "IVA", "total": "Total",
}
LABEL_ARCA = {
    "punto_venta": "Punto de Venta", "numero": "Número",
    "fecha": "Fecha", "tipo": "Tipo", "cuit_emisor": "CUIT Emisor",
    "denominacion": "Denominación", "neto_gravado": "Neto Gravado",
    "neto_no_gravado": "Neto No Gravado", "op_exentas": "Op. Exentas",
    "otros_tributos": "Otros Tributos", "total_iva": "Total IVA", "total": "Total",
}

# Sinónimos conocidos por campo semántico — permiten detección determinista cuando
# el proveedor cambia el nombre de una columna (ej: ARCA usa a veces "Código de Comprobante")
ALIASES_LISTADO: dict[str, list[str]] = {
    "comprobante":   ["comprobante", "nro comprobante", "numero comprobante", "comp",
                      "cod comprobante", "codigo comprobante"],
    "fecha":         ["fecha", "fecha factura", "fecha emision", "fecha_factura"],
    "tipo":          ["tipo", "tipo comprobante", "tipo doc", "clase", "codigo", "cod"],
    "cuit":          ["cuit", "cuit/dni", "cuit dni", "nro doc", "documento",
                      "nro documento", "nrodoc"],
    "razon_social":  ["razon social", "razon", "proveedor", "nombre", "denominacion"],
    "condicion_iva": ["condicion iva", "cond iva", "condicion", "condicioniva"],
    "neto":          ["neto", "importe neto", "monto neto", "neto gravado"],
    "iva":           ["iva", "importe iva", "monto iva"],
    "total":         ["total", "importe total", "monto total", "total factura"],
}

ALIASES_ARCA: dict[str, list[str]] = {
    "punto_venta":     ["punto de venta", "pto venta", "pto de venta", "punto venta", "ptoventa"],
    "numero":          ["numero desde", "nro desde", "numero", "nro",
                        "num desde", "numero comprobante"],
    "fecha":           ["fecha", "fecha cbte", "fecha comprobante"],
    "tipo":            ["tipo", "tipo comprobante", "codigo de comprobante",
                        "cod comprobante", "codigo", "clase"],
    "cuit_emisor":     ["nro doc emisor", "nro. doc. emisor", "cuit emisor", "cuit",
                        "documento emisor", "cuit/dni emisor"],
    "denominacion":    ["denominacion emisor", "denominación emisor",
                        "razon social emisor", "denominacion", "razon social"],
    "neto_gravado":    ["neto gravado total", "neto gravado", "neto grav total", "neto grav"],
    "neto_no_gravado": ["neto no gravado", "neto no grav"],
    "op_exentas":      ["op exentas", "op. exentas", "operaciones exentas", "exentas"],
    "otros_tributos":  ["otros tributos", "otros trib", "tributos"],
    "total_iva":       ["total iva", "iva", "imp iva", "importe iva"],
    "total":           ["imp total", "imp. total", "total", "importe total", "total importe"],
}

MAPEOS_DEFAULT = {
    "listado": {"Colppy": {"header_keyword": "Comprobante",    "columnas": copy.deepcopy(CAMPOS_LISTADO)}},
    "arca":    {"ARCA":   {"header_keyword": "Punto de Venta", "columnas": copy.deepcopy(CAMPOS_ARCA)}},
}

# Grupos de emparejamiento entre columnas de ambos archivos.
# Cada entrada define qué campo semántico del Listado se cruza con cuál del ARCA.
# a_fijo=True → la columna ARCA es construida automáticamente (no hay dropdown).
GRUPOS_PAREJAS = [
    {
        "titulo":  "🔑 Identificación",
        "detalle": "Determinan qué comprobante del Listado corresponde a cuál de ARCA",
        "campos": [
            {
                "label":   "Comprobante",
                "l_campo": "comprobante",
                "a_campo": None,
                "a_fijo":  "Pto. Venta + Número (construido automáticamente)",
                "a_implícitos": ["punto_venta", "numero"],
            },
            {
                "label":   "CUIT proveedor",
                "l_campo": "cuit",
                "a_campo": "cuit_emisor",
            },
        ],
    },
    {
        "titulo":  "💰 Importes comparados",
        "detalle": "Diferencias sobre estos valores determinan el estado de cada comprobante",
        "campos": [
            {"label": "Neto",  "l_campo": "neto",  "a_campo": "neto_gravado"},
            {"label": "IVA",   "l_campo": "iva",   "a_campo": "total_iva"},
            {"label": "Total", "l_campo": "total", "a_campo": "total"},
        ],
    },
    {
        "titulo":  "ℹ️ Información adicional",
        "detalle": "Visible en la tabla de resultados, no usada en el cruce",
        "campos": [
            {"label": "Fecha",         "l_campo": "fecha",       "a_campo": "fecha"},
            {"label": "Tipo",          "l_campo": "tipo",        "a_campo": "tipo"},
            {"label": "Razón Social",  "l_campo": "razon_social","a_campo": "denominacion"},
        ],
    },
]

# Campos ARCA que se usan internamente pero no se muestran como parejas
_ARCA_INTERNOS = ["neto_no_gravado", "op_exentas", "otros_tributos"]

_CONF_RANK  = {"exact": 0, "norm": 1, "alias": 2, "fuzzy": 3, "none": 4}
_CONF_BADGE = {
    "exact": ("✅", "#15803d", "Coincidencia exacta"),
    "norm":  ("✅", "#15803d", "Normalizado — coincide"),
    "alias": ("🟡", "#b45309", "Sinónimo conocido — verificá"),
    "fuzzy": ("🟠", "#c2410c", "Coincidencia aproximada — revisá"),
    "none":  ("❌", "#dc2626", "Sin match — seleccioná manualmente"),
}

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .header-bar {
        background: linear-gradient(135deg, #1e3a5f 0%, #2d6a9f 100%);
        padding: 1.1rem 2rem; border-radius: 10px;
        margin-bottom: 1rem; color: white;
    }
    .header-bar h1 { margin: 0; font-size: 1.5rem; font-weight: 700; }
    .header-bar p  { margin: 0.2rem 0 0; font-size: 0.82rem; opacity: 0.8; }

    .kpi-row { display: flex; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 0.8rem; }
    .kpi {
        flex: 1; min-width: 90px; text-align: center;
        padding: 0.65rem 0.4rem; border-radius: 8px;
        border: 1px solid #e2e8f0; background: #f8fafc;
    }
    .kpi .n { font-size: 1.7rem; font-weight: 700; line-height: 1.1; }
    .kpi .l { font-size: 0.68rem; color: #64748b; text-transform: uppercase; letter-spacing:.04em; }
    .kpi.ok .n { color: #16a34a; }
    .kpi.bl .n { color: #2563eb; }
    .kpi.or .n { color: #d97706; }
    .kpi.re .n { color: #dc2626; }
    .kpi.gr .n { color: #475569; }
    .kpi.pu .n { color: #7c3aed; }

    .saved-badge {
        background: #dcfce7; color: #15803d; font-size: 0.78rem;
        padding: 0.2rem 0.6rem; border-radius: 4px; font-weight: 600;
    }
    #MainMenu { visibility: hidden; }
    footer     { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ── Funciones de mapeo y feedback ────────────────────────────────────────────

# ── SQLite — capa de acceso a datos ───────────────────────────────────────────

def _db_con() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    """Crea las tablas si no existen y migra archivos legacy en la primera ejecución."""
    con = _db_con()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS conciliaciones (
            ts            TEXT PRIMARY KEY,
            periodo       TEXT,
            tolerancia    REAL,
            n_listado     INTEGER,
            n_arca        INTEGER,
            conciliados   INTEGER,
            diferencias   INTEGER,
            fecha_proceso TEXT,
            s1_json       TEXT,
            s2_json       TEXT,
            s3_json       TEXT
        );
        CREATE TABLE IF NOT EXISTS reglas_cuit (
            cuit_norm      TEXT PRIMARY KEY,
            estado         TEXT,
            estado_display TEXT,
            motivo         TEXT,
            razon_social   TEXT,
            creado         TEXT,
            activo         INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS mapeos (
            nombre      TEXT PRIMARY KEY,
            config_json TEXT,
            actualizado TEXT
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ts               TEXT,
            periodo          TEXT,
            comprobante      TEXT,
            cuit_dni         TEXT,
            razon_social     TEXT,
            estado_algoritmo TEXT,
            estado_usuario   TEXT,
            motivo           TEXT,
            nota             TEXT,
            data_json        TEXT
        );
    """)
    con.commit()
    con.close()
    _migrar_legacy()


def _migrar_legacy():
    """Importa archivos legacy (JSON/CSV/JSONL) a SQLite. Corre solo una vez."""
    marker = DATA_DIR / ".db_migrated"
    if marker.exists():
        return
    con = _db_con()
    try:
        # reglas_cuit.json
        if REGLAS_FILE.exists():
            try:
                reglas = json.loads(REGLAS_FILE.read_text(encoding="utf-8"))
                for cuit, v in reglas.items():
                    con.execute(
                        "INSERT OR IGNORE INTO reglas_cuit "
                        "(cuit_norm,estado,estado_display,motivo,razon_social,creado,activo) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (cuit, v.get("estado",""), v.get("estado_display",""),
                         v.get("motivo",""), v.get("razon_social",""),
                         v.get("creado",""), int(v.get("activo", True)))
                    )
            except Exception:
                pass

        # mapeos.json
        if MAPEOS_FILE.exists():
            try:
                mapeos = json.loads(MAPEOS_FILE.read_text(encoding="utf-8"))
                for nombre, config in mapeos.items():
                    con.execute(
                        "INSERT OR IGNORE INTO mapeos (nombre, config_json, actualizado) VALUES (?,?,?)",
                        (nombre, json.dumps(config, ensure_ascii=False), datetime.now().isoformat())
                    )
            except Exception:
                pass

        # feedback.jsonl
        if FEEDBACK_FILE.exists():
            try:
                for line in FEEDBACK_FILE.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    con.execute(
                        "INSERT INTO feedback "
                        "(ts,periodo,comprobante,cuit_dni,razon_social,"
                        "estado_algoritmo,estado_usuario,motivo,nota,data_json) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (r.get("ts"), r.get("periodo"), r.get("Comprobante"),
                         r.get("CUIT_DNI"), r.get("Razon_Social"),
                         r.get("estado_algoritmo"), r.get("estado_usuario"),
                         r.get("motivo"), r.get("nota"),
                         json.dumps(r, ensure_ascii=False))
                    )
                FEEDBACK_FILE.rename(FEEDBACK_FILE.with_suffix(".jsonl.bak"))
            except Exception:
                pass

        # Snapshots históricos de persistencia/
        if PERSIST_DIR.exists():
            for m in sorted(PERSIST_DIR.glob("*_meta.csv")):
                try:
                    row  = pd.read_csv(m).iloc[0].to_dict()
                    ts   = row.get("ts", m.stem.split("_meta")[0])
                    if con.execute("SELECT 1 FROM conciliaciones WHERE ts=?", (ts,)).fetchone():
                        continue
                    p1 = PERSIST_DIR / f"{ts}_conciliacion.csv"
                    p2 = PERSIST_DIR / f"{ts}_solo_listado.csv"
                    p3 = PERSIST_DIR / f"{ts}_solo_arca.csv"
                    if not all(p.exists() for p in (p1, p2, p3)):
                        continue
                    _s1 = _restore_bools(pd.read_csv(p1, encoding="utf-8-sig"))
                    _s2 = pd.read_csv(p2, encoding="utf-8-sig")
                    _s3 = pd.read_csv(p3, encoding="utf-8-sig")
                    periodo = str(row.get("periodo", "")).strip()
                    con.execute(
                        "INSERT OR IGNORE INTO conciliaciones "
                        "(ts,periodo,tolerancia,n_listado,n_arca,conciliados,diferencias,"
                        "fecha_proceso,s1_json,s2_json,s3_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (ts, periodo, float(row.get("tolerancia", 0)),
                         int(row.get("n_listado", 0)), 0,
                         int(row.get("conciliados", 0)), 0, ts,
                         _s1.to_json(orient="records", force_ascii=False, date_format="iso"),
                         _s2.to_json(orient="records", force_ascii=False, date_format="iso"),
                         _s3.to_json(orient="records", force_ascii=False, date_format="iso"))
                    )
                except Exception:
                    pass

        # Último resultado (CSVs _last)
        if CSV_CONC.exists() and CSV_LIST.exists() and CSV_ARCA.exists():
            try:
                meta = pd.read_csv(CSV_META).iloc[0].to_dict() if CSV_META.exists() else {}
                try:
                    _ts_last = datetime.fromisoformat(
                        str(meta.get("fecha_proceso", ""))
                    ).strftime("%Y%m%d_%H%M%S")
                except Exception:
                    _ts_last = datetime.now().strftime("%Y%m%d_%H%M%S")
                if not con.execute("SELECT 1 FROM conciliaciones WHERE ts=?", (_ts_last,)).fetchone():
                    _s1 = _restore_bools(pd.read_csv(CSV_CONC, encoding="utf-8-sig"))
                    _s2 = pd.read_csv(CSV_LIST, encoding="utf-8-sig")
                    _s3 = pd.read_csv(CSV_ARCA, encoding="utf-8-sig")
                    periodo = _detectar_periodo(_s1)
                    tol = float(meta.get("tolerancia", TOLERANCIA_DEFAULT))
                    con.execute(
                        "INSERT OR IGNORE INTO conciliaciones "
                        "(ts,periodo,tolerancia,n_listado,n_arca,conciliados,diferencias,"
                        "fecha_proceso,s1_json,s2_json,s3_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (_ts_last, periodo, tol, len(_s1),
                         int(meta.get("n_arca", 0)), int(meta.get("conciliados", 0)),
                         int(meta.get("diferencias", 0)), str(meta.get("fecha_proceso", "")),
                         _s1.to_json(orient="records", force_ascii=False, date_format="iso"),
                         _s2.to_json(orient="records", force_ascii=False, date_format="iso"),
                         _s3.to_json(orient="records", force_ascii=False, date_format="iso"))
                    )
            except Exception:
                pass

        con.commit()
        marker.touch()
    finally:
        con.close()


def cargar_mapeos() -> dict:
    try:
        con = _db_con()
        rows = con.execute("SELECT nombre, config_json FROM mapeos").fetchall()
        con.close()
        mapeos = copy.deepcopy(MAPEOS_DEFAULT)
        for row in rows:
            try:
                mapeos[row["nombre"]] = json.loads(row["config_json"])
            except Exception:
                pass
        return mapeos
    except Exception:
        return copy.deepcopy(MAPEOS_DEFAULT)


def guardar_mapeos(mapeos: dict):
    try:
        con = _db_con()
        for nombre, config in mapeos.items():
            con.execute(
                "INSERT OR REPLACE INTO mapeos (nombre, config_json, actualizado) VALUES (?,?,?)",
                (nombre, json.dumps(config, ensure_ascii=False), datetime.now().isoformat())
            )
        con.commit()
        con.close()
    except Exception:
        pass


def _normalizar_col(s: str) -> str:
    """Minúsculas, sin acentos, sin puntuación, espacios simples."""
    s = unicodedata.normalize("NFD", str(s).strip().lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip()


def sugerir_mapeo(cols_archivo: list, campos_esperados: dict,
                  aliases: dict | None = None) -> tuple[dict, dict]:
    """Mapea columnas del archivo a campos semánticos con pipeline de 4 pasos.

    Retorna (sugerencias, confianzas):
      sugerencias: {campo: col_nombre}
      confianzas:  {campo: "exact" | "norm" | "alias" | "fuzzy" | "none"}
    """
    cols_lower = {c.lower().strip(): c for c in cols_archivo}
    cols_norm  = {_normalizar_col(c): c for c in cols_archivo}
    usados: set[str] = set()

    def _claim(col: str) -> str | None:
        if col not in usados:
            usados.add(col)
            return col
        return None

    sugerencias: dict[str, str]  = {}
    confianzas:  dict[str, str]  = {}

    for campo, default_col in campos_esperados.items():
        col_enc = None
        nivel   = "none"

        # 1. Exact (case-insensitive)
        k = default_col.lower().strip()
        if k in cols_lower:
            col_enc = _claim(cols_lower[k])
            if col_enc: nivel = "exact"

        # 2. Normalizado (sin acentos, sin puntuación)
        if not col_enc:
            kn = _normalizar_col(default_col)
            if kn in cols_norm:
                col_enc = _claim(cols_norm[kn])
                if col_enc: nivel = "norm"

        # 3. Alias conocidos
        if not col_enc and aliases and campo in aliases:
            for alias in aliases[campo]:
                an = _normalizar_col(alias)
                if an in cols_norm:
                    col_enc = _claim(cols_norm[an])
                    if col_enc:
                        nivel = "alias"
                        break

        # 4. Fuzzy
        if not col_enc:
            libre = [n for n in cols_norm if cols_norm[n] not in usados]
            matches = difflib.get_close_matches(
                _normalizar_col(default_col), libre, n=1, cutoff=0.45
            )
            if matches:
                col_enc = _claim(cols_norm[matches[0]])
                if col_enc: nivel = "fuzzy"

        sugerencias[campo] = col_enc or (cols_archivo[0] if cols_archivo else "")
        confianzas[campo]  = nivel if col_enc else "none"

    return sugerencias, confianzas


def _detectar_columnas(source, header_keyword: str) -> list:
    """Lee solo el encabezado del archivo para devolver la lista de columnas detectadas."""
    try:
        raw   = leer_excel(source)
        sheet = next(iter(raw.values()))
        hr    = _find_header(sheet, header_keyword)
        if hr is None:
            return []
        cols = [str(c).strip() for c in sheet.iloc[hr] if str(c).strip() and str(c) != "nan"]
        if hasattr(source, "seek"):
            source.seek(0)
        return cols
    except Exception:
        return []


def cargar_reglas() -> dict:
    try:
        con = _db_con()
        rows = con.execute(
            "SELECT cuit_norm,estado,estado_display,motivo,razon_social,creado,activo "
            "FROM reglas_cuit"
        ).fetchall()
        con.close()
        return {
            r["cuit_norm"]: {
                "estado":         r["estado"],
                "estado_display": r["estado_display"],
                "motivo":         r["motivo"],
                "razon_social":   r["razon_social"],
                "creado":         r["creado"],
                "activo":         bool(r["activo"]),
            }
            for r in rows
        }
    except Exception:
        return {}


def guardar_regla(cuit_norm: str, estado: str, motivo: str, razon_social: str):
    estado_canon = ICON_ESTADO.get(estado, estado)
    try:
        con = _db_con()
        con.execute(
            "INSERT OR REPLACE INTO reglas_cuit "
            "(cuit_norm,estado,estado_display,motivo,razon_social,creado,activo) "
            "VALUES (?,?,?,?,?,?,1)",
            (cuit_norm, estado_canon, estado, motivo, razon_social, datetime.now().isoformat())
        )
        con.commit()
        con.close()
    except Exception:
        pass


def eliminar_regla(cuit_norm: str):
    try:
        con = _db_con()
        con.execute("DELETE FROM reglas_cuit WHERE cuit_norm=?", (cuit_norm,))
        con.commit()
        con.close()
    except Exception:
        pass


def guardar_feedback(fila: pd.Series, estado_usuario: str,
                     motivo: str, nota: str, periodo: str) -> None:
    campos_num = ["Dif_Neto", "Dif_IVA", "Dif_Total", "Neto", "IVA", "Total"]
    registro: dict = {
        "ts":               datetime.now().isoformat(),
        "periodo":          periodo,
        "Comprobante":      fila.get("Comprobante", ""),
        "CUIT_DNI":         fila.get("CUIT_DNI", ""),
        "Razon_Social":     fila.get("Razon_Social", ""),
        "estado_algoritmo": fila.get("Estado", ""),
        "estado_usuario":   estado_usuario,
        "motivo":           motivo,
        "nota":             nota,
    }
    for c in campos_num:
        if c in fila.index:
            registro[c] = fila[c]
    try:
        con = _db_con()
        con.execute(
            "INSERT INTO feedback "
            "(ts,periodo,comprobante,cuit_dni,razon_social,"
            "estado_algoritmo,estado_usuario,motivo,nota,data_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (registro["ts"], periodo, registro["Comprobante"],
             registro["CUIT_DNI"], registro["Razon_Social"],
             registro["estado_algoritmo"], estado_usuario,
             motivo, nota,
             json.dumps(registro, ensure_ascii=False, default=str))
        )
        con.commit()
        con.close()
    except Exception:
        pass


# ── Lectura robusta de Excel ──────────────────────────────────────────────────

def _find_libreoffice() -> str:
    """Devuelve el ejecutable de LibreOffice según el sistema operativo.

    En Windows LibreOffice se llama soffice.exe y frecuentemente no está en PATH,
    por lo que se busca en las rutas de instalación estándar.
    En Linux/Mac el comando es simplemente 'libreoffice'.
    """
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
        for path in candidates:
            if Path(path).exists():
                return path
        # Si está en PATH (instalación custom o portable)
        return shutil.which("soffice") or shutil.which("libreoffice") or "soffice"
    return "libreoffice"


def _libreoffice_xlsx(src_path: str) -> str:
    """Convierte un XLS a XLSX usando LibreOffice en modo headless.

    Colppy exporta XLS con un bug en el compound document que hace que xlrd
    lo rechace con CompDocError. LibreOffice lo repara al reescribirlo como XLSX.
    Retorna la ruta del XLSX generado en un directorio temporal.
    """
    out = tempfile.mkdtemp()
    subprocess.run(
        [_find_libreoffice(), "--headless", "--convert-to", "xlsx", "--outdir", out, src_path],
        capture_output=True, timeout=60,
    )
    return os.path.join(out, Path(src_path).stem + ".xlsx")


def leer_excel(source) -> dict:
    """Lee un archivo XLS/XLSX o CSV y retorna dict {nombre_hoja: DataFrame}.

    Acepta ruta (str) o BytesIO (upload de Streamlit).
    Para CSV: detecta separador y encoding automáticamente.
    Para XLS: cascada de intentos ante archivos corruptos (común en Colppy):
      1. pandas/xlrd  2. python-calamine  3. LibreOffice headless
    """
    # ── CSV ──────────────────────────────────────────────────────────────────
    _name = source if isinstance(source, str) else getattr(source, "name", "")
    if str(_name).lower().endswith(".csv"):
        try:
            if isinstance(source, str):
                raw = open(source, "rb").read()
            else:
                source.seek(0)
                raw = source.read()
                source.seek(0)
            for _enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
                try:
                    df_csv = pd.read_csv(
                        BytesIO(raw), sep=None, engine="python",
                        header=None, encoding=_enc, dtype=str,
                    )
                    if len(df_csv.columns) >= 1:
                        return {"Hoja1": df_csv}
                except Exception:
                    continue
        except Exception as e:
            raise ValueError(f"No se pudo leer el CSV: {e}") from e

    # ── Excel ─────────────────────────────────────────────────────────────────
    tmp_path = tmp_dir = None

    def _read(src_or_bytes, engine=None):
        kwargs = dict(sheet_name=None, header=None)
        if engine:
            kwargs["engine"] = engine
        if isinstance(src_or_bytes, str):
            return pd.read_excel(src_or_bytes, **kwargs)
        src_or_bytes.seek(0)
        return pd.read_excel(src_or_bytes, **kwargs)

    # Intento 1: engine por defecto (xlrd para .xls, openpyxl para .xlsx)
    try:
        return _read(source)
    except Exception:
        pass

    # Intento 2: calamine (Rust — más tolerante con compound-document corruption)
    try:
        return _read(source, engine="calamine")
    except Exception:
        pass

    # Intento 3: LibreOffice headless — convierte a XLSX y relee
    try:
        if isinstance(source, str):
            src = source
        else:
            source.seek(0)
            fd, src = tempfile.mkstemp(suffix=".xls")
            with os.fdopen(fd, "wb") as f:
                f.write(source.read())
            tmp_path = src

        xlsx = _libreoffice_xlsx(src)
        tmp_dir = os.path.dirname(xlsx)

        if not os.path.exists(xlsx):
            raise FileNotFoundError(
                "No se pudo leer el archivo XLS.\n"
                "Intentá exportarlo como XLSX desde Colppy, o instalá LibreOffice "
                "para que la conversión automática funcione."
            )

        return pd.read_excel(xlsx, sheet_name=None, header=None, engine="openpyxl")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _find_header(sheet: pd.DataFrame, keyword: str) -> int:
    """Busca la primera fila que contenga `keyword` y retorna su índice.

    Colppy y ARCA insertan filas de metadatos antes del encabezado real,
    por lo que no se puede asumir que la fila 0 es el header.
    """
    for i, row in sheet.iterrows():
        if row.astype(str).str.contains(keyword, case=False, na=False).any():
            return i
    return None


# ── Carga Listado IVA Compras (Colppy) ───────────────────────────────────────

def load_listado_iva(source, mapeo: dict | None = None):
    """Carga y normaliza el Listado IVA Compras exportado de Colppy.

    Pasos:
    1. Ubica la fila de encabezado buscando "Comprobante".
    2. Filtra solo las filas cuyo Comprobante tiene formato XXXXX-YYYYYYYY,
       descartando filas de totales y subtotales que Colppy agrega al final.
    3. Agrupa por Comprobante sumando Neto e IVA (Colppy puede tener una fila
       por alícuota de IVA), y toma el primer Total no-cero.
    4. Clasifica el origen (Nacional / Exterior) y aplica etiquetas legibles.

    Retorna un DataFrame con una fila por comprobante, o None si el archivo
    no tiene el formato esperado.
    """
    if mapeo is None:
        mapeo = copy.deepcopy(MAPEOS_DEFAULT["listado"]["Colppy"])
    col = mapeo["columnas"]

    raw   = leer_excel(source)
    sheet = next(iter(raw.values()))
    hr    = _find_header(sheet, mapeo.get("header_keyword", "Comprobante"))
    if hr is None:
        st.error(f"No se encontró encabezado '{mapeo.get('header_keyword','Comprobante')}' en el Listado IVA.")
        return None

    df = sheet.copy()
    df.columns = df.iloc[hr]
    df = df.iloc[hr + 1:].reset_index(drop=True)
    df.columns.name = None

    df = df.rename(columns={
        col.get("fecha",         "Fecha Factura"):  "Fecha_Factura",
        col.get("tipo",          "Tipo"):           "Tipo",
        col.get("comprobante",   "Comprobante"):    "Comprobante",
        col.get("cuit",          "CUIT/DNI"):       "CUIT_DNI",
        col.get("razon_social",  "Razón Social"):   "Razon_Social",
        col.get("condicion_iva", "Condición IVA"):  "Condicion_IVA",
        col.get("neto",          "Neto"):           "Neto",
        col.get("iva",           "IVA"):            "IVA",
        col.get("total",         "Total"):          "Total",
        "Perc. IIBB":                               "Perc_IIBB",
        "Perc. IVA":                                "Perc_IVA",
    })

    # Mantener solo filas de comprobantes reales; las de totales no tienen este formato
    valid = df["Comprobante"].astype(str).str.match(r"^\d{5}-\d{8}$")
    df    = df[valid].copy()

    for col in ["Neto", "IVA", "Total", "Perc_IIBB", "Perc_IVA"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Un comprobante puede tener N filas (una por alícuota de IVA):
    # sumamos Neto e IVA; para Total tomamos el primer valor no-cero
    agg = df.groupby("Comprobante", as_index=False).agg(
        Fecha_Factura=("Fecha_Factura", "first"),
        Tipo=("Tipo", "first"),
        CUIT_DNI=("CUIT_DNI", "first"),
        Razon_Social=("Razon_Social", "first"),
        Condicion_IVA=("Condicion_IVA", "first"),
        Neto=("Neto", "sum"),
        IVA=("IVA", "sum"),
        Total=("Total", lambda x: x[x != 0].iloc[0] if len(x[x != 0]) > 0 else 0),
    )

    # Exterior: tipo FCC-*/FCE-* o CUIT que empieza con 55 (proveedores del exterior)
    def _origen(row):
        if str(row["Tipo"]).lower() in EXT_PREFIXES:
            return "Exterior"
        cuit = str(row["CUIT_DNI"]).replace("-", "").replace(" ", "").replace(".", "")
        return "Exterior" if cuit.startswith("55") else "Nacional"

    agg["Origen"]    = agg.apply(_origen, axis=1)
    agg["CUIT_norm"] = agg["CUIT_DNI"].astype(str).str.replace(r"[-.\s]", "", regex=True)

    tipo_label = {
        "FAC-A": "Factura A", "FAC-B": "Factura B", "FAC-C": "Factura C",
        "FCC-A": "Ext. A", "FCC-B": "Ext. B", "FCC-C": "Ext. C",
        "NCC-A": "NC A", "NCC-B": "NC B", "NCC-C": "NC C",
        "FCA-A": "FC Elect. A",
    }
    agg["Tipo_Doc"] = agg["Tipo"].map(tipo_label).fillna(agg["Tipo"])

    return agg


# ── Carga Mis Comprobantes Recibidos (ARCA) ───────────────────────────────────

def _es_nota_credito_arca(tipo: str) -> bool:
    """True si el tipo de comprobante ARCA es una Nota de Crédito."""
    t = str(tipo).lower()
    return any(nc in t for nc in NC_ARCA)


def load_arca(source, mapeo: dict | None = None):
    """Carga y normaliza el reporte 'Mis Comprobantes Recibidos' de ARCA.

    Pasos:
    1. Ubica el encabezado buscando "Punto de Venta".
    2. Construye la clave de comprobante en formato XXXXX-YYYYYYYY, que es
       como Colppy lo registra, para poder hacer el JOIN.
    3. Convierte todos los importes a numérico.
    4. Detecta Notas de Crédito y multiplica sus importes por -1: ARCA siempre
       exporta montos positivos, pero en el libro contable las NC son negativas.
    5. Calcula Neto comparable = Neto Gravado + Neto No Gravado + Op. Exentas.
    6. Para Factura C (monotributistas), ARCA no desglosa el Neto; deriva:
       Neto = Imp.Total - Total IVA - Otros Tributos.

    Retorna un DataFrame con una fila por comprobante.
    """
    if mapeo is None:
        mapeo = copy.deepcopy(MAPEOS_DEFAULT["arca"]["ARCA"])
    col = mapeo["columnas"]

    raw   = leer_excel(source)
    sheet = next(iter(raw.values()))
    hr    = _find_header(sheet, mapeo.get("header_keyword", "Punto de Venta"))
    if hr is None:
        st.error(f"No se encontró encabezado '{mapeo.get('header_keyword','Punto de Venta')}' en Mis Comprobantes ARCA.")
        return None

    df = sheet.copy()
    df.columns = df.iloc[hr]
    df = df.iloc[hr + 1:].reset_index(drop=True)
    df.columns.name = None

    # Construir clave en formato XXXXX-YYYYYYYY usando los campos configurados
    col_pto  = col.get("punto_venta", "Punto de Venta")
    col_num  = col.get("numero",      "Número Desde")
    df[col_pto] = pd.to_numeric(df.get(col_pto), errors="coerce").fillna(0).astype(int)
    df[col_num] = pd.to_numeric(df.get(col_num), errors="coerce").fillna(0).astype(int)
    df["Comprobante_Key"] = df.apply(
        lambda r: f"{r[col_pto]:05d}-{r[col_num]:08d}", axis=1
    )

    # Todos los campos de importe usando nombres del mapeo con fallback a los de ARCA
    c = mapeo["columnas"]
    COLS_IMPORTE = [
        c.get("neto_gravado",    "Neto Gravado Total"),
        c.get("neto_no_gravado", "Neto No Gravado"),
        c.get("op_exentas",      "Op. Exentas"),
        c.get("otros_tributos",  "Otros Tributos"),
        c.get("total_iva",       "Total IVA"),
        c.get("total",           "Imp. Total"),
    ]
    for col_i in COLS_IMPORTE:
        if col_i in df.columns:
            df[col_i] = pd.to_numeric(df[col_i], errors="coerce").fillna(0)

    col_tipo = c.get("tipo", "Tipo")
    df["es_NC"] = df[col_tipo].apply(_es_nota_credito_arca)
    signo = df["es_NC"].map({True: -1, False: 1})
    for col in COLS_IMPORTE:
        if col in df.columns:
            df[col] = df[col] * signo

    # Neto comparable usando nombres mapeados
    c_ng  = c.get("neto_gravado",    "Neto Gravado Total")
    c_nng = c.get("neto_no_gravado", "Neto No Gravado")
    c_oe  = c.get("op_exentas",      "Op. Exentas")
    c_ot  = c.get("otros_tributos",  "Otros Tributos")
    c_tiva= c.get("total_iva",       "Total IVA")
    c_tot = c.get("total",           "Imp. Total")

    df["Neto_Total_ARCA"] = (
        df.get(c_ng,  pd.Series(0, index=df.index))
        + df.get(c_nng, pd.Series(0, index=df.index))
        + df.get(c_oe,  pd.Series(0, index=df.index))
    )

    sin_desglose = (df["Neto_Total_ARCA"] == 0) & (df.get(c_tot, pd.Series(0, index=df.index)) != 0)
    df.loc[sin_desglose, "Neto_Total_ARCA"] = (
        df.loc[sin_desglose, c_tot]
        - df.loc[sin_desglose, c_tiva]
        - df.loc[sin_desglose, c_ot]
    )
    df["neto_derivado"] = sin_desglose

    tipo_label_arca = {
        "1 - Factura A":         "Factura A",
        "2 - Nota de Débito A":  "N.Débito A",
        "3 - Nota de Crédito A": "N.Crédito A",
        "6 - Factura B":         "Factura B",
        "7 - Nota de Débito B":  "N.Débito B",
        "8 - Nota de Crédito B": "N.Crédito B",
        "11 - Factura C":        "Factura C",
        "12 - Nota de Débito C": "N.Débito C",
        "13 - Nota de Crédito C":"N.Crédito C",
    }
    df["Tipo_Doc_ARCA"] = df[col_tipo].map(tipo_label_arca).fillna(df[col_tipo])

    # Estandarizar nombres de columnas a los esperados por conciliar()
    col_std = {
        c_tiva: "Total IVA", c_ot: "Otros Tributos", c_tot: "Imp. Total",
        c.get("fecha", "Fecha"): "Fecha",
        col_tipo: "Tipo",
        c.get("cuit_emisor",  "Nro. Doc. Emisor"):    "Nro. Doc. Emisor",
        c.get("denominacion", "Denominación Emisor"): "Denominación Emisor",
    }
    df = df.rename(columns={k: v for k, v in col_std.items() if k != v})

    df["CUIT_norm"] = df.get("Nro. Doc. Emisor", pd.Series("", index=df.index)).astype(str).str.replace(r"[-.\s]", "", regex=True)

    return df


# ── Conciliación ──────────────────────────────────────────────────────────────

def conciliar(df_listado, df_arca, tolerancia: float):
    """Cruza el Listado con ARCA y clasifica cada comprobante.

    Lógica:
    - LEFT JOIN del Listado sobre ARCA por clave de comprobante.
    - Para Neto, IVA y Total calcula la diferencia absoluta.
    - Un campo "Match" es True si la diferencia ≤ tolerancia.
    - "Conciliado" = los tres campos hacen match.
    - Estado:
        "Conciliado"              → todos los montos coinciden
        "Total OK / Sin desglose" → Total OK pero Neto difiere (Factura C derivada)
        "Diferencia detectada"    → discrepancia real en algún importe
        "Sin match en ARCA"       → el comprobante no figura en ARCA

    Retorna (sheet1, sheet2, sheet3):
        sheet1: todos los comprobantes del Listado con columnas de comparación
        sheet2: comprobantes solo en Listado (sin match en ARCA)
        sheet3: comprobantes solo en ARCA (no están en el Listado)
    """
    # Reducir ARCA a las columnas necesarias para el JOIN
    arca_slim = df_arca[[c for c in [
        "Comprobante_Key", "CUIT_norm", "Neto_Total_ARCA", "Total IVA", "Otros Tributos", "Imp. Total",
        "Fecha", "Tipo_Doc_ARCA", "Denominación Emisor", "Nro. Doc. Emisor",
        "es_NC", "neto_derivado",
    ] if c in df_arca.columns]].rename(columns={
        "Comprobante_Key":    "ARCA_Key",
        "CUIT_norm":          "ARCA_CUIT_norm",
        "Neto_Total_ARCA":    "ARCA_Neto",
        "Total IVA":          "ARCA_IVA",
        "Otros Tributos":     "ARCA_OtrosTrib",
        "Imp. Total":         "ARCA_Total",
        "Fecha":              "ARCA_Fecha",
        "Tipo_Doc_ARCA":      "ARCA_Tipo",
        "Denominación Emisor":"ARCA_Denominacion",
        "Nro. Doc. Emisor":   "ARCA_CUIT",
        "es_NC":              "ARCA_es_NC",
        "neto_derivado":      "ARCA_Neto_Derivado",
    })

    dupes = df_arca[df_arca.duplicated("Comprobante_Key", keep=False)]
    if not dupes.empty:
        n_keys = dupes["Comprobante_Key"].nunique()
        st.warning(
            f"ARCA contiene {len(dupes)} filas duplicadas para {n_keys} comprobante(s). "
            "Se conserva la primera ocurrencia de cada uno."
        )
        df_arca = df_arca.drop_duplicates("Comprobante_Key", keep="first")

    merged = pd.merge(df_listado, arca_slim, left_on=["Comprobante", "CUIT_norm"], right_on=["ARCA_Key", "ARCA_CUIT_norm"], how="left")
    merged["Existe_en_ARCA"] = merged["ARCA_Key"].notna()

    tol = tolerancia
    for campo in ["Neto", "IVA", "Total"]:
        merged[f"Dif_{campo}"] = (
            merged[campo].fillna(0) - merged[f"ARCA_{campo}"].fillna(0)
        ).abs()

    merged["Match_Neto"]  = merged["Existe_en_ARCA"] & (merged["Dif_Neto"]  <= tol)
    merged["Match_IVA"]   = merged["Existe_en_ARCA"] & (merged["Dif_IVA"]   <= tol)
    merged["Match_Total"] = merged["Existe_en_ARCA"] & (merged["Dif_Total"] <= tol)
    merged["Conciliado"]  = merged["Match_Neto"] & merged["Match_IVA"] & merged["Match_Total"]

    def _estado(row):
        if not row["Existe_en_ARCA"]:
            return "Sin match en ARCA"
        if row["Conciliado"]:
            return "Conciliado"
        # "Sin desglose" solo cuando el neto fue derivado por ser Factura C de monotributista;
        # si el neto es real y difiere del Total, es una diferencia genuina.
        if row["Match_Total"] and not row["Match_Neto"] and row.get("ARCA_Neto_Derivado", False):
            return "Total OK / Sin desglose"
        return "Diferencia detectada"

    merged["Estado"] = merged.apply(_estado, axis=1)

    sheet1 = merged[[c for c in [
        "Estado", "Comprobante", "Fecha_Factura", "Tipo_Doc", "Origen",
        "CUIT_DNI", "CUIT_norm", "Razon_Social",
        "Neto", "IVA", "Total",
        "Existe_en_ARCA", "ARCA_Denominacion", "ARCA_CUIT", "ARCA_Fecha", "ARCA_Tipo",
        "ARCA_es_NC", "ARCA_Neto_Derivado",
        "ARCA_Neto", "ARCA_IVA", "ARCA_OtrosTrib", "ARCA_Total",
        "Dif_Neto", "Dif_IVA", "Dif_Total",
        "Match_Neto", "Match_IVA", "Match_Total", "Conciliado",
    ] if c in merged.columns]].copy()

    # Sheet 2: solo los que no encontraron contraparte en ARCA
    sheet2 = merged[~merged["Existe_en_ARCA"]][[c for c in [
        "Comprobante", "Fecha_Factura", "Tipo_Doc", "Origen",
        "CUIT_DNI", "Razon_Social", "Condicion_IVA", "Neto", "IVA", "Total",
    ] if c in merged.columns]].copy()

    # Sheet 3: comprobantes en ARCA que no figuran en el Listado
    listado_keys = set(df_listado["Comprobante"])
    keep = [c for c in [
        "Comprobante_Key", "Fecha", "Tipo_Doc_ARCA", "Nro. Doc. Emisor",
        "Denominación Emisor", "Neto Gravado Total", "Neto No Gravado", "Op. Exentas",
        "Otros Tributos", "Total IVA", "Imp. Total", "es_NC",
    ] if c in df_arca.columns]
    sheet3 = df_arca[~df_arca["Comprobante_Key"].isin(listado_keys)][keep].copy()

    return sheet1, sheet2, sheet3


# ── Persistencia CSV ──────────────────────────────────────────────────────────

def _detectar_periodo(s1: pd.DataFrame) -> str:
    """Deriva el período (mes/año) procesado desde las fechas de facturas del Listado.

    Toma el mes/año más frecuente en Fecha_Factura. Retorna string tipo '2025-11'
    o '' si no se puede determinar.
    """
    if s1 is None or "Fecha_Factura" not in s1.columns:
        return ""
    fechas = pd.to_datetime(s1["Fecha_Factura"], dayfirst=True, errors="coerce").dropna()
    if fechas.empty:
        return ""
    periodo = fechas.dt.to_period("M").mode()
    return str(periodo.iloc[0]) if len(periodo) > 0 else ""

def _hash_s1(s1: pd.DataFrame | None) -> str:
    """Hash del resultado de conciliación para detectar cambios entre corridas.

    Compara solo [Comprobante, Estado, Conciliado] ordenado: si el conjunto de
    comprobantes y su estado no cambiaron, no vale la pena guardar un snapshot.
    Usa pd.util.hash_pandas_object (determinista para los mismos datos).
    """
    if s1 is None:
        return ""
    try:
        key = s1[["Comprobante", "Estado", "Conciliado"]].sort_values("Comprobante").reset_index(drop=True)
        h   = pd.util.hash_pandas_object(key, index=False).sum()
        return str(h)
    except Exception:
        return ""


def _restore_bools(df: pd.DataFrame) -> pd.DataFrame:
    """Garantiza que las columnas booleanas sean dtype bool.

    Acepta tanto strings "True"/"False" (legacy CSV) como valores nativos
    bool o 0/1 (JSON). Usa fillna(False) para que .astype(bool) no falle
    en pandas 2.x si existen NaN residuales.
    """
    for col in BOOL_COLS:
        if col in df.columns:
            df[col] = (
                df[col]
                .map({"True": True, "False": False, True: True, False: False})
                .fillna(False)
                .astype(bool)
            )
    return df


def cargar_csv():
    """Lee el resultado más reciente de la BD. Retorna (s1, s2, s3, meta) o (None,None,None,{})."""
    try:
        con = _db_con()
        row = con.execute(
            "SELECT * FROM conciliaciones ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        con.close()
        if row is None:
            return None, None, None, {}
        s1 = _restore_bools(pd.read_json(StringIO(row["s1_json"]), orient="records", dtype=False))
        s2 = pd.read_json(StringIO(row["s2_json"]), orient="records", dtype=False)
        s3 = pd.read_json(StringIO(row["s3_json"]), orient="records", dtype=False)
        meta = {
            "fecha_proceso": row["fecha_proceso"],
            "periodo":       row["periodo"],
            "tolerancia":    row["tolerancia"],
            "n_listado":     row["n_listado"],
            "n_arca":        row["n_arca"],
            "conciliados":   row["conciliados"],
            "diferencias":   row["diferencias"],
        }
        return s1, s2, s3, meta
    except Exception:
        return None, None, None, {}


def guardar_csv(s1, s2, s3, tolerancia: float) -> bool:
    """Persiste la conciliación en SQLite. Retorna True si hubo cambios respecto al snapshot anterior."""
    try:
        con = _db_con()
        prev = con.execute(
            "SELECT ts, s1_json FROM conciliaciones ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        prev_s1 = None
        if prev:
            try:
                prev_s1 = pd.read_json(StringIO(prev["s1_json"]), orient="records", dtype=False)
            except Exception:
                pass

        hubo_cambio = _hash_s1(s1) != _hash_s1(prev_s1)
        if not hubo_cambio:
            con.close()
            return False

        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        periodo = _detectar_periodo(s1)
        con.execute(
            "INSERT INTO conciliaciones "
            "(ts,periodo,tolerancia,n_listado,n_arca,conciliados,diferencias,"
            "fecha_proceso,s1_json,s2_json,s3_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts, periodo, tolerancia, len(s1),
             len(s3) + int(s1["Existe_en_ARCA"].sum()),
             int(s1["Conciliado"].sum()),
             int((s1["Estado"] == "Diferencia detectada").sum()),
             datetime.now().isoformat(),
             s1.to_json(orient="records", force_ascii=False, date_format="iso"),
             s2.to_json(orient="records", force_ascii=False, date_format="iso"),
             s3.to_json(orient="records", force_ascii=False, date_format="iso"))
        )
        con.commit()
        con.close()
        return True
    except Exception:
        return False


def listar_historicos() -> list[dict]:
    """Lee los metadatos de todos los snapshots en orden descendente."""
    try:
        con = _db_con()
        rows = con.execute(
            "SELECT ts,periodo,tolerancia,n_listado,conciliados,fecha_proceso "
            "FROM conciliaciones ORDER BY ts DESC"
        ).fetchall()
        con.close()
        result = []
        for row in rows:
            ts      = row["ts"]
            periodo = str(row["periodo"] or "").strip()
            try:
                fecha_label = datetime.strptime(ts, "%Y%m%d_%H%M%S").strftime("%d/%m/%Y %H:%M")
            except Exception:
                fecha_label = ts
            label = f"{periodo}  ({fecha_label})" if periodo else fecha_label
            result.append({
                "ts":      ts,
                "label":   label,
                "periodo": periodo,
                "conc":    int(row["conciliados"] or 0),
                "n":       int(row["n_listado"] or 0),
                "tol":     float(row["tolerancia"] or 0),
            })
        return result
    except Exception:
        return []


def cargar_historico(ts: str):
    """Carga s1, s2, s3 de un snapshot por ts. Retorna (s1, s2, s3) o (None, None, None)."""
    try:
        con = _db_con()
        row = con.execute(
            "SELECT s1_json, s2_json, s3_json FROM conciliaciones WHERE ts=?", (ts,)
        ).fetchone()
        con.close()
        if row is None:
            return None, None, None
        s1 = _restore_bools(pd.read_json(StringIO(row["s1_json"]), orient="records", dtype=False))
        s2 = pd.read_json(StringIO(row["s2_json"]), orient="records", dtype=False)
        s3 = pd.read_json(StringIO(row["s3_json"]), orient="records", dtype=False)
        return s1, s2, s3
    except Exception:
        return None, None, None


# ── Exportación Excel ─────────────────────────────────────────────────────────

def generar_excel(s1, s2, s3) -> bytes:
    """Genera un Excel de tres hojas con formato profesional.

    Convenciones de color:
      - Verde: match / conciliado
      - Rojo: diferencia / no match
      - Amarillo: advertencia (Total OK sin desglose)
      - Gris: sin información (sin match en ARCA)
      - Números negativos en rojo (NC)

    Todas las hojas tienen encabezado fijo (freeze panes) y autofiltro.
    """
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        wb = writer.book
        hdr     = wb.add_format({"bold":True,"font_color":"white","bg_color":"#1e3a5f","border":1,"align":"center","valign":"vcenter"})
        f_ok    = wb.add_format({"bg_color":"#dcfce7","font_color":"#15803d","bold":True,"align":"center"})
        f_ko    = wb.add_format({"bg_color":"#fee2e2","font_color":"#b91c1c","bold":True,"align":"center"})
        f_num   = wb.add_format({"num_format":"#,##0.00"})
        f_dok   = wb.add_format({"num_format":"#,##0.00","bg_color":"#dcfce7"})
        f_dko   = wb.add_format({"num_format":"#,##0.00","bg_color":"#fee2e2"})
        f_neg   = wb.add_format({"num_format":"#,##0.00","font_color":"#dc2626"})
        f_estado = {
            "Conciliado":              wb.add_format({"bg_color":"#dcfce7","font_color":"#15803d","bold":True}),
            "Total OK / Sin desglose": wb.add_format({"bg_color":"#fef9c3","font_color":"#713f12"}),
            "Diferencia detectada":    wb.add_format({"bg_color":"#fee2e2","font_color":"#b91c1c","bold":True}),
            "Sin match en ARCA":       wb.add_format({"bg_color":"#f1f5f9","font_color":"#64748b"}),
            "Revisado / Aceptado":     wb.add_format({"bg_color":"#e0f2fe","font_color":"#0369a1","bold":True}),
            "✔️ Revisado / Aceptado":  wb.add_format({"bg_color":"#e0f2fe","font_color":"#0369a1","bold":True}),
        }

        def wsheet(df, name, bool_cols=None, num_cols=None, diff_cols=None, tol=0.07):
            """Escribe un DataFrame en una hoja con formatos por tipo de columna."""
            df.to_excel(writer, sheet_name=name, index=False, startrow=1, header=False)
            ws = writer.sheets[name]
            for c, col in enumerate(df.columns):
                ws.write(0, c, col, hdr)
            for r, row in enumerate(df.itertuples(index=False), 1):
                for c, (col, val) in enumerate(zip(df.columns, row)):
                    nan = not isinstance(val, str) and pd.isna(val) if hasattr(val, "__class__") else False
                    if col == "Estado":
                        ws.write(r, c, "" if nan else str(val), f_estado.get(str(val), wb.add_format()))
                    elif bool_cols and col in bool_cols:
                        ws.write(r, c, "✓" if val is True else "✗", f_ok if val is True else f_ko)
                    elif diff_cols and col in diff_cols:
                        try:
                            v = float(val)
                            ws.write_number(r, c, v, f_dok if v <= tol else f_dko)
                        except (TypeError, ValueError):
                            ws.write(r, c, val)
                    elif num_cols and col in num_cols:
                        try:
                            v = float(val)
                            ws.write_number(r, c, v, f_neg if v < 0 else f_num)
                        except (TypeError, ValueError):
                            ws.write(r, c, "" if nan else val)
                    else:
                        ws.write(r, c, "" if nan else val)
            for c, col in enumerate(df.columns):
                ws.set_column(c, c, min(max(len(str(col)), 10) + 3, 42))
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, len(df), len(df.columns) - 1)

        NUM1  = {"Neto","IVA","Total","ARCA_Neto","ARCA_IVA","ARCA_OtrosTrib","ARCA_Total"}
        DIFF  = {"Dif_Neto","Dif_IVA","Dif_Total"}
        BOOL1 = {"Existe_en_ARCA","Match_Neto","Match_IVA","Match_Total","Conciliado","ARCA_es_NC","ARCA_Neto_Derivado"}
        wsheet(s1, "Conciliación",    bool_cols=BOOL1, num_cols=NUM1, diff_cols=DIFF)
        wsheet(s2, "Solo en Listado", num_cols={"Neto","IVA","Total"})
        wsheet(s3, "Solo en ARCA",
               num_cols={"Neto Gravado Total","Neto No Gravado","Op. Exentas",
                         "Otros Tributos","Total IVA","Imp. Total"},
               bool_cols={"es_NC"})

    return buf.getvalue()


# ── Helpers UI ────────────────────────────────────────────────────────────────

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


def _selectbox_col(label: str, col_sug: str, cols: list, key: str) -> str:
    """Dropdown de columna con la sugerencia primero en la lista."""
    if not cols:
        return col_sug
    if col_sug and col_sug in cols:
        opts = [col_sug] + [c for c in cols if c != col_sug]
    else:
        opts = ([col_sug] if col_sug else []) + [c for c in cols if c != col_sug]
    return st.selectbox(label, opts or cols, index=0, key=key,
                        label_visibility="collapsed")


def _render_mapeo_parejas(cols_l: list, cols_a: list) -> tuple[dict, dict]:
    """Panel central de emparejamiento columna-a-columna entre Listado y ARCA.

    Muestra los campos agrupados por propósito. Cada fila tiene:
      [Nombre del campo] | [columna Listado ▾] | [badge] | [columna ARCA ▾]

    Al pie muestra columnas sin usar de cada archivo.
    Retorna (columnas_listado, columnas_arca) listos para pasar a load_*().
    """
    sug_l  = st.session_state.get("ml_sug",  {})
    sug_a  = st.session_state.get("ma_sug",  {})
    conf_l = st.session_state.get("ml_conf", {})
    conf_a = st.session_state.get("ma_conf", {})

    res_l: dict[str, str] = {}
    res_a: dict[str, str] = {}
    n_warn = 0

    # ── Encabezado de columnas ────────────────────────────────────────────────
    h0, h1, h2, h3 = st.columns([2.2, 3.5, 0.7, 3.5])
    h1.markdown(
        "<div style='font-size:.75rem;font-weight:700;color:#1e3a5f;"
        "text-transform:uppercase;letter-spacing:.07em;padding-bottom:2px'>"
        "Listado IVA Compras</div>", unsafe_allow_html=True)
    h3.markdown(
        "<div style='font-size:.75rem;font-weight:700;color:#1e3a5f;"
        "text-transform:uppercase;letter-spacing:.07em;padding-bottom:2px'>"
        "ARCA</div>", unsafe_allow_html=True)
    st.markdown("<hr style='margin:0 0 4px;border-color:#cbd5e1'>",
                unsafe_allow_html=True)

    # ── Grupos de parejas ─────────────────────────────────────────────────────
    for grupo in GRUPOS_PAREJAS:
        st.markdown(
            f"<div style='font-size:.72rem;font-weight:700;color:#64748b;"
            f"text-transform:uppercase;letter-spacing:.08em;"
            f"margin:10px 0 2px'>{grupo['titulo']}</div>",
            unsafe_allow_html=True)
        st.caption(grupo["detalle"])

        for campo in grupo["campos"]:
            lc = campo.get("l_campo")
            ac = campo.get("a_campo")

            # Confianza de cada lado
            cl = conf_l.get(lc, "none") if lc else "exact"
            ca = conf_a.get(ac, "none") if ac else "exact"
            worst = max(cl, ca, key=lambda x: _CONF_RANK.get(x, 4))
            badge, color, tooltip = _CONF_BADGE.get(worst, _CONF_BADGE["none"])
            if worst not in ("exact", "norm"):
                n_warn += 1

            peso_l = "600" if cl not in ("exact", "norm") else "400"
            peso_a = "600" if ca not in ("exact", "norm") else "400"

            c0, c1, c2, c3 = st.columns([2.2, 3.5, 0.7, 3.5])

            with c0:
                st.markdown(
                    f"<div style='padding:6px 0;font-size:.92rem'>{campo['label']}</div>",
                    unsafe_allow_html=True)
            with c1:
                if lc and cols_l:
                    res_l[lc] = _selectbox_col(
                        campo["label"], sug_l.get(lc, ""), cols_l, f"ml_{lc}")
                elif lc:
                    res_l[lc] = sug_l.get(lc, "")
            with c2:
                st.markdown(
                    f"<div style='text-align:center;padding:6px 0;font-size:1rem'"
                    f" title='{tooltip}'>{badge}</div>",
                    unsafe_allow_html=True)
            with c3:
                if campo.get("a_fijo"):
                    st.markdown(
                        f"<div style='padding:6px 0;font-size:.85rem;"
                        f"color:#64748b;font-style:italic'>{campo['a_fijo']}</div>",
                        unsafe_allow_html=True)
                    # Guardar los campos implícitos con sus sugerencias auto
                    for impl in campo.get("a_implícitos", []):
                        res_a[impl] = sug_a.get(impl, CAMPOS_ARCA.get(impl, ""))
                elif ac and cols_a:
                    res_a[ac] = _selectbox_col(
                        campo["label"], sug_a.get(ac, ""), cols_a, f"ma_{ac}")
                elif ac:
                    res_a[ac] = sug_a.get(ac, "")

        st.markdown("<hr style='margin:4px 0;border-color:#f1f5f9'>",
                    unsafe_allow_html=True)

    # Campos ARCA internos (no mostrados como parejas, se auto-asignan)
    for _ic in _ARCA_INTERNOS:
        if _ic not in res_a:
            res_a[_ic] = sug_a.get(_ic, CAMPOS_ARCA.get(_ic, ""))

    # ── Resumen de confianza ──────────────────────────────────────────────────
    if n_warn:
        st.warning(f"⚠️ {n_warn} par(es) con coincidencia aproximada — revisá los resaltados en naranja/rojo.", icon=None)
    else:
        st.success("Todos los campos emparejados con alta confianza.", icon="✅")

    # ── Columnas no utilizadas ────────────────────────────────────────────────
    cols_l_usadas = set(res_l.values()) - {""}
    cols_a_usadas = set(res_a.values()) - {""}
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
                st.markdown("**Listado** — no usadas en esta conciliación")
                if ignoradas_l:
                    for c in ignoradas_l:
                        st.markdown(f"<span style='color:#94a3b8;font-size:.85rem'>· {c}</span>",
                                    unsafe_allow_html=True)
                else:
                    st.caption("—")
            with ic2:
                st.markdown("**ARCA** — no usadas en esta conciliación")
                if ignoradas_a:
                    for c in ignoradas_a:
                        st.markdown(f"<span style='color:#94a3b8;font-size:.85rem'>· {c}</span>",
                                    unsafe_allow_html=True)
                else:
                    st.caption("—")

    return res_l, res_a


# ── UI ────────────────────────────────────────────────────────────────────────

st.markdown("""
<div class="header-bar">
    <h1>📊 Conciliación IVA Compras</h1>
    <p>Listado IVA Compras (Colppy) &nbsp;↔&nbsp; Mis Comprobantes Recibidos (ARCA)</p>
</div>
""", unsafe_allow_html=True)

# Inicializar BD (crea tablas + migra legacy una vez)
init_db()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Parámetros")
    tolerancia = st.number_input(
        "Tolerancia ($)", min_value=0.0, max_value=10.0,
        value=TOLERANCIA_DEFAULT, step=0.01, format="%.2f",
    )

    st.markdown("### 📁 Archivos")
    local_dir  = Path(__file__).parent
    local_xlsx = list(local_dir.glob("*.xls")) + list(local_dir.glob("*.xlsx")) + list(local_dir.glob("*.csv"))
    # Autodetección: si hay archivos con nombres estándar en la carpeta, los usa sin upload
    auto_listado = next((str(f) for f in local_xlsx if "listado" in f.name.lower() and "iva" in f.name.lower()), None)
    auto_arca    = next((str(f) for f in local_xlsx if "comprobantes" in f.name.lower() and "recibidos" in f.name.lower()), None)

    if auto_listado:
        st.success(f"Listado: `{Path(auto_listado).name}`", icon="✅")
    if auto_arca:
        st.success(f"ARCA: `{Path(auto_arca).name}`", icon="✅")

    file_listado = st.file_uploader("Subir Listado IVA", type=["xls","xlsx","csv"])
    file_arca    = st.file_uploader("Subir Comprobantes ARCA", type=["xls","xlsx","csv"])

    src_listado = file_listado or auto_listado
    src_arca    = file_arca    or auto_arca

    # ── Autodetección de columnas al cargar nuevo archivo ─────────────────────
    for _fsrc, _kp, _campos, _aliases, _hkw in [
        (src_listado, "ml", CAMPOS_LISTADO, ALIASES_LISTADO, "Comprobante"),
        (src_arca,    "ma", CAMPOS_ARCA,    ALIASES_ARCA,    "Punto de Venta"),
    ]:
        if _fsrc is not None:
            _fid = (
                f"{_fsrc.name}_{_fsrc.size}" if hasattr(_fsrc, "name")
                else str(_fsrc)
            )
            if st.session_state.get(f"{_kp}_file_id") != _fid:
                _cols = _detectar_columnas(_fsrc, _hkw)
                if _cols:
                    _sug, _conf = sugerir_mapeo(_cols, _campos, _aliases)
                    st.session_state[f"{_kp}_sug"]  = _sug
                    st.session_state[f"{_kp}_conf"] = _conf
                    st.session_state[f"{_kp}_cols"] = _cols
                    # Limpiar keys de widgets del mapeo para que usen los nuevos defaults
                    for _ck in list(_campos.keys()):
                        st.session_state.pop(f"{_kp}_{_ck}", None)
                if hasattr(_fsrc, "seek"):
                    _fsrc.seek(0)
                st.session_state[f"{_kp}_file_id"] = _fid

    # ── Reglas de memoria por CUIT ────────────────────────────────────────────
    reglas_sidebar = cargar_reglas()
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
                    if st.button("✕", key=f"del_regla_{_cuit_k}",
                                 help="Eliminar esta regla"):
                        eliminar_regla(_cuit_k)
                        st.rerun()

    procesar = st.button("▶ Procesar", type="primary", use_container_width=True)

    # ── Histórico
    st.markdown("---")
    st.markdown("### 🕓 Histórico")
    prev_s1, prev_s2, prev_s3, prev_meta = cargar_csv()

    if prev_s1 is not None:
        fecha_str = str(prev_meta.get("fecha_proceso", ""))[:16].replace("T", " ")
        st.caption(f"Último proceso: {fecha_str}")
        st.caption(f"Tolerancia: ${float(prev_meta.get('tolerancia', 0)):.2f}")
        cargar_prev = st.button("Cargar último resultado", use_container_width=True)
    else:
        cargar_prev = False

    historicos = listar_historicos()
    if historicos:
        st.markdown(f"**Snapshots guardados:** {len(historicos)}")
        sel_hist = st.selectbox(
            "Comparar snapshot:",
            options=[None] + historicos,
            format_func=lambda h: "— Ninguno —" if h is None else f"{h['label']} ({h['conc']}/{h['n']} conc.)",
        )
    else:
        sel_hist = None


# ── Lógica principal ──────────────────────────────────────────────────────────

# ── Panel central de revisión de mapeo de columnas ───────────────────────────

_cols_l = st.session_state.get("ml_cols", [])
_cols_a = st.session_state.get("ma_cols", [])
_tiene_archivos = bool(_cols_l or _cols_a)

if _tiene_archivos:
    _map_open = not st.session_state.get("loaded", False)
    with st.expander("🗂️ Emparejamiento de columnas", expanded=_map_open):
        if _cols_l and _cols_a:
            _res_l, _res_a = _render_mapeo_parejas(_cols_l, _cols_a)
            st.session_state["mapeo_listado"] = {"header_keyword": "Comprobante",    "columnas": _res_l}
            st.session_state["mapeo_arca"]    = {"header_keyword": "Punto de Venta", "columnas": _res_a}
        elif _cols_l:
            st.info("Cargá también el archivo de ARCA para ver el emparejamiento.")
        else:
            st.info("Cargá también el archivo del Listado para ver el emparejamiento.")

# ─────────────────────────────────────────────────────────────────────────────

if procesar:
    if not src_listado or not src_arca:
        st.error("Se necesitan ambos archivos.")
        st.stop()
    with st.spinner("Procesando..."):
        try:
            mapeo_l = st.session_state.get("mapeo_listado", copy.deepcopy(MAPEOS_DEFAULT["listado"]["Colppy"]))
            mapeo_a = st.session_state.get("mapeo_arca",    copy.deepcopy(MAPEOS_DEFAULT["arca"]["ARCA"]))
            df_listado = load_listado_iva(src_listado, mapeo_l)
            df_arca    = load_arca(src_arca, mapeo_a)
        except Exception as e:
            st.error(f"Error al leer archivos: {e}")
            st.stop()
    if df_listado is None or df_arca is None:
        st.stop()

    s1, s2, s3 = conciliar(df_listado, df_arca, tolerancia)
    hubo_cambio = guardar_csv(s1, s2, s3, tolerancia)
    periodo_det = _detectar_periodo(s1)
    st.session_state.update({
        "s1": s1, "s2": s2, "s3": s3,
        "tol": tolerancia, "loaded": True,
        "periodo_actual": periodo_det,
        "correcciones": {},   # resetea correcciones al reprocesar
    })
    if hubo_cambio:
        st.success("Conciliación procesada. Snapshot histórico guardado (hay cambios).")
    else:
        st.info("Conciliación procesada. Sin cambios respecto al resultado anterior (no se guardó snapshot).")

elif cargar_prev and prev_s1 is not None:
    tol_prev = float(prev_meta.get("tolerancia", TOLERANCIA_DEFAULT))
    st.session_state.update({
        "s1": prev_s1, "s2": prev_s2, "s3": prev_s3,
        "tol": tol_prev, "loaded": True,
        "correcciones":   {},
        "periodo_actual": str(prev_meta.get("periodo", "")),
    })

elif sel_hist is not None and not st.session_state.get("loaded"):
    try:
        s1h, s2h, s3h = cargar_historico(sel_hist["ts"])
        if s1h is None:
            raise ValueError("Snapshot no encontrado en la base de datos.")
        st.session_state.update({
            "s1": s1h, "s2": s2h, "s3": s3h,
            "tol": sel_hist["tol"], "loaded": True,
            "correcciones":   {},
            "periodo_actual": sel_hist.get("periodo", ""),
        })
    except Exception as e:
        st.error(f"No se pudo cargar el snapshot: {e}")


# ── Panel de resultados ───────────────────────────────────────────────────────

if st.session_state.get("loaded"):
    s1  = st.session_state["s1"]
    s2  = st.session_state["s2"]
    s3  = st.session_state["s3"]
    tol = st.session_state["tol"]

    # Cargar reglas UNA VEZ para todo este render (evita múltiples lecturas de disco)
    _reglas_cuit = cargar_reglas()
    _reglas_norm = {
        re.sub(r"[-.\s]", "", k): v
        for k, v in _reglas_cuit.items()
        if v.get("activo", True)
    }

    # KPIs
    n_l    = len(s1)
    n_a    = len(s3) + int(s1["Existe_en_ARCA"].sum())
    n_conc = int(s1["Conciliado"].sum())
    n_sdes = int((s1["Estado"] == "Total OK / Sin desglose").sum())
    n_dif  = int((s1["Estado"] == "Diferencia detectada").sum())
    n_sl   = int((~s1["Existe_en_ARCA"]).sum())
    n_sa   = len(s3)
    n_nc   = int(s1.get("ARCA_es_NC", pd.Series(dtype=bool)).sum())
    n_ext  = int((s1.get("Origen","") == "Exterior").sum()) if "Origen" in s1.columns else 0

    st.markdown(f"""
    <div class="kpi-row">
      <div class="kpi bl"><div class="n">{n_l}</div><div class="l">Listado</div></div>
      <div class="kpi bl"><div class="n">{n_a}</div><div class="l">ARCA</div></div>
      <div class="kpi ok"><div class="n">{n_conc}</div><div class="l">Conciliados</div></div>
      <div class="kpi gr"><div class="n">{n_sdes}</div><div class="l">Total OK s/desg.</div></div>
      <div class="kpi or"><div class="n">{n_dif}</div><div class="l">Con diferencias</div></div>
      <div class="kpi re"><div class="n">{n_sl}</div><div class="l">Solo Listado</div></div>
      <div class="kpi re"><div class="n">{n_sa}</div><div class="l">Solo ARCA</div></div>
      <div class="kpi pu"><div class="n">{n_nc}</div><div class="l">NC ARCA</div></div>
      <div class="kpi or"><div class="n">{n_ext}</div><div class="l">Ext. Listado</div></div>
    </div>
    """, unsafe_allow_html=True)

    # Descarga Excel — aplica correcciones + reglas de memoria antes de generar
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
    lbl_xl = f"⬇ Excel completo (3 hojas)" + (f"  ·  {_lbl_extras}" if _lbl_extras else "")

    col_xl, col_inf = st.columns([2, 5])
    with col_xl:
        st.download_button(
            lbl_xl,
            data=generar_excel(s1_export, s2, s3),
            file_name=f"ConciliacionIVA_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", use_container_width=True,
        )
    with col_inf:
        st.caption(f"Tolerancia: ±${tol:.2f}  |  {n_conc} conciliados / {n_l} totales  |  {n_nc} NC en ARCA  |  {n_ext} exterior en Listado")

    st.markdown("---")

    # ── Tabs ─────────────────────────────────────────────────────────────────
    tab1, tab2, tab3 = st.tabs([
        f"Conciliación ({n_l})",
        f"Solo en Listado ({n_sl})",
        f"Solo en ARCA ({n_sa})",
    ])

    # ── Tab 1: Conciliación ───────────────────────────────────────────────────
    with tab1:
        all_estados = list(ESTADO_ICON.values())
        all_origenes = ["Nacional", "Exterior"] if "Origen" in s1.columns else []

        fc1, fc2, fc3, fc4 = st.columns([3, 2, 2, 2])
        with fc1:
            sel_estados = st.multiselect(
                "Estado:", all_estados, default=all_estados, key="sel_estados",
            )
        with fc2:
            if all_origenes:
                sel_orig = st.multiselect("Origen:", all_origenes, default=all_origenes, key="sel_orig")
            else:
                sel_orig = []
        with fc3:
            solo_nc = st.checkbox("Solo NC (ARCA)", key="solo_nc")
        with fc4:
            solo_memoria = st.checkbox("Solo memoria 📋", key="solo_memoria",
                                       help="Muestra solo filas donde se aplicó una regla guardada")

        correcciones = st.session_state.get("correcciones", {})

        d1 = s1.copy()
        d1["Estado"] = d1["Estado"].map(ESTADO_ICON).fillna(d1["Estado"])

        # Aplicar correcciones manuales de sesión (mayor precedencia)
        if correcciones:
            d1["Estado"] = d1.apply(
                lambda r: correcciones[r["Comprobante"]]["estado_usuario"]
                          if r["Comprobante"] in correcciones else r["Estado"],
                axis=1,
            )

        # Aplicar reglas de memoria por CUIT (solo donde no hay corrección manual)
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

        d1_disp = _fmt_bool(d1, ["Existe_en_ARCA","Match_Neto","Match_IVA","Match_Total","Conciliado","ARCA_es_NC"])
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
            "Dif_Neto":    st.column_config.NumberColumn("Δ Neto",  format="$ %.2f"),
            "Dif_IVA":     st.column_config.NumberColumn("Δ IVA",   format="$ %.2f"),
            "Dif_Total":   st.column_config.NumberColumn("Δ Total", format="$ %.2f"),
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
            pos   = filas_sel[0]
            idx   = d1_disp.index[pos]
            fila_disp = d1_disp.iloc[pos]
            fila_orig = s1.loc[idx]   # valores originales con numéricos

            comp       = fila_orig.get("Comprobante", "")
            razon      = fila_orig.get("Razon_Social", "")
            est_algo   = ESTADO_ICON.get(fila_orig.get("Estado", ""), fila_orig.get("Estado", ""))
            est_tabla  = d1_disp.iloc[pos].get("Estado", est_algo)   # puede diferir si hay memoria/corrección
            periodo_actual = st.session_state.get("periodo_actual", "")

            st.markdown("---")
            st.markdown(f"#### ✏️ Corrección — `{comp}` &nbsp; {razon}")

            pc1, pc2 = st.columns(2)
            with pc1:
                if est_tabla != est_algo:
                    st.markdown(f"**Estado algoritmo:** {est_algo}  ·  **En tabla:** {est_tabla}")
                else:
                    st.markdown(f"**Estado actual:** {est_algo}")

                # Datos de identificación completos
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
                    st.dataframe(
                        pd.DataFrame(meta_data),
                        hide_index=True, use_container_width=True,
                    )

                # Tabla comparativa Listado vs ARCA
                cmp_rows = [
                    {
                        "Campo":   lbl,
                        "Listado": fila_orig.get(campo),
                        "ARCA":    fila_orig.get(f"ARCA_{campo}"),
                        "Δ":       fila_orig.get(f"Dif_{campo}"),
                    }
                    for campo, lbl in [("Neto", "Neto"), ("IVA", "IVA"),
                                       ("Total", "Total")]
                    if campo in fila_orig.index
                ]
                if cmp_rows:
                    st.dataframe(
                        pd.DataFrame(cmp_rows),
                        hide_index=True, use_container_width=True,
                        column_config={
                            "Listado": st.column_config.NumberColumn("Listado", format="$ %.2f"),
                            "ARCA":    st.column_config.NumberColumn("ARCA",    format="$ %.2f"),
                            "Δ":       st.column_config.NumberColumn("Δ",       format="$ %.2f"),
                        },
                    )

            with pc2:
                estados_opciones = list(ESTADO_ICON.values())
                _idx_estado = estados_opciones.index(est_algo) if est_algo in estados_opciones else 0
                nuevo_estado = st.selectbox(
                    "Corregir estado a:",
                    estados_opciones,
                    index=_idx_estado,
                    key=f"corr_estado_{idx}",
                )
                motivo_sel = st.selectbox(
                    "Motivo:", MOTIVOS_CORRECCION, key=f"corr_motivo_{idx}",
                )
                if motivo_sel == "Otro (ver nota)":
                    motivo_custom = st.text_input(
                        "Describí el motivo:",
                        key=f"corr_motivo_custom_{idx}",
                        placeholder="Ej: retención IIBB no contemplada en el sistema",
                    )
                    motivo = f"Otro: {motivo_custom}" if motivo_custom.strip() else "Otro (ver nota)"
                else:
                    motivo = motivo_sel
                nota = st.text_area(
                    "Nota adicional (opcional):", height=68, key=f"corr_nota_{idx}",
                    placeholder="Ej: diferencia de $0.12 por redondeo en alícuota 10.5%",
                )

                cuit_corr      = str(fila_orig.get("CUIT_norm", ""))
                ya_tiene_regla = cuit_corr in _reglas_cuit

                # Forzar valor fresco del checkbox si fue limpiado por un save anterior
                _key_regla = f"corr_regla_{idx}"
                if _key_regla not in st.session_state:
                    st.session_state[_key_regla] = ya_tiene_regla

                guardar_como_regla = st.checkbox(
                    "📋 Guardar como regla para este proveedor"
                    + (" *(ya existe — se actualizará)*" if ya_tiene_regla else ""),
                    key=_key_regla,
                    help="Futuros comprobantes de este CUIT se corregirán automáticamente "
                         "con este estado y motivo en próximas conciliaciones.",
                )

                btn_cols = st.columns(2)
                with btn_cols[0]:
                    if st.button("💾 Guardar corrección", type="primary", use_container_width=True,
                                 key=f"corr_save_{idx}"):
                        guardar_feedback(fila_orig, nuevo_estado, motivo, nota, periodo_actual)
                        if "correcciones" not in st.session_state:
                            st.session_state["correcciones"] = {}
                        st.session_state["correcciones"][comp] = {
                            "estado_usuario": nuevo_estado,
                            "motivo":         motivo,
                            "nota":           nota,
                        }
                        if guardar_como_regla and cuit_corr:
                            guardar_regla(cuit_corr, nuevo_estado, motivo,
                                          str(fila_orig.get("Razon_Social", "")))
                        # Limpiar todos los widgets del panel para que la próxima
                        # selección arranque con estado fresco
                        for _wk in [f"corr_estado_{idx}", f"corr_motivo_{idx}",
                                    f"corr_motivo_custom_{idx}", f"corr_nota_{idx}",
                                    _key_regla]:
                            st.session_state.pop(_wk, None)
                        st.rerun()
                with btn_cols[1]:
                    if st.button("✕ Cancelar", use_container_width=True, key=f"corr_cancel_{idx}"):
                        for _wk in [f"corr_estado_{idx}", f"corr_motivo_{idx}",
                                    f"corr_motivo_custom_{idx}", f"corr_nota_{idx}",
                                    _key_regla]:
                            st.session_state.pop(_wk, None)
                        st.rerun()

    # ── Tab 2: Solo en Listado ────────────────────────────────────────────────
    with tab2:
        all_orig2 = ["Nacional", "Exterior"] if "Origen" in s2.columns else []
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
            resumen = d2["Origen"].value_counts()
            cols_res = st.columns(len(resumen))
            for i, (orig, cnt) in enumerate(resumen.items()):
                cls = "or" if orig == "Exterior" else "bl"
                cols_res[i].markdown(f"""
                <div class="kpi {cls}" style="margin:0">
                  <div class="n">{cnt}</div><div class="l">{orig}</div>
                </div>""", unsafe_allow_html=True)

        st.dataframe(
            d2, use_container_width=True, height=400, hide_index=True,
            column_config={
                "Tipo_Doc":    st.column_config.TextColumn("Tipo"),
                "Origen":      st.column_config.TextColumn("Origen", width="small"),
                "Neto":        st.column_config.NumberColumn("Neto",  format="$ %.2f"),
                "IVA":         st.column_config.NumberColumn("IVA",   format="$ %.2f"),
                "Total":       st.column_config.NumberColumn("Total", format="$ %.2f"),
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
