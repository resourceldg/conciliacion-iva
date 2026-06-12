"""
Tests del pipeline de carga del Listado: finalizador común y extractores.

El contrato canónico (ver docstring de conciliacion.loaders) es lo que une
extractores y finalizador — estos tests lo fijan con datos sintéticos, sin
necesidad de archivos reales.
"""
import pandas as pd
import pytest

from conciliacion.loaders import (
    FormatoInvalido,
    TIPO_LABEL,
    _extraer_libro_iva,
    _extraer_tango,
    _finalizar_listado,
)


def _df_canonico(**overrides):
    """Dos alícuotas de una factura + una NC, formato canónico crudo."""
    base = {
        "Comprobante":   ["00001-00000010", "00001-00000010", "00002-00000005"],
        "Tipo":          ["FAC-A", "FAC-A", "NCC-A"],
        "Fecha_Factura": ["01/05/2026", "01/05/2026", "02/05/2026"],
        "CUIT_DNI":      ["30-11111111-2", "30-11111111-2", "30-11111111-2"],
        "Razon_Social":  ["PROV SA", "PROV SA", "PROV SA"],
        "Condicion_IVA": ["RI", "RI", "RI"],
        "Neto":          [100.0, 50.0, 200.0],
        "IVA":           [21.0, 5.25, 42.0],
        "Total":         [176.25, 0.0, 242.0],
    }
    base.update(overrides)
    return pd.DataFrame(base)


class TestFinalizarListado:
    def test_agrupa_alicuotas_en_una_fila(self):
        agg = _finalizar_listado(_df_canonico())
        assert len(agg) == 2  # 2 comprobantes (3 filas crudas)
        fac = agg[agg["Comprobante"] == "00001-00000010"].iloc[0]
        assert fac["Neto"] == 150.0
        assert fac["IVA"] == 26.25
        assert fac["Total"] == 176.25  # primer Total no-cero

    def test_nc_queda_en_negativo(self):
        agg = _finalizar_listado(_df_canonico())
        nc = agg[agg["Tipo"] == "NCC-A"].iloc[0]
        assert nc["Neto"] == -200.0 and nc["IVA"] == -42.0 and nc["Total"] == -242.0

    def test_cuit_norm_solo_digitos(self):
        agg = _finalizar_listado(_df_canonico())
        assert (agg["CUIT_norm"] == "30111111112").all()

    def test_mismo_numero_distinto_cuit_no_se_mezcla(self):
        df = _df_canonico(
            Comprobante=["00001-00000010"] * 3,
            Tipo=["FAC-A"] * 3,
            CUIT_DNI=["30-11111111-2", "30-11111111-2", "30-22222222-3"],
        )
        agg = _finalizar_listado(df)
        assert len(agg) == 2  # un comprobante por CUIT

    def test_etiquetas_legibles(self):
        agg = _finalizar_listado(_df_canonico())
        assert set(agg["Tipo_Doc"]) == {"Factura A", "NC A"}

    def test_ot_cols_sumadas_negadas_y_en_attrs(self):
        df = _df_canonico(**{"Perc.IIBB": [10.0, 5.0, 30.0]})
        agg = _finalizar_listado(df, ot_cols=["Perc.IIBB"])
        assert agg.attrs["otros_tributos_cols"] == ["Perc.IIBB"]
        fac = agg[agg["Tipo"] == "FAC-A"].iloc[0]
        nc  = agg[agg["Tipo"] == "NCC-A"].iloc[0]
        assert fac["Perc.IIBB"] == 15.0
        assert nc["Perc.IIBB"] == -30.0  # signo de NC aplicado

    def test_extra_cols_sumadas_sin_negar(self):
        df = _df_canonico(**{"IVA 21%": [21.0, 0.0, 42.0]})
        agg = _finalizar_listado(df, extra_sum_cols=["IVA 21%"])
        nc = agg[agg["Tipo"] == "NCC-A"].iloc[0]
        assert nc["IVA 21%"] == 42.0  # informativa: conserva el signo original

    def test_nrodoc_default_es_cuit(self):
        agg = _finalizar_listado(_df_canonico())
        assert (agg["NroDoc_norm"] == agg["CUIT_norm"]).all()

    def test_origen_nacional(self):
        agg = _finalizar_listado(_df_canonico())
        assert (agg["Origen"] == "Nacional").all()


class TestExtractores:
    def test_libro_variante_a(self):
        # Hoja cruda: header en fila 0 con Suc./Letra/Numero/Gravado
        sheet = pd.DataFrame([
            ["Suc.", "Letra", "Numero", "Tipo Comprob.", "Nro.Doc.", "Proveedor",
             "Tipo Iva", "Fec. Factura", "Gravado", "Iva 21%", "Total"],
            [1, "A", 123, "Factura de Compra", "30111111112", "PROV SA",
             "RI", "01/05/2026", 100.0, 21.0, 121.0],
            [1, "A", 124, "Credito de Compra", "30111111112", "PROV SA",
             "RI", "02/05/2026", 50.0, 10.5, 60.5],
        ])
        df, ot_cols, nrodoc = _extraer_libro_iva(sheet)
        assert list(df["Comprobante"]) == ["00001-00000123", "00001-00000124"]
        assert list(df["Tipo"]) == ["FAC-A", "NCC-A"]
        assert df["IVA"].tolist() == [21.0, 10.5]
        assert nrodoc is not None  # Nro.Doc. presente

    def test_libro_sin_comprobantes_lanza_formato_invalido(self):
        sheet = pd.DataFrame([
            ["Suc.", "Numero", "Gravado"],
            ["texto", "x", "y"],   # ninguna fila con Suc. numérico
        ])
        with pytest.raises(FormatoInvalido):
            _extraer_libro_iva(sheet)

    def test_tango_columnas_faltantes_lanza_formato_invalido(self):
        sheet = pd.DataFrame([
            ["N_COMP.", "OTRA"],   # variante con punto: no es el export estándar
            ["A0000100000001", 1],
        ])
        with pytest.raises(FormatoInvalido):
            _extraer_tango(sheet)

    def test_tango_basico(self):
        sheet = pd.DataFrame([
            ["T_COMP", "N_COMP", "IDENTIFTRI", "NOM_PROVE", "FECHA_EMI",
             "COND_IVA", "IMP_NETO", "IMP_IVA", "IMP_TOTAL"],
            ["FAC", "A0000100000123", "30111111112", "PROV SA", "01/05/2026",
             "RI", 100.0, 21.0, 121.0],
            ["N/C", "A0000100000124", "30111111112", "PROV SA", "02/05/2026",
             "RI", 50.0, 10.5, 60.5],
        ])
        df, ot_cols, nrodoc = _extraer_tango(sheet)
        assert list(df["Comprobante"]) == ["00001-00000123", "00001-00000124"]
        assert list(df["Tipo"]) == ["FAC-A", "NCC-A"]
        assert ot_cols == [] and nrodoc is None


class TestContratoExtractorFinalizador:
    def test_extractor_a_finalizador_end_to_end(self):
        sheet = pd.DataFrame([
            ["Suc.", "Letra", "Numero", "Tipo Comprob.", "Nro.Doc.", "Proveedor",
             "Tipo Iva", "Fec. Factura", "Gravado", "Iva 21%", "Total", "Perc.IIBB"],
            [1, "A", 123, "Factura de Compra", "30111111112", "PROV SA",
             "RI", "01/05/2026", 100.0, 21.0, 131.0, 10.0],
            [2, "A", 50, "Credito de Compra", "30222222223", "OTRO SA",
             "RI", "02/05/2026", 40.0, 8.4, 48.4, 0.0],
        ])
        df, ot_cols, nrodoc = _extraer_libro_iva(sheet)
        agg = _finalizar_listado(df, ot_cols=ot_cols, nrodoc_col=nrodoc)
        assert len(agg) == 2
        assert agg.attrs["otros_tributos_cols"] == ["Perc.IIBB"]
        nc = agg[agg["Tipo"] == "NCC-A"].iloc[0]
        assert nc["Neto"] == -40.0 and nc["Total"] == -48.4
        # Todas las etiquetas resueltas por el mapa unificado
        assert set(agg["Tipo_Doc"]) <= set(TIPO_LABEL.values())
