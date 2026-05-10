"""
Generación del reporte Excel de tres hojas.

Incluye:
  - generar_excel: produce un .xlsx con hojas "Conciliación", "Solo en Listado" y "Solo en ARCA"

Convenciones de color en el Excel:
  - Verde:    match / conciliado
  - Rojo:     diferencia / no match
  - Amarillo: advertencia (Total OK sin desglose)
  - Gris:     sin información (sin match en ARCA)
  - Azul:     comprobante exterior
  - Celeste:  revisado / aceptado manualmente
  - Números negativos en rojo (NC)

Todas las hojas tienen encabezado fijo (freeze panes) y autofiltro activado.
El ancho de columna se ajusta automáticamente al contenido.
"""
from io import BytesIO

import pandas as pd


def generar_excel(s1, s2, s3) -> bytes:
    """Genera un Excel de tres hojas con formato profesional.

    Recibe los tres DataFrames de resultado de conciliar() y retorna los bytes
    del archivo Excel listo para descargar.
    """
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        wb  = writer.book
        hdr = wb.add_format({
            "bold": True, "font_color": "white", "bg_color": "#1e3a5f",
            "border": 1, "align": "center", "valign": "vcenter",
        })
        f_ok  = wb.add_format({"bg_color": "#dcfce7", "font_color": "#15803d", "bold": True, "align": "center"})
        f_ko  = wb.add_format({"bg_color": "#fee2e2", "font_color": "#b91c1c", "bold": True, "align": "center"})
        f_num = wb.add_format({"num_format": "#,##0.00"})
        f_dok = wb.add_format({"num_format": "#,##0.00", "bg_color": "#dcfce7"})
        f_dko = wb.add_format({"num_format": "#,##0.00", "bg_color": "#fee2e2"})
        f_neg = wb.add_format({"num_format": "#,##0.00", "font_color": "#dc2626"})
        f_estado = {
            "Conciliado":              wb.add_format({"bg_color": "#dcfce7", "font_color": "#15803d", "bold": True}),
            "Total OK / Sin desglose": wb.add_format({"bg_color": "#fef9c3", "font_color": "#713f12"}),
            "Diferencia detectada":    wb.add_format({"bg_color": "#fee2e2", "font_color": "#b91c1c", "bold": True}),
            "Sin match en ARCA":       wb.add_format({"bg_color": "#f1f5f9", "font_color": "#64748b"}),
            "Exterior / No en ARCA":   wb.add_format({"bg_color": "#eff6ff", "font_color": "#1d4ed8"}),
            "Revisado / Aceptado":     wb.add_format({"bg_color": "#e0f2fe", "font_color": "#0369a1", "bold": True}),
            "✔️ Revisado / Aceptado":  wb.add_format({"bg_color": "#e0f2fe", "font_color": "#0369a1", "bold": True}),
        }

        def wsheet(df, name, bool_cols=None, num_cols=None, diff_cols=None, tol=0.07):
            """Escribe un DataFrame en una hoja con formatos por tipo de columna."""
            df.to_excel(writer, sheet_name=name, index=False, startrow=1, header=False)
            ws = writer.sheets[name]
            for c, col in enumerate(df.columns):
                ws.write(0, c, col, hdr)
            for r, row in enumerate(df.itertuples(index=False), 1):
                for c, (col, val) in enumerate(zip(df.columns, row)):
                    nan = not isinstance(val, str) and pd.isna(val) if hasattr(val, "__class__") else False
                    if col == "Estado":
                        ws.write(r, c, "" if nan else str(val), f_estado.get(str(val), wb.add_format()))
                    elif bool_cols and col in bool_cols:
                        ws.write(r, c, "✓" if val is True else "✗", f_ok if val is True else f_ko)
                    elif diff_cols and col in diff_cols:
                        try:
                            v = float(val)
                            ws.write_number(r, c, v, f_dok if v <= tol else f_dko)
                        except (TypeError, ValueError):
                            ws.write(r, c, val)
                    elif num_cols and col in num_cols:
                        try:
                            v = float(val)
                            ws.write_number(r, c, v, f_neg if v < 0 else f_num)
                        except (TypeError, ValueError):
                            ws.write(r, c, "" if nan else val)
                    else:
                        ws.write(r, c, "" if nan else val)
            for c, col in enumerate(df.columns):
                ws.set_column(c, c, min(max(len(str(col)), 10) + 3, 42))
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, len(df), len(df.columns) - 1)

        NUM1  = {"Neto", "IVA", "Total", "ARCA_Neto", "ARCA_IVA", "ARCA_OtrosTrib", "ARCA_Total"}
        DIFF  = {"Dif_Neto", "Dif_IVA", "Dif_Total"}
        BOOL1 = {"Existe_en_ARCA", "Match_Neto", "Match_IVA", "Match_Total", "Conciliado", "ARCA_es_NC", "ARCA_Neto_Derivado"}

        wsheet(s1, "Conciliación",    bool_cols=BOOL1, num_cols=NUM1, diff_cols=DIFF)
        wsheet(s2, "Solo en Listado", num_cols={"Neto", "IVA", "Total"})
        wsheet(s3, "Solo en ARCA",
               num_cols={"Neto Gravado Total", "Neto No Gravado", "Op. Exentas",
                         "Otros Tributos", "Total IVA", "Imp. Total"},
               bool_cols={"es_NC"})

    return buf.getvalue()
