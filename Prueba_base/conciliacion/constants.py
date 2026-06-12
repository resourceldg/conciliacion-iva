"""
Constantes y configuración semántica de columnas.

Incluye:
  - Rutas de datos (DATA_DIR, DB_FILE, etc.)
  - Conjuntos de control: BOOL_COLS, NC_ARCA, EXT_PREFIXES
  - Mapa de estados con íconos (ESTADO_ICON / ICON_ESTADO)
  - Definición canónica de columnas por fuente (CAMPOS_LISTADO, CAMPOS_ARCA)
  - Sinónimos de columnas para detección robusta (ALIASES_*)
  - Grupos de emparejamiento para la UI (GRUPOS_PAREJAS)
  - Listas de motivos de corrección (MOTIVOS_CORRECCION)
"""
import copy
from pathlib import Path

# ── Tolerancia por defecto ────────────────────────────────────────────────────

TOLERANCIA_DEFAULT = 0.07

# ── Rutas de datos ────────────────────────────────────────────────────────────

DATA_DIR    = Path(__file__).parent.parent / "data"
PERSIST_DIR = DATA_DIR / "persistencia"
DATA_DIR.mkdir(exist_ok=True)
PERSIST_DIR.mkdir(exist_ok=True)

DB_FILE = DATA_DIR / "conciliacion_iva.db"

# Rutas legacy — solo usadas por _migrar_legacy() en la primera ejecución
CSV_CONC      = DATA_DIR / "conciliacion_last.csv"
CSV_LIST      = DATA_DIR / "solo_listado_last.csv"
CSV_ARCA      = DATA_DIR / "solo_arca_last.csv"
CSV_META      = DATA_DIR / "meta_last.csv"
REGLAS_FILE   = DATA_DIR / "reglas_cuit.json"
MAPEOS_FILE   = DATA_DIR / "mapeos.json"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"

# ── Columnas booleanas ────────────────────────────────────────────────────────

# Necesitan conversión explícita al releer desde JSON/CSV
BOOL_COLS = {
    "Existe_en_ARCA", "Match_Neto", "Match_IVA", "Match_Total", "Conciliado",
    "es_NC", "ARCA_es_NC", "ARCA_Neto_Derivado", "neto_derivado", "CUIT_mismatch",
}

# ── Tipos de comprobante especiales ──────────────────────────────────────────

# ARCA exporta estos valores en el campo "Tipo" para Notas de Crédito.
# Incluye forma singular ("3 - Nota de Crédito A", export XLSX) y plural
# ("Notas de Crédito A", export CSV de Mis Comprobantes).
NC_ARCA = {"nota de crédito", "nota de credito", "notas de crédito", "notas de credito"}

# ── Tabla oficial AFIP/ARCA de tipos de comprobante ──────────────────────────
# Fuente: "TABLA TIPO COMPROBANTES V.0 25082010" (códigos 001-104) + códigos
# FCE MiPyME (201-213, RG 4367/2018). El export CSV de "Mis Comprobantes"
# trae el código numérico crudo; el XLSX trae "N - Denominación".

TIPOS_AFIP: dict[int, str] = {
    1: "Factura A", 2: "N.Débito A", 3: "N.Crédito A",
    4: "Recibo A", 5: "NV Contado A",
    6: "Factura B", 7: "N.Débito B", 8: "N.Crédito B",
    9: "Recibo B", 10: "NV Contado B",
    11: "Factura C", 12: "N.Débito C", 13: "N.Crédito C",
    14: "Doc. Aduanero", 15: "Recibo C", 16: "NV Contado C",
    19: "Fact. Exportación", 20: "ND Exterior", 21: "NC Exterior",
    22: "Fact. Exp. Simplif.",
    30: "Compra B. Usados", 31: "Mandato/Consig.", 32: "Comp. Reciclados",
    34: "Comp. A RG1415", 35: "Comp. B RG1415", 36: "Comp. C RG1415",
    37: "ND RG1415", 38: "NC RG1415",
    39: "Otros A RG1415", 40: "Otros B RG1415", 41: "Otros C RG1415",
    50: "Recibo Fact. A FC", 51: "Factura M", 52: "N.Débito M",
    53: "N.Crédito M", 54: "Recibo M", 55: "NV Contado M",
    56: "Comp. M RG1415", 57: "Otros M RG1415",
    58: "Cta. Vta. M", 59: "Liquidación M",
    60: "Cta. Vta. A", 61: "Cta. Vta. B",
    63: "Liquidación A", 64: "Liquidación B",
    65: "NC Liquidación",
    66: "Desp. Importación", 67: "Import. Servicios", 68: "Liquidación C",
    70: "Recibo Fact. Créd.", 71: "Créd. Contrib. Patr.",
    73: "F.1116 RT", 74: "Carta Porte Autom.", 75: "Carta Porte Ferrov.",
    80: "Cierre Z", 81: "Tique Fact. A", 82: "Tique Fact. B", 83: "Tique",
    84: "Fact. Serv. Públicos", 85: "NC Serv. Públicos", 86: "ND Serv. Públicos",
    87: "Otros Serv. Exterior", 88: "Remito Electrónico",
    89: "ND Doc. Exceptuados", 90: "NC Doc. Exceptuados", 91: "Remito R",
    92: "Ajuste +DF", 93: "Ajuste -DF", 94: "Ajuste +CF", 95: "Ajuste -CF",
    96: "F.1116 B", 97: "F.1116 C", 99: "Otros RG3419",
    101: "Ajuste DJ IVA (+)", 102: "Ajuste DJ IVA (-)",
    103: "Nota Asignación", 104: "NC Asignación",
    # FCE MiPyME (no figura en la tabla 2010; vigente desde RG 4367/2018)
    201: "FCE A", 202: "ND FCE A", 203: "NC FCE A",
    206: "FCE B", 207: "ND FCE B", 208: "NC FCE B",
    211: "FCE C", 212: "ND FCE C", 213: "NC FCE C",
}

# Códigos que corresponden a Notas de Crédito → los importes se netean
# en negativo (todas las denominaciones "NOTAS DE CREDITO..." de la tabla
# + las NC de FCE MiPyME).
NC_AFIP: set[int] = {3, 8, 13, 21, 38, 53, 65, 85, 90, 104, 203, 208, 213}

# Prefijos Colppy que corresponden a facturas del exterior (FCC/FCE)
EXT_PREFIXES = {"fcc-a", "fcc-b", "fcc-c", "fce-a", "fce-b", "fce-c"}

# ── Estados de conciliación ───────────────────────────────────────────────────

# Mapa estado canónico → etiqueta con ícono para la UI
ESTADO_ICON = {
    "Conciliado":              "✅ Conciliado",
    "Total OK / Sin desglose": "🟡 Total OK s/desglose",
    "Diferencia detectada":    "🔴 Diferencia",
    "CUIT no coincide":        "⚠️ CUIT no coincide",
    "Sin match en ARCA":       "⬜ Sin match",
    "Exterior / No en ARCA":   "🌐 Exterior s/ARCA",
    "Revisado / Aceptado":     "✔️ Revisado / Aceptado",
}
ICON_ESTADO = {v: k for k, v in ESTADO_ICON.items()}

# ── Definición canónica de columnas por fuente ────────────────────────────────

# Campos semánticos → nombre de columna por defecto en el Listado (Colppy)
CAMPOS_LISTADO = {
    "comprobante":      "Comprobante",
    "fecha":            "Fecha Factura",
    "tipo":             "Tipo",
    "cuit":             "CUIT/DNI",
    "razon_social":     "Razón Social",
    "condicion_iva":    "Condición IVA",
    "neto":             "Neto",
    "iva":              "IVA",
    "total":            "Total",
    "otros_tributos_l": "",
}

# Campos semánticos → nombre de columna por defecto en ARCA
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

# ── Sinónimos conocidos por campo semántico ───────────────────────────────────
# Permiten detección determinista cuando el proveedor cambia el nombre de una columna.

ALIASES_LISTADO: dict[str, list[str]] = {
    "comprobante":   ["comprobante", "nro comprobante", "numero comprobante", "comp",
                      "cod comprobante", "codigo comprobante", "numero", "nro"],
    "fecha":         ["fecha", "fecha factura", "fecha emision", "fecha_factura",
                      "fec factura", "fec. factura", "fec emision"],
    "tipo":          ["tipo", "tipo comprobante", "tipo doc", "clase", "codigo", "cod",
                      "tipo comprob", "tipo comprob."],
    "cuit":          ["cuit", "cuit/dni", "cuit dni", "nro doc", "documento",
                      "nro documento", "nrodoc", "nro.doc.", "nro.doc", "nrodoc."],
    "razon_social":  ["razon social", "razon", "proveedor", "nombre", "denominacion"],
    "condicion_iva": ["condicion iva", "cond iva", "condicion", "condicioniva", "tipo iva"],
    "neto":             ["neto", "importe neto", "monto neto", "neto gravado", "gravado"],
    "iva":              ["iva", "importe iva", "monto iva", "iva 21%", "iva 10.5%", "iva 27%"],
    "total":            ["total", "importe total", "monto total", "total factura"],
    "otros_tributos_l": ["perc iibb", "perc.iibb", "percepcion iibb", "percepción iibb",
                         "perc iva", "perc.iva", "percepcion iva", "percepción iva",
                         "perc gan", "perc.gan", "percepcion ganancias", "ganancias",
                         "ret iva", "ret.iva", "retencion iva", "retención iva",
                         "imp int", "imp.int", "impuesto interno", "imp interno",
                         "otros tributos", "otros trib", "percepciones", "retenciones"],
}

ALIASES_ARCA: dict[str, list[str]] = {
    "punto_venta":     ["punto de venta", "pto venta", "pto de venta", "punto venta", "ptoventa"],
    "numero":          ["numero desde", "nro desde", "numero", "nro",
                        "num desde", "numero comprobante"],
    "fecha":           ["fecha", "fecha cbte", "fecha comprobante",
                        "fecha de emision", "fecha emision"],
    "tipo":            ["tipo", "tipo comprobante", "tipo de comprobante",
                        "codigo de comprobante", "cod comprobante", "codigo", "clase"],
    "cuit_emisor":     ["nro doc emisor", "nro. doc. emisor", "cuit emisor", "cuit",
                        "documento emisor", "cuit/dni emisor"],
    "denominacion":    ["denominacion emisor", "denominación emisor",
                        "razon social emisor", "denominacion", "razon social"],
    "neto_gravado":    ["neto gravado total", "neto gravado", "neto grav total", "neto grav",
                        "imp neto gravado total", "imp. neto gravado total"],
    "neto_no_gravado": ["neto no gravado", "neto no grav",
                        "imp neto no gravado", "imp. neto no gravado"],
    "op_exentas":      ["op exentas", "op. exentas", "operaciones exentas", "exentas",
                        "imp op exentas", "imp. op. exentas"],
    "otros_tributos":  ["otros tributos", "otros trib", "tributos"],
    "total_iva":       ["total iva", "iva", "imp iva", "importe iva"],
    "total":           ["imp total", "imp. total", "total", "importe total", "total importe"],
}

# Mapeos por defecto (usados cuando no hay configuración guardada en BD)
MAPEOS_DEFAULT = {
    "listado": {"Colppy": {"header_keyword": "Comprobante",    "columnas": copy.deepcopy(CAMPOS_LISTADO)}},
    "arca":    {"ARCA":   {"header_keyword": "Punto de Venta", "columnas": copy.deepcopy(CAMPOS_ARCA)}},
}

# ── Configuración de emparejamiento UI ───────────────────────────────────────

# Grupos de emparejamiento entre columnas de ambos archivos.
# required=True  → sin este campo la conciliación no puede ejecutarse
# required=False → campo informativo; puede dejarse sin mapear
GRUPOS_PAREJAS = [
    {
        "titulo":  "🔑 Identificación",
        "detalle": "Determinan qué comprobante del Listado corresponde a cuál de ARCA",
        "campos": [
            {
                "label":         "Comprobante *",
                "l_campo":       "comprobante",
                "a_campo":       None,
                "a_fijo":        "Pto. Venta + Número (construido automáticamente)",
                "a_implícitos":  ["punto_venta", "numero"],
                "required":      True,
                "operation":     "identity",
                "comparison":    "exact",
                "semantic_type": "comprobante",
            },
            {
                "label":         "CUIT proveedor *",
                "l_campo":       "cuit",
                "a_campo":       "cuit_emisor",
                "required":      True,
                "operation":     "identity",
                "comparison":    "exact",
                "semantic_type": "cuit",
            },
        ],
    },
    {
        "titulo":  "💰 Importes comparados",
        "detalle": "Requeridos para la conciliación — las diferencias determinan el estado",
        "campos": [
            {
                "label":         "Neto *",
                "l_campo":       "neto",
                "a_campo":       "neto_gravado",
                "required":      True,
                "multi_l":       True,
                "operation":     "sum",
                "comparison":    "approx",
                "semantic_type": "importe",
            },
            {
                "label":         "IVA *",
                "l_campo":       "iva",
                "a_campo":       "total_iva",
                "required":      True,
                "multi_l":       True,
                "multi_a":       True,
                "operation":     "sum",
                "comparison":    "approx",
                "semantic_type": "importe",
            },
            {
                "label":         "Total *",
                "l_campo":       "total",
                "a_campo":       "total",
                "required":      True,
                "multi_l":       True,
                "multi_a":       True,
                "operation":     "sum",
                "comparison":    "approx",
                "semantic_type": "importe",
            },
            {
                "label":         "Otros Tributos",
                "l_campo":       "otros_tributos_l",
                "a_campo":       "otros_tributos",
                "required":      False,
                "multi_l":       True,
                "operation":     "sum",
                "comparison":    "approx",
                "semantic_type": "importe",
            },
        ],
    },
    {
        "titulo":  "ℹ️ Información adicional",
        "detalle": "Opcional — visible en la tabla, no afecta el cruce",
        "campos": [
            {
                "label":         "Fecha",
                "l_campo":       "fecha",
                "a_campo":       "fecha",
                "required":      False,
                "operation":     "identity",
                "comparison":    "exact",
                "semantic_type": "fecha",
            },
            {
                "label":         "Tipo",
                "l_campo":       "tipo",
                "a_campo":       "tipo",
                "required":      False,
                "operation":     "identity",
                "comparison":    "exact",
                "semantic_type": "texto",
            },
            {
                "label":         "Razón Social",
                "l_campo":       "razon_social",
                "a_campo":       "denominacion",
                "required":      False,
                "operation":     "identity",
                "comparison":    "contains",
                "semantic_type": "texto",
            },
        ],
    },
]

# Campos ARCA que se usan internamente pero no se muestran como parejas
_ARCA_INTERNOS = ["neto_no_gravado", "op_exentas"]

# Ranking y badges de confianza para el panel de emparejamiento
_CONF_RANK = {"exact": 0, "norm": 1, "multi": 1, "alias": 2, "fuzzy": 3, "none": 4}
_CONF_BADGE = {
    "exact": ("✅", "#15803d", "Coincidencia exacta"),
    "norm":  ("✅", "#15803d", "Normalizado — coincide"),
    "multi": ("🔢", "#1d4ed8", "Columnas combinadas — se suman al procesar"),
    "alias": ("🟡", "#b45309", "Sinónimo conocido — verificá"),
    "fuzzy": ("🟠", "#c2410c", "Coincidencia aproximada — revisá"),
    "none":  ("❌", "#dc2626", "Sin match — seleccioná manualmente"),
}

# Motivos predefinidos para correcciones manuales
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

# Descripción de columnas auto-mapeadas del Libro IVA Compras (informativo en UI)
_LIBRO_MAPEO_AUTO = {
    "Comprobante":  "Col. Comprobante  o  Suc. + Letra + Número",
    "Fecha":        "Fecha  o  Fec. Factura",
    "Tipo":         "Tipo + Letra del Comprobante  →  FAC-A/B/C, NCC-A/B/C",
    "CUIT":         "CUIT  o  Nro.Doc.",
    "Razón Social": "Proveedor",
    "Cond. IVA":    "Condición IVA  o  Tipo Iva",
    "Neto":         "Neto gravado X% (suma)  o  Gravado",
    "IVA":          "IVA X% (suma de todas las alícuotas)",
    "Total":        "Total",
}
