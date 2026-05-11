"""
Algoritmo de conciliación.

Incluye:
  - conciliar: cruza el Listado con ARCA y clasifica cada comprobante

Lógica principal:
  1. Deduplicar ARCA (avisa si encuentra duplicados)
  2. Separar comprobantes nacionales de externos:
     - Nacionales: JOIN por (Comprobante, CUIT_norm) — evita falsos positivos
     - Externos: JOIN solo por Comprobante — los FCC/FCE no tienen CUIT argentino
  3. Calcular diferencias absolutas para Neto, IVA y Total
  4. Clasificar estado:
     - "Conciliado": los tres importes coinciden dentro de la tolerancia
     - "Total OK / Sin desglose": Total OK pero Neto difiere (típico de Factura C)
     - "Diferencia detectada": discrepancia real en algún importe
     - "Sin match en ARCA": el comprobante no figura en ARCA
     - "Exterior / No en ARCA": comprobante exterior sin contraparte (esperado)

Retorna (sheet1, sheet2, sheet3):
  sheet1: todos los comprobantes del Listado con columnas de comparación
  sheet2: comprobantes solo en Listado (no encontrados en ARCA)
  sheet3: comprobantes solo en ARCA (sin contraparte en el Listado)
"""
import re

import pandas as pd
import streamlit as st


def conciliar(df_listado, df_arca, tolerancia: float, extra_cols=None):
    """Cruza Listado con ARCA y clasifica cada comprobante.

    extra_cols: lista de {"label": str, "col_l": str, "col_a": str} para comparaciones
    adicionales de alícuotas. Generan columnas Dif_<label> y Match_<label> informativas
    (no afectan el flag Conciliado).
    """
    extra_cols = extra_cols or []

    # Normalizar índices para evitar IndexingError en mascaras booleanas
    df_listado = df_listado.reset_index(drop=True)
    df_arca    = df_arca.reset_index(drop=True)

    # Deduplicar ARCA — avisa pero no falla
    dupes = df_arca[df_arca.duplicated("Comprobante_Key", keep=False)]
    if not dupes.empty:
        n_keys = dupes["Comprobante_Key"].nunique()
        st.warning(
            f"ARCA contiene {len(dupes)} filas duplicadas para {n_keys} comprobante(s). "
            "Se conserva la primera ocurrencia de cada uno."
        )
        df_arca = df_arca.drop_duplicates("Comprobante_Key", keep="first")

    # Columnas estándar de ARCA renombradas para el JOIN
    _arca_std_rename = {
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
    }

    # Columnas extra de ARCA para comparaciones de alícuotas
    _xtra_arca_rename: dict[str, str] = {}
    for _xi, _xc in enumerate(extra_cols):
        _col_a = _xc.get("col_a", "")
        if _col_a and _col_a in df_arca.columns and _col_a not in _arca_std_rename:
            _xtra_arca_rename[_col_a] = f"ARCA_X{_xi}"

    _arca_all_cols = [c for c in list(_arca_std_rename) + list(_xtra_arca_rename)
                      if c in df_arca.columns]
    arca_slim = df_arca[_arca_all_cols].rename(columns={**_arca_std_rename, **_xtra_arca_rename})

    # Separar nacionales y externos para JOIN diferente
    mask_ext    = (df_listado.get("Origen", pd.Series("Nacional", index=df_listado.index)) == "Exterior")
    listado_nac = df_listado[~mask_ext]
    listado_ext = df_listado[mask_ext]

    merged_nac = pd.merge(
        listado_nac, arca_slim,
        left_on=["Comprobante", "CUIT_norm"],
        right_on=["ARCA_Key", "ARCA_CUIT_norm"],
        how="left",
    )

    # ── Fallback: mismo comprobante, CUIT vía NroDoc_norm ────────────────────
    # Cuando el CUIT registrado en el Listado difiere del Nro. Doc. Emisor de ARCA
    # (ej.: Benvido), se reintenta el join usando la columna Nro. Doc. del Listado.
    if "NroDoc_norm" in listado_nac.columns:
        _unm_mask = merged_nac["ARCA_Key"].isna()
        if _unm_mask.any():
            _used_keys   = set(merged_nac["ARCA_Key"].dropna())
            _arca_for_fb = arca_slim[~arca_slim["ARCA_Key"].isin(_used_keys)]
            _unm_comps   = set(merged_nac.loc[_unm_mask, "Comprobante"])
            _unm_l       = listado_nac[listado_nac["Comprobante"].isin(_unm_comps)].copy()
            if not _unm_l.empty and not _arca_for_fb.empty:
                _fb = pd.merge(
                    _unm_l, _arca_for_fb,
                    left_on=["Comprobante", "NroDoc_norm"],
                    right_on=["ARCA_Key", "ARCA_CUIT_norm"],
                    how="left",
                )
                merged_nac = pd.concat(
                    [merged_nac[~_unm_mask], _fb],
                    ignore_index=True, sort=False,
                )

    # ── Fallback 2: solo Comprobante — CUIT difiere ──────────────────────────
    # Para Libro IVA Compras y otros formatos donde el CUIT registrado en el
    # Listado (Nro.Doc.) difiere del Nro. Doc. Emisor de ARCA pero el número
    # de comprobante sí coincide. Se matchea igual para que la detección de
    # CUIT mismatch pueda marcarlos como "CUIT no coincide".
    _unm_mask_cuit = merged_nac["ARCA_Key"].isna()
    if _unm_mask_cuit.any():
        _used_keys_c   = set(merged_nac["ARCA_Key"].dropna())
        _arca_for_cuit = arca_slim[~arca_slim["ARCA_Key"].isin(_used_keys_c)]
        _unm_comps_c   = set(merged_nac.loc[_unm_mask_cuit, "Comprobante"])
        _unm_l_c       = listado_nac[listado_nac["Comprobante"].isin(_unm_comps_c)].copy()
        if not _unm_l_c.empty and not _arca_for_cuit.empty:
            _fb_cuit = pd.merge(
                _unm_l_c, _arca_for_cuit,
                left_on="Comprobante",
                right_on="ARCA_Key",
                how="left",
            )
            merged_nac = pd.concat(
                [merged_nac[~_unm_mask_cuit], _fb_cuit],
                ignore_index=True, sort=False,
            )

    merged_ext = pd.merge(
        listado_ext, arca_slim,
        left_on="Comprobante",
        right_on="ARCA_Key",
        how="left",
    )
    merged = pd.concat([merged_nac, merged_ext], ignore_index=True, sort=False)
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

    # Otros Tributos — OR lógico: Match si ARCA.Otros Tributos ≈ CUALQUIERA columna candidata.
    # No afecta Conciliado (informativo). Las columnas candidatas vienen del df attrs.
    _ot_candidatos = [
        c for c in df_listado.attrs.get("otros_tributos_cols", [])
        if c in merged.columns
    ]
    if _ot_candidatos and "ARCA_OtrosTrib" in merged.columns:
        _arca_ot = merged["ARCA_OtrosTrib"].fillna(0)
        _diffs = pd.concat(
            [(merged[c].fillna(0) - _arca_ot).abs() for c in _ot_candidatos],
            axis=1,
        )
        merged["Dif_OtrosTrib"]   = _diffs.min(axis=1)
        merged["Match_OtrosTrib"] = merged["Existe_en_ARCA"] & (_diffs.min(axis=1) <= tol)

    # Comparaciones adicionales de alícuotas (informativas)
    _xtra_sheet_cols: list[str] = []
    for _xi, _xc in enumerate(extra_cols):
        _col_l      = _xc.get("col_l", "")
        _arca_alias = f"ARCA_X{_xi}"
        _safe_lbl   = re.sub(r"[^A-Za-z0-9]", "_", _xc.get("label", f"X{_xi}")).strip("_") or f"X{_xi}"
        if _col_l and _col_l in merged.columns and _arca_alias in merged.columns:
            merged[f"Dif_{_safe_lbl}"]   = (
                merged[_col_l].fillna(0) - merged[_arca_alias].fillna(0)
            ).abs()
            merged[f"Match_{_safe_lbl}"] = (
                merged["Existe_en_ARCA"] & (merged[f"Dif_{_safe_lbl}"] <= tol)
            )
            _xtra_sheet_cols += [f"Dif_{_safe_lbl}", f"Match_{_safe_lbl}"]

    def _estado(row):
        if not row["Existe_en_ARCA"]:
            if row.get("Origen", "Nacional") == "Exterior":
                return "Exterior / No en ARCA"
            return "Sin match en ARCA"
        if row["Conciliado"]:
            return "Conciliado"
        # NC donde Total coincide: el Neto/IVA puede no desglosa (exento) — es conciliado
        if row["Match_Total"] and row.get("ARCA_es_NC", False):
            return "Conciliado"
        # Factura C / exento: ARCA no desglosa Neto/IVA, Total derivado → no es diferencia genuina
        if row["Match_Total"] and row.get("ARCA_Neto_Derivado", False):
            return "Total OK / Sin desglose"
        return "Diferencia detectada"

    merged["Estado"] = merged.apply(_estado, axis=1)

    # ── Comprobantes con CUIT distinto entre Listado y ARCA ──────────────────
    # Un CUIT diferente al de ARCA es un error grave: el comprobante NO puede
    # quedar como "Conciliado" aunque los importes coincidan. Requiere corrección
    # manual en el sistema contable.
    if "ARCA_CUIT_norm" in merged.columns:
        _origen_nac = (
            merged.get("Origen", pd.Series("Nacional", index=merged.index)) == "Nacional"
        )
        _cuit_mismatch = (
            merged["Existe_en_ARCA"]
            & _origen_nac
            & (merged["CUIT_norm"].fillna("") != merged["ARCA_CUIT_norm"].fillna(""))
            & merged["CUIT_norm"].str.len().gt(0)
            & merged["ARCA_CUIT_norm"].str.len().gt(0)
        )
        merged["CUIT_mismatch"] = _cuit_mismatch
        if _cuit_mismatch.any():
            merged.loc[_cuit_mismatch, "Estado"] = "CUIT no coincide"
            n_cd = int(_cuit_mismatch.sum())
            st.warning(
                f"⚠️ {n_cd} comprobante(s) con CUIT diferente al de ARCA — "
                "no pueden conciliarse hasta corregir el CUIT en el sistema contable.",
                icon="🚨",
            )
    else:
        merged["CUIT_mismatch"] = False

    sheet1 = merged[[c for c in [
        "Estado", "Comprobante", "Fecha_Factura", "Tipo_Doc", "Origen",
        "CUIT_DNI", "CUIT_norm", "Razon_Social",
        "Neto", "IVA", "Total",
        "Existe_en_ARCA", "ARCA_Denominacion", "ARCA_CUIT", "ARCA_Fecha", "ARCA_Tipo",
        "ARCA_es_NC", "ARCA_Neto_Derivado",
        "ARCA_Neto", "ARCA_IVA", "ARCA_OtrosTrib", "ARCA_Total",
        "Dif_Neto", "Dif_IVA", "Dif_Total",
        "Match_Neto", "Match_IVA", "Match_Total", "Conciliado",
        "CUIT_mismatch",
        "Dif_OtrosTrib", "Match_OtrosTrib",
    ] + _xtra_sheet_cols if c in merged.columns]].copy()

    # Comprobantes solo en Listado
    sheet2 = merged[~merged["Existe_en_ARCA"]][[c for c in [
        "Comprobante", "Fecha_Factura", "Tipo_Doc", "Origen",
        "CUIT_DNI", "Razon_Social", "Condicion_IVA", "Neto", "IVA", "Total",
    ] if c in merged.columns]].copy()

    # Comprobantes solo en ARCA (no unieron en el JOIN)
    arca_matched_keys = set(merged["ARCA_Key"].dropna())
    keep = [c for c in [
        "Comprobante_Key", "Fecha", "Tipo_Doc_ARCA", "Nro. Doc. Emisor",
        "Denominación Emisor", "Neto Gravado Total", "Neto No Gravado", "Op. Exentas",
        "Otros Tributos", "Total IVA", "Imp. Total", "es_NC",
    ] if c in df_arca.columns]
    sheet3 = df_arca[~df_arca["Comprobante_Key"].isin(arca_matched_keys)][keep].copy()

    return sheet1, sheet2, sheet3
