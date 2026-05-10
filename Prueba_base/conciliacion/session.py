"""
Gestor centralizado del session state de Streamlit.

Garantiza que cuando cambia el contexto de carga (nuevo archivo, nueva sesión)
se invalide exactamente el estado derivado correspondiente — sin dejar residuos
de datasets anteriores, mappings viejos ni widgets congelados.

Principio de diseño:
  - Cada carga de archivo genera un upload_session_id único (UUID hex).
  - Todo el estado derivado de esa carga se invalida cuando el ID cambia.
  - Hay tres niveles de invalidación: upload completo > un lado > resultados.
"""
from __future__ import annotations

import uuid
import streamlit as st

# ── Claves por categoría ──────────────────────────────────────────────────────

# IDs de archivo (detectados por nombre+tamaño)
_FILE_ID_KEYS = ["ml_file_id", "ma_file_id"]

# Estado derivado del análisis de un archivo
_FILE_STATE_KEYS_ML = ["ml_cols", "ml_formato", "ml_sug", "ml_conf", "left_fingerprints"]
_FILE_STATE_KEYS_MA = ["ma_cols", "ma_sug", "ma_conf", "right_fingerprints"]

# Estado del mapeo construido por el usuario
_MAPPING_KEYS = ["mapeo_listado", "mapeo_arca", "mapping_rules",
                 "perfil_sel", "extra_comparaciones", "perfil_match_score"]

# Resultados de conciliación
_RESULT_KEYS = [
    "loaded", "s1", "s2", "s3",
    "correcciones", "periodo_actual",
    "sel_hist_key", "_hist_loaded_ts",
    "_excel_hash", "_excel_bytes",
]

# Prefijos de claves de widgets de mapeo — se eliminan por coincidencia de prefijo
_WIDGET_PREFIXES = ("ml_", "ma_", "opt_ml_", "opt_ma_", "xtra_")


# ── Manager ───────────────────────────────────────────────────────────────────

class SessionManager:
    """Punto de control único para el ciclo de vida del session state."""

    # ── Reset completo ────────────────────────────────────────────────────────

    @classmethod
    def new_upload_session(cls):
        """Reset TOTAL — nuevo ID único, limpia TODO el estado derivado.

        Usar cuando el contexto de trabajo cambia completamente
        (ej: se sustituyen ambos archivos a la vez, o se reinicia la app).
        """
        cls._clear_widget_keys()
        for k in (_FILE_ID_KEYS
                  + _FILE_STATE_KEYS_ML
                  + _FILE_STATE_KEYS_MA
                  + _MAPPING_KEYS
                  + _RESULT_KEYS):
            st.session_state.pop(k, None)
        st.session_state["upload_session_id"] = uuid.uuid4().hex[:8]

    # ── Reset de un lado ──────────────────────────────────────────────────────

    @classmethod
    def invalidate_file(cls, side: str):
        """Invalida el estado de un archivo al detectar un nuevo ID.

        side: 'ml' (Listado) | 'ma' (ARCA)

        Limpia:
          - columnas detectadas y formato del lado indicado
          - todo el mapeo construido (depende de ambos archivos)
          - los resultados de conciliación
          - los widgets de ese lado
        """
        file_keys = _FILE_STATE_KEYS_ML if side == "ml" else _FILE_STATE_KEYS_MA
        for k in file_keys + _MAPPING_KEYS + _RESULT_KEYS:
            st.session_state.pop(k, None)
        cls._clear_widget_keys(side=side)
        st.session_state[f"{side}_file_id"] = None  # fuerza re-detección

    # ── Reset de resultados ───────────────────────────────────────────────────

    @classmethod
    def invalidate_results(cls):
        """Invalida solo los resultados — mantiene archivo y mapeo."""
        for k in _RESULT_KEYS:
            st.session_state.pop(k, None)

    # ── Limpieza de widgets ───────────────────────────────────────────────────

    @classmethod
    def _clear_widget_keys(cls, side: str | None = None):
        """Elimina claves de widgets de mapeo (por prefijo).

        Si side='ml' o 'ma', solo limpia ese lado.
        Si side=None, limpia todos los prefijos.
        """
        if side:
            prefixes = (f"{side}_", f"opt_{side}_")
        else:
            prefixes = _WIDGET_PREFIXES
        to_remove = [
            k for k in list(st.session_state.keys())
            if k.startswith(prefixes)
        ]
        for k in to_remove:
            st.session_state.pop(k, None)

    # ── Lecturas de estado ────────────────────────────────────────────────────

    @classmethod
    def upload_session_id(cls) -> str:
        return st.session_state.get("upload_session_id", "init")

    @classmethod
    def has_results(cls) -> bool:
        return bool(st.session_state.get("loaded"))

    @classmethod
    def has_files(cls) -> bool:
        return bool(
            st.session_state.get("ml_cols") or st.session_state.get("ma_cols")
        )

    @classmethod
    def get_mapping_rules(cls) -> list:
        """Lee MappingRules actuales del session state."""
        from .models import MappingRule
        raw = st.session_state.get("mapping_rules", [])
        if not raw:
            return []
        if isinstance(raw[0], dict):
            return [MappingRule.from_dict(r) for r in raw]
        return raw

    @classmethod
    def set_mapping_rules(cls, rules: list):
        """Persiste MappingRules en session state como dicts serializables."""
        from .models import MappingRule
        st.session_state["mapping_rules"] = [
            r.to_dict() if isinstance(r, MappingRule) else r
            for r in rules
        ]

    @classmethod
    def file_changed(cls, side: str, new_id: str) -> bool:
        """Retorna True si el archivo del lado indicado cambió respecto al estado actual."""
        return st.session_state.get(f"{side}_file_id") != new_id

    @classmethod
    def mark_file_processed(cls, side: str, file_id: str):
        """Marca un archivo como procesado para evitar re-análisis innecesario."""
        st.session_state[f"{side}_file_id"] = file_id
