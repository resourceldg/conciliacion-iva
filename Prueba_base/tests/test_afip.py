"""
Tests de la decodificación de tipos de comprobante AFIP y del parser
numérico argentino — incorporados junto con el soporte del export CSV
de Mis Comprobantes (código numérico + coma decimal).
"""
import pandas as pd
import pytest

from conciliacion.constants import NC_AFIP, TIPOS_AFIP
from conciliacion.utils import (
    _codigo_afip,
    _es_nota_credito_arca,
    _tipo_doc_afip,
    _to_num_ar,
)


class TestCodigoAfip:
    def test_codigo_crudo_str(self):
        assert _codigo_afip("3") == 3

    def test_codigo_crudo_int(self):
        assert _codigo_afip(3) == 3

    def test_codigo_float(self):
        assert _codigo_afip(3.0) == 3

    def test_codigo_con_ceros(self):
        assert _codigo_afip("003") == 3

    def test_formato_xlsx(self):
        assert _codigo_afip("3 - Nota de Crédito A") == 3

    def test_formato_xlsx_dos_digitos(self):
        assert _codigo_afip("11 - Factura C") == 11

    def test_texto_puro_retorna_none(self):
        assert _codigo_afip("Notas de Crédito A") is None

    def test_vacio_retorna_none(self):
        assert _codigo_afip("") is None

    def test_codigo_fce(self):
        assert _codigo_afip("203") == 203


class TestTipoDocAfip:
    def test_codigo_crudo(self):
        assert _tipo_doc_afip("1") == "Factura A"

    def test_codigo_nc(self):
        assert _tipo_doc_afip(13) == "N.Crédito C"

    def test_formato_xlsx(self):
        assert _tipo_doc_afip("3 - Nota de Crédito A") == "N.Crédito A"

    def test_codigo_desconocido_retorna_original(self):
        assert _tipo_doc_afip("999") == "999"

    def test_texto_puro_retorna_original(self):
        assert _tipo_doc_afip("Factura X") == "Factura X"


class TestEsNotaCreditoArca:
    @pytest.mark.parametrize("cod", sorted(NC_AFIP))
    def test_todos_los_codigos_nc(self, cod):
        assert _es_nota_credito_arca(str(cod)) is True

    def test_factura_a_no_es_nc(self):
        assert _es_nota_credito_arca("1") is False

    def test_factura_c_no_es_nc(self):
        assert _es_nota_credito_arca("11") is False

    def test_nd_no_es_nc(self):
        assert _es_nota_credito_arca("2") is False

    def test_formato_xlsx_nc(self):
        assert _es_nota_credito_arca("3 - Nota de Crédito A") is True

    def test_formato_xlsx_factura(self):
        assert _es_nota_credito_arca("1 - Factura A") is False

    def test_texto_plural_csv_viejo(self):
        assert _es_nota_credito_arca("Notas de Crédito A") is True

    def test_codigos_nc_estan_en_tabla(self):
        # Todos los códigos NC deben tener etiqueta en la tabla
        assert NC_AFIP <= set(TIPOS_AFIP)


class TestToNumAr:
    def test_coma_decimal(self):
        s = pd.Series(["106470,00", "89250,50"])
        assert _to_num_ar(s).tolist() == [106470.0, 89250.5]

    def test_miles_y_coma(self):
        s = pd.Series(["1.234.567,89"])
        assert _to_num_ar(s).tolist() == [1234567.89]

    def test_punto_decimal_pasa_directo(self):
        s = pd.Series(["1234.56"])
        assert _to_num_ar(s).tolist() == [1234.56]

    def test_negativos(self):
        s = pd.Series(["-48.037,00"])
        assert _to_num_ar(s).tolist() == [-48037.0]

    def test_ya_numerico_pasa_directo(self):
        s = pd.Series([100.5, 200])
        assert _to_num_ar(s).tolist() == [100.5, 200.0]

    def test_vacios_y_basura_a_cero(self):
        s = pd.Series(["", "abc", None])
        assert _to_num_ar(s).tolist() == [0.0, 0.0, 0.0]
