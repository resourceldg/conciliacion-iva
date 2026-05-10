"""
Tests de funciones utilitarias, loaders y detección de formato.

Cubre:
  - _normalizar_col
  - _combinar_cols
  - _restore_bools
  - _agg_total
  - _origen_from_cuit_tipo
  - _es_nota_credito_arca
  - _detectar_periodo
  - _hash_s1
  - _detectar_cols_multi
  - _detectar_formato_colppy (tango, subdiario, pasion, libro, listado)
  - _load_tango_iva / _load_subdiario_iva / _load_pasion_iva (via load_listado_iva)
  - NC sign normalization en todos los loaders
  - load_arca: double-count, solo-exento, neto_derivado
"""
import io

import pandas as pd
import pytest

from conciliacion.utils import (
    _normalizar_col,
    _combinar_cols,
    _restore_bools,
    _agg_total,
    _origen_from_cuit_tipo,
    _es_nota_credito_arca,
    _detectar_periodo,
    _hash_s1,
)
from conciliacion.column_mapping import _detectar_cols_multi
from conciliacion.constants import BOOL_COLS


# ── _normalizar_col ───────────────────────────────────────────────────────────

class TestNormalizarCol:
    def test_pasa_a_minusculas(self):
        assert _normalizar_col("CUIT") == "cuit"

    def test_elimina_acentos(self):
        assert _normalizar_col("Razón Social") == "razon social"

    def test_elimina_puntuacion(self):
        assert _normalizar_col("Nro. Doc.") == "nro doc"

    def test_colapsa_espacios_multiples(self):
        assert _normalizar_col("  Total   Factura  ") == "total factura"

    def test_tilde_en_e(self):
        assert _normalizar_col("Crédito") == "credito"

    def test_combinado_colppy(self):
        assert _normalizar_col("CUIT/DNI") == "cuit dni"

    def test_ya_normalizado(self):
        assert _normalizar_col("neto") == "neto"

    def test_string_vacio(self):
        assert _normalizar_col("") == ""


# ── _combinar_cols ────────────────────────────────────────────────────────────

class TestCombinarCols:
    @pytest.fixture(autouse=True)
    def df(self):
        self.df = pd.DataFrame({"A": [1.0, 2.0], "B": [3.0, 4.0], "C": [0.5, 1.5]})

    def test_columna_unica_str(self):
        res = _combinar_cols(self.df, "A")
        pd.testing.assert_series_equal(res, pd.Series([1.0, 2.0]), check_names=False)

    def test_dos_columnas_suma(self):
        res = _combinar_cols(self.df, ["A", "B"])
        pd.testing.assert_series_equal(res, pd.Series([4.0, 6.0]), check_names=False)

    def test_tres_columnas_suma(self):
        res = _combinar_cols(self.df, ["A", "B", "C"])
        pd.testing.assert_series_equal(res, pd.Series([4.5, 7.5]), check_names=False)

    def test_columna_inexistente_ignorada(self):
        res = _combinar_cols(self.df, ["A", "NO_EXISTE"])
        pd.testing.assert_series_equal(res, pd.Series([1.0, 2.0]), check_names=False)

    def test_todas_inexistentes_retorna_ceros(self):
        res = _combinar_cols(self.df, ["X", "Y"])
        assert (res == 0.0).all()

    def test_lista_vacia_retorna_ceros(self):
        res = _combinar_cols(self.df, [])
        assert (res == 0.0).all()

    def test_str_vacio_retorna_ceros(self):
        res = _combinar_cols(self.df, "")
        assert (res == 0.0).all()

    def test_no_numericos_coerce_a_cero(self):
        df2 = pd.DataFrame({"X": ["texto", "2.5"]})
        res = _combinar_cols(df2, "X")
        pd.testing.assert_series_equal(res, pd.Series([0.0, 2.5]), check_names=False)

    def test_str_equivale_a_lista_un_elemento(self):
        res_str  = _combinar_cols(self.df, "A")
        res_list = _combinar_cols(self.df, ["A"])
        pd.testing.assert_series_equal(res_str, res_list, check_names=False)


# ── _detectar_cols_multi ──────────────────────────────────────────────────────

class TestDetectarColsMulti:
    COLS_COLPPY = [
        "Neto gravado 21%", "Neto gravado 10,5%", "Neto gravado 27%",
        "IVA 21%", "IVA 10,5%", "IVA 27%", "Total", "Comprobante",
    ]
    COLS_ARCA = [
        "IVA 10,5%", "IVA 21%", "IVA 27%", "IVA 2,5%", "IVA 5%",
        "Total IVA", "Imp. Total", "Neto Gravado Total",
    ]

    def test_detecta_neto_gravado_listado(self):
        res = _detectar_cols_multi(self.COLS_COLPPY, "neto")
        assert res == ["Neto gravado 21%", "Neto gravado 10,5%", "Neto gravado 27%"]

    def test_detecta_iva_listado(self):
        res = _detectar_cols_multi(self.COLS_COLPPY, "iva")
        assert res == ["IVA 21%", "IVA 10,5%", "IVA 27%"]

    def test_detecta_iva_arca(self):
        res = _detectar_cols_multi(self.COLS_ARCA, "total_iva")
        assert res == ["IVA 10,5%", "IVA 21%", "IVA 27%", "IVA 2,5%", "IVA 5%"]

    def test_no_incluye_total_iva_en_iva(self):
        res = _detectar_cols_multi(self.COLS_ARCA, "total_iva")
        assert "Total IVA" not in res

    def test_campo_sin_prefijos_retorna_vacio(self):
        res = _detectar_cols_multi(self.COLS_COLPPY, "total")
        assert res == []

    def test_campo_desconocido_retorna_vacio(self):
        res = _detectar_cols_multi(self.COLS_COLPPY, "campo_inexistente")
        assert res == []

    def test_lista_vacia_retorna_vacio(self):
        res = _detectar_cols_multi([], "iva")
        assert res == []

    def test_sin_columnas_que_calzan_retorna_vacio(self):
        res = _detectar_cols_multi(["Total", "Comprobante", "Fecha"], "iva")
        assert res == []


# ── _restore_bools ────────────────────────────────────────────────────────────

class TestRestoreBools:
    def test_strings_true_false(self):
        df = pd.DataFrame({"Conciliado": ["True", "False", "True"]})
        res = _restore_bools(df)
        assert res["Conciliado"].dtype == bool
        assert list(res["Conciliado"]) == [True, False, True]

    def test_strings_minuscula(self):
        df = pd.DataFrame({"Match_Neto": ["true", "false"]})
        res = _restore_bools(df)
        assert list(res["Match_Neto"]) == [True, False]

    def test_enteros_1_0(self):
        df = pd.DataFrame({"Existe_en_ARCA": [1, 0, 1]})
        res = _restore_bools(df)
        assert list(res["Existe_en_ARCA"]) == [True, False, True]

    def test_bool_nativo(self):
        df = pd.DataFrame({"Conciliado": [True, False]})
        res = _restore_bools(df)
        assert res["Conciliado"].dtype == bool

    def test_nan_se_convierte_en_false(self):
        df = pd.DataFrame({"Match_IVA": [None, "True"]})
        res = _restore_bools(df)
        assert list(res["Match_IVA"]) == [False, True]

    def test_columnas_no_bool_sin_tocar(self):
        df = pd.DataFrame({"Comprobante": ["00001-00000001"], "Neto": [100.0]})
        res = _restore_bools(df)
        assert list(res["Comprobante"]) == ["00001-00000001"]
        assert res["Neto"].iloc[0] == 100.0

    def test_todas_las_columnas_bool_reconocidas(self):
        data = {col: ["True", "False"] for col in BOOL_COLS}
        df = pd.DataFrame(data)
        res = _restore_bools(df)
        for col in BOOL_COLS:
            assert res[col].dtype == bool, f"{col} debería ser bool"

    def test_arca_es_nc_en_bool_cols(self):
        assert "ARCA_es_NC" in BOOL_COLS

    def test_neto_derivado_en_bool_cols(self):
        assert "neto_derivado" in BOOL_COLS


# ── _agg_total ────────────────────────────────────────────────────────────────

class TestAggTotal:
    def test_primer_valor_no_cero(self):
        assert _agg_total(pd.Series([0, 0, 150.0, 150.0])) == 150.0

    def test_todos_cero_retorna_cero(self):
        assert _agg_total(pd.Series([0, 0, 0])) == 0

    def test_primer_posicion_no_cero(self):
        assert _agg_total(pd.Series([99.5, 0, 200.0])) == 99.5

    def test_valor_unico(self):
        assert _agg_total(pd.Series([121.0])) == 121.0

    def test_negativos_son_validos(self):
        assert _agg_total(pd.Series([0, 0, -121.0])) == -121.0


# ── _origen_from_cuit_tipo ────────────────────────────────────────────────────

class TestOrigenFromCuitTipo:
    def test_cuit_55_es_exterior(self):
        assert _origen_from_cuit_tipo("55-99999999-0") == "Exterior"

    def test_cuit_30_es_nacional(self):
        assert _origen_from_cuit_tipo("30-12345678-9") == "Nacional"

    def test_tipo_fcc_a_es_exterior(self):
        assert _origen_from_cuit_tipo("20-11111111-1", "FCC-A") == "Exterior"

    def test_tipo_fcc_b_es_exterior(self):
        assert _origen_from_cuit_tipo("20-11111111-1", "FCC-B") == "Exterior"

    def test_tipo_fce_es_exterior(self):
        assert _origen_from_cuit_tipo("20-11111111-1", "FCE-C") == "Exterior"

    def test_tipo_fac_es_nacional(self):
        assert _origen_from_cuit_tipo("20-11111111-1", "FAC-A") == "Nacional"

    def test_cuit_sin_guiones(self):
        assert _origen_from_cuit_tipo("30123456789") == "Nacional"

    def test_tipo_vacio_usa_cuit(self):
        assert _origen_from_cuit_tipo("55000000000", "") == "Exterior"


# ── _es_nota_credito_arca ─────────────────────────────────────────────────────

class TestEsNotaCreditoArca:
    def test_nc_con_tilde(self):
        assert _es_nota_credito_arca("Nota de Crédito") is True

    def test_nc_sin_tilde(self):
        assert _es_nota_credito_arca("nota de credito") is True

    def test_nc_con_codigo_arca(self):
        assert _es_nota_credito_arca("3 - Nota de Crédito A") is True

    def test_factura_no_es_nc(self):
        assert _es_nota_credito_arca("Factura A") is False

    def test_nota_debito_no_es_nc(self):
        assert _es_nota_credito_arca("Nota de Débito A") is False

    def test_string_vacio_no_es_nc(self):
        assert _es_nota_credito_arca("") is False


# ── _detectar_periodo ─────────────────────────────────────────────────────────

class TestDetectarPeriodo:
    def _s1(self, fechas):
        return pd.DataFrame({"Fecha_Factura": fechas})

    def test_mes_mayoritario(self):
        s1 = self._s1(["01/11/2025"] * 5 + ["01/12/2025"])
        assert _detectar_periodo(s1) == "2025-11"

    def test_empate_toma_mes_mas_reciente(self):
        s1 = self._s1(["01/11/2025"] * 3 + ["01/12/2025"] * 3)
        assert _detectar_periodo(s1) == "2025-12"

    def test_sin_columna_retorna_vacio(self):
        s1 = pd.DataFrame({"Comprobante": ["00001-00000001"]})
        assert _detectar_periodo(s1) == ""

    def test_fechas_invalidas_retorna_vacio(self):
        s1 = self._s1(["no-es-fecha", "tampoco", "xxx"])
        assert _detectar_periodo(s1) == ""

    def test_dataframe_vacio_retorna_vacio(self):
        s1 = pd.DataFrame({"Fecha_Factura": []})
        assert _detectar_periodo(s1) == ""

    def test_formato_periodo_correcto(self):
        s1 = self._s1(["15/03/2024"] * 4)
        assert _detectar_periodo(s1) == "2024-03"


# ── _hash_s1 ──────────────────────────────────────────────────────────────────

class TestHashS1:
    def _make(self, estado="Conciliado"):
        return pd.DataFrame({
            "Comprobante": ["00001-00000001", "00001-00000002"],
            "Estado":      [estado,           "Sin match en ARCA"],
            "Conciliado":  [True,             False],
        })

    def test_mismo_df_mismo_hash(self):
        s1 = self._make()
        assert _hash_s1(s1) == _hash_s1(s1.copy())

    def test_estado_distinto_hash_distinto(self):
        s1 = self._make("Conciliado")
        s2 = self._make("Diferencia detectada")
        assert _hash_s1(s1) != _hash_s1(s2)

    def test_none_retorna_string_vacio(self):
        assert _hash_s1(None) == ""

    def test_orden_de_filas_no_importa(self):
        s1 = self._make()
        s2 = s1.iloc[::-1].reset_index(drop=True)
        assert _hash_s1(s1) == _hash_s1(s2)

    def test_comprobante_adicional_cambia_hash(self):
        s1 = self._make()
        extra = pd.DataFrame({
            "Comprobante": ["00001-00000003"],
            "Estado":      ["Conciliado"],
            "Conciliado":  [True],
        })
        s2 = pd.concat([s1, extra], ignore_index=True)
        assert _hash_s1(s1) != _hash_s1(s2)


# ── Detección de formato (_detectar_formato_colppy) ───────────────────────────

def _make_xlsx(header_row: list, data_rows: list | None = None) -> io.BytesIO:
    """Crea un xlsx en memoria con una fila de encabezado y filas de datos opcionales."""
    rows = [header_row] + (data_rows or [])
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, header=False)
    buf.seek(0)
    return buf


class TestDetectarFormato:
    def test_tango_por_n_comp(self):
        from conciliacion.file_reader import _detectar_formato_colppy
        f = _make_xlsx(["N_COMP", "T_COMP", "FECHA_EMI", "IDENTIFTRI", "IMP_NETO", "IMP_IVA", "IMP_TOTAL"])
        assert _detectar_formato_colppy(f) == "tango"

    def test_tango_por_t_comp(self):
        from conciliacion.file_reader import _detectar_formato_colppy
        f = _make_xlsx(["T_COMP", "N_COMP", "IMP_TOTAL"])
        assert _detectar_formato_colppy(f) == "tango"

    def test_subdiario_por_n_de_comprobante(self):
        from conciliacion.file_reader import _detectar_formato_colppy
        f = _make_xlsx(["N° de comprobante", "Tipo de documento", "Cuit", "Total"])
        assert _detectar_formato_colppy(f) == "subdiario"

    def test_subdiario_por_tipo_de_documento(self):
        from conciliacion.file_reader import _detectar_formato_colppy
        f = _make_xlsx(["Tipo de documento", "Proveedor", "Total"])
        assert _detectar_formato_colppy(f) == "subdiario"

    def test_pasion_por_tipo_comprob_y_n_comprob(self):
        from conciliacion.file_reader import _detectar_formato_colppy
        f = _make_xlsx(["Fecha Contable", "Tipo Comprob.", "L", "Nº Comprob.", "C.U.I.T.", "Total"])
        assert _detectar_formato_colppy(f) == "pasion"

    def test_listado_colppy_default(self):
        from conciliacion.file_reader import _detectar_formato_colppy
        f = _make_xlsx(["Comprobante", "Fecha Factura", "CUIT/DNI", "Neto", "IVA", "Total"])
        assert _detectar_formato_colppy(f) == "listado"


# ── Loaders: formato Tango ────────────────────────────────────────────────────

def _make_tango_xlsx(rows: list) -> io.BytesIO:
    header = ["N_COMP", "T_COMP", "FECHA_EMI", "IDENTIFTRI", "NOM_PROVE",
              "COND_IVA", "IMP_NETO", "IMP_EXENTO", "IMP_IVA", "IMP_TOTAL"]
    df = pd.DataFrame([header] + rows)
    buf = io.BytesIO()
    buf.name = "tango.xlsx"
    df.to_excel(buf, index=False, header=False)
    buf.seek(0)
    return buf


class TestLoadTango:
    def test_carga_basica(self):
        from conciliacion.loaders import load_listado_iva
        rows = [["A0262100000001", "FAC", "2026-03-01", "30123456789", "PROVEEDOR SA",
                 "IVA Responsable", 1000.0, 0.0, 210.0, 1210.0]]
        df = load_listado_iva(_make_tango_xlsx(rows))
        assert df is not None
        assert len(df) == 1
        assert df["Comprobante"].iloc[0] == "02621-00000001"
        assert df["Tipo"].iloc[0] == "FAC-A"

    def test_nc_negativa(self):
        from conciliacion.loaders import load_listado_iva
        rows = [["A0010100001234", "N/C", "2026-03-01", "30123456789", "PROV SRL",
                 "IVA Responsable", 500.0, 0.0, 105.0, 605.0]]
        df = load_listado_iva(_make_tango_xlsx(rows))
        assert df is not None
        assert df["Total"].iloc[0] < 0
        assert df["Neto"].iloc[0] < 0

    def test_exento_suma_en_neto(self):
        from conciliacion.loaders import load_listado_iva
        rows = [["B0010100000099", "FAC", "2026-03-01", "20987654321", "EXENTO SA",
                 "Exento", 0.0, 10000.0, 0.0, 10000.0]]
        df = load_listado_iva(_make_tango_xlsx(rows))
        assert df is not None
        assert df["Neto"].iloc[0] == 10000.0


# ── Loaders: formato Subdiario ────────────────────────────────────────────────

def _make_subdiario_xlsx(rows: list) -> io.BytesIO:
    header = ["Fecha contable", "Tipo de documento", "N° de comprobante",
              "Proveedor", "Cuit", "Cat. fiscal",
              "Neto gravado 21%", "Neto gravado 10.50%", "No gravado",
              "Iva 21%", "Iva 10.50%", "Total"]
    df = pd.DataFrame([header] + rows)
    buf = io.BytesIO()
    buf.name = "subdiario.xlsx"
    df.to_excel(buf, index=False, header=False)
    buf.seek(0)
    return buf


class TestLoadSubdiario:
    def test_comprobante_con_letra_prefijo(self):
        from conciliacion.loaders import load_listado_iva
        rows = [["2026-03-01", "001-FC A", "A-00001-00354190",
                 "PROV SA", "30123456789", "Responsable Inscripto",
                 1000.0, 0.0, 0.0, 210.0, 0.0, 1210.0]]
        df = load_listado_iva(_make_subdiario_xlsx(rows))
        assert df is not None
        assert df["Comprobante"].iloc[0] == "00001-00354190"
        assert df["Tipo"].iloc[0] == "FAC-A"

    def test_comprobante_sin_letra_prefijo(self):
        from conciliacion.loaders import load_listado_iva
        rows = [["2026-03-01", "001-FC A", "0007-26032026",
                 "PROV SA", "30123456789", "Responsable Inscripto",
                 500.0, 0.0, 0.0, 105.0, 0.0, 605.0]]
        df = load_listado_iva(_make_subdiario_xlsx(rows))
        assert df is not None
        assert df["Comprobante"].iloc[0] == "00007-26032026"

    def test_nc_negativa(self):
        from conciliacion.loaders import load_listado_iva
        rows = [["2026-03-01", "003-NC A", "A-00003-00000099",
                 "PROV SRL", "20111222333", "Responsable Inscripto",
                 500.0, 0.0, 0.0, 105.0, 0.0, 605.0]]
        df = load_listado_iva(_make_subdiario_xlsx(rows))
        assert df is not None
        assert df["Total"].iloc[0] < 0
        assert df["Tipo"].iloc[0] == "NCC-A"

    def test_neto_suma_alicuotas(self):
        from conciliacion.loaders import load_listado_iva
        rows = [["2026-03-01", "001-FC A", "A-00001-00000001",
                 "PROV SA", "30999888777", "Responsable Inscripto",
                 800.0, 200.0, 100.0, 168.0, 21.0, 1289.0]]
        df = load_listado_iva(_make_subdiario_xlsx(rows))
        assert df is not None
        assert abs(df["Neto"].iloc[0] - 1100.0) < 0.01  # 800 + 200 + 100


# ── Loaders: formato Pasión ───────────────────────────────────────────────────

def _make_pasion_xlsx(rows: list) -> io.BytesIO:
    header = ["Fecha Contable", "Tipo Comprob.", "L", "Nº Comprob.",
              "Nombre del Proveedor", "C.U.I.T.", "Tipo Responsable",
              "Imp. Gravado", "GRAV 5%", "Imp.Exento", "Imp. Monotrib.",
              "IVA Fact.", "Imp.IVA", "Total", "Fecha Comprobante"]
    df = pd.DataFrame([header] + rows)
    buf = io.BytesIO()
    buf.name = "pasion.xlsx"
    df.to_excel(buf, index=False, header=False)
    buf.seek(0)
    return buf


class TestLoadPasion:
    def test_carga_basica(self):
        from conciliacion.loaders import load_listado_iva
        rows = [["2026-03-01", "FACTURA   ", "A", "0001-00000321",
                 "PROVEEDOR SA", "30-12345678-9", "INSCRIPTO",
                 1000.0, 0.0, 0.0, 0.0, 210.0, 210.0, 1210.0, "2026-03-01"]]
        df = load_listado_iva(_make_pasion_xlsx(rows))
        assert df is not None
        assert df["Comprobante"].iloc[0] == "00001-00000321"
        assert df["Tipo"].iloc[0] == "FAC-A"
        assert abs(df["Neto"].iloc[0] - 1000.0) < 0.01
        assert abs(df["IVA"].iloc[0] - 210.0) < 0.01

    def test_nc_negativa(self):
        from conciliacion.loaders import load_listado_iva
        rows = [["2026-03-01", "N/CREDITO ", "A", "0134-00124403",
                 "PROV SRL", "30-66666666-6", "INSCRIPTO",
                 500.0, 0.0, 0.0, 0.0, 105.0, 105.0, 605.0, "2026-03-01"]]
        df = load_listado_iva(_make_pasion_xlsx(rows))
        assert df is not None
        assert df["Total"].iloc[0] < 0
        assert df["Tipo"].iloc[0] == "NCC-A"

    def test_exento_incluido_en_neto(self):
        from conciliacion.loaders import load_listado_iva
        rows = [["2026-03-01", "FACTURA   ", "A", "0004-00138989",
                 "NEPTUNO VIAJES", "30-66343716-6", "INSCRIPTO",
                 234020.79, 0.0, 27200.0, 0.0, 37979.21, 37979.21, 299200.0, "2026-03-05"]]
        df = load_listado_iva(_make_pasion_xlsx(rows))
        assert df is not None
        assert abs(df["Neto"].iloc[0] - 261220.79) < 0.01

    def test_iva_con_coma_decimal(self):
        from conciliacion.loaders import load_listado_iva
        # Simula el bug de Pasión donde Imp.IVA viene con coma
        header = ["Fecha Contable", "Tipo Comprob.", "L", "Nº Comprob.",
                  "Nombre del Proveedor", "C.U.I.T.", "Tipo Responsable",
                  "Imp. Gravado", "GRAV 5%", "Imp.Exento", "Imp. Monotrib.",
                  "IVA Fact.", "Imp.IVA", "Total", "Fecha Comprobante"]
        data_row = ["2026-03-25", "FACTURA   ", "A", "0001-00000078",
                    "CAFEPE SRL", "30-71737443-2", "INSCRIPTO",
                    15215067.0, 0.0, 0.0, 0.0, 3195164.07, "3195164,07", 18410231.07, "2026-03-25"]
        df_raw = pd.DataFrame([header, data_row])
        buf = io.BytesIO()
        buf.name = "pasion_coma.xlsx"
        df_raw.to_excel(buf, index=False, header=False)
        buf.seek(0)
        result = load_listado_iva(buf)
        assert result is not None
        assert abs(result["IVA"].iloc[0] - 3195164.07) < 0.01

    def test_comprobante_normalizado(self):
        from conciliacion.loaders import load_listado_iva
        rows = [["2026-03-01", "FACTURA   ", "B", "0002-00000001",
                 "PROV SA", "20-11111111-1", "INSCRIPTO",
                 100.0, 0.0, 0.0, 0.0, 21.0, 21.0, 121.0, "2026-03-01"]]
        df = load_listado_iva(_make_pasion_xlsx(rows))
        assert df is not None
        assert df["Comprobante"].iloc[0] == "00002-00000001"
        assert df["Tipo"].iloc[0] == "FAC-B"


# ── load_arca: double-count y solo-exento ─────────────────────────────────────

def _make_arca_xlsx(rows: list) -> io.BytesIO:
    header = ["Fecha", "Tipo", "Punto de Venta", "Número Desde", "Número Hasta",
              "Cód. Autorización", "Tipo Doc. Emisor", "Nro. Doc. Emisor",
              "Denominación Emisor", "Tipo Doc. Receptor", "Nro. Doc. Receptor",
              "Tipo Cambio", "Moneda",
              "Neto Grav. IVA 0%", "IVA 2,5%", "Neto Grav. IVA 2,5%",
              "IVA 5%", "Neto Grav. IVA 5%", "IVA 10,5%", "Neto Grav. IVA 10,5%",
              "IVA 21%", "Neto Grav. IVA 21%", "IVA 27%", "Neto Grav. IVA 27%",
              "Neto Gravado Total", "Neto No Gravado", "Op. Exentas",
              "Otros Tributos", "Total IVA", "Imp. Total"]
    df = pd.DataFrame([header] + rows)
    buf = io.BytesIO()
    buf.name = "arca.xlsx"
    df.to_excel(buf, index=False, header=False)
    buf.seek(0)
    return buf


def _arca_row(pto_vta, nro, tipo, cuit, neto_grav=0, neto_ng=0, op_ex=0,
              otros=0, total_iva=0, total=0):
    return ["2026-03-01", tipo, pto_vta, nro, nro,
            "", "80", cuit, "PROV SA", "80", "30714541818",
            "1", "PES",
            0, 0, 0, 0, 0, 0, 0,
            total_iva, neto_grav, 0, 0,
            neto_grav, neto_ng, op_ex, otros, total_iva, total]


class TestLoadArca:
    def test_neto_derivado_false_para_factura_normal(self):
        from conciliacion.loaders import load_arca
        row = _arca_row(1, 321, "Factura A", "30123456789",
                        neto_grav=1000, total_iva=210, total=1210)
        df = load_arca(_make_arca_xlsx([row]))
        assert df is not None
        assert df["neto_derivado"].iloc[0] == False

    def test_neto_derivado_true_doble_conteo(self):
        from conciliacion.loaders import load_arca
        # ARCA pone el mismo importe en Neto Gravado Y Op. Exentas (doble conteo)
        row = _arca_row(1, 118219, "Factura A", "30123456789",
                        neto_grav=184337.3, op_ex=184337.3,
                        total_iva=0, total=184337.3)
        df = load_arca(_make_arca_xlsx([row]))
        assert df is not None
        assert df["neto_derivado"].iloc[0] == True
        assert abs(df["Neto_Total_ARCA"].iloc[0] - 184337.3) < 0.01

    def test_neto_derivado_true_solo_exento(self):
        from conciliacion.loaders import load_arca
        row = _arca_row(1, 999, "Factura C", "20111222333",
                        neto_grav=0, op_ex=5000, total_iva=0, total=5000)
        df = load_arca(_make_arca_xlsx([row]))
        assert df is not None
        assert df["neto_derivado"].iloc[0] == True

    def test_nc_total_negativo(self):
        from conciliacion.loaders import load_arca
        row = _arca_row(5, 152591, "Nota de Crédito A", "30123456789",
                        neto_grav=1000, total_iva=210, total=1210)
        df = load_arca(_make_arca_xlsx([row]))
        assert df is not None
        assert df["es_NC"].iloc[0] == True
        assert df["Imp. Total"].iloc[0] < 0
        assert df["Neto_Total_ARCA"].iloc[0] < 0


# ── Reconciler: NC exento concilia por Total ──────────────────────────────────

class TestReconcilerNC:
    def _listado_nc_exento(self):
        """Simula NC del Libro IVA con Neto=0 (exento), Total negativo."""
        return pd.DataFrame([{
            "Comprobante": "00151-00118219",
            "Tipo": "NCC-A", "Tipo_Doc": "NC A", "Origen": "Nacional",
            "CUIT_DNI": "30682737650", "CUIT_norm": "30682737650",
            "Razon_Social": "PROV SA",
            "Neto": 0.0, "IVA": 0.0, "Total": -184337.30,
        }])

    def _arca_nc_exento(self):
        """Simula ARCA NC exento con Neto derivado del Total."""
        return pd.DataFrame([{
            "Comprobante_Key": "00151-00118219",
            "CUIT_norm": "30682737650",
            "Neto_Total_ARCA": -184337.30,
            "Total IVA": 0.0,
            "Otros Tributos": 0.0,
            "Imp. Total": -184337.30,
            "Fecha": "2026-03-01",
            "Tipo_Doc_ARCA": "Nota de Crédito A",
            "Denominación Emisor": "PROV SA",
            "Nro. Doc. Emisor": "30682737650",
            "es_NC": True,
            "neto_derivado": True,
        }])

    def test_nc_exento_concilia_por_total(self):
        from conciliacion.reconciler import conciliar
        s1, _, _ = conciliar(self._listado_nc_exento(), self._arca_nc_exento(), 0.07)
        assert s1["Estado"].iloc[0] == "Conciliado"

    def test_nc_con_neto_iva_completo_concilia(self):
        from conciliacion.reconciler import conciliar
        l = pd.DataFrame([{
            "Comprobante": "00002-00000099", "Tipo": "NCC-A", "Tipo_Doc": "NC A",
            "Origen": "Nacional", "CUIT_DNI": "30123456789", "CUIT_norm": "30123456789",
            "Razon_Social": "PROV SA", "Neto": -660000.0, "IVA": -138600.0, "Total": -798600.0,
        }])
        a = pd.DataFrame([{
            "Comprobante_Key": "00002-00000099", "CUIT_norm": "30123456789",
            "Neto_Total_ARCA": -660000.0, "Total IVA": -138600.0, "Otros Tributos": 0.0,
            "Imp. Total": -798600.0, "Fecha": "2026-03-01", "Tipo_Doc_ARCA": "Nota de Crédito A",
            "Denominación Emisor": "PROV SA", "Nro. Doc. Emisor": "30123456789",
            "es_NC": True, "neto_derivado": False,
        }])
        s1, _, _ = conciliar(l, a, 0.07)
        assert s1["Estado"].iloc[0] == "Conciliado"

    def test_nc_sin_match_arca(self):
        from conciliacion.reconciler import conciliar
        l = pd.DataFrame([{
            "Comprobante": "00003-00000515", "Tipo": "NCC-A", "Tipo_Doc": "NC A",
            "Origen": "Nacional", "CUIT_DNI": "30714732540", "CUIT_norm": "30714732540",
            "Razon_Social": "PROV SA", "Neto": -420000.0, "IVA": -88200.0, "Total": -508200.0,
        }])
        a = pd.DataFrame(columns=["Comprobante_Key", "CUIT_norm", "Neto_Total_ARCA",
                                   "Total IVA", "Otros Tributos", "Imp. Total", "Fecha",
                                   "Tipo_Doc_ARCA", "Denominación Emisor", "Nro. Doc. Emisor",
                                   "es_NC", "neto_derivado"])
        s1, _, _ = conciliar(l, a, 0.07)
        assert s1["Estado"].iloc[0] == "Sin match en ARCA"
