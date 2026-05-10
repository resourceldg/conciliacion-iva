"""Genera el manual de instalación Windows en PDF.
   Usa solo fuentes Helvetica/Courier (latin-1) de fpdf2.
"""
from fpdf import FPDF, XPos, YPos
from datetime import date

AZUL        = (0,   122, 204)
OSCURO      = (30,  30,  30)
GRIS        = (80,  80,  80)
VERDE       = (21,  128, 61)
ROJO        = (185, 28,  28)
BORDE       = (62,  62,  66)
AMARILLO_BG = (254, 249, 195)
AZUL_BG     = (239, 246, 255)
VERDE_BG    = (220, 252, 231)
ANCHO_UTIL  = 175


def _s(text: str) -> str:
    """Sustituye caracteres fuera de latin-1 por equivalentes ASCII."""
    return (text
        .replace("—", " - ")   # em dash
        .replace("–", " - ")   # en dash
        .replace("•", "*")     # bullet
        .replace("→", "->")    # ->
        .replace("←", "<-")    # <-
        .replace("─", "-")     # box drawing horizontal
        .replace("ℹ", "(i)")   # info symbol
        .replace("⚠", "(!)")   # warning
        .replace("✓", "(OK)")  # check mark
        .replace("▶", ">")     # play triangle
        .replace("×", "x")     # multiplication sign
        .replace("·", ".")     # middle dot
        .replace("’", "'")     # right single quote
        .replace("‘", "'")     # left single quote
        .replace("“", '"')     # left double quote
        .replace("”", '"')     # right double quote
    )


class PDF(FPDF):

    def header(self):
        self.set_fill_color(20, 20, 20)
        self.rect(0, 0, 210, 14, "F")
        self.set_xy(10, 3)
        self.set_font("Helvetica", "B", 8.5)
        self.set_text_color(0, 122, 204)
        self.cell(0, 8, "Conciliacion IVA Compras  |  Manual de Instalacion Windows", align="L")
        self.set_text_color(100, 100, 100)
        self.set_xy(0, 3)
        self.cell(200, 8, f"Version 1.0  |  {date.today().strftime('%d/%m/%Y')}", align="R")

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(*BORDE)
        self.set_line_width(0.3)
        self.line(10, self.get_y(), 200, self.get_y())
        self.set_font("Helvetica", "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Pagina {self.page_no()}", align="C")

    def h1(self, text):
        self.ln(4)
        self.set_fill_color(20, 20, 20)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 13)
        self.cell(0, 9, _s(f"  {text}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
        self.ln(2)
        self.set_text_color(*OSCURO)

    def h2(self, num, text):
        self.ln(3)
        self.set_fill_color(*AZUL)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 11)
        self.cell(8, 7, str(num), align="C", fill=True)
        self.set_fill_color(235, 235, 235)
        self.set_text_color(*OSCURO)
        self.cell(ANCHO_UTIL - 8, 7, _s(f"  {text}"), fill=True,
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def h3(self, text):
        self.ln(3)
        x0 = self.get_x()
        y0 = self.get_y()
        self.set_fill_color(*AZUL)
        self.rect(x0, y0 + 1, 2, 4, "F")
        self.set_x(x0 + 4)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*AZUL)
        self.cell(0, 6, _s(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*OSCURO)
        self.ln(1)

    def body(self, text, indent=0):
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(*GRIS)
        self.set_x(10 + indent)
        self.multi_cell(ANCHO_UTIL - indent, 5.5, _s(text),
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def bullet(self, text, indent=4, color=None):
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(*(color or GRIS))
        x0 = 10 + indent
        self.set_x(x0)
        self.cell(4, 5.5, chr(149), new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.multi_cell(ANCHO_UTIL - indent - 4, 5.5, _s(text),
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def code_block(self, lines, bg=None):
        self.set_fill_color(*(bg or (30, 30, 30)))
        self.set_draw_color(*BORDE)
        self.set_line_width(0.2)
        pad = 3
        lh  = 5.2
        total_h = len(lines) * lh + pad * 2
        x0  = 10
        y0  = self.get_y() + 1
        self.rect(x0, y0, ANCHO_UTIL, total_h, "FD")
        self.set_xy(x0 + pad, y0 + pad)
        self.set_font("Courier", "", 8.5)
        self.set_text_color(156, 220, 254)
        for line in lines:
            self.set_x(x0 + pad)
            self.cell(0, lh, _s(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(3)
        self.set_text_color(*OSCURO)

    def callout(self, icon, title, text, bg, fg=(30, 30, 30)):
        self.ln(1)
        x0 = 10
        y0 = self.get_y()
        lines_n = max(1, len(text) // 65 + text.count("\n") + 2)
        h = 7 + lines_n * 5 + 3
        self.set_fill_color(*bg)
        self.set_draw_color(*BORDE)
        self.set_line_width(0.2)
        self.rect(x0, y0, ANCHO_UTIL, h, "FD")
        self.set_xy(x0 + 4, y0 + 3)
        self.set_font("Helvetica", "B", 9.5)
        self.set_text_color(*fg)
        self.cell(0, 5, _s(f"{icon}  {title}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_x(x0 + 4)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(50, 50, 50)
        self.multi_cell(ANCHO_UTIL - 8, 5, _s(text),
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)
        self.set_text_color(*OSCURO)

    def step_cmd(self, num, cmd, desc):
        self.ln(1)
        self.set_fill_color(*AZUL)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 9)
        self.cell(7, 6, str(num), align="C", fill=True)
        self.set_fill_color(30, 30, 30)
        self.set_text_color(156, 220, 254)
        self.set_font("Courier", "B", 8.5)
        self.cell(82, 6, _s(f"  {cmd}"), fill=True)
        self.set_fill_color(245, 245, 245)
        self.set_text_color(*GRIS)
        self.set_font("Helvetica", "", 9)
        self.cell(ANCHO_UTIL - 89, 6, _s(f"  {desc}"), fill=True,
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def table_row(self, cols, widths, header=False):
        bg = (20, 60, 100) if header else (245, 245, 245)
        fg = (255, 255, 255) if header else GRIS
        self.set_fill_color(*bg)
        self.set_text_color(*fg)
        self.set_font("Helvetica", "B" if header else "", 8.5)
        for text, w in zip(cols, widths):
            self.cell(w, 6, _s(f"  {text}"), border=1, fill=True)
        self.ln()
        self.set_text_color(*OSCURO)

    def divider(self):
        self.ln(2)
        self.set_draw_color(*BORDE)
        self.set_line_width(0.2)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)


# ══════════════════════════════════════════════════════════════════════════════

pdf = PDF()
pdf.set_margins(10, 18, 10)
pdf.set_auto_page_break(auto=True, margin=15)

# ── PORTADA ────────────────────────────────────────────────────────────────────
pdf.add_page()

pdf.set_fill_color(20, 20, 20)
pdf.rect(0, 0, 210, 85, "F")
pdf.set_fill_color(*AZUL)
pdf.rect(0, 0, 5, 297, "F")

# Icono cuadrado azul
pdf.set_fill_color(*AZUL)
pdf.rect(88, 22, 32, 32, "F")
pdf.set_font("Helvetica", "B", 22)
pdf.set_text_color(255, 255, 255)
pdf.set_xy(86, 26)
pdf.cell(36, 24, "IVA", align="C")

pdf.set_font("Helvetica", "B", 22)
pdf.set_text_color(255, 255, 255)
pdf.set_xy(10, 63)
pdf.cell(0, 10, "Conciliacion IVA Compras", align="C")
pdf.ln(10)

pdf.set_font("Helvetica", "", 13)
pdf.set_text_color(156, 220, 254)
pdf.set_x(10)
pdf.cell(0, 7, "Manual de Instalacion - Windows", align="C")
pdf.ln(8)

pdf.set_font("Helvetica", "", 10)
pdf.set_text_color(150, 150, 150)
pdf.set_x(10)
pdf.cell(0, 6, f"Colppy  x  ARCA  |  {date.today().strftime('%B %Y').capitalize()}", align="C")
pdf.ln(18)

pdf.set_text_color(*OSCURO)
pdf.body(
    "Esta guia explica como instalar la herramienta en Windows, crear la estructura "
    "de carpetas, generar el icono y acceso directo en el Escritorio, lanzar la "
    "aplicacion y monitorizarla desde la terminal."
)
pdf.ln(2)

# Indice
pdf.set_fill_color(235, 235, 235)
pdf.set_font("Helvetica", "B", 10)
pdf.set_text_color(*OSCURO)
pdf.cell(0, 7, "  Contenido", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.set_font("Helvetica", "", 9.5)
pdf.set_text_color(*GRIS)
secciones = [
    "1.  Requisitos previos",
    "2.  Descarga y estructura de carpetas",
    "3.  Primera ejecucion - instalacion automatica",
    "4.  Generar icono y acceso directo en el Escritorio",
    "5.  Uso diario",
    "6.  Monitoreo desde la terminal",
    "7.  Actualizacion y mantenimiento",
    "8.  Resolucion de problemas",
]
for s in secciones:
    pdf.set_x(14)
    pdf.cell(0, 5.5, s, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


# ══════════════════════════════════════════════════════════════════════════════
# Pag 2 - Requisitos
# ══════════════════════════════════════════════════════════════════════════════
pdf.add_page()

pdf.h1("1   Requisitos previos")
pdf.body("Antes de instalar la herramienta verificar que el equipo tenga:")
pdf.ln(1)

pdf.table_row(["Componente", "Version minima", "Donde obtenerlo"], [48, 35, 92], header=True)
pdf.table_row(["Python", "3.11 o superior", "https://www.python.org/downloads/"], [48, 35, 92])
pdf.table_row(["Windows", "10 / 11 (64-bit)", "Incluido en el equipo"], [48, 35, 92])
pdf.table_row(["Internet", "Solo la 1a vez", "Para descargar dependencias Python"], [48, 35, 92])
pdf.table_row(["LibreOffice (opcional)", "7.x o superior", "https://www.libreoffice.org/download/"], [48, 35, 92])
pdf.ln(2)

pdf.callout(
    "(i)", "Python - instalacion correcta en Windows",
    "Durante la instalacion de Python marcar la casilla\n"
    "\"Add Python to PATH\" (esta desmarcada por defecto).\n"
    "Sin esa opcion el script no podra encontrar Python.",
    AZUL_BG
)

pdf.callout(
    "(!)", "LibreOffice - solo si el XLS de Colppy no abre",
    "La app intenta tres metodos de lectura antes de necesitar LibreOffice.\n"
    "Si el archivo .xls de Colppy falla con los dos primeros metodos, "
    "LibreOffice se usa como fallback automatico. La mayoria de los archivos "
    "modernos de Colppy (.xlsx) no lo necesitan.",
    AMARILLO_BG
)

# ══════════════════════════════════════════════════════════════════════════════
# Pag 3 - Estructura de carpetas
# ══════════════════════════════════════════════════════════════════════════════
pdf.h1("2   Descarga y estructura de carpetas")

pdf.h3("Estructura esperada de la carpeta del proyecto")

pdf.code_block([
    "C:\\Trabajo\\ConciliacionIVA\\            <- carpeta raiz (nombre libre)",
    "    app_conciliacion_iva.py",
    "    inicio.bat                          <- lanzador principal",
    "    crear_acceso_directo.bat            <- genera acceso en Escritorio",
    "    _crear_icono.py                     <- genera icon.ico (sin dependencias)",
    "    requirements.txt",
    "    .streamlit\\",
    "        config.toml                     <- puerto 8502, tema visual",
    "    data\\                               <- se crea automaticamente",
    "        conciliacion_iva.db             <- base de datos SQLite",
])

pdf.h3("Opciones para obtener los archivos")
pdf.bullet("Descarga ZIP desde el repositorio -> descomprimir en la carpeta destino.")
pdf.bullet("Copia directa desde otra maquina (copiar toda la carpeta tal como esta).")
pdf.bullet(
    "Si ya hay archivos CSV en data\\persistencia\\ o data\\reglas_cuit.json, "
    "no borrarlos: se migran a SQLite automaticamente en la primera ejecucion."
)

pdf.callout(
    "(!)", "Ruta sin espacios ni caracteres especiales",
    "Evitar rutas del tipo C:\\Users\\Maria Jose\\Documents\\ porque algunos "
    "interpretes de Windows fallan con tildes o espacios.\n"
    "Usar preferentemente C:\\Trabajo\\ConciliacionIVA\\ o similar.",
    AMARILLO_BG
)


# ══════════════════════════════════════════════════════════════════════════════
# Pag 4 - Primera ejecucion
# ══════════════════════════════════════════════════════════════════════════════
pdf.add_page()

pdf.h1("3   Primera ejecucion - instalacion automatica")

pdf.body(
    "El archivo inicio.bat se encarga de todo: verifica Python, crea un entorno "
    "virtual aislado (.venv), instala las dependencias y lanza la aplicacion. "
    "Solo es necesario hacer doble clic."
)
pdf.ln(1)

pdf.h3("Pasos de la primera ejecucion")
pdf.step_cmd(1, "Doble clic en inicio.bat", "Abre una ventana CMD negra")
pdf.step_cmd(2, "[OK] Python X.XX encontrado", "Detecta la version instalada")
pdf.step_cmd(3, "Creando entorno virtual...", "Solo la primera vez (~5 segundos)")
pdf.step_cmd(4, "Instalando dependencias...", "Descarga ~60 MB una sola vez")
pdf.step_cmd(5, "Abriendo en :8502", "El navegador se abre automaticamente")
pdf.ln(2)

pdf.h3("Lo que se vera en la terminal la primera vez")
pdf.code_block([
    "================================================",
    "  Conciliacion IVA - Iniciando...",
    "================================================",
    "",
    "[OK] Python 3.12.3 encontrado.",
    "[Setup] Creando entorno virtual (solo la primera vez)...",
    "[OK] Entorno virtual creado.",
    "[Setup] Instalando dependencias (puede tardar unos minutos)...",
    "  Installing: streamlit, pandas, openpyxl, xlrd, xlsxwriter ...",
    "[OK] Dependencias instaladas.",
    "[INFO] LibreOffice no detectado (opcional).",
    "",
    "================================================",
    "  Abriendo en http://localhost:8502",
    "  Presiona Ctrl+C para cerrar la app.",
    "================================================",
    "",
    "  You can now view your Streamlit app in your browser.",
    "  Local URL: http://localhost:8502",
])

pdf.callout(
    "(OK)", "Desde la segunda vez en adelante",
    "El entorno virtual ya existe y las dependencias ya estan instaladas.\n"
    "El inicio es inmediato (menos de 3 segundos).",
    VERDE_BG, VERDE
)


# ══════════════════════════════════════════════════════════════════════════════
# Pag 5 - Acceso directo
# ══════════════════════════════════════════════════════════════════════════════
pdf.add_page()

pdf.h1("4   Generar icono y acceso directo en el Escritorio")

pdf.body(
    "Una vez que la primera ejecucion fue exitosa, se puede crear un acceso "
    "directo permanente en el Escritorio con icono personalizado."
)
pdf.ln(1)

pdf.h3("Paso a paso")
pdf.step_cmd(1, "Doble clic en crear_acceso_directo.bat", "Ejecutar como usuario normal")
pdf.step_cmd(2, "Genera icon.ico", "Sin dependencias externas, usa solo stdlib Python")
pdf.step_cmd(3, "Crea acceso directo en Escritorio", "Usa PowerShell + WScript.Shell")
pdf.step_cmd(4, 'Buscar \"Conciliacion IVA\" en Escritorio', "Listo para usar")
pdf.ln(2)

pdf.h3("Salida esperada")
pdf.code_block([
    "================================================",
    "  Creando acceso directo en el Escritorio...",
    "================================================",
    "",
    "Generando icono...",
    "Icono creado: C:\\Trabajo\\ConciliacionIVA\\icon.ico",
    "[OK] Acceso directo creado en el Escritorio.",
    "     Doble clic en \"Conciliacion IVA\" para abrir la app.",
])

pdf.h3("Si PowerShell muestra error de politica de ejecucion")
pdf.body(
    "En equipos corporativos PowerShell puede tener restricciones. "
    "Abrir PowerShell como Administrador y ejecutar:"
)
pdf.code_block([
    "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned",
])
pdf.body("Luego volver a ejecutar crear_acceso_directo.bat.")

pdf.h3("Crear el acceso directo manualmente (alternativa sin PowerShell)")
pdf.bullet("Clic derecho sobre inicio.bat -> Enviar a -> Escritorio (crear acceso directo).")
pdf.bullet("Clic derecho en el acceso directo -> Propiedades -> Cambiar icono.")
pdf.bullet("Elegir icon.ico de la carpeta del proyecto.")
pdf.bullet("Renombrarlo a  Conciliacion IVA.")

pdf.callout(
    "(i)", "Si se mueve la carpeta del proyecto",
    "Si se cambia la carpeta de lugar, volver a ejecutar crear_acceso_directo.bat "
    "para regenerar el acceso directo con la nueva ruta.\n"
    "La base de datos y el historial quedan en data\\ y se mueven con la carpeta.",
    AZUL_BG
)


# ══════════════════════════════════════════════════════════════════════════════
# Pag 6 - Uso diario
# ══════════════════════════════════════════════════════════════════════════════
pdf.add_page()

pdf.h1("5   Uso diario")

pdf.h3("Iniciar la aplicacion")
pdf.bullet('Doble clic en "Conciliacion IVA" en el Escritorio.')
pdf.bullet("O doble clic directamente en inicio.bat dentro de la carpeta.")
pdf.bullet("El navegador predeterminado abre http://localhost:8502 automaticamente.")
pdf.ln(1)

pdf.h3("Flujo de trabajo mensual")
pdf.step_cmd(1, "Exportar de Colppy", "Reportes -> Listado IVA Compras (.xls/.xlsx)")
pdf.step_cmd(2, "Exportar de ARCA", "Mis Comprobantes Recibidos (.xlsx)")
pdf.step_cmd(3, "Subir archivos", "Sidebar -> Subir Listado IVA / Subir ARCA")
pdf.step_cmd(4, "Verificar mapeo", "Panel 'Emparejamiento de columnas'")
pdf.step_cmd(5, "Procesar", "Clic en > Procesar")
pdf.step_cmd(6, "Revisar resultados", "Tabs: Conciliacion / Solo Listado / Solo ARCA")
pdf.step_cmd(7, "Descargar Excel", "Boton 'Excel completo (3 hojas)'")
pdf.ln(2)

pdf.callout(
    "(i)", "Archivos en la carpeta raiz - deteccion automatica",
    "Si se dejan los archivos XLS/XLSX en la carpeta del proyecto con nombres "
    "que contengan 'listado'+'iva' o 'comprobantes'+'recibidos', la app los "
    "detecta automaticamente sin necesidad de subirlos cada vez.",
    AZUL_BG
)

pdf.h3("Cerrar la aplicacion")
pdf.bullet("En la terminal negra: presionar  Ctrl + C.")
pdf.bullet("O cerrar la ventana de la terminal directamente.")
pdf.body(
    "El navegador puede cerrarse de forma independiente. La aplicacion se ejecuta "
    "en la terminal, no en el navegador. Cerrar la pestana no detiene el servidor."
)

pdf.h3("Estados de conciliacion")
pdf.table_row(["Icono", "Estado", "Significado"], [15, 48, 112], header=True)
pdf.table_row(["[OK]",  "Conciliado",               "Neto, IVA y Total coinciden dentro de tolerancia"], [15, 48, 112])
pdf.table_row(["[~]",  "Total OK / Sin desglose",   "Total OK, Neto difiere (Factura C derivada)"],       [15, 48, 112])
pdf.table_row(["[!!]", "Diferencia detectada",       "Al menos un importe discrepa sobre la tolerancia"], [15, 48, 112])
pdf.table_row(["[ ]",  "Sin match en ARCA",          "El comprobante no existe en el registro ARCA"],      [15, 48, 112])
pdf.table_row(["[EX]", "Exterior / No en ARCA",      "Factura de proveedor del exterior (FCC/FCE)"],       [15, 48, 112])


# ══════════════════════════════════════════════════════════════════════════════
# Pag 7 - Monitoreo desde la terminal
# ══════════════════════════════════════════════════════════════════════════════
pdf.add_page()

pdf.h1("6   Monitoreo desde la terminal")

pdf.body(
    "La ventana de la terminal que abre inicio.bat muestra en tiempo real los logs "
    "de Streamlit. Esta seccion explica como leerlos e interpretarlos."
)
pdf.ln(1)

pdf.h3("Salida normal durante el uso")
pdf.code_block([
    "  You can now view your Streamlit app in your browser.",
    "  Local URL:      http://localhost:8502",
    "  Network URL:    http://192.168.1.100:8502",
    "",
    "2025-11-01 10:23:45.123  INFO  Started server; browser tab opened.",
    "2025-11-01 10:24:02.456  INFO  Script started running.",
    "2025-11-01 10:24:03.781  INFO  Script finished running.",
])

pdf.h3("Interpretacion de los mensajes")
pdf.table_row(["Mensaje en terminal", "Significado"], [95, 80], header=True)
pdf.table_row(["Script started running.", "Usuario hizo una accion en la UI"], [95, 80])
pdf.table_row(["Script finished running.", "Ciclo de render completado con exito"], [95, 80])
pdf.table_row(["[ERROR procesamiento] ...", "Error en la carga o conciliacion de archivos"], [95, 80])
pdf.table_row(["[ERROR guardar_regla] ...", "Fallo al escribir en la base de datos"], [95, 80])
pdf.table_row(["Address already in use", "Puerto 8502 ocupado por otra instancia"], [95, 80])
pdf.ln(2)

pdf.h3("Abrir sesion de monitoreo dedicada")
pdf.body(
    "Para una terminal separada de monitoreo sin cerrar la app:"
)
pdf.code_block([
    "cd C:\\Trabajo\\ConciliacionIVA",
    ".venv\\Scripts\\activate",
    "streamlit run app_conciliacion_iva.py --server.fileWatcherType none",
])

pdf.h3("Guardar el log en un archivo")
pdf.code_block([
    "cd C:\\Trabajo\\ConciliacionIVA",
    ".venv\\Scripts\\activate",
    "streamlit run app_conciliacion_iva.py --server.fileWatcherType none > log.txt 2>&1",
    "",
    "# Ver el log en tiempo real en otra terminal:",
    "powershell Get-Content log.txt -Wait",
])

pdf.h3("Verificar que la app esta corriendo")
pdf.code_block([
    "netstat -aon | findstr \":8502\"",
    "",
    "# Resultado esperado:",
    "#  TCP  0.0.0.0:8502  0.0.0.0:0  LISTENING  12345",
    "#                                             ^--- PID del proceso",
])

pdf.h3("Detener la app si la terminal se cerro accidentalmente")
pdf.code_block([
    "# Encontrar y terminar el proceso en el puerto 8502",
    "for /f \"tokens=5\" %a in ('netstat -aon ^| findstr \":8502\"') do taskkill /F /PID %a",
    "",
    "# Alternativa grafica: Administrador de tareas -> buscar 'python'",
])


# ══════════════════════════════════════════════════════════════════════════════
# Pag 8 - Actualizacion y mantenimiento
# ══════════════════════════════════════════════════════════════════════════════
pdf.add_page()

pdf.h1("7   Actualizacion y mantenimiento")

pdf.h3("Actualizar la aplicacion")
pdf.body("Para actualizar a una nueva version sin perder el historial ni las reglas:")
pdf.bullet("Copiar el nuevo app_conciliacion_iva.py en la carpeta del proyecto.")
pdf.bullet("No tocar la carpeta data\\ : contiene la base de datos y el historial.")
pdf.bullet("Ejecutar inicio.bat normalmente; si hay dependencias nuevas las instala solo.")
pdf.ln(1)

pdf.callout(
    "(!)", "Nunca borrar la carpeta data\\",
    "Contiene conciliacion_iva.db con todo el historial, las reglas de memoria "
    "por CUIT y las correcciones manuales. Es el unico archivo a respaldar.",
    AMARILLO_BG
)

pdf.h3("Backup de la base de datos")
pdf.body("Un solo archivo es suficiente para respaldar todo:")
pdf.code_block([
    "# Copiar la base de datos con fecha en el nombre",
    "copy \"C:\\Trabajo\\ConciliacionIVA\\data\\conciliacion_iva.db\"",
    "     \"C:\\Respaldos\\conciliacion_iva_20251101.db\"",
])

pdf.h3("Actualizar dependencias Python")
pdf.code_block([
    "cd C:\\Trabajo\\ConciliacionIVA",
    ".venv\\Scripts\\activate",
    "pip install --upgrade -r requirements.txt",
])

pdf.h3("Reinstalar el entorno virtual desde cero")
pdf.body("Si el entorno .venv se corrompe o necesita reinstalarse:")
pdf.code_block([
    "cd C:\\Trabajo\\ConciliacionIVA",
    "rmdir /s /q .venv",
    "# Ejecutar inicio.bat - recrea el entorno automaticamente",
])

pdf.h3("Consultar el historial de conciliaciones")
pdf.body(
    "Desde la propia aplicacion, el sidebar muestra todos los snapshots guardados "
    "con fecha, periodo y estadisticas. Para acceso directo a la base de datos:"
)
pdf.code_block([
    "# Con Python (desde la terminal del entorno virtual):",
    ".venv\\Scripts\\activate",
    "python",
    ">>> import sqlite3",
    ">>> con = sqlite3.connect('data/conciliacion_iva.db')",
    ">>> print([r for r in con.execute('SELECT ts,periodo,conciliados FROM conciliaciones')])",
])


# ══════════════════════════════════════════════════════════════════════════════
# Pag 9 - Resolucion de problemas
# ══════════════════════════════════════════════════════════════════════════════
pdf.add_page()

pdf.h1("8   Resolucion de problemas")

pdf.h3("Python no encontrado")
pdf.code_block([
    "[ERROR] Python no encontrado.",
    "Instala Python 3.11+ desde https://www.python.org/downloads/",
    "Durante la instalacion, marca \"Add Python to PATH\".",
])
pdf.body(
    "Solucion: desinstalar Python y reinstalarlo marcando \"Add Python to PATH\".\n"
    "Verificar con: Win + R -> cmd -> python --version"
)
pdf.ln(1)

pdf.h3("El navegador no abre automaticamente")
pdf.body("Abrir manualmente Chrome / Edge / Firefox y navegar a:")
pdf.code_block(["http://localhost:8502"])
pdf.ln(1)

pdf.h3("Puerto 8502 ya en uso")
pdf.code_block([
    "# Error: Address already in use",
    "# Liberar el puerto:",
    "for /f \"tokens=5\" %a in ('netstat -aon ^| findstr \":8502\"') do taskkill /F /PID %a",
    "# Luego ejecutar inicio.bat nuevamente",
])
pdf.ln(1)

pdf.h3("El XLS de Colppy no se puede leer")
pdf.code_block([
    "ValueError: No se pudo leer el archivo XLS.",
    "Intenta exportarlo como XLSX desde Colppy, o instala LibreOffice.",
])
pdf.body("Soluciones en orden de preferencia:")
pdf.bullet("En Colppy, exportar como XLSX en lugar de XLS.")
pdf.bullet("Instalar LibreOffice 7.x desde https://www.libreoffice.org/download/")
pdf.bullet("Abrir el XLS con Excel, guardarlo como XLSX y subir ese archivo.")
pdf.ln(1)

pdf.h3("Error al instalar dependencias (sin internet o red corporativa)")
pdf.body("Instalar dependencias offline desde otra maquina con internet:")
pdf.code_block([
    "# En maquina CON internet:",
    "pip download -r requirements.txt -d C:\\paquetes_offline",
    "",
    "# Copiar la carpeta paquetes_offline al equipo sin internet, luego:",
    ".venv\\Scripts\\activate",
    "pip install --no-index --find-links=C:\\paquetes_offline -r requirements.txt",
])
pdf.ln(1)

pdf.h3("La app no responde o queda en 'Running...'")
pdf.bullet("Verificar que la terminal negra siga abierta (es el servidor).")
pdf.bullet("Si la terminal muestra un error, cerrarla y volver a abrir inicio.bat.")
pdf.bullet("Probar recargar la pagina del navegador (F5).")
pdf.ln(1)

pdf.h3("Permisos insuficientes para crear el acceso directo")
pdf.body(
    "Si crear_acceso_directo.bat falla con error de permisos, "
    "ejecutarlo como Administrador: clic derecho -> 'Ejecutar como administrador'."
)

pdf.ln(4)
pdf.divider()

pdf.set_font("Helvetica", "I", 8.5)
pdf.set_text_color(120, 120, 120)
pdf.multi_cell(0, 5,
    "Archivo a respaldar: data\\conciliacion_iva.db\n"
    "Contiene todo el historial de conciliaciones, reglas por CUIT y correcciones manuales.\n"
    "El resto del codigo y configuracion es reemplazable.",
    align="C"
)

# ── Guardar ────────────────────────────────────────────────────────────────────
out = "Manual_Instalacion_Windows_ConciliacionIVA.pdf"
pdf.output(out)
print(f"PDF generado: {out}")
