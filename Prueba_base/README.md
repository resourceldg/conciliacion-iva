# Conciliación IVA Compras

Herramienta web para la conciliación mensual de IVA compras en Argentina.  
Cruza el **Listado IVA Compras de Colppy** (libro contable interno) contra **Mis Comprobantes Recibidos de ARCA** (registro fiscal oficial), detecta coincidencias y diferencias, y produce un informe Excel de tres hojas.

> **Base de datos**: la app usa **SQLite** (`data/conciliacion_iva.db`), un único archivo local sin servidor. Si ya tenés archivos CSV de versiones anteriores, se migran automáticamente la primera vez que se ejecuta la nueva versión.

---

## Requisitos

- Python 3.11+ ([python.org/downloads](https://www.python.org/downloads/))
- LibreOffice — **último recurso opcional**: la app primero intenta reparar el XLS con `python-calamine` (Rust, sin deps externas); solo recurre a LibreOffice si calamine también falla

Las dependencias Python se instalan solas la primera vez que se lanza la app.

---

## Cómo ejecutar

### Windows

Doble clic en **`inicio.bat`**.

La primera vez descarga e instala las dependencias (puede tardar un par de minutos). Las siguientes veces abre directamente. El navegador se abre solo en `http://localhost:8502`.

> **Si el XLS de Colppy no abre:** instalá [LibreOffice](https://www.libreoffice.org/download/) y volvé a intentar. El script avisa si no lo detecta.

### Linux / Mac

```bash
./run.sh
```

Requiere Python 3.11+ y las dependencias instaladas:
```bash
pip install -r requirements.txt
```

---

## Archivos de entrada

| Archivo | Origen | Formato |
|---|---|---|
| Listado IVA Compras | Colppy → Reportes → Listado IVA Compras | `.xls` o `.xlsx` |
| Mis Comprobantes Recibidos | ARCA → Mis Comprobantes Recibidos | `.xlsx` |

Los archivos se pueden subir desde la UI o dejar en la carpeta raíz del proyecto. Si están presentes con nombres que contengan `"listado"+"iva"` o `"comprobantes"+"recibidos"`, la app los detecta automáticamente.

Se aceptan formatos `.xls`, `.xlsx` y `.csv`.

---

## Algoritmo paso a paso

### Paso 1 — Lectura robusta del Listado IVA Compras

El XLS que exporta Colppy frecuentemente tiene un bug en el compound document que xlrd rechaza (`CompDocError: Workbook corruption`). El sistema lo maneja así:

1. Intenta leer con pandas/xlrd directamente.
2. Si falla, guarda el contenido en un archivo temporal y llama a **LibreOffice headless** para convertirlo a XLSX.
3. Lee el XLSX convertido con openpyxl y limpia los temporales.

Luego ubica la fila de encabezado buscando la columna `"Comprobante"` (Colppy puede insertar filas de metadatos antes del header real).

### Paso 2 — Normalización del Listado

- **Filtro de filas válidas**: mantiene solo las que tienen un Comprobante en formato `XXXXX-YYYYYYYY` (cinco dígitos, guión, ocho dígitos). Descarta las filas de totales y subtotales que Colppy agrega al pie.
- **Agrupación por comprobante**: Colppy puede tener una fila por alícuota de IVA (10.5%, 21%, etc.) para un mismo comprobante. Se agrupa sumando `Neto` e `IVA`, y tomando el primer `Total` no-cero.
- **Clasificación de origen**: se marca como `"Exterior"` si el tipo es `FCC-*` / `FCE-*`, **o si el CUIT del proveedor empieza con `55`** (proveedores del exterior registrados en Colppy con CUIT 55-XXXXXXXX-X). El resto como `"Nacional"`.

### Paso 3 — Lectura y normalización de ARCA

ARCA provee los datos en un formato diferente:

- **Construcción de clave**: ARCA separa el número de comprobante en `Punto de Venta` y `Número Desde`. Se construye la clave `f"{pto:05d}-{num:08d}"` para poder hacer el JOIN con el Listado.
- **Columnas de importe**: se convierten a numérico: `Neto Gravado Total`, `Neto No Gravado`, `Op. Exentas`, `Otros Tributos`, `Total IVA`, `Imp. Total`.
- **Notas de Crédito**: ARCA exporta todos los montos como positivos, incluso las NC. El Listado las registra como negativas. Al detectar `"nota de crédito"` en el campo `Tipo`, todos los importes se multiplican por `-1`.
- **Factura C (monotributistas)**: ARCA no desglosa el Neto para Factura C — los campos `Neto Gravado Total`, `Neto No Gravado` y `Op. Exentas` quedan en 0, pero `Imp. Total` es correcto. Para que la comparación no falle, el sistema deriva el Neto por diferencia: `Neto = Imp.Total − Total IVA − Otros Tributos`. Estas filas quedan marcadas con `neto_derivado = True`.

### Paso 4 — Conciliación

Se hace un **LEFT JOIN** del Listado sobre ARCA usando la clave de comprobante:

- Para cada campo (`Neto`, `IVA`, `Total`) se calcula la diferencia absoluta.
- Un campo hace **match** si la diferencia ≤ tolerancia (por defecto ±$0.07).
- Un comprobante queda **Conciliado** si los tres campos hacen match.

**Clasificación de estado:**

| Estado | Condición |
|---|---|
| ✅ Conciliado | Neto, IVA y Total coinciden dentro de tolerancia |
| 🟡 Total OK / Sin desglose | Total coincide pero Neto difiere (Factura C derivada) |
| 🔴 Diferencia detectada | Al menos un importe discrepa por encima de tolerancia |
| ⬜ Sin match en ARCA | El comprobante no existe en el registro de ARCA |

### Paso 5 — Persistencia histórica (SQLite)

- Se calcula un hash de `[Comprobante, Estado, Conciliado]` ordenado por comprobante. Si difiere del resultado anterior, se guarda un **snapshot completo** en la tabla `conciliaciones` de `data/conciliacion_iva.db`.
- Desde el sidebar se puede cargar el resultado más reciente o comparar snapshots históricos.
- Las **reglas de memoria por CUIT** (`data/conciliacion_iva.db` → tabla `reglas_cuit`) permiten que el estado corregido se aplique automáticamente en futuras conciliaciones del mismo proveedor.
- El historial de **correcciones manuales** queda en la tabla `feedback` para trazabilidad.

### Paso 6 — Exportación

El botón **Excel completo (3 hojas)** genera un XLSX con:

| Hoja | Contenido |
|---|---|
| **Conciliación** | Todos los comprobantes del Listado con columnas de comparación y estado |
| **Solo en Listado** | Comprobantes del Listado sin contraparte en ARCA (exterior + nacionales no registrados) |
| **Solo en ARCA** | Comprobantes en ARCA que no figuran en el Listado |

Convenciones de color en el Excel:
- **Verde**: coincide / conciliado
- **Rojo**: diferencia / no match / montos negativos (NC)
- **Amarillo**: Total OK sin desglose
- **Gris**: sin match en ARCA

Cada tab en la UI también ofrece descarga CSV de la **vista filtrada actual**.

---

## Estructura de archivos

```
Prueba_base/
├── app.py                       # Entry point (st.navigation registra las páginas)
├── inicio.bat                   # Lanzador Windows (instala deps la primera vez, luego corre)
├── run.sh                       # Lanzador Linux/Mac
├── requirements.txt
├── .streamlit/
│   └── config.toml              # Puerto fijo 8502, tema visual
└── data/
    └── conciliacion_iva.db      # Base de datos SQLite (se crea automáticamente)
                                 # Tablas: conciliaciones, reglas_cuit, mapeos, feedback
```

> Si existían archivos CSV de versiones anteriores (`data/persistencia/`, `data/reglas_cuit.json`, etc.), se migran a SQLite automáticamente la primera vez y quedan como backup (`.bak`).

---

## Glosario de columnas (hoja Conciliación)

| Columna | Descripción |
|---|---|
| `Estado` | Resultado de la conciliación (ver tabla de estados) |
| `Comprobante` | Número en formato `XXXXX-YYYYYYYY` |
| `Neto` / `IVA` / `Total` | Importes del Listado Colppy |
| `ARCA_Neto` / `ARCA_IVA` / `ARCA_OtrosTrib` / `ARCA_Total` | Importes de ARCA |
| `Dif_Neto` / `Dif_IVA` / `Dif_Total` | Diferencia absoluta entre fuentes |
| `Match_Neto` / `Match_IVA` / `Match_Total` | True si la diferencia ≤ tolerancia |
| `Conciliado` | True si los tres campos hacen match |
| `Existe_en_ARCA` | False = no figura en ARCA |
| `Origen` | Nacional o Exterior (FCC/FCE en Colppy) |
| `ARCA_es_NC` | True si es Nota de Crédito (montos ya están en negativo) |
| `ARCA_Neto_Derivado` | True si el Neto se calculó por diferencia (Factura C) |

---

## Funcionalidades adicionales

### Mapeo automático de columnas
Al cargar un archivo, la app detecta las columnas en 4 pasos: coincidencia exacta → normalizado (sin acentos) → sinónimos conocidos → fuzzy match. El panel "Emparejamiento de columnas" muestra la correspondencia Listado ↔ ARCA con un badge de confianza y permite corregir manualmente.

### Correcciones y memoria por CUIT
- Al hacer clic en una fila se abre el panel de corrección donde se puede cambiar el estado, elegir un motivo y agregar una nota.
- La opción **"Guardar como regla para este proveedor"** persiste el estado corregido en la base de datos: en futuras conciliaciones, todos los comprobantes del mismo CUIT recibirán ese estado automáticamente (indicado con 📋 en la tabla).
- Las reglas se gestionan desde el sidebar (borrar individualmente).

### Filtros de tabla
- **Estado**: multiselect (Conciliado, Diferencia, Sin match, etc.)
- **Origen**: Nacional / Exterior
- **Solo NC**: muestra solo Notas de Crédito de ARCA
- **Solo memoria 📋**: muestra solo las filas donde se aplicó una regla guardada

---

## Notas importantes

- **Tolerancia**: el valor por defecto de ±$0.07 cubre diferencias de redondeo entre sistemas. Se puede ajustar en el sidebar.
- **Facturas del exterior**: `FCC-A/B/C` y `FCE-A/B/C` en el Listado no tienen contraparte en ARCA (no son comprobantes electrónicos argentinos). Aparecen siempre en "Solo en Listado" con `Origen = Exterior`.
- **Fórmula del Total**: `Imp. Total = Neto Gravado + Neto No Gravado + Op. Exentas + Otros Tributos + Total IVA`.
- **Base de datos**: `data/conciliacion_iva.db` es el único archivo de datos. Copiarlo es suficiente para hacer un backup completo del historial y las reglas.
