"""
Funciones utilitarias puras — sin I/O, sin Streamlit.

Incluye:
  - _normalizar_col: normaliza nombres de columna para comparación
  - _combinar_cols: suma una o varias columnas numéricas
  - _restore_bools: convierte columnas booleanas de string/int a bool nativo
  - _agg_total: función de agregación para groupby (primer valor no-cero)
  - _origen_from_cuit_tipo: clasifica Nacional vs Exterior por CUIT y tipo
  - _es_nota_credito_arca: detecta si un tipo ARCA es Nota de Crédito
  - _detectar_periodo: deriva el período (mes/año) desde fechas de facturas
  - _hash_s1: hash del resultado de conciliación para detectar cambios

Estas funciones no tienen efectos secundarios y son completamente testeables
sin necesidad de mockear Streamlit ni acceder a la base de datos.
"""
import re
import unicodedata

import pandas as pd

from .constants import BOOL_COLS, EXT_PREFIXES, NC_ARCA


def _fix_mojibake(s) -> str:
    """Repara texto UTF-8 mal decodificado como Latin-1 (mojibake).

    El export CSV de 'Mis Comprobantes' de ARCA a veces llega con los acentos
    corruptos (ej: 'DenominaciÃ³n Emisor' en vez de 'Denominación Emisor',
    'Fecha de EmisiÃ³n' en vez de 'Fecha de Emisión') cuando el CSV UTF-8 se
    reinterpreta como Latin-1 al convertirlo a XLSX. Esto rompe el emparejamiento
    de columnas (los nombres con tilde dejan de coincidir con los aliases) y deja
    campos como Fecha/Tipo/Denominación vacíos en la conciliación.

    Solo reconvierte si detecta el patrón típico de mojibake (Ã/Â) y la
    reconversión latin-1→utf-8 es válida; en cualquier otro caso devuelve el
    texto sin tocar para no corromper datos limpios.
    """
    s = str(s)
    if "Ã" not in s and "Â" not in s:
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _normalizar_col(s: str) -> str:
    """Minúsculas, sin acentos, sin puntuación, espacios simples."""
    s = unicodedata.normalize("NFD", _fix_mojibake(s).strip().lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip()


def _combinar_cols(df: pd.DataFrame, col_spec, default: float = 0.0) -> pd.Series:
    """Suma una o varias columnas numéricas. col_spec puede ser str o list[str]."""
    specs = col_spec if isinstance(col_spec, list) else [col_spec]
    parts = [pd.to_numeric(df[c], errors="coerce").fillna(0)
             for c in specs if c and c in df.columns]
    if not parts:
        return pd.Series(default, index=df.index, dtype=float)
    return sum(parts)


def _restore_bools(df: pd.DataFrame) -> pd.DataFrame:
    """Garantiza que las columnas booleanas sean dtype bool.

    Acepta tanto strings "True"/"False" (legacy CSV) como valores nativos
    bool o 0/1 (JSON). Usa where(notna) para evitar FutureWarning de pandas 2.x
    sobre downcasting implícito en .fillna().
    """
    for col in BOOL_COLS:
        if col in df.columns:
            mapped = df[col].map({
                "True": True,  "False": False,
                "true": True,  "false": False,
                "1":    True,  "0":     False,
                True:   True,  False:   False,
                1:      True,  0:       False,
            })
            df[col] = mapped.where(mapped.notna(), other=False).astype(bool)
    return df


def _agg_total(x: pd.Series):
    """Para groupby.agg: primer valor no-cero del Total de un grupo."""
    non_zero = x[x != 0]
    return non_zero.iloc[0] if len(non_zero) > 0 else 0


def _origen_from_cuit_tipo(cuit: str, tipo: str = "") -> str:
    """'Exterior' si el CUIT comienza con 55 o el tipo es FCC/FCE; 'Nacional' en otro caso."""
    if tipo and str(tipo).lower() in EXT_PREFIXES:
        return "Exterior"
    cuit_clean = re.sub(r"[^0-9]", "", str(cuit))
    return "Exterior" if cuit_clean.startswith("55") else "Nacional"


def _es_nota_credito_arca(tipo: str) -> bool:
    """True si el tipo de comprobante ARCA es una Nota de Crédito."""
    t = str(tipo).lower()
    return any(nc in t for nc in NC_ARCA)


def _detectar_periodo(s1: pd.DataFrame) -> str:
    """Deriva el período (mes/año) procesado desde las fechas de facturas del Listado.

    Toma el mes/año más frecuente en Fecha_Factura. En caso de empate, el más reciente.
    Retorna string tipo '2025-11' o '' si no se puede determinar.
    """
    if s1 is None or "Fecha_Factura" not in s1.columns:
        return ""
    fechas = pd.to_datetime(
        s1["Fecha_Factura"], format="mixed", dayfirst=True, errors="coerce"
    ).dropna()
    if fechas.empty:
        return ""
    counts = fechas.dt.to_period("M").value_counts()
    if counts.empty:
        return ""
    max_count  = counts.iloc[0]
    candidates = counts[counts == max_count].index
    return str(max(candidates))


def _hash_s1(s1: pd.DataFrame | None) -> str:
    """Hash del resultado de conciliación para detectar cambios entre corridas.

    Compara solo [Comprobante, Estado, Conciliado] ordenado. Si el conjunto
    de comprobantes y su estado no cambiaron, no vale la pena guardar un snapshot.
    """
    if s1 is None:
        return ""
    try:
        key = s1[["Comprobante", "Estado", "Conciliado"]].sort_values("Comprobante").reset_index(drop=True)
        h   = pd.util.hash_pandas_object(key, index=False).sum()
        return str(h)
    except Exception:
        return ""
