"""
Carga y normalización de los archivos de entrada.

Arquitectura (lado Listado):
  - Un EXTRACTOR por formato (`_extraer_*`): función pura que recibe la hoja
    cruda y produce el registro canónico de comprobante (ver contrato abajo).
    No conoce Streamlit; ante un archivo inválido lanza FormatoInvalido.
  - Un FINALIZADOR común (`_finalizar_listado`): agrupa por (Comprobante,
    CUIT_norm), aplica signos de NC, Origen, NroDoc_norm y etiquetas legibles.
  - `load_listado_iva` despacha según el formato detectado y muestra los
    mensajes de UI. Agregar un formato nuevo = escribir un extractor y
    registrarlo en _FORMATOS.

Contrato del registro canónico (salida de cada extractor, una fila por
alícuota — el finalizador colapsa a una por comprobante):
  Comprobante   "XXXXX-YYYYYYYY" ya validado
  Tipo          FAC-X / NCC-X / NDB-X / NDC-X  (X = letra A/B/C/E…)
  Fecha_Factura, CUIT_DNI, Razon_Social, Condicion_IVA
  Neto, IVA, Total   (numéricos; las NC pueden venir en positivo,
                      el finalizador las pasa a negativo)

Lado ARCA: `load_arca` normaliza 'Mis Comprobantes Recibidos' (XLSX o CSV).

Notas de Crédito: ARCA exporta montos positivos para NC; el loader los convierte a
negativos para que la comparación con el Listado (donde las NC son negativas) sea
directa. El Listado de Colppy ya tiene las NC con valores positivos, así que también
se normalizan aquí.

Factura C (monotributistas): ARCA no desglosa Neto en estos casos. Se deriva
Neto = Imp.Total - Total IVA - Otros Tributos y se marca con 'neto_derivado'.
"""
import re
import unicodedata
import copy

import pandas as pd
import streamlit as st

from .constants import (
    CAMPOS_ARCA, CAMPOS_LISTADO, MAPEOS_DEFAULT,
)
from .file_reader import _mejor_hoja, _find_header, leer_excel, _detectar_formato_colppy
from .utils import (
    _agg_total, _combinar_cols, _es_nota_credito_arca, _fix_mojibake,
    _origen_from_cuit_tipo, _tipo_doc_afip, _to_num_ar,
)


class FormatoInvalido(ValueError):
    """El archivo no tiene el formato esperado. El mensaje es apto para mostrar
    al usuario tal cual (lo renderiza el dispatcher con st.error)."""


# Etiquetas legibles por código de tipo interno (todas las fuentes).
TIPO_LABEL = {
    "FAC-A": "Factura A", "FAC-B": "Factura B", "FAC-C": "Factura C",
    "FAC-E": "Fact. Exterior",
    "FCC-A": "Ext. A", "FCC-B": "Ext. B", "FCC-C": "Ext. C",
    "NCC-A": "NC A", "NCC-B": "NC B", "NCC-C": "NC C",
    "NDB-A": "ND A", "NDB-B": "ND B", "NDB-C": "ND C",
    "NDC-A": "ND A", "NDC-B": "ND B", "NDC-C": "ND C",
    "FCA-A": "FC Elect. A",
}

# Columnas de percepciones/retenciones que se preservan INDIVIDUALMENTE en el
# agrupado, para que el reconciler compare con OR: Match si ARCA.Otros Tributos
# ≈ CUALQUIERA de ellas (no la suma de todas).
_OT_COLS_RE = re.compile(
    r"(perc\.?\s*(iibb|iva|gan|i\.?b\.|gananc)|ret\.?\s*iva|imp\.?\s*int|percep|retenci)"
)

_COMP_RE = r"^\d{5}-\d{8}$"


def _sumar_cols(df: pd.DataFrame, cols: list) -> pd.Series:
    """Suma columnas convertidas a numérico; serie de ceros si la lista es vacía."""
    if not cols:
        return pd.Series(0.0, index=df.index)
    return sum(pd.to_numeric(df[c], errors="coerce").fillna(0) for c in cols)


def _hoja_con_header(sheet: pd.DataFrame, hr: int) -> pd.DataFrame:
    """Toma la fila `hr` como encabezado (con fix de mojibake) y retorna los datos."""
    df = sheet.iloc[hr + 1:].copy()
    df.columns = [_fix_mojibake(c).strip() for c in sheet.iloc[hr]]
    return df.reset_index(drop=True)


def _detectar_ot_cols(df: pd.DataFrame) -> list:
    return [c for c in df.columns if _OT_COLS_RE.search(str(c).lower().strip())]


# ── Finalizador común ─────────────────────────────────────────────────────────

def _finalizar_listado(
    df: pd.DataFrame,
    ot_cols: list | None = None,
    nrodoc_col: str | None = None,
    extra_sum_cols: list | None = None,
) -> pd.DataFrame:
    """Pipeline común post-extracción para todos los formatos del Listado.

    Recibe el registro canónico crudo (una fila por alícuota) y produce el
    DataFrame final: una fila por (Comprobante, CUIT_norm) con NC en negativo,
    Origen, NroDoc_norm y Tipo_Doc legible.

    ot_cols:        columnas de percepciones, sumadas en el agrupado y con el
                    signo de NC aplicado; quedan registradas en
                    attrs["otros_tributos_cols"] para el OR del reconciler.
    nrodoc_col:     columna alternativa de documento (Libro IVA variante B,
                    donde Nro.Doc. puede diferir del CUIT). Si no se indica,
                    NroDoc_norm = CUIT_norm.
    extra_sum_cols: columnas adicionales a preservar sumadas (comparaciones de
                    alícuotas configuradas por el usuario); NO se les aplica el
                    signo de NC, igual que en el comportamiento histórico.
    """
    ot_cols        = [c for c in (ot_cols or []) if c in df.columns]
    extra_sum_cols = [c for c in (extra_sum_cols or [])
                      if c in df.columns and c not in ot_cols]
    for _c in ot_cols + extra_sum_cols:
        df[_c] = pd.to_numeric(df[_c], errors="coerce").fillna(0)

    # CUIT_norm como clave secundaria de agrupación: evita mezclar comprobantes
    # de distintos proveedores que coincidan en número (XXXXX-YYYYYYYY no es
    # único entre proveedores; el par Comprobante+CUIT_norm sí lo es).
    df["CUIT_norm"] = df["CUIT_DNI"].astype(str).str.replace(r"[^0-9]", "", regex=True)
    _agg_extra = {c: (c, "sum") for c in ot_cols + extra_sum_cols}
    agg = df.groupby(["Comprobante", "CUIT_norm"], as_index=False).agg(
        Fecha_Factura=("Fecha_Factura", "first"),
        Tipo=("Tipo", "first"),
        CUIT_DNI=("CUIT_DNI", "first"),
        Razon_Social=("Razon_Social", "first"),
        Condicion_IVA=("Condicion_IVA", "first"),
        Neto=("Neto", "sum"),
        IVA=("IVA", "sum"),
        Total=("Total", _agg_total),
        **_agg_extra,
    )

    agg["Origen"] = agg.apply(
        lambda r: _origen_from_cuit_tipo(r["CUIT_DNI"], r["Tipo"]), axis=1
    )

    if nrodoc_col and nrodoc_col in df.columns:
        _nd_map = df.groupby("Comprobante")[nrodoc_col].first()
        agg["NroDoc_norm"] = (
            agg["Comprobante"].map(_nd_map).astype(str).str.replace(r"[^0-9]", "", regex=True)
        )
    else:
        agg["NroDoc_norm"] = agg["CUIT_norm"]

    _nc_mask = agg["Tipo"].str.upper().str.startswith("NCC")
    for _nc_col in ["Neto", "IVA", "Total"] + ot_cols:
        if _nc_col in agg.columns:
            agg.loc[_nc_mask, _nc_col] = -agg.loc[_nc_mask, _nc_col].abs()

    agg["Tipo_Doc"] = agg["Tipo"].map(TIPO_LABEL).fillna(agg["Tipo"])

    result = agg.reset_index(drop=True)
    result.attrs["otros_tributos_cols"] = list(ot_cols)
    return result


# ── Extractores por formato ───────────────────────────────────────────────────
# Cada extractor retorna (df_canonico, ot_cols, nrodoc_col).

def _extraer_libro_iva(sheet: pd.DataFrame):
    """Libro IVA Compras de Colppy/Xubio. Tres variantes:

    A (clásica): columnas Suc. + Letra + Numero, campo Gravado.
    B (nueva):   columna Comprobante "X-SSSSS-NNNNNNNN", "Neto gravado X%".
    C (IvaCompras): columna "Nro Factura" con formato "FCA 00002-00021696".
    """
    _MSG = "No se encontraron comprobantes válidos en el Libro IVA Compras."

    # Buscar header priorizando "Comprobante" (Variante B) y "Suc." (Variante A).
    # "Suc." puede aparecer en nombres de empresa en filas de datos, generando
    # un falso positivo si se busca primero.
    hr = _find_header(sheet, "Comprobante",
                      ["Suc.", "Numero", "Proveedor", "Nro.Doc.", "Nro Factura", "Nro Factur"])
    if hr is None:
        hr = 0
    df = _hoja_con_header(sheet, hr)

    def _map_tipo_str(t: str, l: str) -> str:
        t_a = unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode("ascii").lower()
        if "credito" in t_a:
            return f"NCC-{l}"
        if "debito" in t_a:
            return f"NDB-{l}"
        return f"FAC-{l}"

    # ── Variante A: Suc. + Letra + Numero ────────────────────────────────────
    if "Suc." in df.columns:
        df = df[pd.to_numeric(df.get("Suc.", pd.Series(dtype=object, index=df.index)),
                              errors="coerce").notna()].copy()
        if df.empty:
            raise FormatoInvalido(_MSG)
        suc   = pd.to_numeric(df["Suc."], errors="coerce").fillna(0).astype(int)
        num   = pd.to_numeric(df["Numero"], errors="coerce").fillna(0).astype(int)
        letra = df.get("Letra", pd.Series("A", index=df.index)).astype(str).str.strip().str.upper()
        df["Comprobante"] = suc.apply(lambda x: f"{x:05d}") + "-" + num.apply(lambda x: f"{x:08d}")
        df = df[df["Comprobante"].str.match(_COMP_RE)].copy()
        tipo_raw = df.get("Tipo Comprob.", pd.Series("", index=df.index)).astype(str)
        df["Tipo"] = [_map_tipo_str(t, l) for t, l in zip(tipo_raw, letra)]
        df["Fecha_Factura"] = df.get("Fec. Factura", pd.Series("", index=df.index))
        df["CUIT_DNI"]      = df.get("Nro.Doc.",     pd.Series("", index=df.index)).astype(str).str.strip()
        df["Razon_Social"]  = df.get("Proveedor",    pd.Series("", index=df.index))
        df["Condicion_IVA"] = df.get("Tipo Iva",     pd.Series("", index=df.index))
        neto_cols = [c for c in df.columns if re.match(r"neto\s+gravado", c.lower().strip())]
        df["Neto"] = (
            _sumar_cols(df, neto_cols)
            if neto_cols
            else pd.to_numeric(df.get("Gravado", pd.Series(0.0, index=df.index)),
                               errors="coerce").fillna(0)
        )
        # No Gravado y Exento integran el neto comparable — espeja Neto_Total_ARCA
        # (= Neto Gravado + Neto No Gravado + Op. Exentas).
        _extra_neto = [c for c in df.columns
                       if re.match(r"^(no\s*gravado|exento)s?$", str(c).lower().strip())]
        df["Neto"] = df["Neto"] + _sumar_cols(df, _extra_neto)
        df["Total"] = pd.to_numeric(df.get("Total", pd.Series(0.0, index=df.index)),
                                    errors="coerce").fillna(0)

    # ── Variante B: Comprobante "X-SSSSS-NNNNNNNN" ya construido ────────────
    elif "Comprobante" in df.columns:
        comp_str = df["Comprobante"].astype(str).str.strip()
        cp = comp_str.str.extract(r'^([A-Za-z])-(\d{1,5})-(\d+)$', expand=True)
        df["_letra"]      = cp[0].fillna("A").str.upper()
        df["Comprobante"] = cp[1].str.zfill(5) + "-" + cp[2].str.zfill(8)
        df = df[df["Comprobante"].str.match(_COMP_RE, na=False)].copy()
        if df.empty:
            raise FormatoInvalido(_MSG)
        tipo_raw = df.get("Tipo", pd.Series("", index=df.index)).astype(str)
        df["Tipo"] = [_map_tipo_str(t, l) for t, l in zip(tipo_raw, df["_letra"])]
        df["Fecha_Factura"] = df.get("Fecha",         pd.Series("", index=df.index))
        df["CUIT_DNI"]      = df.get("CUIT",          pd.Series("", index=df.index)).astype(str).str.strip()
        df["Razon_Social"]  = df.get("Proveedor",     pd.Series("", index=df.index))
        df["Condicion_IVA"] = df.get("Condición IVA", pd.Series("", index=df.index))
        neto_cols = [c for c in df.columns if re.match(r"neto\s+gravado", c.lower().strip())]
        df["Neto"] = _sumar_cols(df, neto_cols)
        _extra_neto = [c for c in df.columns
                       if re.match(r"^(no\s*gravado|exento)s?$", str(c).lower().strip())]
        df["Neto"] = df["Neto"] + _sumar_cols(df, _extra_neto)
        df["Total"] = pd.to_numeric(df.get("Total", pd.Series(0.0, index=df.index)),
                                    errors="coerce").fillna(0)

    # ── Variante C: IvaCompras — "Nro Factura" con formato "FCA 00002-00021696"
    else:
        _nro_col = next((c for c in df.columns if re.search(r"nro[\s._]?factur", c.lower())), None)
        if _nro_col is None:
            raise FormatoInvalido(_MSG)

        comp_raw = df[_nro_col].astype(str).str.strip()
        cp = comp_raw.str.extract(r"^([A-Z]{2,3})\s+(\d{1,5})-(\d+)$", expand=True)
        df["_tipo_pfx"] = cp[0].fillna("FAC")
        df["_letra"]    = cp[0].str[-1].fillna("A").str.upper()
        df["Comprobante"] = cp[1].str.zfill(5) + "-" + cp[2].str.zfill(8)
        df = df[df["Comprobante"].str.match(_COMP_RE, na=False)].copy()
        if df.empty:
            raise FormatoInvalido(_MSG)

        def _pfx_tipo(pfx: str, letra: str) -> str:
            if pfx.upper().startswith("NC"):
                return f"NCC-{letra}"
            return f"FAC-{letra}"

        df["Tipo"] = [_pfx_tipo(p, l) for p, l in zip(df["_tipo_pfx"], df["_letra"])]

        def _col_por_regex(pat):
            return next((c for c in df.columns if re.search(pat, c.lower())), None)

        _fecha_col = _col_por_regex(r"fecha[\s._]?(emis|fact|cbte)?")
        df["Fecha_Factura"] = df[_fecha_col] if _fecha_col else pd.Series("", index=df.index)
        _cuit_col = next((c for c in df.columns if re.search(r"^cuit$", c.lower().strip())), None)
        df["CUIT_DNI"] = (df[_cuit_col].astype(str).str.strip() if _cuit_col
                          else pd.Series("", index=df.index))
        _rs_col = _col_por_regex(r"razon[\s._]?soci")
        df["Razon_Social"] = df[_rs_col] if _rs_col else pd.Series("", index=df.index)
        _cond_col = _col_por_regex(r"condic")
        df["Condicion_IVA"] = df[_cond_col] if _cond_col else pd.Series("", index=df.index)

        # Neto = Monto Gravado* + Monto No Gravado* (espeja Neto_Total_ARCA)
        _neto_cols = (
            [c for c in df.columns if re.search(r"monto\s*grav(?:ado)?", c.lower())]
            + [c for c in df.columns if re.search(r"monto\s*no\s*grav", c.lower())]
        )
        df["Neto"] = _sumar_cols(df, _neto_cols)
        df["IVA"]  = _sumar_cols(df, [c for c in df.columns
                                      if re.search(r"iva[\s._]?factur", c.lower())])
        _total_col = _col_por_regex(r"total[\s._]?fact")
        df["Total"] = (pd.to_numeric(df[_total_col], errors="coerce").fillna(0) if _total_col
                       else pd.Series(0.0, index=df.index))

    # IVA: suma de todas las columnas "IVA X%" (variantes A/B; C ya lo asignó)
    iva_cols = [c for c in df.columns if re.match(r"^iva\s*[\d,.]", c.lower().strip())]
    if iva_cols:
        df["IVA"] = _sumar_cols(df, iva_cols)
    elif "IVA" not in df.columns:
        df["IVA"] = pd.Series(0.0, index=df.index)

    # NroDoc_norm alternativo: "Nro.Doc." si existe (Variante B puede traer un
    # documento distinto al CUIT; en la A es la misma fuente y queda igual).
    nrodoc_col = next(
        (c for c in df.columns if re.sub(r"[^a-z0-9]", "", c.lower()) in ("nrodoc", "nrodocumento")),
        None,
    )
    return df, _detectar_ot_cols(df), nrodoc_col


def _extraer_subdiario(sheet: pd.DataFrame):
    """Subdiario de Compras de Contabilium/Finnegans. Header en fila 0,
    columnas por alícuota; N° de comprobante 'A-00001-00354190' o '0007-26032026'."""
    df = _hoja_con_header(sheet, 0)

    comp_raw = df.get("N° de comprobante",
                      df.get("N°  de comprobante", pd.Series("", index=df.index))).astype(str).str.strip()

    def _parse_comp(s: str) -> str:
        m = re.match(r"^[A-Za-z]-(\d{1,5})-(\d{1,8})$", s)
        if m:
            return m.group(1).zfill(5) + "-" + m.group(2).zfill(8)
        m2 = re.match(r"^(\d{1,5})-(\d{1,8})$", s)
        if m2:
            return m2.group(1).zfill(5) + "-" + m2.group(2).zfill(8)
        return ""

    df["Comprobante"] = comp_raw.apply(_parse_comp)
    df = df[df["Comprobante"].str.match(_COMP_RE, na=False)].copy()
    if df.empty:
        raise FormatoInvalido("No se encontraron comprobantes válidos en el Subdiario de Compras.")

    def _map_tipo(t: str) -> str:
        t = str(t).upper()
        letra = t[-1] if t and t[-1].isalpha() else "A"   # "001-FC A" → "A"
        if "NC" in t:
            return f"NCC-{letra}"
        if "ND" in t:
            return f"NDB-{letra}"
        return f"FAC-{letra}"

    df["Tipo"] = df.get("Tipo de documento", pd.Series("", index=df.index)).astype(str).apply(_map_tipo)
    df["Fecha_Factura"] = df.get("Fecha contable", pd.Series("", index=df.index))
    df["CUIT_DNI"]      = df.get("Cuit",        pd.Series("", index=df.index)).astype(str).str.strip()
    df["Razon_Social"]  = df.get("Proveedor",   pd.Series("", index=df.index))
    df["Condicion_IVA"] = df.get("Cat. fiscal", pd.Series("", index=df.index))

    # Neto: "Neto gravado X%" + "No gravado" (espeja Neto_Total_ARCA)
    df["Neto"] = _sumar_cols(df, [c for c in df.columns if re.match(r"neto\s+gravado", c.lower())]
                                 + [c for c in df.columns if re.match(r"no\s+gravado", c.lower())])
    df["IVA"]  = _sumar_cols(df, [c for c in df.columns if re.match(r"iva\s+[\d,.]", c.lower())])
    df["Total"] = pd.to_numeric(df.get("Total", pd.Series(0.0, index=df.index)),
                                errors="coerce").fillna(0)
    return df, [], None


def _extraer_pasion(sheet: pd.DataFrame):
    """Pasión ERP. Header en fila 0; 'Tipo Comprob.' + 'L' (letra) + 'Nº Comprob.'
    ya en formato XXXXX-YYYYYYYY. Puede exportar decimales con coma."""
    df = _hoja_con_header(sheet, 0)

    comp_col = next((c for c in df.columns if re.match(r"n[º°]\s*comprob", c.lower())), None)
    if comp_col is None:
        raise FormatoInvalido("No se encontró la columna 'Nº Comprob.' en el archivo Pasión.")

    def _norm_comp(s: str) -> str:
        m = re.match(r"^(\d{1,5})-(\d{1,8})$", s.strip())
        return m.group(1).zfill(5) + "-" + m.group(2).zfill(8) if m else ""

    df["Comprobante"] = df[comp_col].astype(str).str.strip().apply(_norm_comp)
    df = df[df["Comprobante"].str.match(_COMP_RE, na=False)].copy()
    if df.empty:
        raise FormatoInvalido("No se encontraron comprobantes válidos en el archivo Pasión.")

    tipo_col  = next((c for c in df.columns if re.match(r"tipo\s+comprob", c.lower())), None)
    letra_col = "L" if "L" in df.columns else None

    def _map_tipo(t: str, l: str) -> str:
        t = str(t).strip().upper()
        l = str(l).strip().upper() if l else "A"
        if not l or l == "NAN":
            l = "A"
        if "CRED" in t or t in ("NC", "N/C", "NCC"):
            return f"NCC-{l}"
        if "DEB" in t or t in ("ND", "N/D", "NDB"):
            return f"NDB-{l}"
        return f"FAC-{l}"

    letras = df[letra_col].astype(str) if letra_col else pd.Series("A", index=df.index)
    tipos  = df[tipo_col].astype(str)  if tipo_col  else pd.Series("FACTURA", index=df.index)
    df["Tipo"] = [_map_tipo(t, l) for t, l in zip(tipos, letras)]

    df["Fecha_Factura"] = df.get("Fecha Comprobante", df.get("Fecha", pd.Series("", index=df.index)))
    df["CUIT_DNI"]      = df.get("C.U.I.T.", pd.Series("", index=df.index)).astype(str).str.strip()
    df["Razon_Social"]  = df.get("Nombre del Proveedor", pd.Series("", index=df.index))
    df["Condicion_IVA"] = df.get("Tipo Responsable", pd.Series("", index=df.index))

    # Neto: gravado + exento + monotributo (ARCA los agrupa en Neto_Total_ARCA)
    neto_cols = (
        [c for c in df.columns if re.match(r"imp\.?\s*gravado", c.lower())]
        + [c for c in df.columns if re.match(r"grav\s+\d", c.lower())]
        + [c for c in df.columns if re.match(r"imp\.?\s*exento", c.lower())]
        + [c for c in df.columns if re.match(r"imp\.?\s*monotrib", c.lower())]
    )
    df["Neto"] = (sum(_to_num_ar(df[c]) for c in neto_cols)
                  if neto_cols else pd.Series(0.0, index=df.index))

    # IVA: columna "Imp.IVA" pre-agregada; fallback a la suma de "IVA *"
    iva_col = next((c for c in df.columns if re.match(r"imp\.?\s*iva", c.lower())), None)
    if iva_col is None:
        iva_cols = [c for c in df.columns
                    if re.match(r"iva\s", c.lower()) or c.lower() == "iva fact."]
        df["IVA"] = (sum(_to_num_ar(df[c]) for c in iva_cols)
                     if iva_cols else pd.Series(0.0, index=df.index))
    else:
        df["IVA"] = _to_num_ar(df[iva_col])

    df["Total"] = _to_num_ar(df.get("Total", pd.Series(0.0, index=df.index)))
    return df, [], None


def _extraer_tango(sheet: pd.DataFrame):
    """Tango Gestión. Header en fila 0, columnas T_COMP / N_COMP / IDENTIFTRI.
    N_COMP = Letra(1) + PtoVta(5) + Num(8), ej. 'A0262100624528'."""
    df = _hoja_con_header(sheet, 0)

    # La detección de formato usa substring ("n_comp" matchea variantes como
    # "N_COMP."), pero este extractor necesita las columnas con nombre exacto.
    _faltantes = [c for c in ("N_COMP", "T_COMP") if c not in df.columns]
    if _faltantes:
        raise FormatoInvalido(
            f"Columnas no encontradas en el archivo Tango: {_faltantes}. "
            "Verificá que sea el export estándar de Tango Gestión."
        )

    # Filtrar filas sin N_COMP válido (filas de totales al final)
    comp_raw   = df["N_COMP"].astype(str).str.strip()
    valid_comp = comp_raw.str.match(r"^[A-Za-z]\d{13}$")
    df = df[valid_comp].copy()
    if df.empty:
        raise FormatoInvalido("No se encontraron comprobantes válidos en el archivo Tango.")

    df["Comprobante"] = comp_raw[valid_comp].str[1:6] + "-" + comp_raw[valid_comp].str[6:14]
    df["_letra"]      = comp_raw[valid_comp].str[0].str.upper().fillna("A")

    t_comp = df["T_COMP"].astype(str).str.strip().str.upper()

    def _map_tipo(t: str, l: str) -> str:
        if t in ("N/C", "NCC", "NC"):
            return f"NCC-{l}"
        if t in ("N/D", "NDB", "ND"):
            return f"NDB-{l}"
        return f"FAC-{l}"

    df["Tipo"] = [_map_tipo(t, l) for t, l in zip(t_comp, df["_letra"])]

    df["Fecha_Factura"] = df.get("FECHA_EMI",  pd.Series("", index=df.index))
    df["CUIT_DNI"]      = df.get("IDENTIFTRI", pd.Series("", index=df.index)).astype(str).str.strip()
    df["Razon_Social"]  = df.get("NOM_PROVE",  pd.Series("", index=df.index))
    df["Condicion_IVA"] = df.get("COND_IVA",   pd.Series("", index=df.index))

    # Neto = gravado + exento (para comparar con Neto_Total_ARCA)
    df["Neto"] = (
        pd.to_numeric(df.get("IMP_NETO",   pd.Series(0, index=df.index)), errors="coerce").fillna(0)
        + pd.to_numeric(df.get("IMP_EXENTO", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    )
    df["IVA"]   = pd.to_numeric(df.get("IMP_IVA",   pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    df["Total"] = pd.to_numeric(df.get("IMP_TOTAL", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    return df, [], None


def _extraer_libro_bim(sheet: pd.DataFrame):
    """Libro IVA Compras con columna B.Imponible.
    Comprobante "FC -A-0008-00036818" → "00008-00036818", Tipo → FAC-A.
    Neto = B.Imponible + No Gravado."""
    hr = _find_header(sheet, "B.Imponible", ["B. Imponible", "Comprobante", "C.U.I.T."])
    if hr is None:
        hr = _find_header(sheet, "B. Imponible", ["Comprobante", "Proveedor"])
    if hr is None:
        raise FormatoInvalido("No se encontró el encabezado en el Libro IVA (B.Imponible).")
    df = _hoja_con_header(sheet, hr)

    _comp_col = next((c for c in df.columns if c.strip().lower() == "comprobante"), None)
    if _comp_col is None:
        raise FormatoInvalido("No se encontró la columna 'Comprobante' en el Libro IVA (B.Imponible).")

    def _parse_comp(s: str):
        m = re.match(r"^(FC|NC|ND)\s+-([A-Za-z])-(\d+)-(\d+)$", s.strip())
        if m:
            tipo_map = {"FC": "FAC", "NC": "NCC", "ND": "NDB"}
            return (f"{m.group(3).zfill(5)}-{m.group(4).zfill(8)}",
                    f"{tipo_map.get(m.group(1).upper(), 'FAC')}-{m.group(2).upper()}")
        return "", ""

    parsed = df[_comp_col].astype(str).str.strip().apply(_parse_comp)
    df["Comprobante"] = [p[0] for p in parsed]
    df["Tipo"]        = [p[1] for p in parsed]
    df = df[df["Comprobante"].str.match(_COMP_RE, na=False)].copy()
    if df.empty:
        raise FormatoInvalido("No se encontraron comprobantes válidos en el Libro IVA (B.Imponible).")

    def _col_por_regex(pat):
        return next((c for c in df.columns if re.search(pat, c.lower())), None)

    _fecha_col = _col_por_regex(r"fecha")
    df["Fecha_Factura"] = df[_fecha_col] if _fecha_col else pd.Series("", index=df.index)
    _cuit_col = _col_por_regex(r"c\.?u\.?i\.?t\.?")
    df["CUIT_DNI"] = (df[_cuit_col].astype(str).str.strip() if _cuit_col
                      else pd.Series("", index=df.index))
    _rs_col = _col_por_regex(r"provee")
    df["Razon_Social"] = df[_rs_col] if _rs_col else pd.Series("", index=df.index)
    # Columna "IVA" contiene la condición del proveedor (RI/MON/NOC/EXE)
    _iva_cond_col = next((c for c in df.columns if c.strip().upper() == "IVA"), None)
    df["Condicion_IVA"] = (df[_iva_cond_col] if _iva_cond_col
                           else pd.Series("", index=df.index))

    # Neto = B.Imponible + No Gravado (espeja Neto_Total_ARCA)
    _bim_col = _col_por_regex(r"b\.?\s*imponible")
    _nog_col = _col_por_regex(r"no\s*gravado")
    df["Neto"] = (
        (pd.to_numeric(df[_bim_col], errors="coerce").fillna(0) if _bim_col
         else pd.Series(0.0, index=df.index))
        + (pd.to_numeric(df[_nog_col], errors="coerce").fillna(0) if _nog_col
           else pd.Series(0.0, index=df.index))
    )

    # IVA: suma de columnas "IVA X%" (excluye la columna de condición "IVA")
    df["IVA"] = _sumar_cols(df, [c for c in df.columns
                                 if re.match(r"^iva\s*[\d,.]", c.lower().strip())])
    _total_col = next((c for c in df.columns if c.strip().lower() == "total"), None)
    df["Total"] = (pd.to_numeric(df[_total_col], errors="coerce").fillna(0) if _total_col
                   else pd.Series(0.0, index=df.index))
    return df, _detectar_ot_cols(df), None


# Registro de formatos: clave de _detectar_formato_colppy → (extractor, etiqueta, ícono)
_FORMATOS = {
    "libro":     (_extraer_libro_iva, "Libro IVA Compras",               "📖"),
    "libro_bim": (_extraer_libro_bim, "Libro IVA Compras (B.Imponible)", "📖"),
    "tango":     (_extraer_tango,     "Tango Gestión",                   "📋"),
    "subdiario": (_extraer_subdiario, "Subdiario de Compras",            "📋"),
    "pasion":    (_extraer_pasion,    "Pasión ERP",                      "📋"),
}


def load_listado_iva(source, mapeo: dict | None = None):
    """Carga y normaliza el lado contable (Listado/Libro IVA Compras).

    Detecta el formato del archivo y despacha al extractor correspondiente;
    el formato "listado" (Listado IVA Compras estándar de Colppy, con mapeo
    de columnas configurable) tiene su propio camino porque es el único que
    usa el emparejamiento de la UI.

    Retorna un DataFrame con una fila por (Comprobante, CUIT) o None si el
    archivo no tiene un formato reconocible (el error ya se mostró al usuario).
    """
    from .column_mapping import _LISTADO_FALLBACKS
    if mapeo is None:
        mapeo = copy.deepcopy(MAPEOS_DEFAULT["listado"]["Colppy"])
    col = mapeo["columnas"]

    fmt = _detectar_formato_colppy(source)
    if hasattr(source, "seek"):
        source.seek(0)

    if fmt in _FORMATOS:
        extractor, etiqueta, icono = _FORMATOS[fmt]
        raw   = leer_excel(source)
        sheet = _mejor_hoja(raw)
        try:
            df, ot_cols, nrodoc_col = extractor(sheet)
        except FormatoInvalido as e:
            st.error(str(e))
            return None
        result = _finalizar_listado(df, ot_cols=ot_cols, nrodoc_col=nrodoc_col)
        st.info(f"{etiqueta} detectado — {len(result)} comprobantes cargados.", icon=icono)
        return result

    # ── Listado IVA Compras estándar (mapeo de columnas configurable) ────────
    raw   = leer_excel(source)
    sheet = _mejor_hoja(raw)
    hr    = _find_header(sheet, mapeo.get("header_keyword", "Comprobante"), _LISTADO_FALLBACKS)
    if hr is None:
        st.error(
            "No se encontró el encabezado en el Listado IVA. "
            "Verificá que sea el **Listado IVA Compras** de Colppy. "
            "Si usás otro formato, configurá el emparejamiento de columnas."
        )
        return None

    df = _hoja_con_header(sheet, hr)

    _renames = {"Perc. IIBB": "Perc_IIBB", "Perc. IVA": "Perc_IVA"}
    for _spec, _dst in [
        (col.get("fecha",         "Fecha Factura"), "Fecha_Factura"),
        (col.get("tipo",          "Tipo"),          "Tipo"),
        (col.get("comprobante",   "Comprobante"),   "Comprobante"),
        (col.get("cuit",          "CUIT/DNI"),      "CUIT_DNI"),
        (col.get("razon_social",  "Razón Social"),  "Razon_Social"),
        (col.get("condicion_iva", "Condición IVA"), "Condicion_IVA"),
        (col.get("neto",          "Neto"),          "Neto"),
        (col.get("iva",           "IVA"),           "IVA"),
        (col.get("total",         "Total"),         "Total"),
    ]:
        if isinstance(_spec, str) and _spec:
            _renames[_spec] = _dst
    df = df.rename(columns=_renames)
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated(keep="first")]

    df = df[df["Comprobante"].astype(str).str.match(_COMP_RE)].copy()

    for _dst, _fld, _dflt in [("Neto", "neto", "Neto"), ("IVA", "iva", "IVA"), ("Total", "total", "Total")]:
        _spec = col.get(_fld, _dflt)
        if isinstance(_spec, list):
            df[_dst] = _combinar_cols(df, _spec)
        elif _dst in df.columns:
            df[_dst] = pd.to_numeric(df[_dst], errors="coerce").fillna(0)
        else:
            df[_dst] = 0.0

    # Otros Tributos según el mapeo del usuario (no autodetección)
    _ot_spec = col.get("otros_tributos_l", "")
    _ot_col_names = (_ot_spec if isinstance(_ot_spec, list)
                     else ([_ot_spec] if isinstance(_ot_spec, str) and _ot_spec else []))
    _ot_valid = [c for c in _ot_col_names if c in df.columns]

    for _opt in ["Fecha_Factura", "Tipo", "Razon_Social", "Condicion_IVA"]:
        if _opt not in df.columns:
            df[_opt] = ""

    # Comparaciones adicionales de alícuotas configuradas por el usuario
    _extra_cols = [
        _xc.get("col_l", "") for _xc in mapeo.get("extra_cols", [])
        if _xc.get("col_l", "") and _xc.get("col_l", "") in df.columns
    ]

    return _finalizar_listado(df, ot_cols=_ot_valid, extra_sum_cols=_extra_cols)


def load_arca(source, mapeo: dict | None = None):
    """Carga y normaliza el reporte 'Mis Comprobantes Recibidos' de ARCA.

    Pasos:
    1. Ubica el encabezado buscando "Punto de Venta".
    2. Construye la clave de comprobante en formato XXXXX-YYYYYYYY para el JOIN.
    3. Convierte todos los importes a numérico.
    4. Detecta Notas de Crédito y multiplica sus importes por -1.
    5. Calcula Neto comparable = Neto Gravado + Neto No Gravado + Op. Exentas.
    6. Para Factura C (monotributistas), deriva Neto = Total - IVA - Otros Tributos.

    Retorna un DataFrame con una fila por comprobante.
    """
    from .column_mapping import _ARCA_FALLBACKS
    if mapeo is None:
        mapeo = copy.deepcopy(MAPEOS_DEFAULT["arca"]["ARCA"])
    col = mapeo["columnas"]

    raw   = leer_excel(source)
    sheet = _mejor_hoja(raw, mapeo.get("header_keyword", "Punto de Venta"))
    hr    = _find_header(sheet, mapeo.get("header_keyword", "Punto de Venta"))
    if hr is None:
        st.error(f"No se encontró encabezado '{mapeo.get('header_keyword','Punto de Venta')}' en Mis Comprobantes ARCA.")
        return None

    df = sheet.copy()
    df.columns = df.iloc[hr]
    df = df.iloc[hr + 1:].reset_index(drop=True)
    df.columns.name = None
    df.columns = [_fix_mojibake(c).strip() for c in df.columns]

    col_pto  = col.get("punto_venta", "Punto de Venta") or "Punto de Venta"
    col_num  = col.get("numero",      "Número Desde")  or "Número Desde"
    _missing = [c for c in [col_pto, col_num] if c not in df.columns]
    if _missing:
        st.error(f"Columnas no encontradas en el archivo ARCA: {_missing}. Revisá el emparejamiento de columnas.")
        return None
    df[col_pto] = pd.to_numeric(df[col_pto], errors="coerce").fillna(0).astype(int)
    df[col_num] = pd.to_numeric(df[col_num], errors="coerce").fillna(0).astype(int)
    df["Comprobante_Key"] = df.apply(
        lambda r: f"{r[col_pto]:05d}-{r[col_num]:08d}", axis=1
    )

    # Pre-combinar campos multi-columna (ej: IVA = IVA 21% + IVA 10.5% + ...)
    c = dict(mapeo["columnas"])

    # ── Fallbacks para el export CSV de Mis Comprobantes ─────────────────────
    # El CSV usa nombres con prefijo "Imp." y "Fecha de Emisión"/"Tipo de
    # Comprobante" en lugar de los nombres del XLSX. Si la columna mapeada
    # (o el default) no existe, probar las variantes conocidas.
    _CSV_VARIANTES = {
        "neto_gravado":    ["Imp. Neto Gravado Total"],
        "neto_no_gravado": ["Imp. Neto No Gravado"],
        "op_exentas":      ["Imp. Op. Exentas"],
        "fecha":           ["Fecha de Emisión", "Fecha de Emision"],
        "tipo":            ["Tipo de Comprobante"],
    }
    for _fld, _alts in _CSV_VARIANTES.items():
        _cur = c.get(_fld)
        if not _cur or _cur not in df.columns:
            for _alt in _alts:
                if _alt in df.columns:
                    c[_fld] = _alt
                    break

    # ── Importes como texto (CSV con coma decimal) → float ───────────────────
    # Convierte todas las columnas de importe reconocibles antes de cualquier
    # combinación o aritmética. Las columnas ya numéricas pasan directo.
    _NUM_COL_RE = re.compile(
        r"^(iva\s*[\d,.]|imp\.?\s*neto|imp\.?\s*op|neto\s|op\.?\s*exentas"
        r"|otros\s*tributos|total\s*iva|imp\.?\s*total|tipo\s*cambio)",
        re.IGNORECASE,
    )
    for _col in df.columns:
        if (
            isinstance(_col, str)
            and _NUM_COL_RE.match(_col.strip().lower())
            and not pd.api.types.is_numeric_dtype(df[_col])
        ):
            df[_col] = _to_num_ar(df[_col])
    for _fld, _std in [("total_iva", "Total IVA"), ("total", "Imp. Total")]:
        _spec = c.get(_fld)
        if isinstance(_spec, list) and len(_spec) > 1:
            df[_std] = _combinar_cols(df, _spec)
            c[_fld] = _std

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
            df[col_i] = _to_num_ar(df[col_i])

    # Fallback al nombre estándar si el mapeo viene vacío (campo opcional sin
    # mapear en la UI): sin Tipo no se detectan las NC y los importes de ARCA
    # quedan con signo incorrecto.
    col_tipo = c.get("tipo") or "Tipo"
    if col_tipo and col_tipo in df.columns:
        df["es_NC"] = df[col_tipo].apply(_es_nota_credito_arca)
    else:
        df["es_NC"] = False
        col_tipo = None
    signo = df["es_NC"].map({True: -1, False: 1})

    # Conversión de moneda extranjera → pesos.
    # ARCA exporta los importes en la moneda original; "Tipo Cambio" es el TC
    # del día del comprobante. Se multiplica solo las filas con Moneda != "$".
    _PESOS = {"$", "pes", "ars"}
    if "Tipo Cambio" in df.columns and "Moneda" in df.columns:
        tc = pd.to_numeric(df["Tipo Cambio"], errors="coerce").fillna(1)
        es_ext = ~df["Moneda"].astype(str).str.strip().str.lower().isin(_PESOS)
        tc_factor = tc.where(es_ext, 1.0)
        df["Moneda_orig"]   = df["Moneda"]
        df["Tipo_Cambio_orig"] = tc
    else:
        tc_factor = pd.Series(1.0, index=df.index)

    for col_i in COLS_IMPORTE:
        if col_i in df.columns:
            df[col_i] = df[col_i] * signo * tc_factor

    for _xc in mapeo.get("extra_cols", []):
        _xcol_a = _xc.get("col_a", "")
        if _xcol_a and _xcol_a in df.columns and _xcol_a not in COLS_IMPORTE:
            df[_xcol_a] = _to_num_ar(df[_xcol_a]) * signo * tc_factor

    c_ng  = c.get("neto_gravado",    "Neto Gravado Total") or "Neto Gravado Total"
    c_nng = c.get("neto_no_gravado", "Neto No Gravado")    or "Neto No Gravado"
    c_oe  = c.get("op_exentas",      "Op. Exentas")        or "Op. Exentas"
    c_ot  = c.get("otros_tributos",  "Otros Tributos")     or "Otros Tributos"
    c_tiva = c.get("total_iva",      "Total IVA")          or "Total IVA"
    c_tot  = c.get("total",          "Imp. Total")         or "Imp. Total"

    # Garantizar float64 en todos los campos de importe referenciados.
    # COLS_IMPORTE usa c.get() crudo (puede ser "") y omite columnas mapeadas con fallback.
    # Si alguna quedó como object (mixed Excel) la aritmética produce object y rompe el
    # dtype-strict assignment de pandas 2.x (TypeError: Invalid value for dtype 'float64').
    for _ec in {c_ng, c_nng, c_oe, c_ot, c_tiva, c_tot}:
        if _ec and _ec in df.columns and not pd.api.types.is_float_dtype(df[_ec]):
            df[_ec] = _to_num_ar(df[_ec])

    df["Neto_Total_ARCA"] = (
        df.get(c_ng,  pd.Series(0, index=df.index))
        + df.get(c_nng, pd.Series(0, index=df.index))
        + df.get(c_oe,  pd.Series(0, index=df.index))
    )

    _col_tot_series = df.get(c_tot, pd.Series(0.0, index=df.index))

    # ARCA a veces pone el mismo importe exento/no-gravado en múltiples columnas
    # (ej: mismo valor en Neto Gravado Total Y Op. Exentas), duplicando el neto.
    # Si el neto calculado supera el total en más de 1%, es doble conteo:
    # derivar neto = Total − IVA − OtrosTrib.
    _col_tiva_s = df.get(c_tiva, pd.Series(0.0, index=df.index))
    _col_ot_s   = df.get(c_ot,   pd.Series(0.0, index=df.index))
    _doble_conteo = (
        df["Neto_Total_ARCA"].abs() > _col_tot_series.abs() * 1.01
    ) & (_col_tot_series.abs() > 0)
    if _doble_conteo.any():
        df.loc[_doble_conteo, "Neto_Total_ARCA"] = (
            _col_tot_series[_doble_conteo]
            - _col_tiva_s[_doble_conteo]
            - _col_ot_s[_doble_conteo]
        )

    sin_desglose = (df["Neto_Total_ARCA"] == 0) & (_col_tot_series != 0)
    df.loc[sin_desglose, "Neto_Total_ARCA"] = (
        _col_tot_series[sin_desglose]
        - _col_tiva_s[sin_desglose]
        - _col_ot_s[sin_desglose]
    )
    # Marcar como derivado: Factura C (sin desglose), doble conteo, y comprobantes
    # donde el importe es íntegramente exento/no-gravado (Neto Gravado ≈ 0).
    # En estos casos el Listado/Libro IVA tiene Neto=0 por diseño — solo el Total es comparable.
    _c_ng_s  = df.get(c_ng,  pd.Series(0.0, index=df.index)).abs()
    _c_nng_s = df.get(c_nng, pd.Series(0.0, index=df.index)).abs()
    _c_oe_s  = df.get(c_oe,  pd.Series(0.0, index=df.index)).abs()
    _solo_exento = (_c_ng_s <= 0.01) & ((_c_nng_s + _c_oe_s) > 0.01)
    df["neto_derivado"] = sin_desglose | _doble_conteo | _solo_exento

    # Etiqueta legible vía tabla oficial AFIP: cubre código numérico crudo
    # (CSV), "N - Denominación" (XLSX) y deja el valor original si no decodifica.
    if col_tipo and col_tipo in df.columns:
        df["Tipo_Doc_ARCA"] = df[col_tipo].map(_tipo_doc_afip)
    else:
        df["Tipo_Doc_ARCA"] = ""

    c_fecha = c.get("fecha") or "Fecha"
    c_cuit  = c.get("cuit_emisor") or "Nro. Doc. Emisor"
    c_denom = c.get("denominacion") or "Denominación Emisor"
    col_std = {
        c_tiva: "Total IVA", c_ot: "Otros Tributos", c_tot: "Imp. Total",
    }
    if c_fecha and c_fecha in df.columns:
        col_std[c_fecha] = "Fecha"
    if col_tipo and col_tipo in df.columns:
        col_std[col_tipo] = "Tipo"
    if c_cuit and c_cuit in df.columns:
        col_std[c_cuit] = "Nro. Doc. Emisor"
    if c_denom and c_denom in df.columns:
        col_std[c_denom] = "Denominación Emisor"
    _renames = {k: v for k, v in col_std.items() if k != v}
    # Drop existing target columns before rename to prevent duplicate axis labels.
    # This happens when the user maps an individual alícuota column (e.g. "IVA 21%")
    # to a standard name ("Total IVA") that already exists as a pre-aggregated column.
    _drop_before_rename = [v for k, v in _renames.items() if v in df.columns]
    if _drop_before_rename:
        df = df.drop(columns=_drop_before_rename)
    df = df.rename(columns=_renames)

    for _opt in ["Fecha", "Tipo", "Denominación Emisor"]:
        if _opt not in df.columns:
            df[_opt] = ""

    df["CUIT_norm"] = df.get("Nro. Doc. Emisor", pd.Series("", index=df.index)).astype(str).str.replace(r"[^0-9]", "", regex=True)

    return df.reset_index(drop=True)
