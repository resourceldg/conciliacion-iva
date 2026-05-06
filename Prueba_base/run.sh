#!/bin/bash
# Mata cualquier instancia previa en el puerto 8502 y relanza la app
PORT=8502
APP=app_conciliacion_iva.py

# Matar proceso anterior en ese puerto si existe
OLD_PID=$(lsof -ti tcp:$PORT 2>/dev/null)
if [ -n "$OLD_PID" ]; then
    echo "Matando proceso anterior (PID $OLD_PID) en puerto $PORT..."
    kill "$OLD_PID"
    sleep 1
fi

echo "Iniciando Conciliación IVA en http://localhost:$PORT"
streamlit run "$APP"
