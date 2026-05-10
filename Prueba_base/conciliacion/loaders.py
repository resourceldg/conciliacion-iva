"""
Carga y normalización de los archivos de entrada.

Incluye:
  - _load_libro_iva: carga el Libro IVA Compras de Colppy (variante A y B)
  - load_listado_iva: carga el Listado IVA Compras exportado de Colppy
  - load_arca: carga el reporte 'Mis Comprobantes Recibidos' de ARCA

Ambas fuentes tienen formatos con filas de metadatos previas al encabezado real y
pueden tener varias filas por comprobante (una por alícuota de IVA). El resultado
normalizado es siempre una fila por comprobante con Neto, IVA y Total calculados.

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
from .utils import _agg_total, _combinar_cols, _es_nota_credito_arca, _origen_from_cuit_tipo


def _load_libro_iva(source) -> "pd.DataFrame | None":
    """Carga el Libro IVA Compras de Colppy. Soporta dos variantes:

    Variante A (clásica): columnas Suc. + Letra + Numero, campo Gravado.
    Variante B (nueva):   columna Comprobante "X-SSSSS-NNNNNNNN",
                          columnas "Neto gravado X%" e "IVA X%".
    """
    raw   = leer_excel(source)
    sheet = _mejor_hoja(raw)

    hr = _find_header(sheet, "Suc.", ["Comprobante", "Numero", "Proveedor", "Nro.Doc."])
    if hr is None:
        hr = 0
    header_cols = [str(c).strip() for c in sheet.iloc[hr]]

    df = sheet.iloc[hr + 1:].copy()
    df.columns = header_cols
    df = df.reset_index(drop=True)

    def _map_tipo_str(t: str, l: str) -> str:
        t_a = unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode("ascii").lower()
        if "credito" in t_a:
            return f"NCC-{l}"
        if "debito" in t_a:
            return f"NDB-{l}"
        return f"FAC-{l}"

    # ── Variante A: Suc. + Letra + Numero ────────────────────────────────────
    if "Suc." in df.columns:
        df = df[pd.to_numeric(df.get("Suc.", pd.Series(dtype=object, index=df.index)), errors="coerce").notna()].copy()
        if df.empty:
            st.error("No se encontraron comprobantes válidos en el Libro IVA Compras.")
            return None
        suc   = pd.to_numeric(df["Suc."], errors="coerce").fillna(0).astype(int)
        num   = pd.to_numeric(df["Numero"], errors="coerce").fillna(0).astype(int)
        letra = df.get("Letra", pd.Series("A", index=df.index)).astype(str).str.strip().str.upper()
        df["Comprobante"] = suc.apply(lambda x: f"{x:05d}") + "-" + num.apply(lambda x: f"{x:08d}")
        df = df[df["Comprobante"].str.match(r"^\d{5}-\d{8}$")].copy()
        tipo_raw = df.get("Tipo Comprob.", pd.Series("", index=df.index)).astype(str)
        df["Tipo"] = [_map_tipo_str(t, l) for t, l in zip(tipo_raw, letra)]
        df["Fecha_Factura"] = df.get("Fec. Factura", pd.Series("", index=df.index))
        df["CUIT_DNI"]      = df.get("Nro.Doc.",     pd.Series("", index=df.index)).astype(str).str.strip()
        df["Razon_Social"]  = df.get("Proveedor",    pd.Series("", index=df.index))
        df["Condicion_IVA"] = df.get("Tipo Iva",     pd.Series("", index=df.index))
        neto_cols_a = [c for c in df.columns if re.match(r"neto\s+gravado", c.lower().strip())]
        df["Neto"] = (
            sum(pd.to_numeric(df[c], errors="coerce").fillna(0) for c in neto_cols_a)
            if neto_cols_a
            else pd.to_numeric(df.get("Gravado", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0)
        )
        df["Total"] = pd.to_numeric(df.get("Total", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0)

    # ── Variante B: Comprobante "X-SSSSS-NNNNNNNN" ya construido ────────────
    elif "Comprobante" in df.columns:
        comp_str = df["Comprobante"].astype(str).str.strip()
        cp = comp_str.str.extract(r'^([A-Za-z])-(\d{1,5})-(\d+)$', expand=True)
        df["_letra"]      = cp[0].fillna("A").str.upper()
        df["Comprobante"] = cp[1].str.zfill(5) + "-" + cp[2].str.zfill(8)
        df = df[df["Comprobante"].str.match(r"^\d{5}-\d{8}$", na=False)].copy()
        if df.empty:
            st.error("No se encontraron comprobantes válidos en el Libro IVA Compras.")
            return None
        tipo_raw = df.get("Tipo", pd.Series("", index=df.index)).astype(str)
        df["Tipo"] = [_map_tipo_str(t, l) for t, l in zip(tipo_raw, df["_letra"])]
        df["Fecha_Factura"] = df.get("Fecha",         pd.Series("", index=df.index))
        df["CUIT_DNI"]      = df.get("CUIT",          pd.Series("", index=df.index)).astype(str).str.strip()
        df["Razon_Social"]  = df.get("Proveedor",     pd.Series("", index=df.index))
        df["Condicion_IVA"] = df.get("Condición IVA", pd.Series("", index=df.index))
        neto_cols_b = [c for c in df.columns if re.match(r"neto\s+gravado", c.lower().strip())]
        df["Neto"] = (
            sum(pd.to_numeric(df[c], errors="coerce").fillna(0) for c in neto_cols_b)
            if neto_cols_b else pd.Series(0.0, index=df.index)
        )
        df["Total"] = pd.to_numeric(df.get("Total", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0)

    else:
        st.error("No se encontraron comprobantes válidos en el Libro IVA Compras.")
        return None

    # IVA: suma de todas las columnas "IVA X%" (ambas variantes)
    iva_cols = [c for c in df.columns if re.match(r"^iva\s*[\d,.]", c.lower().strip())]
    df["IVA"] = (
        sum(pd.to_numeric(df[c], errors="coerce").fillna(0) for c in iva_cols)
        if iva_cols else pd.Series(0.0, index=df.index)
    )

    agg = df.groupby("Comprobante", as_index=False).agg(
        Fecha_Factura=("Fecha_Factura", "first"),
        Tipo=("Tipo", "first"),
        CUIT_DNI=("CUIT_DNI", "first"),
        Razon_Social=("Razon_Social", "first"),
        Condicion_IVA=("Condicion_IVA", "first"),
        Neto=("Neto", "sum"),
        IVA=("IVA", "sum"),
        Total=("Total", _agg_total),
    )

    agg["Origen"]    = agg.apply(lambda r: _origen_from_cuit_tipo(r["CUIT_DNI"]), axis=1)
    agg["CUIT_norm"] = agg["CUIT_DNI"].astype(str).str.replace(r"[^0-9]", "", regex=True)

    _nc_mask = agg["Tipo"].str.upper().str.startswith("NCC")
    for _nc_col in ["Neto", "IVA", "Total"]:
        agg.loc[_nc_mask, _nc_col] = -agg.loc[_nc_mask, _nc_col].abs()

    tipo_label = {
        "FAC-A": "Factura A", "FAC-B": "Factura B", "FAC-C": "Factura C",
        "NCC-A": "NC A", "NCC-B": "NC B", "NCC-C": "NC C",
        "NDB-A": "ND A", "NDB-B": "ND B", "NDB-C": "ND C",
    }
    agg["Tipo_Doc"] = agg["Tipo"].map(tipo_label).fillna(agg["Tipo"])

    n = len(agg)
    st.info(f"Libro IVA Compras detectado — {n} comprobantes cargados.", icon="📖")
    return agg


def load_listado_iva(source, mapeo: dict | None = None):
    """Carga y normaliza el Listado IVA Compras exportado de Colppy.

    Pasos:
    1. Ubica la fila de encabezado buscando "Comprobante".
    2. Filtra solo las filas cuyo Comprobante tiene formato XXXXX-YYYYYYYY,
       descartando filas de totales y subtotales que Colppy agrega al final.
    3. Agrupa por Comprobante sumando Neto e IVA (Colppy puede tener una fila
       por alícuota de IVA), y toma el primer Total no-cero.
    4. Clasifica el origen (Nacional / Exterior) y aplica etiquetas legibles.

    Si detecta formato Libro IVA Compras, delega a _load_libro_iva.
    Retorna un DataFrame con una fila por comprobante, o None si el archivo
    no tiene el formato esperado.
    """
    from .column_mapping import _LISTADO_FALLBACKS
    if mapeo is None:
        mapeo = copy.deepcopy(MAPEOS_DEFAULT["listado"]["Colppy"])
    col = mapeo["columnas"]

    fmt = _detectar_formato_colppy(source)
    if hasattr(source, "seek"):
        source.seek(0)
    if fmt == "libro":
        return _load_libro_iva(source)

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

    df = sheet.copy()
    df.columns = df.iloc[hr]
    df = df.iloc[hr + 1:].reset_index(drop=True)
    df.columns.name = None
    df.columns = [str(c).strip() for c in df.columns]

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

    valid = df["Comprobante"].astype(str).str.match(r"^\d{5}-\d{8}$")
    df    = df[valid].copy()

    for _dst, _fld, _dflt in [("Neto", "neto", "Neto"), ("IVA", "iva", "IVA"), ("Total", "total", "Total")]:
        _spec = col.get(_fld, _dflt)
        if isinstance(_spec, list):
            df[_dst] = _combinar_cols(df, _spec)
        elif _dst in df.columns:
            df[_dst] = pd.to_numeric(df[_dst], errors="coerce").fillna(0)
        else:
            df[_dst] = 0.0

    for _col_n in ["Perc_IIBB", "Perc_IVA"]:
        if _col_n in df.columns:
            df[_col_n] = pd.to_numeric(df[_col_n], errors="coerce").fillna(0)

    for _opt in ["Fecha_Factura", "Tipo", "Razon_Social", "Condicion_IVA"]:
        if _opt not in df.columns:
            df[_opt] = ""

    agg = df.groupby("Comprobante", as_index=False).agg(
        Fecha_Factura=("Fecha_Factura", "first"),
        Tipo=("Tipo", "first"),
        CUIT_DNI=("CUIT_DNI", "first"),
        Razon_Social=("Razon_Social", "first"),
        Condicion_IVA=("Condicion_IVA", "first"),
        Neto=("Neto", "sum"),
        IVA=("IVA", "sum"),
        Total=("Total", _agg_total),
    )

    for _xc in mapeo.get("extra_cols", []):
        _xcol_l = _xc.get("col_l", "")
        if _xcol_l and _xcol_l in df.columns:
            _xserie = pd.to_numeric(df[_xcol_l], errors="coerce").fillna(0)
            agg[_xcol_l] = agg["Comprobante"].map(_xserie.groupby(df["Comprobante"]).sum()).fillna(0)

    agg["Origen"]    = agg.apply(lambda r: _origen_from_cuit_tipo(r["CUIT_DNI"], r["Tipo"]), axis=1)
    agg["CUIT_norm"] = agg["CUIT_DNI"].astype(str).str.replace(r"[^0-9]", "", regex=True)

    _nc_mask = agg["Tipo"].str.upper().str.startswith("NCC")
    for _nc_col in ["Neto", "IVA", "Total"]:
        agg.loc[_nc_mask, _nc_col] = -agg.loc[_nc_mask, _nc_col].abs()

    tipo_label = {
        "FAC-A": "Factura A", "FAC-B": "Factura B", "FAC-C": "Factura C",
        "FCC-A": "Ext. A", "FCC-B": "Ext. B", "FCC-C": "Ext. C",
        "NCC-A": "NC A", "NCC-B": "NC B", "NCC-C": "NC C",
        "FCA-A": "FC Elect. A",
    }
    agg["Tipo_Doc"] = agg["Tipo"].map(tipo_label).fillna(agg["Tipo"])

    return agg


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
    sheet = _mejor_hoja(raw)
    hr    = _find_header(sheet, mapeo.get("header_keyword", "Punto de Venta"))
    if hr is None:
        st.error(f"No se encontró encabezado '{mapeo.get('header_keyword','Punto de Venta')}' en Mis Comprobantes ARCA.")
        return None

    df = sheet.copy()
    df.columns = df.iloc[hr]
    df = df.iloc[hr + 1:].reset_index(drop=True)
    df.columns.name = None
    df.columns = [str(c).strip() for c in df.columns]

    col_pto  = col.get("punto_venta", "Punto de Venta")
    col_num  = col.get("numero",      "Número Desde")
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
            df[col_i] = pd.to_numeric(df[col_i], errors="coerce").fillna(0)

    col_tipo = c.get("tipo", "Tipo")
    if col_tipo and col_tipo in df.columns:
        df["es_NC"] = df[col_tipo].apply(_es_nota_credito_arca)
    else:
        df["es_NC"] = False
        col_tipo = None
    signo = df["es_NC"].map({True: -1, False: 1})
    for col_i in COLS_IMPORTE:
        if col_i in df.columns:
            df[col_i] = df[col_i] * signo

    for _xc in mapeo.get("extra_cols", []):
        _xcol_a = _xc.get("col_a", "")
        if _xcol_a and _xcol_a in df.columns and _xcol_a not in COLS_IMPORTE:
            df[_xcol_a] = pd.to_numeric(df[_xcol_a], errors="coerce").fillna(0) * signo

    c_ng  = c.get("neto_gravado",    "Neto Gravado Total")
    c_nng = c.get("neto_no_gravado", "Neto No Gravado")
    c_oe  = c.get("op_exentas",      "Op. Exentas")
    c_ot  = c.get("otros_tributos",  "Otros Tributos")
    c_tiva = c.get("total_iva",      "Total IVA")
    c_tot  = c.get("total",          "Imp. Total")

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
        "13 - Nota de Crédito C": "N.Crédito C",
    }
    if col_tipo and col_tipo in df.columns:
        df["Tipo_Doc_ARCA"] = df[col_tipo].map(tipo_label_arca).fillna(df[col_tipo])
    else:
        df["Tipo_Doc_ARCA"] = ""

    c_fecha = c.get("fecha", "Fecha")
    c_cuit  = c.get("cuit_emisor",  "Nro. Doc. Emisor")
    c_denom = c.get("denominacion", "Denominación Emisor")
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
    df = df.rename(columns={k: v for k, v in col_std.items() if k != v})

    for _opt in ["Fecha", "Tipo", "Denominación Emisor"]:
        if _opt not in df.columns:
            df[_opt] = ""

    df["CUIT_norm"] = df.get("Nro. Doc. Emisor", pd.Series("", index=df.index)).astype(str).str.replace(r"[^0-9]", "", regex=True)

    return df
