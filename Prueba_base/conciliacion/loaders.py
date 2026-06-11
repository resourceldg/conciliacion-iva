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
from .utils import _agg_total, _combinar_cols, _es_nota_credito_arca, _fix_mojibake, _origen_from_cuit_tipo


def _load_libro_iva(source) -> "pd.DataFrame | None":
    """Carga el Libro IVA Compras de Colppy. Soporta dos variantes:

    Variante A (clásica): columnas Suc. + Letra + Numero, campo Gravado.
    Variante B (nueva):   columna Comprobante "X-SSSSS-NNNNNNNN",
                          columnas "Neto gravado X%" e "IVA X%".
    """
    raw   = leer_excel(source)
    sheet = _mejor_hoja(raw)

    # Buscar header priorizando "Comprobante" (Variante B) y "Suc." (Variante A).
    # "Suc." puede aparecer en nombres de empresa en filas de datos, generando
    # un falso positivo si se busca primero.
    hr = _find_header(sheet, "Comprobante", ["Suc.", "Numero", "Proveedor", "Nro.Doc.", "Nro Factura", "Nro Factur"])
    if hr is None:
        hr = 0
    header_cols = [_fix_mojibake(c).strip() for c in sheet.iloc[hr]]

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

    # ── Variante C: IvaCompras — columna "Nro Factura" con formato "FCA 00002-00021696"
    else:
        _nro_col_c = next(
            (c for c in df.columns if re.search(r"nro[\s._]?factur", c.lower())),
            None,
        )
        if _nro_col_c is None:
            st.error("No se encontraron comprobantes válidos en el Libro IVA Compras.")
            return None

        comp_raw = df[_nro_col_c].astype(str).str.strip()
        cp = comp_raw.str.extract(r"^([A-Z]{2,3})\s+(\d{1,5})-(\d+)$", expand=True)
        df["_tipo_pfx"] = cp[0].fillna("FAC")
        df["_letra"]    = cp[0].str[-1].fillna("A").str.upper()
        df["Comprobante"] = cp[1].str.zfill(5) + "-" + cp[2].str.zfill(8)
        df = df[df["Comprobante"].str.match(r"^\d{5}-\d{8}$", na=False)].copy()
        if df.empty:
            st.error("No se encontraron comprobantes válidos en el Libro IVA Compras.")
            return None

        def _pfx_tipo_c(pfx: str, letra: str) -> str:
            p = pfx.upper()
            if p.startswith("NC"):
                return f"NCC-{letra}"
            return f"FAC-{letra}"

        df["Tipo"] = [_pfx_tipo_c(p, l) for p, l in zip(df["_tipo_pfx"], df["_letra"])]

        _fecha_col_c = next(
            (c for c in df.columns if re.search(r"fecha[\s._]?(emis|fact|cbte)?", c.lower())),
            None,
        )
        df["Fecha_Factura"] = df[_fecha_col_c] if _fecha_col_c else pd.Series("", index=df.index)

        _cuit_col_c = next(
            (c for c in df.columns if re.search(r"^cuit$", c.lower().strip())),
            None,
        )
        df["CUIT_DNI"] = (
            df[_cuit_col_c].astype(str).str.strip() if _cuit_col_c
            else pd.Series("", index=df.index)
        )

        _rs_col_c = next(
            (c for c in df.columns if re.search(r"razon[\s._]?soci", c.lower())),
            None,
        )
        df["Razon_Social"] = df[_rs_col_c] if _rs_col_c else pd.Series("", index=df.index)

        _cond_col_c = next(
            (c for c in df.columns if re.search(r"condic", c.lower())),
            None,
        )
        df["Condicion_IVA"] = df[_cond_col_c] if _cond_col_c else pd.Series("", index=df.index)

        # Neto = Monto Gravado* + Monto No Gravado* (espeja Neto_Total_ARCA =
        # Neto Gravado Total + Neto No Gravado + Op. Exentas)
        _neto_cols_c = [c for c in df.columns if re.search(r"monto\s*grav(?:ado)?", c.lower())]
        _neto_no_cols_c = [c for c in df.columns if re.search(r"monto\s*no\s*grav", c.lower())]
        _all_neto_cols_c = _neto_cols_c + _neto_no_cols_c
        df["Neto"] = (
            sum(pd.to_numeric(df[c], errors="coerce").fillna(0) for c in _all_neto_cols_c)
            if _all_neto_cols_c else pd.Series(0.0, index=df.index)
        )

        _iva_cols_c = [c for c in df.columns if re.search(r"iva[\s._]?factur", c.lower())]
        df["IVA"] = (
            sum(pd.to_numeric(df[c], errors="coerce").fillna(0) for c in _iva_cols_c)
            if _iva_cols_c else pd.Series(0.0, index=df.index)
        )

        _total_col_c = next(
            (c for c in df.columns if re.search(r"total[\s._]?fact", c.lower())),
            None,
        )
        df["Total"] = (
            pd.to_numeric(df[_total_col_c], errors="coerce").fillna(0) if _total_col_c
            else pd.Series(0.0, index=df.index)
        )

    # IVA: suma de todas las columnas "IVA X%" (ambas variantes)
    # Variante C ya asigna df["IVA"]; Variante A/B usan columnas "IVA X%"
    iva_cols = [c for c in df.columns if re.match(r"^iva\s*[\d,.]", c.lower().strip())]
    if iva_cols:
        df["IVA"] = sum(pd.to_numeric(df[c], errors="coerce").fillna(0) for c in iva_cols)
    elif "IVA" not in df.columns:
        df["IVA"] = pd.Series(0.0, index=df.index)

    # Otros Tributos: detecta columnas candidatas (Perc.IIBB, Perc.IVA, Perc.Gan, Ret.IVA, Imp.Int…)
    # Se preservan INDIVIDUALMENTE en el agg para que el reconciler pueda comparar con OR:
    # Match si ARCA.Otros Tributos ≈ CUALQUIERA de ellas (no la suma de todas).
    _OT_RE = r"(perc\.?\s*(iibb|iva|gan)|ret\.?\s*iva|imp\.?\s*int|percep|retenci)"
    ot_cols = [c for c in df.columns if re.search(_OT_RE, c.lower().strip())]
    for _oc in ot_cols:
        df[_oc] = pd.to_numeric(df[_oc], errors="coerce").fillna(0)

    # CUIT_norm como clave secundaria de agrupación: evita mezclar comprobantes
    # de distintos proveedores que coincidan en número (XXXXX-YYYYYYYY no es único
    # entre proveedores; el par Comprobante+CUIT_norm sí lo es).
    df["CUIT_norm"] = df["CUIT_DNI"].astype(str).str.replace(r"[^0-9]", "", regex=True)
    _ot_agg = {c: (c, "sum") for c in ot_cols}
    agg = df.groupby(["Comprobante", "CUIT_norm"], as_index=False).agg(
        Fecha_Factura=("Fecha_Factura", "first"),
        Tipo=("Tipo", "first"),
        CUIT_DNI=("CUIT_DNI", "first"),
        Razon_Social=("Razon_Social", "first"),
        Condicion_IVA=("Condicion_IVA", "first"),
        Neto=("Neto", "sum"),
        IVA=("IVA", "sum"),
        Total=("Total", _agg_total),
        **_ot_agg,
    )
    # Guardar los nombres de columnas candidatas como metadata en el df
    agg.attrs["otros_tributos_cols"] = ot_cols

    agg["Origen"] = agg.apply(lambda r: _origen_from_cuit_tipo(r["CUIT_DNI"]), axis=1)

    # NroDoc_norm: columna "Nro.Doc." si existe y es distinta a la fuente de CUIT_DNI.
    # Variante A: CUIT_DNI ya proviene de Nro.Doc. → NroDoc_norm == CUIT_norm.
    # Variante B: CUIT_DNI proviene de "CUIT" → Nro.Doc. puede ser diferente.
    _nrodoc_col_libro = next(
        (c for c in df.columns if re.sub(r"[^a-z0-9]", "", c.lower()) in ("nrodoc", "nrodocumento")),
        None,
    )
    if _nrodoc_col_libro:
        _nd_map = df.groupby("Comprobante")[_nrodoc_col_libro].first()
        agg["NroDoc_norm"] = (
            agg["Comprobante"].map(_nd_map).astype(str).str.replace(r"[^0-9]", "", regex=True)
        )
    else:
        agg["NroDoc_norm"] = agg["CUIT_norm"]

    _nc_mask = agg["Tipo"].str.upper().str.startswith("NCC")
    for _nc_col in ["Neto", "IVA", "Total"] + ot_cols:
        if _nc_col in agg.columns:
            agg.loc[_nc_mask, _nc_col] = -agg.loc[_nc_mask, _nc_col].abs()

    tipo_label = {
        "FAC-A": "Factura A", "FAC-B": "Factura B", "FAC-C": "Factura C",
        "NCC-A": "NC A", "NCC-B": "NC B", "NCC-C": "NC C",
        "NDB-A": "ND A", "NDB-B": "ND B", "NDB-C": "ND C",
    }
    agg["Tipo_Doc"] = agg["Tipo"].map(tipo_label).fillna(agg["Tipo"])

    n = len(agg)
    st.info(f"Libro IVA Compras detectado — {n} comprobantes cargados.", icon="📖")
    result = agg.reset_index(drop=True)
    result.attrs["otros_tributos_cols"] = ot_cols
    return result


def _load_subdiario_iva(source) -> "pd.DataFrame | None":
    """Carga el Subdiario de Compras exportado de Contabilium o Finnegans.

    Formato: header en fila 0, columnas por alícuota (Neto gravado 21%, Iva 21%…).
    N° de comprobante tiene formato 'A-00001-00354190' o '0007-26032026'.
    Una fila por comprobante (no hay múltiples filas por alícuota).
    """
    raw   = leer_excel(source)
    sheet = _mejor_hoja(raw)

    df = sheet.copy()
    df.columns = [_fix_mojibake(c).strip() for c in sheet.iloc[0]]
    df = df.iloc[1:].reset_index(drop=True)

    comp_raw = df.get("N° de comprobante", df.get("N°  de comprobante", pd.Series("", index=df.index))).astype(str).str.strip()

    def _parse_comp(s: str) -> str:
        import re
        m = re.match(r"^[A-Za-z]-(\d{1,5})-(\d{1,8})$", s)
        if m:
            return m.group(1).zfill(5) + "-" + m.group(2).zfill(8)
        m2 = re.match(r"^(\d{1,5})-(\d{1,8})$", s)
        if m2:
            return m2.group(1).zfill(5) + "-" + m2.group(2).zfill(8)
        return ""

    df["Comprobante"] = comp_raw.apply(_parse_comp)
    df = df[df["Comprobante"].str.match(r"^\d{5}-\d{8}$", na=False)].copy()

    if df.empty:
        st.error("No se encontraron comprobantes válidos en el Subdiario de Compras.")
        return None

    def _map_subdiario_tipo(t: str) -> str:
        t = str(t).upper()
        # Extraer letra del final: "001-FC A" → letra "A"
        letra = t[-1] if t and t[-1].isalpha() else "A"
        if "NC" in t:
            return f"NCC-{letra}"
        if "ND" in t:
            return f"NDB-{letra}"
        if "FC" in t or "FAC" in t:
            return f"FAC-{letra}"
        return f"FAC-{letra}"

    tipo_raw = df.get("Tipo de documento", pd.Series("", index=df.index)).astype(str)
    df["Tipo"] = tipo_raw.apply(_map_subdiario_tipo)

    df["Fecha_Factura"] = df.get("Fecha contable",   pd.Series("", index=df.index))
    df["CUIT_DNI"]      = df.get("Cuit",             pd.Series("", index=df.index)).astype(str).str.strip()
    df["Razon_Social"]  = df.get("Proveedor",        pd.Series("", index=df.index))
    df["Condicion_IVA"] = df.get("Cat. fiscal",      pd.Series("", index=df.index))

    # Neto: suma de todos los "Neto gravado X%" + "No gravado"
    neto_cols = [c for c in df.columns if re.match(r"neto\s+gravado", c.lower())]
    no_grav   = [c for c in df.columns if re.match(r"no\s+gravado", c.lower())]
    all_neto  = neto_cols + no_grav
    df["Neto"] = (
        sum(pd.to_numeric(df[c], errors="coerce").fillna(0) for c in all_neto)
        if all_neto else pd.Series(0.0, index=df.index)
    )

    # IVA: suma de todos los "Iva X%"
    iva_cols = [c for c in df.columns if re.match(r"iva\s+[\d,.]", c.lower())]
    df["IVA"] = (
        sum(pd.to_numeric(df[c], errors="coerce").fillna(0) for c in iva_cols)
        if iva_cols else pd.Series(0.0, index=df.index)
    )

    df["Total"]     = pd.to_numeric(df.get("Total", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0)
    df["CUIT_norm"] = df["CUIT_DNI"].astype(str).str.replace(r"[^0-9]", "", regex=True)

    agg = df.groupby(["Comprobante", "CUIT_norm"], as_index=False).agg(
        Fecha_Factura=("Fecha_Factura", "first"),
        Tipo=("Tipo", "first"),
        CUIT_DNI=("CUIT_DNI", "first"),
        Razon_Social=("Razon_Social", "first"),
        Condicion_IVA=("Condicion_IVA", "first"),
        Neto=("Neto", "sum"),
        IVA=("IVA", "sum"),
        Total=("Total", _agg_total),
    )

    agg["Origen"]      = agg.apply(lambda r: _origen_from_cuit_tipo(r["CUIT_DNI"], r["Tipo"]), axis=1)
    agg["NroDoc_norm"] = agg["CUIT_norm"]

    _nc_mask = agg["Tipo"].str.upper().str.startswith("NCC")
    for _nc_col in ["Neto", "IVA", "Total"]:
        agg.loc[_nc_mask, _nc_col] = -agg.loc[_nc_mask, _nc_col].abs()

    tipo_label = {
        "FAC-A": "Factura A", "FAC-B": "Factura B", "FAC-C": "Factura C",
        "FAC-E": "Fact. Exterior",
        "NCC-A": "NC A", "NCC-B": "NC B", "NCC-C": "NC C",
        "NDB-A": "ND A", "NDB-B": "ND B", "NDB-C": "ND C",
    }
    agg["Tipo_Doc"] = agg["Tipo"].map(tipo_label).fillna(agg["Tipo"])

    n = len(agg)
    st.info(f"Subdiario de Compras detectado — {n} comprobantes cargados.", icon="📋")
    return agg.reset_index(drop=True)


def _load_pasion_iva(source) -> "pd.DataFrame | None":
    """Carga el libro IVA Compras exportado de Pasión ERP.

    Formato: header en fila 0, columnas 'Tipo Comprob.' + 'L' (letra) + 'Nº Comprob.'.
    Nº Comprob. ya tiene formato XXXXX-YYYYYYYY. NC ya vienen con signo negativo.
    Neto = Imp. Gravado + GRAV 5% (neto gravado por alícuota).
    IVA  = Imp.IVA (columna pre-agregada).
    """
    raw   = leer_excel(source)
    sheet = _mejor_hoja(raw)

    df = sheet.copy()
    df.columns = [_fix_mojibake(c).strip() for c in sheet.iloc[0]]
    df = df.iloc[1:].reset_index(drop=True)

    # Pasión puede exportar decimales con coma (ej. "3195164,07") en algunas columnas
    def _to_num(col: "pd.Series") -> "pd.Series":
        return pd.to_numeric(
            col.astype(str).str.replace(",", ".", regex=False).str.replace(r"\s", "", regex=True),
            errors="coerce",
        ).fillna(0)

    # Comprobante: "Nº Comprob." ya en formato XXXXX-YYYYYYYY
    comp_col = next((c for c in df.columns if re.match(r"n[º°]\s*comprob", c.lower())), None)
    if comp_col is None:
        st.error("No se encontró la columna 'Nº Comprob.' en el archivo Pasión.")
        return None

    comp_raw = df[comp_col].astype(str).str.strip()
    # Normalizar: "0001-00000321" → "00001-00000321" (zero-pad cada parte)
    def _norm_comp(s: str) -> str:
        m = re.match(r"^(\d{1,5})-(\d{1,8})$", s.strip())
        if m:
            return m.group(1).zfill(5) + "-" + m.group(2).zfill(8)
        return ""
    df["Comprobante"] = comp_raw.apply(_norm_comp)
    df = df[df["Comprobante"].str.match(r"^\d{5}-\d{8}$", na=False)].copy()

    if df.empty:
        st.error("No se encontraron comprobantes válidos en el archivo Pasión.")
        return None

    # Tipo: "Tipo Comprob." (FACTURA / N/CREDITO / N/DEBITO) + "L" (letra A/B/C)
    tipo_col  = next((c for c in df.columns if re.match(r"tipo\s+comprob", c.lower())), None)
    letra_col = "L" if "L" in df.columns else None

    def _map_pasion_tipo(t: str, l: str) -> str:
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
    tipos  = df[tipo_col].astype(str) if tipo_col else pd.Series("FACTURA", index=df.index)
    df["Tipo"] = [_map_pasion_tipo(t, l) for t, l in zip(tipos, letras)]

    df["Fecha_Factura"] = df.get("Fecha Comprobante", df.get("Fecha", pd.Series("", index=df.index)))
    df["CUIT_DNI"]      = df.get("C.U.I.T.", pd.Series("", index=df.index)).astype(str).str.strip()
    df["Razon_Social"]  = df.get("Nombre del Proveedor", pd.Series("", index=df.index))
    df["Condicion_IVA"] = df.get("Tipo Responsable", pd.Series("", index=df.index))

    # Neto: gravado + exento + no gravado (ARCA los agrupa en Neto_Total_ARCA)
    neto_cols = (
        [c for c in df.columns if re.match(r"imp\.?\s*gravado", c.lower())]
        + [c for c in df.columns if re.match(r"grav\s+\d", c.lower())]
        + [c for c in df.columns if re.match(r"imp\.?\s*exento", c.lower())]
        + [c for c in df.columns if re.match(r"imp\.?\s*monotrib", c.lower())]
    )
    df["Neto"] = (
        sum(_to_num(df[c]) for c in neto_cols)
        if neto_cols else pd.Series(0.0, index=df.index)
    )

    # IVA: columna "Imp.IVA" pre-agregada (puede tener coma decimal)
    iva_col = next((c for c in df.columns if re.match(r"imp\.?\s*iva", c.lower())), None)
    if iva_col is None:
        # fallback: suma IVA Fact. + IVA Diferencial + IVA 27%
        iva_cols = [c for c in df.columns if re.match(r"iva\s", c.lower()) or c.lower() == "iva fact."]
        df["IVA"] = (
            sum(_to_num(df[c]) for c in iva_cols)
            if iva_cols else pd.Series(0.0, index=df.index)
        )
    else:
        df["IVA"] = _to_num(df[iva_col])

    df["Total"]     = _to_num(df.get("Total", pd.Series(0.0, index=df.index)))
    df["CUIT_norm"] = df["CUIT_DNI"].astype(str).str.replace(r"[^0-9]", "", regex=True)

    agg = df.groupby(["Comprobante", "CUIT_norm"], as_index=False).agg(
        Fecha_Factura=("Fecha_Factura", "first"),
        Tipo=("Tipo", "first"),
        CUIT_DNI=("CUIT_DNI", "first"),
        Razon_Social=("Razon_Social", "first"),
        Condicion_IVA=("Condicion_IVA", "first"),
        Neto=("Neto", "sum"),
        IVA=("IVA", "sum"),
        Total=("Total", _agg_total),
    )

    agg["Origen"]      = agg.apply(lambda r: _origen_from_cuit_tipo(r["CUIT_DNI"], r["Tipo"]), axis=1)
    agg["NroDoc_norm"] = agg["CUIT_norm"]

    _nc_mask = agg["Tipo"].str.upper().str.startswith("NCC")
    for _nc_col in ["Neto", "IVA", "Total"]:
        agg.loc[_nc_mask, _nc_col] = -agg.loc[_nc_mask, _nc_col].abs()

    tipo_label = {
        "FAC-A": "Factura A", "FAC-B": "Factura B", "FAC-C": "Factura C",
        "FAC-E": "Fact. Exterior",
        "NCC-A": "NC A", "NCC-B": "NC B", "NCC-C": "NC C",
        "NDB-A": "ND A", "NDB-B": "ND B", "NDB-C": "ND C",
    }
    agg["Tipo_Doc"] = agg["Tipo"].map(tipo_label).fillna(agg["Tipo"])

    n = len(agg)
    st.info(f"Pasión ERP detectado — {n} comprobantes cargados.", icon="📋")
    return agg.reset_index(drop=True)


def _load_tango_iva(source) -> "pd.DataFrame | None":
    """Carga el libro IVA Compras exportado de Tango Gestión.

    Formato: header en fila 0, columnas T_COMP / N_COMP / IDENTIFTRI / etc.
    N_COMP tiene formato Letra(1) + PtoVta(5) + Num(8), ej. 'A0262100624528'.
    Puede haber varias filas por comprobante (una por alícuota de IVA).
    """
    raw   = leer_excel(source)
    sheet = _mejor_hoja(raw)

    df = sheet.copy()
    df.columns = [_fix_mojibake(c).strip() for c in sheet.iloc[0]]
    df = df.iloc[1:].reset_index(drop=True)

    # La detección de formato usa substring ("n_comp" matchea variantes como
    # "N_COMP."), pero este loader necesita las columnas con nombre exacto.
    _faltantes_tango = [c for c in ("N_COMP", "T_COMP") if c not in df.columns]
    if _faltantes_tango:
        st.error(
            f"Columnas no encontradas en el archivo Tango: {_faltantes_tango}. "
            "Verificá que sea el export estándar de Tango Gestión."
        )
        return None

    # Filtrar filas sin N_COMP válido (filas de totales al final)
    comp_raw = df["N_COMP"].astype(str).str.strip()
    valid_comp = comp_raw.str.match(r"^[A-Za-z]\d{13}$")
    df = df[valid_comp].copy()

    if df.empty:
        st.error("No se encontraron comprobantes válidos en el archivo Tango.")
        return None

    # Construir clave XXXXX-YYYYYYYY desde N_COMP
    df["Comprobante"] = comp_raw[valid_comp].str[1:6] + "-" + comp_raw[valid_comp].str[6:14]
    df["_letra"]      = comp_raw[valid_comp].str[0].str.upper().fillna("A")

    # Tipo: "FAC" → "FAC-A", "N/C" → "NCC-A", etc.
    t_comp = df["T_COMP"].astype(str).str.strip().str.upper()
    def _map_tango_tipo(t: str, l: str) -> str:
        if t in ("N/C", "NCC", "NC"):
            return f"NCC-{l}"
        if t in ("N/D", "NDB", "ND"):
            return f"NDB-{l}"
        return f"FAC-{l}"
    df["Tipo"] = [_map_tango_tipo(t, l) for t, l in zip(t_comp, df["_letra"])]

    df["Fecha_Factura"] = df.get("FECHA_EMI", pd.Series("", index=df.index))
    df["CUIT_DNI"]      = df.get("IDENTIFTRI", pd.Series("", index=df.index)).astype(str).str.strip()
    df["Razon_Social"]  = df.get("NOM_PROVE",  pd.Series("", index=df.index))
    df["Condicion_IVA"] = df.get("COND_IVA",   pd.Series("", index=df.index))

    # Neto = gravado + exento (para comparar con Neto_Total_ARCA de ARCA)
    neto_grav = pd.to_numeric(df.get("IMP_NETO",   pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    neto_exen = pd.to_numeric(df.get("IMP_EXENTO", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    df["Neto"]      = neto_grav + neto_exen
    df["IVA"]       = pd.to_numeric(df.get("IMP_IVA",   pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    df["Total"]     = pd.to_numeric(df.get("IMP_TOTAL", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    df["CUIT_norm"] = df["CUIT_DNI"].astype(str).str.replace(r"[^0-9]", "", regex=True)

    agg = df.groupby(["Comprobante", "CUIT_norm"], as_index=False).agg(
        Fecha_Factura=("Fecha_Factura", "first"),
        Tipo=("Tipo", "first"),
        CUIT_DNI=("CUIT_DNI", "first"),
        Razon_Social=("Razon_Social", "first"),
        Condicion_IVA=("Condicion_IVA", "first"),
        Neto=("Neto", "sum"),
        IVA=("IVA", "sum"),
        Total=("Total", _agg_total),
    )

    agg["Origen"] = agg.apply(lambda r: _origen_from_cuit_tipo(r["CUIT_DNI"], r["Tipo"]), axis=1)

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
    st.info(f"Tango Gestión detectado — {n} comprobantes cargados.", icon="📋")
    return agg.reset_index(drop=True)


def _load_libro_iva_bim(source) -> "pd.DataFrame | None":
    """Carga el Libro IVA Compras con columna B.Imponible.

    Comprobante: "FC -A-0008-00036818" → "00008-00036818", Tipo → FAC-A
                 "NC -A-0700-00016822" → "00700-00016822", Tipo → NCC-A
    Neto = B.Imponible + No Gravado  (espeja Neto_Total_ARCA)
    IVA  = suma de columnas "IVA X%"
    Otros Tributos: Perc.IVA, Perc. I.B.CAB, Perc. I.B.Bs As, Perc. Gan.
    """
    raw   = leer_excel(source)
    sheet = _mejor_hoja(raw)

    hr = _find_header(sheet, "B.Imponible", ["B. Imponible", "Comprobante", "C.U.I.T."])
    if hr is None:
        hr = _find_header(sheet, "B. Imponible", ["Comprobante", "Proveedor"])
    if hr is None:
        st.error("No se encontró el encabezado en el Libro IVA (B.Imponible).")
        return None

    header_cols = [_fix_mojibake(c).strip() for c in sheet.iloc[hr]]
    df = sheet.iloc[hr + 1:].copy()
    df.columns = header_cols
    df = df.reset_index(drop=True)

    # ── Comprobante: "FC -A-0008-00036818" → "00008-00036818" ────────────────
    _comp_col = next(
        (c for c in df.columns if c.strip().lower() == "comprobante"),
        None,
    )
    if _comp_col is None:
        st.error("No se encontró la columna 'Comprobante' en el Libro IVA (B.Imponible).")
        return None

    comp_raw = df[_comp_col].astype(str).str.strip()

    def _parse_comp_bim(s: str):
        m = re.match(r"^(FC|NC|ND)\s+-([A-Za-z])-(\d+)-(\d+)$", s.strip())
        if m:
            tipo_pfx = m.group(1).upper()
            letra    = m.group(2).upper()
            ptovta   = m.group(3).zfill(5)
            num      = m.group(4).zfill(8)
            tipo_map = {"FC": "FAC", "NC": "NCC", "ND": "NDB"}
            return f"{ptovta}-{num}", f"{tipo_map.get(tipo_pfx, 'FAC')}-{letra}"
        return "", ""

    parsed = comp_raw.apply(_parse_comp_bim)
    df["Comprobante"] = [p[0] for p in parsed]
    df["Tipo"]        = [p[1] for p in parsed]
    df = df[df["Comprobante"].str.match(r"^\d{5}-\d{8}$", na=False)].copy()

    if df.empty:
        st.error("No se encontraron comprobantes válidos en el Libro IVA (B.Imponible).")
        return None

    # ── Campos identificatorios ───────────────────────────────────────────────
    _fecha_col = next(
        (c for c in df.columns if re.search(r"fecha", c.lower())), None
    )
    df["Fecha_Factura"] = (
        df[_fecha_col] if _fecha_col else pd.Series("", index=df.index)
    )

    _cuit_col = next(
        (c for c in df.columns if re.search(r"c\.?u\.?i\.?t\.?", c.lower())), None
    )
    df["CUIT_DNI"] = (
        df[_cuit_col].astype(str).str.strip() if _cuit_col
        else pd.Series("", index=df.index)
    )

    _rs_col = next(
        (c for c in df.columns if re.search(r"provee", c.lower())), None
    )
    df["Razon_Social"] = (
        df[_rs_col] if _rs_col else pd.Series("", index=df.index)
    )

    # Columna "IVA" contiene la condición del proveedor (RI/MON/NOC/EXE)
    _iva_cond_col = next(
        (c for c in df.columns if c.strip().upper() == "IVA"), None
    )
    df["Condicion_IVA"] = (
        df[_iva_cond_col] if _iva_cond_col else pd.Series("", index=df.index)
    )

    # ── Importes ──────────────────────────────────────────────────────────────
    _bim_col = next(
        (c for c in df.columns if re.search(r"b\.?\s*imponible", c.lower())), None
    )
    bim = (
        pd.to_numeric(df[_bim_col], errors="coerce").fillna(0) if _bim_col
        else pd.Series(0.0, index=df.index)
    )

    _nog_col = next(
        (c for c in df.columns if re.search(r"no\s*gravado", c.lower())), None
    )
    no_gravado = (
        pd.to_numeric(df[_nog_col], errors="coerce").fillna(0) if _nog_col
        else pd.Series(0.0, index=df.index)
    )

    # Neto = B.Imponible + No Gravado  (espeja ARCA: Neto Gravado + Neto No Gravado + Op. Exentas)
    df["Neto"] = bim + no_gravado

    # IVA: suma de columnas "IVA X%" (excluye la columna de condición "IVA")
    iva_cols = [
        c for c in df.columns
        if re.match(r"^iva\s*[\d,.]", c.lower().strip())
    ]
    df["IVA"] = (
        sum(pd.to_numeric(df[c], errors="coerce").fillna(0) for c in iva_cols)
        if iva_cols else pd.Series(0.0, index=df.index)
    )

    _total_col = next(
        (c for c in df.columns if c.strip().lower() == "total"), None
    )
    df["Total"] = (
        pd.to_numeric(df[_total_col], errors="coerce").fillna(0) if _total_col
        else pd.Series(0.0, index=df.index)
    )

    # ── Otros Tributos (percepciones individuales) ────────────────────────────
    _OT_RE = r"(perc\.?\s*(iibb|iva|gan|i\.?b\.|gananc)|ret\.?\s*iva|imp\.?\s*int|percep|retenci)"
    ot_cols = [c for c in df.columns if re.search(_OT_RE, c.lower().strip())]
    for _oc in ot_cols:
        df[_oc] = pd.to_numeric(df[_oc], errors="coerce").fillna(0)

    df["CUIT_norm"] = df["CUIT_DNI"].astype(str).str.replace(r"[^0-9]", "", regex=True)
    _ot_agg = {c: (c, "sum") for c in ot_cols}
    agg = df.groupby(["Comprobante", "CUIT_norm"], as_index=False).agg(
        Fecha_Factura=("Fecha_Factura", "first"),
        Tipo=("Tipo", "first"),
        CUIT_DNI=("CUIT_DNI", "first"),
        Razon_Social=("Razon_Social", "first"),
        Condicion_IVA=("Condicion_IVA", "first"),
        Neto=("Neto", "sum"),
        IVA=("IVA", "sum"),
        Total=("Total", _agg_total),
        **_ot_agg,
    )
    agg.attrs["otros_tributos_cols"] = ot_cols

    agg["Origen"]      = agg.apply(lambda r: _origen_from_cuit_tipo(r["CUIT_DNI"]), axis=1)
    agg["NroDoc_norm"] = agg["CUIT_norm"]

    _nc_mask = agg["Tipo"].str.upper().str.startswith("NCC")
    for _nc_col in ["Neto", "IVA", "Total"] + ot_cols:
        if _nc_col in agg.columns:
            agg.loc[_nc_mask, _nc_col] = -agg.loc[_nc_mask, _nc_col].abs()

    tipo_label = {
        "FAC-A": "Factura A", "FAC-B": "Factura B", "FAC-C": "Factura C",
        "NCC-A": "NC A", "NCC-B": "NC B", "NCC-C": "NC C",
        "NDB-A": "ND A", "NDB-B": "ND B", "NDB-C": "ND C",
    }
    agg["Tipo_Doc"] = agg["Tipo"].map(tipo_label).fillna(agg["Tipo"])

    n = len(agg)
    st.info(f"Libro IVA Compras (B.Imponible) detectado — {n} comprobantes cargados.", icon="📖")
    result = agg.reset_index(drop=True)
    result.attrs["otros_tributos_cols"] = ot_cols
    return result


def load_listado_iva(source, mapeo: dict | None = None):
    """Carga y normaliza el Listado IVA Compras exportado de Colppy.

    Pasos:
    1. Ubica la fila de encabezado buscando "Comprobante".
    2. Filtra solo las filas cuyo Comprobante tiene formato XXXXX-YYYYYYYY,
       descartando filas de totales y subtotales que Colppy agrega al final.
    3. Agrupa por Comprobante sumando Neto e IVA (Colppy puede tener una fila
       por alícuota de IVA), y toma el primer Total no-cero.
    4. Clasifica el origen (Nacional / Exterior) y aplica etiquetas legibles.

    Si detecta formato Libro IVA Compras o Tango, delega al loader correspondiente.
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
    if fmt == "tango":
        return _load_tango_iva(source)
    if fmt == "subdiario":
        return _load_subdiario_iva(source)
    if fmt == "pasion":
        return _load_pasion_iva(source)
    if fmt == "libro_bim":
        return _load_libro_iva_bim(source)

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
    df.columns = [_fix_mojibake(c).strip() for c in df.columns]

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

    # Otros Tributos: preservar columnas candidatas individualmente (OR en el reconciler)
    _ot_spec = col.get("otros_tributos_l", "")
    _ot_col_names = _ot_spec if isinstance(_ot_spec, list) else ([_ot_spec] if isinstance(_ot_spec, str) and _ot_spec else [])
    _ot_valid = [c for c in _ot_col_names if c in df.columns]
    for _oc in _ot_valid:
        df[_oc] = pd.to_numeric(df[_oc], errors="coerce").fillna(0)

    for _col_n in ["Perc_IIBB", "Perc_IVA"]:
        if _col_n in df.columns:
            df[_col_n] = pd.to_numeric(df[_col_n], errors="coerce").fillna(0)

    for _opt in ["Fecha_Factura", "Tipo", "Razon_Social", "Condicion_IVA"]:
        if _opt not in df.columns:
            df[_opt] = ""

    # CUIT_norm como clave secundaria: evita mezclar comprobantes de distintos
    # proveedores con el mismo número XXXXX-YYYYYYYY (el par Comp+CUIT es único).
    df["CUIT_norm"] = df["CUIT_DNI"].astype(str).str.replace(r"[^0-9]", "", regex=True)
    _ot_agg_std = {c: (c, "sum") for c in _ot_valid}
    agg = df.groupby(["Comprobante", "CUIT_norm"], as_index=False).agg(
        Fecha_Factura=("Fecha_Factura", "first"),
        Tipo=("Tipo", "first"),
        CUIT_DNI=("CUIT_DNI", "first"),
        Razon_Social=("Razon_Social", "first"),
        Condicion_IVA=("Condicion_IVA", "first"),
        Neto=("Neto", "sum"),
        IVA=("IVA", "sum"),
        Total=("Total", _agg_total),
        **_ot_agg_std,
    )

    for _xc in mapeo.get("extra_cols", []):
        _xcol_l = _xc.get("col_l", "")
        if _xcol_l and _xcol_l in df.columns:
            _xserie = pd.to_numeric(df[_xcol_l], errors="coerce").fillna(0)
            _key = df["Comprobante"] + "|" + df["CUIT_norm"]
            _agg_key = agg["Comprobante"] + "|" + agg["CUIT_norm"]
            agg[_xcol_l] = _agg_key.map(_xserie.groupby(_key).sum()).fillna(0)

    agg["Origen"] = agg.apply(lambda r: _origen_from_cuit_tipo(r["CUIT_DNI"], r["Tipo"]), axis=1)

    _nc_mask = agg["Tipo"].str.upper().str.startswith("NCC")
    for _nc_col in ["Neto", "IVA", "Total"] + _ot_valid:
        if _nc_col in agg.columns:
            agg.loc[_nc_mask, _nc_col] = -agg.loc[_nc_mask, _nc_col].abs()

    tipo_label = {
        "FAC-A": "Factura A", "FAC-B": "Factura B", "FAC-C": "Factura C",
        "FCC-A": "Ext. A", "FCC-B": "Ext. B", "FCC-C": "Ext. C",
        "NCC-A": "NC A", "NCC-B": "NC B", "NCC-C": "NC C",
        "FCA-A": "FC Elect. A",
    }
    agg["Tipo_Doc"] = agg["Tipo"].map(tipo_label).fillna(agg["Tipo"])

    result = agg.reset_index(drop=True)
    result.attrs["otros_tributos_cols"] = _ot_valid
    return result


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
            df[_xcol_a] = pd.to_numeric(df[_xcol_a], errors="coerce").fillna(0) * signo * tc_factor

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
            df[_ec] = pd.to_numeric(df[_ec], errors="coerce").fillna(0)

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
