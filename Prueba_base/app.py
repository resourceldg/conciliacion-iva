"""
IVA Tools — Entry point principal
==================================
Registra las páginas y aplica configuración global (page_config + CSS).
Toda la lógica de UI vive en pages/.
"""

import streamlit as st

from conciliacion.ui_helpers import APP_CSS

st.set_page_config(
    page_title="IVA Tools",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(APP_CSS, unsafe_allow_html=True)

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
