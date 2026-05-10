"""
Crea y persiste el perfil de emparejamiento para los archivos reales del usuario.

Ejecutar una sola vez:
    cd /home/zen/carolina_1/Prueba_base && python3 _crear_perfil_archivos.py
"""
import sys
from pathlib import Path

# Make sure the package is importable
sys.path.insert(0, str(Path(__file__).parent))

from conciliacion.database import init_db, guardar_perfil
from conciliacion.fingerprint import fingerprint_dataframe
from conciliacion.loaders import load_listado_iva, load_arca
from conciliacion.models import MappingProfile, MappingRule
from conciliacion.constants import CAMPOS_LISTADO, CAMPOS_ARCA, MAPEOS_DEFAULT
from datetime import datetime
import copy, uuid

# ── Archivos a registrar ──────────────────────────────────────────────────────
LISTADO_FILE = Path("_Listado Iva Compras_estefania@rpa-consulting.com (12).xls")
ARCA_FILE    = Path("Mis Comprobantes Recibidos - CUIT 30712461221 (3).xlsx")

# ── Mapeo correcto para estos archivos ───────────────────────────────────────
# Listado: columnas reales del archivo XLS (header en fila 4, formato Colppy)
MAPEO_LISTADO_COLS = {
    "comprobante":   "Comprobante",
    "fecha":         "Fecha Factura",
    "tipo":          "Tipo",
    "cuit":          "CUIT/DNI",
    "razon_social":  "Razón Social",
    "condicion_iva": "Condición IVA",
    "neto":          "Neto",
    "iva":           "IVA",
    "total":         "Total",
}

# ARCA: columnas reales del archivo XLSX (header en fila 1, formato ARCA estándar)
# IMPORTANTE: total_iva apunta a "Total IVA" (columna pre-agregada) — NO a columnas
# individuales de alícuota, para evitar el bug de duplicados de nombre.
MAPEO_ARCA_COLS = {
    "punto_venta":     "Punto de Venta",
    "numero":          "Número Desde",
    "fecha":           "Fecha",
    "tipo":            "Tipo",
    "cuit_emisor":     "Nro. Doc. Emisor",
    "denominacion":    "Denominación Emisor",
    "neto_gravado":    "Neto Gravado Total",
    "neto_no_gravado": "Neto No Gravado",
    "op_exentas":      "Op. Exentas",
    "otros_tributos":  "Otros Tributos",
    "total_iva":       "Total IVA",
    "total":           "Imp. Total",
}

NOMBRE_PERFIL = "Colppy-ARCA Estándar (estefania)"

def build_rules() -> list[MappingRule]:
    from conciliacion.constants import GRUPOS_PAREJAS
    rules = []
    for grupo in GRUPOS_PAREJAS:
        for campo in grupo["campos"]:
            lc  = campo.get("l_campo")
            ac  = campo.get("a_campo")
            op  = campo.get("operation",     "identity")
            cmp = campo.get("comparison",    "approx")
            sem = campo.get("semantic_type", "texto")
            req = campo.get("required",      True)

            left_val  = MAPEO_LISTADO_COLS.get(lc, "") if lc else None
            right_val = MAPEO_ARCA_COLS.get(ac,  "") if ac else None

            left_cols  = [left_val]  if left_val  else []
            right_cols = [right_val] if right_val else []

            for impl in campo.get("a_implícitos", []):
                iv = MAPEO_ARCA_COLS.get(impl, "")
                if iv and iv not in right_cols:
                    right_cols.append(iv)

            if not left_cols and not right_cols:
                continue

            tol = 0.07 if (cmp == "approx" and sem == "importe") else 0.0
            rules.append(MappingRule(
                id=f"{lc or 'x'}_{ac or 'x'}",
                label=campo.get("label", "").rstrip(" *"),
                left_columns=left_cols,
                right_columns=right_cols,
                operation=op,
                comparison=cmp,
                tolerance=tol,
                semantic_type=sem,
                required=req,
                confidence=1.0,
                affects_status=req,
                l_campo=lc or "",
                a_campo=ac or "",
            ))
    return rules


def main():
    init_db()

    # ── Cargar los archivos para obtener fingerprints reales ──────────────────
    mapeo_l = copy.deepcopy(MAPEOS_DEFAULT["listado"]["Colppy"])
    mapeo_l["columnas"] = copy.deepcopy(MAPEO_LISTADO_COLS)

    mapeo_a = copy.deepcopy(MAPEOS_DEFAULT["arca"]["ARCA"])
    mapeo_a["columnas"] = copy.deepcopy(MAPEO_ARCA_COLS)

    print(f"Cargando Listado: {LISTADO_FILE}")
    with open(LISTADO_FILE, "rb") as fh_l:
        df_l = load_listado_iva(fh_l, mapeo_l)
    if df_l is None:
        print("ERROR: no se pudo cargar el Listado")
        sys.exit(1)
    print(f"  → {len(df_l)} filas, {list(df_l.columns[:6])} ...")

    print(f"Cargando ARCA: {ARCA_FILE}")
    with open(ARCA_FILE, "rb") as fh_a:
        df_a = load_arca(fh_a, mapeo_a)
    if df_a is None:
        print("ERROR: no se pudo cargar el archivo ARCA")
        sys.exit(1)
    print(f"  → {len(df_a)} filas, {list(df_a.columns[:6])} ...")

    # ── Fingerprints ──────────────────────────────────────────────────────────
    fps_l = {k: v.to_dict() for k, v in fingerprint_dataframe(df_l).items()}
    fps_a = {k: v.to_dict() for k, v in fingerprint_dataframe(df_a).items()}
    print(f"  Fingerprints: {len(fps_l)} cols Listado, {len(fps_a)} cols ARCA")

    # ── Construir reglas ──────────────────────────────────────────────────────
    rules = build_rules()
    print(f"  MappingRules generadas: {len(rules)}")

    # ── Aliases para este perfil ──────────────────────────────────────────────
    aliases = {
        "listado": MAPEO_LISTADO_COLS,
        "arca":    MAPEO_ARCA_COLS,
    }

    # ── Crear y guardar el perfil ─────────────────────────────────────────────
    now = datetime.now().isoformat()
    profile = MappingProfile(
        id=f"perfil_{uuid.uuid4().hex[:8]}",
        name=NOMBRE_PERFIL,
        rules=rules,
        left_fingerprint=fps_l,
        right_fingerprint=fps_a,
        aliases=aliases,
        created_at=now,
        updated_at=now,
        use_count=1,
        match_score=1.0,
    )

    ok = guardar_perfil(profile)
    if ok:
        print(f"\n✅ Perfil guardado: '{NOMBRE_PERFIL}' (id={profile.id})")
        print("   La próxima vez que subas estos archivos el sistema lo reconocerá automáticamente.")
    else:
        print("\n❌ Error al guardar el perfil")
        sys.exit(1)


if __name__ == "__main__":
    main()
