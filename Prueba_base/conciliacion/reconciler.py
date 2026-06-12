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

Retorna (sheet1, sheet2, sheet3, warnings):
  sheet1:   todos los comprobantes del Listado con columnas de comparación
  sheet2:   comprobantes solo en Listado (no encontrados en ARCA)
  sheet3:   comprobantes solo en ARCA (sin contraparte en el Listado)
  warnings: lista de dict {"msg": str, "type": "warning"|"error"} para renderizar
            fuera de st.status() y evitar el bug removeChild de Streamlit
"""
import re

import pandas as pd


def conciliar(df_listado, df_arca, tolerancia: float, extra_cols=None):
    """Cruza Listado con ARCA y clasifica cada comprobante.

    extra_cols: lista de {"label": str, "col_l": str, "col_a": str} para comparaciones
    adicionales de alícuotas. Generan columnas Dif_<label> y Match_<label> informativas
    (no afectan el flag Conciliado).

    Retorna (sheet1, sheet2, sheet3, warnings). Los warnings deben renderizarse en la
    app después de que el bloque st.status() cierre, para evitar el bug removeChild.
    """
    extra_cols = extra_cols or []
    _warnings: list[dict] = []

    # Normalizar índices para evitar IndexingError en mascaras booleanas
    df_listado = df_listado.reset_index(drop=True)
    df_arca    = df_arca.reset_index(drop=True)

    # Agregar ARCA por comprobante: un comprobante con múltiples alícuotas de IVA
    # aparece como varias filas en ARCA (una por alícuota). Se suman Neto, IVA y
    # OtrosTrib; se toma el primer valor para Total (repetido en cada fila) y
    # para todos los campos de metadata (fecha, CUIT, denominación, etc.).
    #
    # La agrupación es por (Comprobante_Key, CUIT_norm, es_NC): la clave
    # PtoVta-Número NO es única entre emisores distintos (Facturas C de
    # monotributistas suelen compartir punto de venta 1 y números bajos), y
    # la numeración de ARCA es POR TIPO de comprobante — la Factura C N°1 y
    # la NC C N°1 del mismo emisor comparten clave. Agrupar sin estos campos
    # fusiona comprobantes distintos en una fila falsa.
    _has_cuit_arca = "CUIT_norm" in df_arca.columns
    _has_nc_arca   = "es_NC" in df_arca.columns
    _group_keys_arca = ["Comprobante_Key"]
    if _has_cuit_arca:
        _group_keys_arca.append("CUIT_norm")
    if _has_nc_arca:
        _group_keys_arca.append("es_NC")
    _dupes_arca = df_arca[df_arca.duplicated(_group_keys_arca, keep=False)]
    if not _dupes_arca.empty:
        n_keys = _dupes_arca["Comprobante_Key"].nunique()
        _warnings.append({
            "msg": (
                f"ARCA contiene múltiples filas para {n_keys} comprobante(s) "
                "(distintas alícuotas de IVA). Se agrupan sumando IVA y Neto."
            ),
            "type": "warning",
        })
    _sum_fields_arca = {"Neto_Total_ARCA", "Total IVA", "Otros Tributos"}
    for _xc in extra_cols:
        _ca = _xc.get("col_a", "")
        if _ca and _ca in df_arca.columns:
            _sum_fields_arca.add(_ca)
    _sum_c_arca   = [c for c in df_arca.columns if c in _sum_fields_arca]
    _first_c_arca = [c for c in df_arca.columns
                     if c not in _sum_fields_arca and c not in _group_keys_arca]
    _agg_arca = {c: (c, "sum") for c in _sum_c_arca}
    _agg_arca.update({c: (c, "first") for c in _first_c_arca})
    df_arca = df_arca.groupby(_group_keys_arca, as_index=False).agg(**_agg_arca)

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

    # Flag NC del Listado para el JOIN: como la numeración de ARCA es por tipo,
    # el cruce debe distinguir factura de NC para no aparear la Factura N°1
    # con la NC N°1 del mismo emisor.
    _tipo_l_j  = df_listado.get("Tipo",     pd.Series("", index=df_listado.index)).astype(str)
    _tipod_l_j = df_listado.get("Tipo_Doc", pd.Series("", index=df_listado.index)).astype(str)
    df_listado["_es_nc_l"] = (
        _tipo_l_j.str.upper().str.startswith("NCC")
        | _tipod_l_j.str.upper().str.startswith("NC ")
    )

    # Separar nacionales y externos para JOIN diferente
    mask_ext    = (df_listado.get("Origen", pd.Series("Nacional", index=df_listado.index)) == "Exterior")
    listado_nac = df_listado[~mask_ext]
    listado_ext = df_listado[mask_ext]

    _l_keys = ["Comprobante", "CUIT_norm"]
    _r_keys = ["ARCA_Key", "ARCA_CUIT_norm"]
    if _has_nc_arca:
        _l_keys = _l_keys + ["_es_nc_l"]
        _r_keys = _r_keys + ["ARCA_es_NC"]

    merged_nac = pd.merge(
        listado_nac, arca_slim,
        left_on=_l_keys,
        right_on=_r_keys,
        how="left",
    )

    # ── Fallback: mismo comprobante, CUIT vía NroDoc_norm ────────────────────
    # Cuando el CUIT registrado en el Listado difiere del Nro. Doc. Emisor de ARCA
    # (ej.: Benvido), se reintenta el join usando la columna Nro. Doc. del Listado.
    # Las filas no matcheadas se extraen del propio merge (columnas del Listado),
    # nunca filtrando listado_nac por Comprobante: dos proveedores pueden compartir
    # número y eso duplicaría la fila que sí matcheó.
    _listado_cols = [c for c in listado_nac.columns if c in merged_nac.columns]
    if "NroDoc_norm" in listado_nac.columns:
        _unm_mask = merged_nac["ARCA_Key"].isna()
        if _unm_mask.any():
            _used_keys   = set(merged_nac["ARCA_Key"].dropna())
            _arca_for_fb = arca_slim[~arca_slim["ARCA_Key"].isin(_used_keys)]
            _unm_l       = merged_nac.loc[_unm_mask, _listado_cols].copy()
            if not _unm_l.empty and not _arca_for_fb.empty:
                _fb_l = ["Comprobante", "NroDoc_norm"]
                _fb_r = ["ARCA_Key", "ARCA_CUIT_norm"]
                if _has_nc_arca and "_es_nc_l" in _unm_l.columns:
                    _fb_l = _fb_l + ["_es_nc_l"]
                    _fb_r = _fb_r + ["ARCA_es_NC"]
                _fb = pd.merge(
                    _unm_l, _arca_for_fb,
                    left_on=_fb_l,
                    right_on=_fb_r,
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
        # Este join es por clave sola: si varias filas de ARCA comparten la
        # clave (emisores distintos), quedarse con una para no duplicar la
        # fila del Listado — el estado será "CUIT no coincide" igualmente.
        _arca_for_cuit = (
            arca_slim[~arca_slim["ARCA_Key"].isin(_used_keys_c)]
            .drop_duplicates("ARCA_Key", keep="first")
        )
        _unm_l_c       = merged_nac.loc[_unm_mask_cuit, _listado_cols].copy()
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
        listado_ext, arca_slim.drop_duplicates("ARCA_Key", keep="first"),
        left_on="Comprobante",
        right_on="ARCA_Key",
        how="left",
    )
    merged = pd.concat([merged_nac, merged_ext], ignore_index=True, sort=False)
    merged["Existe_en_ARCA"] = merged["ARCA_Key"].notna()

    # Capturar los pares matcheados ANTES de la corrección de signo de NC:
    # ese bloque puede mutar ARCA_es_NC y corrompería el rastreo que usa
    # sheet3 para determinar qué filas de ARCA quedaron sin contraparte.
    _matched_tuples: set = set()
    if _has_cuit_arca and "ARCA_CUIT_norm" in merged.columns:
        _m_ok = merged[merged["ARCA_Key"].notna()]
        if _has_nc_arca and "ARCA_es_NC" in merged.columns:
            _matched_tuples = set(zip(
                _m_ok["ARCA_Key"].astype(str),
                _m_ok["ARCA_CUIT_norm"].fillna("").astype(str),
                _m_ok["ARCA_es_NC"].fillna(False).astype(bool),
            ))
        else:
            _matched_tuples = set(zip(
                _m_ok["ARCA_Key"].astype(str),
                _m_ok["ARCA_CUIT_norm"].fillna("").astype(str),
            ))

    # Corrección de signo para NC donde ARCA no detectó es_NC
    # (ocurre cuando la columna Tipo de ARCA no está mapeada o usa código numérico).
    # Se usa el Tipo del Listado como fuente de verdad para identificar la NC.
    _tipo_l     = merged.get("Tipo",     pd.Series("", index=merged.index))
    _tipo_doc_l = merged.get("Tipo_Doc", pd.Series("", index=merged.index))
    _nc_listado = (
        _tipo_l.str.upper().str.startswith("NCC")
        | _tipo_doc_l.str.upper().str.startswith("NC ")
    )
    _arca_es_nc_cur = pd.to_numeric(
        merged.get("ARCA_es_NC", pd.Series(0, index=merged.index)),
        errors="coerce",
    ).fillna(0).astype(bool)
    _sign_fix_mask = _nc_listado & merged["Existe_en_ARCA"] & ~_arca_es_nc_cur
    if _sign_fix_mask.any():
        for _sc in ["ARCA_Neto", "ARCA_IVA", "ARCA_OtrosTrib", "ARCA_Total"]:
            if _sc in merged.columns:
                merged.loc[_sign_fix_mask, _sc] = -merged.loc[_sign_fix_mask, _sc].abs()
        if "ARCA_es_NC" in merged.columns:
            merged.loc[_sign_fix_mask, "ARCA_es_NC"] = True

    tol = tolerancia
    for campo in ["Neto", "IVA", "Total"]:
        merged[f"Dif_{campo}"] = (
            pd.to_numeric(merged[campo], errors="coerce").fillna(0)
            - pd.to_numeric(merged[f"ARCA_{campo}"], errors="coerce").fillna(0)
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
            _warnings.append({
                "msg":  (
                    f"⚠️ {n_cd} comprobante(s) con CUIT diferente al de ARCA — "
                    "no pueden conciliarse hasta corregir el CUIT en el sistema contable."
                ),
                "type": "error",
            })
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

    # Comprobantes solo en ARCA (no unieron en el JOIN).
    # El descarte es por par (clave, CUIT): con claves compartidas entre
    # emisores, descartar por clave sola ocultaría las filas de los otros
    # emisores que NO matchearon.
    keep = [c for c in [
        "Comprobante_Key", "Fecha", "Tipo_Doc_ARCA", "Nro. Doc. Emisor",
        "Denominación Emisor", "Neto Gravado Total", "Neto No Gravado", "Op. Exentas",
        "Otros Tributos", "Total IVA", "Imp. Total", "es_NC",
    ] if c in df_arca.columns]
    if _matched_tuples or (_has_cuit_arca and "ARCA_CUIT_norm" in merged.columns):
        if _has_nc_arca:
            _tuples_arca = list(zip(
                df_arca["Comprobante_Key"].astype(str),
                df_arca["CUIT_norm"].fillna("").astype(str),
                df_arca["es_NC"].fillna(False).astype(bool),
            ))
        else:
            _tuples_arca = list(zip(
                df_arca["Comprobante_Key"].astype(str),
                df_arca["CUIT_norm"].fillna("").astype(str),
            ))
        # Series booleana (no list): una lista vacía sería interpretada por
        # pandas como selección de columnas y rompería con df_arca vacío.
        _mask_solo = pd.Series(
            [t not in _matched_tuples for t in _tuples_arca],
            index=df_arca.index, dtype=bool,
        )
        sheet3 = df_arca[_mask_solo][keep].copy()
    else:
        arca_matched_keys = set(merged["ARCA_Key"].dropna())
        sheet3 = df_arca[~df_arca["Comprobante_Key"].isin(arca_matched_keys)][keep].copy()

    return sheet1, sheet2, sheet3, _warnings
