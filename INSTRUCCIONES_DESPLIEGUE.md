# 📦 Instrucciones de Despliegue - Sistema de Conciliación Bancaria

## 📥 Archivo Comprimido Generado

**Archivo:** `Conciliacion_Bancaria_v1.0.zip`  
**Tamaño:** 90 KB (comprimido) | 362 KB (descomprimido)  
**Ubicación:** `/home/zen/carolina_1/Conciliacion_Bancaria_v1.0.zip`

---

## 📦 Contenido del Paquete (16 archivos)

### 🚀 Scripts de Inicio:
- ✅ `iniciar_app.bat` - **Script de inicio para Windows**
- ✅ `iniciar_app.sh` - Script de inicio para Linux/Mac

### 🎨 Aplicación:
- ✅ `app_conciliacion.py` - Interfaz web (Streamlit)
- ✅ `conciliacion_contable.py` - Motor de conciliación principal
- ✅ `conciliacion_auto.py` - Versión CLI (opcional)

### 📚 Documentación:
- ✅ `LEEME_PRIMERO.txt` - **Instrucciones rápidas de instalación**
- ✅ `README_WINDOWS.md` - Guía completa Windows
- ✅ `README_APP.md` - Guía de uso de la aplicación
- ✅ `CAMBIOS_UX.md` - Características de UX
- ✅ `CAMBIOS_UI_V2.md` - Mejoras de interfaz
- ✅ `CAMBIOS_UI.md` - Historial de cambios

### ⚙️ Configuración:
- ✅ `requirements.txt` - Dependencias Python

### 📊 Archivos de Ejemplo:
- ✅ `bind_lucas.csv` - Extracto bancario de ejemplo
- ✅ `Mayor_Lucas.csv` - Libro mayor de ejemplo
- ✅ `discrepanciasreales.csv` - Discrepancias de ejemplo (para testing)

---

## 🚀 Despliegue en Windows - Paso a Paso

### **Método 1: Instalación Automática (Recomendado)**

1. **Copiar el archivo ZIP**
   ```
   Transferir: Conciliacion_Bancaria_v1.0.zip
   A la PC Windows: C:\Conciliacion\
   ```

2. **Descomprimir**
   - Clic derecho en el ZIP
   - "Extraer todo..."
   - Elegir ubicación (ej: `C:\Conciliacion\`)

3. **Verificar Python**
   - Abrir CMD o PowerShell
   - Ejecutar: `python --version`
   - Si no está instalado: https://www.python.org/downloads/
   - **IMPORTANTE:** Marcar "Add Python to PATH"

4. **Iniciar Aplicación**
   ```
   Doble clic en: iniciar_app.bat
   ```
   
5. **El script automáticamente:**
   - ✓ Crea el entorno virtual en `venv/`
   - ✓ Instala dependencias desde `requirements.txt`
   - ✓ Inicia Streamlit
   - ✓ Abre el navegador en http://localhost:8501

### **Método 2: Instalación Manual**

```cmd
REM 1. Abrir CMD en la carpeta descomprimida
cd C:\Conciliacion\Algoritmo_conciliatorio_mensual

REM 2. Crear entorno virtual
python -m venv venv

REM 3. Activar entorno
venv\Scripts\activate

REM 4. Instalar dependencias
pip install -r requirements.txt

REM 5. Iniciar aplicación
streamlit run app_conciliacion.py
```

---

## 🐧 Despliegue en Linux/Mac

### **Método Rápido:**

```bash
# 1. Descomprimir
unzip Conciliacion_Bancaria_v1.0.zip
cd Algoritmo_conciliatorio_mensual

# 2. Dar permisos de ejecución
chmod +x iniciar_app.sh

# 3. Ejecutar
./iniciar_app.sh
```

### **Método Manual:**

```bash
# 1. Crear entorno virtual
python3 -m venv venv

# 2. Activar entorno
source venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Iniciar aplicación
streamlit run app_conciliacion.py
```

---

## 📁 Estructura del Proyecto Desplegado

```
C:\Conciliacion\Algoritmo_conciliatorio_mensual\
│
├── 📄 LEEME_PRIMERO.txt          ← LEER PRIMERO
├── 📄 iniciar_app.bat             ← DOBLE CLIC AQUÍ (Windows)
├── 📄 iniciar_app.sh              ← Ejecutar en Linux/Mac
│
├── 🎨 Aplicación:
│   ├── app_conciliacion.py        ← UI web (Streamlit)
│   ├── conciliacion_contable.py   ← Motor principal
│   └── conciliacion_auto.py       ← Versión CLI
│
├── 📚 Documentación:
│   ├── README_WINDOWS.md
│   ├── README_APP.md
│   ├── CAMBIOS_UX.md
│   └── ...
│
├── ⚙️ Configuración:
│   └── requirements.txt
│
├── 📊 Ejemplos:
│   ├── bind_lucas.csv
│   ├── Mayor_Lucas.csv
│   └── discrepanciasreales.csv
│
└── 📁 venv/                       ← Se crea automáticamente
    └── (Entorno virtual Python)
```

---

## ✅ Verificación Post-Instalación

### **1. Verificar instalación de Python:**
```cmd
python --version
```
Debe mostrar: `Python 3.8.x` o superior

### **2. Verificar pip:**
```cmd
pip --version
```

### **3. Probar la aplicación:**
- Abrir navegador
- Ir a: http://localhost:8501
- Debería ver el título: "💰 Sistema de Conciliación Bancaria"

### **4. Verificar archivos:**
Asegurarse de que existen:
- ✓ `iniciar_app.bat`
- ✓ `app_conciliacion.py`
- ✓ `requirements.txt`
- ✓ `LEEME_PRIMERO.txt`

---

## 🔧 Solución de Problemas Comunes

### ❌ Error: "Python no reconocido"
**Solución:**
1. Reinstalar Python desde https://www.python.org
2. **Marcar**: "Add Python to PATH"
3. Reiniciar CMD/PowerShell

### ❌ Error: "pip no encontrado"
**Solución:**
```cmd
python -m ensurepip --upgrade
```

### ❌ Error: "No se puede activar venv"
**Solución (PowerShell como Admin):**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### ❌ Puerto 8501 ocupado
**Solución:**
```cmd
streamlit run app_conciliacion.py --server.port 8502
```

### ❌ Navegador no se abre
**Solución:**
- Abrir manualmente: http://localhost:8501

### ❌ Error de encoding en CSV
**Solución:**
- Asegurarse de que los CSV tengan separador `;`
- Probar con encoding `latin-1` o `utf-8`

---

## 🎯 Uso Rápido de la Aplicación

### **1. Cargar Archivos**
```
Pestaña: 📤 Cargar
  → Subir extracto bancario (CSV)
  → Subir libro mayor (CSV)
  → Click "🚀 Ejecutar Conciliación"
```

### **2. Ver Resultados**
```
Pestaña: 📊 Resultados
  → Ver NO conciliados (errores a revisar)
  → Ver estadísticas
  → Filtrar por categoría
```

### **3. Descargar Reportes**
```
Pestaña: 📥 Descargar
  → Descargar ZIP con todos los reportes
  → O descargar archivos individuales
```

---

## 📊 Reportes Generados

La aplicación genera estos archivos en `Reportes_Conciliacion_[timestamp]/`:

| Archivo | Contenido |
|---------|-----------|
| `matches_1a1.csv` | Conciliaciones individuales |
| `matches_resumen_categoria.csv` | Asientos consolidados |
| `no_conciliados_banco.csv` | Movimientos sin match (banco) |
| `no_conciliados_mayor.csv` | Movimientos sin match (mayor) |
| `discrepancias_detectadas.csv` | Transferencias sin match |
| `resumen.txt` | Resumen ejecutivo |

---

## 🔐 Requisitos del Sistema

### **Mínimos:**
- Windows 10/11, Linux, o macOS
- Python 3.8+
- 4 GB RAM
- 100 MB espacio en disco

### **Recomendados:**
- Python 3.10+
- 8 GB RAM
- Navegador moderno (Chrome, Firefox, Edge)
- Conexión a internet (solo para instalación)

---

## 📦 Dependencias Incluidas

El archivo `requirements.txt` incluye:
```
pandas>=2.0.0         # Análisis de datos
rapidfuzz>=3.0.0      # Similitud de texto
scipy>=1.10.0         # Optimización (Hungarian)
tqdm>=4.65.0          # Barras de progreso
streamlit>=1.20.0     # Interfaz web
```

Todas se instalan automáticamente con `iniciar_app.bat`

---

## 🔄 Actualización de la Aplicación

Para actualizar a una nueva versión:

1. Respaldar datos importantes
2. Eliminar carpeta actual
3. Descomprimir nuevo ZIP
4. Ejecutar `iniciar_app.bat`

**Nota:** El entorno virtual (`venv/`) se recrea automáticamente

---

## 📞 Soporte y Documentación

### **Documentos incluidos:**
- 📄 `LEEME_PRIMERO.txt` - Inicio rápido
- 📄 `README_WINDOWS.md` - Guía Windows completa
- 📄 `README_APP.md` - Manual de usuario
- 📄 `CAMBIOS_UX.md` - Características UX

### **Logs:**
Si hay errores, revisar:
```
venv\Scripts\streamlit.log  (Windows)
venv/bin/streamlit.log      (Linux/Mac)
```

---

## ✅ Checklist de Despliegue

- [ ] Python 3.8+ instalado
- [ ] Python agregado al PATH
- [ ] ZIP descomprimido
- [ ] Ubicación confirmada (ej: C:\Conciliacion)
- [ ] Ejecutado `iniciar_app.bat`
- [ ] Entorno virtual creado (`venv/`)
- [ ] Dependencias instaladas
- [ ] Aplicación corriendo en http://localhost:8501
- [ ] Archivos CSV de prueba cargados exitosamente
- [ ] Reportes generados correctamente

---

## 🎉 ¡Listo para Producción!

La aplicación está 100% funcional y lista para usar en cualquier Windows.

**Inicio rápido:** Doble clic en `iniciar_app.bat`

**URL:** http://localhost:8501

---

**Versión:** 1.0  
**Fecha:** Diciembre 2025  
**Tamaño:** 90 KB (ZIP) | 362 KB (descomprimido)  
**Archivos:** 16  
**Compatible con:** Windows 10/11, Linux, macOS


## 📥 Archivo Comprimido Generado

**Archivo:** `Conciliacion_Bancaria_v1.0.zip`  
**Tamaño:** 90 KB (comprimido) | 362 KB (descomprimido)  
**Ubicación:** `/home/zen/carolina_1/Conciliacion_Bancaria_v1.0.zip`

---

## 📦 Contenido del Paquete (16 archivos)

### 🚀 Scripts de Inicio:
- ✅ `iniciar_app.bat` - **Script de inicio para Windows**
- ✅ `iniciar_app.sh` - Script de inicio para Linux/Mac

### 🎨 Aplicación:
- ✅ `app_conciliacion.py` - Interfaz web (Streamlit)
- ✅ `conciliacion_contable.py` - Motor de conciliación principal
- ✅ `conciliacion_auto.py` - Versión CLI (opcional)

### 📚 Documentación:
- ✅ `LEEME_PRIMERO.txt` - **Instrucciones rápidas de instalación**
- ✅ `README_WINDOWS.md` - Guía completa Windows
- ✅ `README_APP.md` - Guía de uso de la aplicación
- ✅ `CAMBIOS_UX.md` - Características de UX
- ✅ `CAMBIOS_UI_V2.md` - Mejoras de interfaz
- ✅ `CAMBIOS_UI.md` - Historial de cambios

### ⚙️ Configuración:
- ✅ `requirements.txt` - Dependencias Python

### 📊 Archivos de Ejemplo:
- ✅ `bind_lucas.csv` - Extracto bancario de ejemplo
- ✅ `Mayor_Lucas.csv` - Libro mayor de ejemplo
- ✅ `discrepanciasreales.csv` - Discrepancias de ejemplo (para testing)

---

## 🚀 Despliegue en Windows - Paso a Paso

### **Método 1: Instalación Automática (Recomendado)**

1. **Copiar el archivo ZIP**
   ```
   Transferir: Conciliacion_Bancaria_v1.0.zip
   A la PC Windows: C:\Conciliacion\
   ```

2. **Descomprimir**
   - Clic derecho en el ZIP
   - "Extraer todo..."
   - Elegir ubicación (ej: `C:\Conciliacion\`)

3. **Verificar Python**
   - Abrir CMD o PowerShell
   - Ejecutar: `python --version`
   - Si no está instalado: https://www.python.org/downloads/
   - **IMPORTANTE:** Marcar "Add Python to PATH"

4. **Iniciar Aplicación**
   ```
   Doble clic en: iniciar_app.bat
   ```
   
5. **El script automáticamente:**
   - ✓ Crea el entorno virtual en `venv/`
   - ✓ Instala dependencias desde `requirements.txt`
   - ✓ Inicia Streamlit
   - ✓ Abre el navegador en http://localhost:8501

### **Método 2: Instalación Manual**

```cmd
REM 1. Abrir CMD en la carpeta descomprimida
cd C:\Conciliacion\Algoritmo_conciliatorio_mensual

REM 2. Crear entorno virtual
python -m venv venv

REM 3. Activar entorno
venv\Scripts\activate

REM 4. Instalar dependencias
pip install -r requirements.txt

REM 5. Iniciar aplicación
streamlit run app_conciliacion.py
```

---

## 🐧 Despliegue en Linux/Mac

### **Método Rápido:**

```bash
# 1. Descomprimir
unzip Conciliacion_Bancaria_v1.0.zip
cd Algoritmo_conciliatorio_mensual

# 2. Dar permisos de ejecución
chmod +x iniciar_app.sh

# 3. Ejecutar
./iniciar_app.sh
```

### **Método Manual:**

```bash
# 1. Crear entorno virtual
python3 -m venv venv

# 2. Activar entorno
source venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Iniciar aplicación
streamlit run app_conciliacion.py
```

---

## 📁 Estructura del Proyecto Desplegado

```
C:\Conciliacion\Algoritmo_conciliatorio_mensual\
│
├── 📄 LEEME_PRIMERO.txt          ← LEER PRIMERO
├── 📄 iniciar_app.bat             ← DOBLE CLIC AQUÍ (Windows)
├── 📄 iniciar_app.sh              ← Ejecutar en Linux/Mac
│
├── 🎨 Aplicación:
│   ├── app_conciliacion.py        ← UI web (Streamlit)
│   ├── conciliacion_contable.py   ← Motor principal
│   └── conciliacion_auto.py       ← Versión CLI
│
├── 📚 Documentación:
│   ├── README_WINDOWS.md
│   ├── README_APP.md
│   ├── CAMBIOS_UX.md
│   └── ...
│
├── ⚙️ Configuración:
│   └── requirements.txt
│
├── 📊 Ejemplos:
│   ├── bind_lucas.csv
│   ├── Mayor_Lucas.csv
│   └── discrepanciasreales.csv
│
└── 📁 venv/                       ← Se crea automáticamente
    └── (Entorno virtual Python)
```

---

## ✅ Verificación Post-Instalación

### **1. Verificar instalación de Python:**
```cmd
python --version
```
Debe mostrar: `Python 3.8.x` o superior

### **2. Verificar pip:**
```cmd
pip --version
```

### **3. Probar la aplicación:**
- Abrir navegador
- Ir a: http://localhost:8501
- Debería ver el título: "💰 Sistema de Conciliación Bancaria"

### **4. Verificar archivos:**
Asegurarse de que existen:
- ✓ `iniciar_app.bat`
- ✓ `app_conciliacion.py`
- ✓ `requirements.txt`
- ✓ `LEEME_PRIMERO.txt`

---

## 🔧 Solución de Problemas Comunes

### ❌ Error: "Python no reconocido"
**Solución:**
1. Reinstalar Python desde https://www.python.org
2. **Marcar**: "Add Python to PATH"
3. Reiniciar CMD/PowerShell

### ❌ Error: "pip no encontrado"
**Solución:**
```cmd
python -m ensurepip --upgrade
```

### ❌ Error: "No se puede activar venv"
**Solución (PowerShell como Admin):**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### ❌ Puerto 8501 ocupado
**Solución:**
```cmd
streamlit run app_conciliacion.py --server.port 8502
```

### ❌ Navegador no se abre
**Solución:**
- Abrir manualmente: http://localhost:8501

### ❌ Error de encoding en CSV
**Solución:**
- Asegurarse de que los CSV tengan separador `;`
- Probar con encoding `latin-1` o `utf-8`

---

## 🎯 Uso Rápido de la Aplicación

### **1. Cargar Archivos**
```
Pestaña: 📤 Cargar
  → Subir extracto bancario (CSV)
  → Subir libro mayor (CSV)
  → Click "🚀 Ejecutar Conciliación"
```

### **2. Ver Resultados**
```
Pestaña: 📊 Resultados
  → Ver NO conciliados (errores a revisar)
  → Ver estadísticas
  → Filtrar por categoría
```

### **3. Descargar Reportes**
```
Pestaña: 📥 Descargar
  → Descargar ZIP con todos los reportes
  → O descargar archivos individuales
```

---

## 📊 Reportes Generados

La aplicación genera estos archivos en `Reportes_Conciliacion_[timestamp]/`:

| Archivo | Contenido |
|---------|-----------|
| `matches_1a1.csv` | Conciliaciones individuales |
| `matches_resumen_categoria.csv` | Asientos consolidados |
| `no_conciliados_banco.csv` | Movimientos sin match (banco) |
| `no_conciliados_mayor.csv` | Movimientos sin match (mayor) |
| `discrepancias_detectadas.csv` | Transferencias sin match |
| `resumen.txt` | Resumen ejecutivo |

---

## 🔐 Requisitos del Sistema

### **Mínimos:**
- Windows 10/11, Linux, o macOS
- Python 3.8+
- 4 GB RAM
- 100 MB espacio en disco

### **Recomendados:**
- Python 3.10+
- 8 GB RAM
- Navegador moderno (Chrome, Firefox, Edge)
- Conexión a internet (solo para instalación)

---

## 📦 Dependencias Incluidas

El archivo `requirements.txt` incluye:
```
pandas>=2.0.0         # Análisis de datos
rapidfuzz>=3.0.0      # Similitud de texto
scipy>=1.10.0         # Optimización (Hungarian)
tqdm>=4.65.0          # Barras de progreso
streamlit>=1.20.0     # Interfaz web
```

Todas se instalan automáticamente con `iniciar_app.bat`

---

## 🔄 Actualización de la Aplicación

Para actualizar a una nueva versión:

1. Respaldar datos importantes
2. Eliminar carpeta actual
3. Descomprimir nuevo ZIP
4. Ejecutar `iniciar_app.bat`

**Nota:** El entorno virtual (`venv/`) se recrea automáticamente

---

## 📞 Soporte y Documentación

### **Documentos incluidos:**
- 📄 `LEEME_PRIMERO.txt` - Inicio rápido
- 📄 `README_WINDOWS.md` - Guía Windows completa
- 📄 `README_APP.md` - Manual de usuario
- 📄 `CAMBIOS_UX.md` - Características UX

### **Logs:**
Si hay errores, revisar:
```
venv\Scripts\streamlit.log  (Windows)
venv/bin/streamlit.log      (Linux/Mac)
```

---

## ✅ Checklist de Despliegue

- [ ] Python 3.8+ instalado
- [ ] Python agregado al PATH
- [ ] ZIP descomprimido
- [ ] Ubicación confirmada (ej: C:\Conciliacion)
- [ ] Ejecutado `iniciar_app.bat`
- [ ] Entorno virtual creado (`venv/`)
- [ ] Dependencias instaladas
- [ ] Aplicación corriendo en http://localhost:8501
- [ ] Archivos CSV de prueba cargados exitosamente
- [ ] Reportes generados correctamente

---

## 🎉 ¡Listo para Producción!

La aplicación está 100% funcional y lista para usar en cualquier Windows.

**Inicio rápido:** Doble clic en `iniciar_app.bat`

**URL:** http://localhost:8501

---

**Versión:** 1.0  
**Fecha:** Diciembre 2025  
**Tamaño:** 90 KB (ZIP) | 362 KB (descomprimido)  
**Archivos:** 16  
**Compatible con:** Windows 10/11, Linux, macOS













