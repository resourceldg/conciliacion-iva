"""
IVA Tools — Entry point principal
==================================
Registra las páginas y aplica configuración global (page_config + CSS).
Toda la lógica de UI vive en pages/.
"""

import subprocess
from pathlib import Path

import streamlit as st

from conciliacion.ui_helpers import APP_CSS

st.set_page_config(
    page_title="IVA Tools",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(APP_CSS, unsafe_allow_html=True)


@st.cache_resource
def _version_build() -> str:
    """Hash y fecha del commit en ejecución — visible para debugging.

    Si no hay git disponible (instalación por copia de archivos en Windows),
    el badge simplemente no se muestra.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h · %ad", "--date=format:%d/%m %H:%M"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


_ver = _version_build()
if _ver:
    st.sidebar.markdown(
        f"<div style='font-size:.72rem;color:#858585;background:#252526;"
        f"border:1px solid #3e3e42;border-radius:4px;padding:.25rem .6rem;"
        f"margin-bottom:.3rem'>🔧 build <code style='color:#9cdcfe'>{_ver}</code></div>",
        unsafe_allow_html=True,
    )

pg = st.navigation(
    {
        "Herramientas": [
            st.Page(
                "pages/conciliacion.py",
                title="Conciliación IVA",
                icon="📊",
                default=True,
            ),
            st.Page(
                "pages/posiciones.py",
                title="Posiciones IVA",
                icon="📋",
            ),
        ]
    },
    position="sidebar",
)
pg.run()
