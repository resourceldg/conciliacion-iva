"""
Lectura robusta de archivos Excel y CSV.

Incluye:
  - _find_libreoffice: localiza el ejecutable de LibreOffice según el OS
  - _libreoffice_xlsx: convierte XLS a XLSX usando LibreOffice headless
  - leer_excel: lee XLS/XLSX/CSV con cascada de fallbacks ante archivos corruptos
  - _mejor_hoja: selecciona la hoja de datos relevante de un dict {nombre: DataFrame}
  - _find_header: localiza la fila de encabezado buscando una keyword
  - _detectar_formato_colppy: clasifica un archivo Colppy como 'listado' o 'libro'
  - _detectar_columnas: lee solo el encabezado para devolver las columnas disponibles

El reto principal es que tanto Colppy como ARCA insertan filas de metadatos antes
del encabezado real, y los XLS de Colppy a veces tienen compound-document corruption
que xlrd rechaza. La cascada de intentos (xlrd → calamine → LibreOffice) cubre los
casos conocidos en producción.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path

import pandas as pd

from .utils import _fix_mojibake


def _find_libreoffice() -> str:
    """Devuelve el ejecutable de LibreOffice según el sistema operativo.

    En Windows LibreOffice se llama soffice.exe y frecuentemente no está en PATH.
    En Linux/Mac el comando es simplemente 'libreoffice'.
    """
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
        for path in candidates:
            if Path(path).exists():
                return path
        return shutil.which("soffice") or shutil.which("libreoffice") or "soffice"
    return "libreoffice"


def _libreoffice_xlsx(src_path: str) -> str:
    """Convierte un XLS a XLSX usando LibreOffice en modo headless.

    Colppy exporta XLS con un bug en el compound document que hace que xlrd
    lo rechace con CompDocError. LibreOffice lo repara al reescribirlo como XLSX.
    Retorna la ruta del XLSX generado en un directorio temporal.
    """
    out = tempfile.mkdtemp()
    subprocess.run(
        [_find_libreoffice(), "--headless", "--convert-to", "xlsx", "--outdir", out, src_path],
        capture_output=True, timeout=60,
    )
    return os.path.join(out, Path(src_path).stem + ".xlsx")


def leer_excel(source) -> dict:
    """Lee un archivo XLS/XLSX o CSV y retorna dict {nombre_hoja: DataFrame}.

    Acepta ruta (str) o BytesIO (upload de Streamlit).
    Para CSV: detecta separador y encoding automáticamente.
    Para XLS: cascada de intentos ante archivos corruptos (común en Colppy):
      1. pandas/xlrd  2. python-calamine  3. LibreOffice headless
    """
    # ── CSV ──────────────────────────────────────────────────────────────────
    _name = source if isinstance(source, str) else getattr(source, "name", "")
    if str(_name).lower().endswith(".csv"):
        try:
            if isinstance(source, str):
                raw = open(source, "rb").read()
            else:
                source.seek(0)
                raw = source.read()
                source.seek(0)
            for _enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
                try:
                    df_csv = pd.read_csv(
                        BytesIO(raw), sep=None, engine="python",
                        header=None, encoding=_enc, dtype=str,
                    )
                    if len(df_csv.columns) >= 1:
                        return {"Hoja1": df_csv}
                except Exception:
                    continue
        except Exception as e:
            raise ValueError(f"No se pudo leer el CSV: {e}") from e

    # ── Excel ─────────────────────────────────────────────────────────────────
    tmp_path = tmp_dir = None

    def _read(src_or_bytes, engine=None):
        kwargs = dict(sheet_name=None, header=None)
        if engine:
            kwargs["engine"] = engine
        if isinstance(src_or_bytes, str):
            return pd.read_excel(src_or_bytes, **kwargs)
        src_or_bytes.seek(0)
        return pd.read_excel(src_or_bytes, **kwargs)

    try:
        return _read(source)
    except Exception:
        pass

    try:
        return _read(source, engine="calamine")
    except Exception:
        pass

    _MSG_LO = (
        "No se pudo leer el archivo XLS.\n"
        "Intentá exportarlo como XLSX desde Colppy, o instalá LibreOffice "
        "para que la conversión automática funcione."
    )
    try:
        if isinstance(source, str):
            src = source
        else:
            source.seek(0)
            fd, src = tempfile.mkstemp(suffix=".xls")
            with os.fdopen(fd, "wb") as f:
                f.write(source.read())
            tmp_path = src
        try:
            xlsx = _libreoffice_xlsx(src)
        except Exception as lo_err:
            raise FileNotFoundError(_MSG_LO) from lo_err

        tmp_dir = os.path.dirname(xlsx)

        if not os.path.exists(xlsx):
            raise FileNotFoundError(_MSG_LO)

        try:
            return pd.read_excel(xlsx, sheet_name=None, header=None, engine="openpyxl")
        except Exception as read_err:
            raise ValueError(f"{_MSG_LO}\nDetalle técnico: {read_err}") from read_err
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _mejor_hoja(raw: dict, header_keyword: str | None = None) -> pd.DataFrame:
    """Selecciona la hoja de datos más relevante de un dict {nombre: DataFrame}.

    Si se indica `header_keyword`, prioriza la hoja que contenga una celda
    exactamente igual a esa keyword: un workbook puede traer hojas ajenas
    (ej. un export de ARCA con una copia del Libro IVA pegada en otra hoja),
    y el nombre de hoja solo no alcanza para distinguirlas.
    En su defecto prioriza hojas nombradas con keywords conocidos de Colppy,
    y como último recurso la hoja con más filas.
    """
    if len(raw) == 1:
        return next(iter(raw.values()))
    if header_keyword:
        kw = header_keyword.strip().lower()
        for df in raw.values():
            try:
                head = df.head(30)
                if head.apply(
                    lambda r: (r.astype(str).str.strip().str.lower() == kw).any(),
                    axis=1,
                ).any():
                    return df
            except Exception:
                pass
    _prio_kw = ["libro iva", "listado iva", "iva compras", "comprobantes", "datos"]
    for name, df in raw.items():
        name_l = str(name).lower()
        if any(kw in name_l for kw in _prio_kw):
            return df
    return max(raw.values(), key=lambda d: d.shape[0])


def _find_header(sheet: pd.DataFrame, keyword: str,
                 fallbacks: list[str] | None = None) -> int:
    """Busca la fila de encabezado y retorna su índice.

    Colppy y ARCA insertan filas de metadatos antes del encabezado real,
    por lo que no se puede asumir que la fila 0 es el header.
    Si no se encuentra `keyword`, intenta con `fallbacks` en orden.
    Retorna None si no encuentra ninguna de las palabras clave.

    Dos pases: primero igualdad exacta de celda (un encabezado real es una
    celda igual al keyword), después substring como red de seguridad. El pase
    exacto evita que una anotación libre en una fila de datos (ej. una celda
    "N° de comprobante correcto 3008-57124") se confunda con el encabezado.
    """
    kws = [keyword] + (fallbacks or [])
    for kw in kws:
        kw_n = str(kw).strip().lower()
        for i, row in sheet.iterrows():
            if (row.astype(str).str.strip().str.lower() == kw_n).any():
                return i
    for kw in kws:
        for i, row in sheet.iterrows():
            if row.astype(str).str.contains(kw, case=False, na=False).any():
                return i
    return None


def _detectar_formato_colppy(source) -> str:
    """Retorna 'libro', 'tango' o 'listado' según el formato del archivo.

    - 'tango':   columnas N_COMP y T_COMP en la primera fila (export de Tango)
    - 'libro':   Libro IVA Compras de Colppy/Xubio (hoja 'libro iva' o Suc.+Letra)
    - 'listado': Listado IVA Compras estándar de Colppy (default)
    """
    try:
        raw = leer_excel(source)
        if hasattr(source, "seek"):
            source.seek(0)
        sheet = _mejor_hoja(raw)
        header = sheet.iloc[0].astype(str).str.lower()
        if header.str.contains("n_comp", na=False).any() or header.str.contains("t_comp", na=False).any():
            return "tango"
        # Pasión ERP: columnas "tipo comprob." y "nº comprob." en fila 0
        if (header.str.contains(r"tipo\s+comprob", na=False, regex=True).any()
                and header.str.contains(r"n[º°]\s*comprob", na=False, regex=True).any()):
            return "pasion"
        # Subdiario de Compras (Contabilium / Finnegans): columna "n° de comprobante" o "tipo de documento"
        if (header.str.contains(r"n[°o]\s*de\s*comprobante", na=False, regex=True).any()
                or header.str.contains("tipo de documento", na=False).any()):
            return "subdiario"
        for name in raw:
            if "libro" in str(name).lower() and "iva" in str(name).lower():
                return "libro"
        if header.str.contains(r"suc\.", na=False).any() and header.str.contains("letra", na=False).any():
            return "libro"
        # IvaCompras / Libro IVA Compras con columna "Nro Factura" y datos por fila
        for i in range(min(10, len(sheet))):
            row = sheet.iloc[i].astype(str).str.lower()
            if row.str.contains(r"nro[\s._]?factur", na=False, regex=True).any():
                return "libro"
        # Libro IVA con columna "B.Imponible" (formato FC -A-XXXX-YYYYYYYY)
        for i in range(min(10, len(sheet))):
            row = sheet.iloc[i].astype(str).str.lower()
            if row.str.contains(r"b\.?\s*imponible", na=False, regex=True).any():
                return "libro_bim"
    except Exception:
        pass
    return "listado"


def _detectar_columnas(source, header_keyword: str,
                       fallbacks: list[str] | None = None) -> list:
    """Lee solo el encabezado del archivo para devolver la lista de columnas detectadas.

    Llamada una sola vez al cargar un nuevo archivo en el sidebar; el resultado se
    guarda en session_state para no releer el archivo en cada rerun.
    """
    try:
        raw   = leer_excel(source)
        sheet = _mejor_hoja(raw, header_keyword)
        hr    = _find_header(sheet, header_keyword, fallbacks)
        if hr is None:
            hr = 0
        cols = [_fix_mojibake(c).strip() for c in sheet.iloc[hr]
                if str(c).strip() and str(c) != "nan"]
        if hasattr(source, "seek"):
            source.seek(0)
        return cols
    except Exception:
        return []
