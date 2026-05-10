"""
Configuración global de pytest.

Mockea Streamlit ANTES de importar el módulo de la app para evitar que
el código de nivel superior (set_page_config, widgets, etc.) falle.
Los tests de persistencia usan el fixture `tmp_db` para trabajar sobre
una base SQLite temporal aislada del archivo de producción.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

# ── 1. Mock de Streamlit ──────────────────────────────────────────────────────
# Debe ocurrir antes de cualquier `import app_conciliacion_iva` o
# `import conciliacion.*`, porque todos ellos hacen `import streamlit`.


class _SessionState(dict):
    """Dict que emula st.session_state; soporta operador `in` correctamente."""
    pass


_st = MagicMock()
_st.session_state = _SessionState()

# Widgets del flujo principal → valores seguros para que la importación
# no dispare procesamiento ni cargue archivos reales.
_st.button.return_value        = False
_st.file_uploader.return_value = None
_st.selectbox.return_value     = None
_st.number_input.return_value  = 0.07
_st.multiselect.return_value   = []
_st.checkbox.return_value      = False
_st.sidebar                    = MagicMock()


def _mock_columns(spec):
    n = len(spec) if isinstance(spec, (list, tuple)) else max(int(spec or 1), 1)
    return [MagicMock() for _ in range(n)]


_st.columns.side_effect = _mock_columns

sys.modules["streamlit"] = _st

# ── 2. Importar el módulo (el código de nivel superior se ejecuta aquí) ───────
import app_conciliacion_iva as app  # noqa: E402


# ── 3. Fixture: base de datos SQLite temporal ─────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """
    Redirige DB_FILE y DATA_DIR a un directorio temporal.
    Crea las tablas limpias y bloquea la migración legacy.

    Parchea tanto los módulos internos (donde se ejecuta el código)
    como el namespace de app (compatibilidad con tests existentes).
    """
    import conciliacion.database as _db_mod
    import conciliacion.constants as _const_mod

    db = tmp_path / "test.db"

    # Parchear en el módulo donde el código realmente usa las variables
    monkeypatch.setattr(_db_mod, "DB_FILE",    db)
    monkeypatch.setattr(_db_mod, "DATA_DIR",   tmp_path)
    monkeypatch.setattr(_db_mod, "PERSIST_DIR", tmp_path / "persistencia")
    monkeypatch.setattr(_const_mod, "DB_FILE",    db)
    monkeypatch.setattr(_const_mod, "DATA_DIR",   tmp_path)
    monkeypatch.setattr(_const_mod, "PERSIST_DIR", tmp_path / "persistencia")

    # Compat: el conftest anterior parcheaba `app.*`
    monkeypatch.setattr(app, "DB_FILE",    db)
    monkeypatch.setattr(app, "DATA_DIR",   tmp_path)
    monkeypatch.setattr(app, "PERSIST_DIR", tmp_path / "persistencia")

    # Evitar que _migrar_legacy() intente leer archivos legacy
    (tmp_path / ".db_migrated").touch()

    app.init_db()
    return db
