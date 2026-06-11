"""
Capa de acceso a datos — SQLite.

Incluye:
  - _db_con: abre conexión con WAL mode y foreign keys
  - init_db: crea las tablas si no existen; llama a _migrar_legacy en primera ejecución
  - _migrar_legacy: importa archivos JSON/CSV/JSONL legacy a SQLite (corre una sola vez)
  - cargar_mapeos / guardar_mapeos: configuración de emparejamiento de columnas
  - cargar_reglas / guardar_regla / eliminar_regla: reglas de memoria por CUIT
  - guardar_feedback: log de correcciones manuales del usuario
  - cargar_csv / guardar_csv: snapshot activo de conciliación
  - listar_historicos / cargar_historico: historial de snapshots

Todas las funciones de escritura usan transacciones y cierran la conexión en el
bloque finally para evitar conexiones huérfanas.

Esquema de tablas:
  conciliaciones: snapshot completo de cada corrida (s1/s2/s3 en JSON)
  reglas_cuit:    reglas de memoria por CUIT proveedor
  mapeos:         perfiles de emparejamiento de columnas guardados por el usuario
  feedback:       log de correcciones manuales para auditoría y mejora futura
"""
import json
import os
import sqlite3
import sys
from datetime import datetime
from io import StringIO

import pandas as pd

from .constants import (
    CSV_CONC, CSV_LIST, CSV_ARCA, CSV_META,
    DATA_DIR, DB_FILE, FEEDBACK_FILE, ICON_ESTADO,
    MAPEOS_DEFAULT, MAPEOS_FILE, PERSIST_DIR, REGLAS_FILE,
    TOLERANCIA_DEFAULT,
)
from .utils import _detectar_periodo, _hash_s1, _restore_bools


def _db_con() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    """Crea las tablas si no existen y migra archivos legacy en la primera ejecución."""
    con = _db_con()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS conciliaciones (
            ts            TEXT PRIMARY KEY,
            periodo       TEXT,
            tolerancia    REAL,
            n_listado     INTEGER,
            n_arca        INTEGER,
            conciliados   INTEGER,
            diferencias   INTEGER,
            fecha_proceso TEXT,
            s1_json       TEXT,
            s2_json       TEXT,
            s3_json       TEXT
        );
        CREATE TABLE IF NOT EXISTS reglas_cuit (
            cuit_norm      TEXT PRIMARY KEY,
            estado         TEXT,
            estado_display TEXT,
            motivo         TEXT,
            razon_social   TEXT,
            creado         TEXT,
            activo         INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS mapeos (
            nombre      TEXT PRIMARY KEY,
            config_json TEXT,
            actualizado TEXT
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ts               TEXT,
            periodo          TEXT,
            comprobante      TEXT,
            cuit_dni         TEXT,
            razon_social     TEXT,
            estado_algoritmo TEXT,
            estado_usuario   TEXT,
            motivo           TEXT,
            nota             TEXT,
            data_json        TEXT
        );
        CREATE TABLE IF NOT EXISTS semantic_aliases (
            canonical  TEXT NOT NULL,
            synonym    TEXT NOT NULL,
            direction  TEXT DEFAULT 'both',
            use_count  INTEGER DEFAULT 1,
            last_seen  TEXT,
            PRIMARY KEY (canonical, synonym)
        );
        CREATE TABLE IF NOT EXISTS mapping_profiles (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            profile_json TEXT NOT NULL,
            use_count    INTEGER DEFAULT 0,
            created_at   TEXT,
            updated_at   TEXT
        );
    """)
    con.commit()
    con.close()
    try:
        os.chmod(DB_FILE, 0o600)
    except Exception:
        pass
    _migrar_legacy()


def _migrar_legacy():
    """Importa archivos legacy (JSON/CSV/JSONL) a SQLite. Corre solo una vez."""
    marker = DATA_DIR / ".db_migrated"
    if marker.exists():
        return
    con = _db_con()
    try:
        # reglas_cuit.json
        if REGLAS_FILE.exists():
            try:
                reglas = json.loads(REGLAS_FILE.read_text(encoding="utf-8"))
                for cuit, v in reglas.items():
                    con.execute(
                        "INSERT OR IGNORE INTO reglas_cuit "
                        "(cuit_norm,estado,estado_display,motivo,razon_social,creado,activo) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (cuit, v.get("estado",""), v.get("estado_display",""),
                         v.get("motivo",""), v.get("razon_social",""),
                         v.get("creado",""), int(v.get("activo", True)))
                    )
            except Exception:
                pass

        # mapeos.json
        if MAPEOS_FILE.exists():
            try:
                mapeos = json.loads(MAPEOS_FILE.read_text(encoding="utf-8"))
                for nombre, config in mapeos.items():
                    con.execute(
                        "INSERT OR IGNORE INTO mapeos (nombre, config_json, actualizado) VALUES (?,?,?)",
                        (nombre, json.dumps(config, ensure_ascii=False), datetime.now().isoformat())
                    )
            except Exception:
                pass

        # feedback.jsonl
        if FEEDBACK_FILE.exists():
            try:
                for line in FEEDBACK_FILE.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    con.execute(
                        "INSERT INTO feedback "
                        "(ts,periodo,comprobante,cuit_dni,razon_social,"
                        "estado_algoritmo,estado_usuario,motivo,nota,data_json) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (r.get("ts"), r.get("periodo"), r.get("Comprobante"),
                         r.get("CUIT_DNI"), r.get("Razon_Social"),
                         r.get("estado_algoritmo"), r.get("estado_usuario"),
                         r.get("motivo"), r.get("nota"),
                         json.dumps(r, ensure_ascii=False))
                    )
                FEEDBACK_FILE.rename(FEEDBACK_FILE.with_suffix(".jsonl.bak"))
            except Exception:
                pass

        # Snapshots históricos de persistencia/
        if PERSIST_DIR.exists():
            for m in sorted(PERSIST_DIR.glob("*_meta.csv")):
                try:
                    row  = pd.read_csv(m).iloc[0].to_dict()
                    ts   = row.get("ts", m.stem.split("_meta")[0])
                    if con.execute("SELECT 1 FROM conciliaciones WHERE ts=?", (ts,)).fetchone():
                        continue
                    p1 = PERSIST_DIR / f"{ts}_conciliacion.csv"
                    p2 = PERSIST_DIR / f"{ts}_solo_listado.csv"
                    p3 = PERSIST_DIR / f"{ts}_solo_arca.csv"
                    if not all(p.exists() for p in (p1, p2, p3)):
                        continue
                    _s1 = _restore_bools(pd.read_csv(p1, encoding="utf-8-sig"))
                    _s2 = pd.read_csv(p2, encoding="utf-8-sig")
                    _s3 = pd.read_csv(p3, encoding="utf-8-sig")
                    periodo = str(row.get("periodo", "")).strip()
                    con.execute(
                        "INSERT OR IGNORE INTO conciliaciones "
                        "(ts,periodo,tolerancia,n_listado,n_arca,conciliados,diferencias,"
                        "fecha_proceso,s1_json,s2_json,s3_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (ts, periodo, float(row.get("tolerancia", 0)),
                         int(row.get("n_listado", 0)), 0,
                         int(row.get("conciliados", 0)), 0, ts,
                         _s1.to_json(orient="records", force_ascii=False, date_format="iso"),
                         _s2.to_json(orient="records", force_ascii=False, date_format="iso"),
                         _s3.to_json(orient="records", force_ascii=False, date_format="iso"))
                    )
                except Exception:
                    pass

        # Último resultado (CSVs _last)
        if CSV_CONC.exists() and CSV_LIST.exists() and CSV_ARCA.exists():
            try:
                meta = pd.read_csv(CSV_META).iloc[0].to_dict() if CSV_META.exists() else {}
                try:
                    _ts_last = datetime.fromisoformat(
                        str(meta.get("fecha_proceso", ""))
                    ).strftime("%Y%m%d_%H%M%S")
                except Exception:
                    _ts_last = datetime.now().strftime("%Y%m%d_%H%M%S")
                if not con.execute("SELECT 1 FROM conciliaciones WHERE ts=?", (_ts_last,)).fetchone():
                    _s1 = _restore_bools(pd.read_csv(CSV_CONC, encoding="utf-8-sig"))
                    _s2 = pd.read_csv(CSV_LIST, encoding="utf-8-sig")
                    _s3 = pd.read_csv(CSV_ARCA, encoding="utf-8-sig")
                    periodo = _detectar_periodo(_s1)
                    tol = float(meta.get("tolerancia", TOLERANCIA_DEFAULT))
                    con.execute(
                        "INSERT OR IGNORE INTO conciliaciones "
                        "(ts,periodo,tolerancia,n_listado,n_arca,conciliados,diferencias,"
                        "fecha_proceso,s1_json,s2_json,s3_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (_ts_last, periodo, tol, len(_s1),
                         int(meta.get("n_arca", 0)), int(meta.get("conciliados", 0)),
                         int(meta.get("diferencias", 0)), str(meta.get("fecha_proceso", "")),
                         _s1.to_json(orient="records", force_ascii=False, date_format="iso"),
                         _s2.to_json(orient="records", force_ascii=False, date_format="iso"),
                         _s3.to_json(orient="records", force_ascii=False, date_format="iso"))
                    )
            except Exception:
                pass

        con.commit()
        marker.touch()
    finally:
        con.close()


# ── Mapeos de columnas ────────────────────────────────────────────────────────

def cargar_mapeos() -> dict:
    """Lee perfiles de mapeo guardados; devuelve MAPEOS_DEFAULT si no hay ninguno."""
    import copy
    try:
        con  = _db_con()
        rows = con.execute("SELECT nombre, config_json FROM mapeos").fetchall()
        con.close()
        mapeos = copy.deepcopy(MAPEOS_DEFAULT)
        for row in rows:
            try:
                mapeos[row["nombre"]] = json.loads(row["config_json"])
            except Exception:
                pass
        return mapeos
    except Exception:
        import copy as _c
        return _c.deepcopy(MAPEOS_DEFAULT)


def guardar_mapeos(mapeos: dict):
    """Persiste uno o varios perfiles de mapeo en la tabla 'mapeos'."""
    try:
        con = _db_con()
        for nombre, config in mapeos.items():
            con.execute(
                "INSERT OR REPLACE INTO mapeos (nombre, config_json, actualizado) VALUES (?,?,?)",
                (nombre, json.dumps(config, ensure_ascii=False), datetime.now().isoformat())
            )
        con.commit()
        con.close()
    except Exception:
        pass


# ── Reglas de memoria por CUIT ────────────────────────────────────────────────

def cargar_reglas() -> dict:
    """Lee todas las reglas activas de la tabla 'reglas_cuit'."""
    try:
        con  = _db_con()
        rows = con.execute(
            "SELECT cuit_norm,estado,estado_display,motivo,razon_social,creado,activo "
            "FROM reglas_cuit"
        ).fetchall()
        con.close()
        return {
            r["cuit_norm"]: {
                "estado":         r["estado"],
                "estado_display": r["estado_display"],
                "motivo":         r["motivo"],
                "razon_social":   r["razon_social"],
                "creado":         r["creado"],
                "activo":         bool(r["activo"]),
            }
            for r in rows
        }
    except Exception:
        return {}


def guardar_regla(cuit_norm: str, estado: str, motivo: str, razon_social: str) -> bool:
    """Inserta o actualiza una regla de memoria para el CUIT dado."""
    estado_canon = ICON_ESTADO.get(estado, estado)
    con = None
    try:
        con = _db_con()
        con.execute(
            "INSERT OR REPLACE INTO reglas_cuit "
            "(cuit_norm,estado,estado_display,motivo,razon_social,creado,activo) "
            "VALUES (?,?,?,?,?,?,1)",
            (cuit_norm, estado_canon, estado, motivo, razon_social, datetime.now().isoformat())
        )
        con.commit()
        return True
    except Exception as e:
        print(f"[ERROR guardar_regla] {e}", file=sys.stderr)
        return False
    finally:
        if con:
            con.close()


def eliminar_regla(cuit_norm: str) -> bool:
    """Elimina la regla de memoria para el CUIT dado."""
    con = None
    try:
        con = _db_con()
        con.execute("DELETE FROM reglas_cuit WHERE cuit_norm=?", (cuit_norm,))
        con.commit()
        return True
    except Exception as e:
        print(f"[ERROR eliminar_regla] {e}", file=sys.stderr)
        return False
    finally:
        if con:
            con.close()


# ── Feedback y correcciones ───────────────────────────────────────────────────

def guardar_feedback(fila: pd.Series, estado_usuario: str,
                     motivo: str, nota: str, periodo: str) -> bool:
    """Registra una corrección manual del usuario en la tabla 'feedback'."""
    campos_num = ["Dif_Neto", "Dif_IVA", "Dif_Total", "Neto", "IVA", "Total"]
    registro: dict = {
        "ts":               datetime.now().isoformat(),
        "periodo":          periodo,
        "Comprobante":      fila.get("Comprobante", ""),
        "CUIT_DNI":         fila.get("CUIT_DNI", ""),
        "Razon_Social":     fila.get("Razon_Social", ""),
        "estado_algoritmo": fila.get("Estado", ""),
        "estado_usuario":   estado_usuario,
        "motivo":           motivo,
        "nota":             nota,
    }
    for c in campos_num:
        if c in fila.index:
            registro[c] = fila[c]
    con = None
    try:
        con = _db_con()
        con.execute(
            "INSERT INTO feedback "
            "(ts,periodo,comprobante,cuit_dni,razon_social,"
            "estado_algoritmo,estado_usuario,motivo,nota,data_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (registro["ts"], periodo, registro["Comprobante"],
             registro["CUIT_DNI"], registro["Razon_Social"],
             registro["estado_algoritmo"], estado_usuario,
             motivo, nota,
             json.dumps(registro, ensure_ascii=False, default=str))
        )
        con.commit()
        return True
    except Exception as e:
        print(f"[ERROR guardar_feedback] {e}", file=sys.stderr)
        return False
    finally:
        if con:
            con.close()


# ── Snapshots de conciliación ─────────────────────────────────────────────────

def cargar_csv():
    """Lee el resultado más reciente de la BD. Retorna (s1, s2, s3, meta) o (None,None,None,{})."""
    try:
        con = _db_con()
        row = con.execute(
            "SELECT * FROM conciliaciones ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        con.close()
        if row is None:
            return None, None, None, {}
        s1 = _restore_bools(pd.read_json(StringIO(row["s1_json"]), orient="records", dtype=False))
        s2 = pd.read_json(StringIO(row["s2_json"]), orient="records", dtype=False)
        s3 = pd.read_json(StringIO(row["s3_json"]), orient="records", dtype=False)
        meta = {
            "fecha_proceso": row["fecha_proceso"],
            "periodo":       row["periodo"],
            "tolerancia":    row["tolerancia"],
            "n_listado":     row["n_listado"],
            "n_arca":        row["n_arca"],
            "conciliados":   row["conciliados"],
            "diferencias":   row["diferencias"],
        }
        return s1, s2, s3, meta
    except Exception:
        return None, None, None, {}


def guardar_csv(s1, s2, s3, tolerancia: float) -> str:
    """Persiste la conciliación en SQLite.

    Retorna "saved" si se guardó un snapshot nuevo, "unchanged" si el resultado
    es idéntico al anterior, o "error" si la escritura falló — la UI debe
    distinguir los tres casos para no reportar un fallo como "sin cambios".
    """
    try:
        con  = _db_con()
        prev = con.execute(
            "SELECT ts, s1_json FROM conciliaciones ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        prev_s1 = None
        if prev:
            try:
                prev_s1 = pd.read_json(StringIO(prev["s1_json"]), orient="records", dtype=False)
            except Exception:
                pass

        if _hash_s1(s1) == _hash_s1(prev_s1):
            con.close()
            return "unchanged"

        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        periodo = _detectar_periodo(s1)
        con.execute(
            "INSERT INTO conciliaciones "
            "(ts,periodo,tolerancia,n_listado,n_arca,conciliados,diferencias,"
            "fecha_proceso,s1_json,s2_json,s3_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts, periodo, tolerancia, len(s1),
             len(s3) + int(s1["Existe_en_ARCA"].sum()),
             int(s1["Conciliado"].sum()),
             int((s1["Estado"] == "Diferencia detectada").sum()),
             datetime.now().isoformat(),
             s1.to_json(orient="records", force_ascii=False, date_format="iso"),
             s2.to_json(orient="records", force_ascii=False, date_format="iso"),
             s3.to_json(orient="records", force_ascii=False, date_format="iso"))
        )
        con.commit()
        con.close()
        return "saved"
    except Exception as e:
        print(f"[ERROR guardar_csv] {e}", file=sys.stderr)
        return "error"


def listar_historicos() -> list[dict]:
    """Lee los metadatos de todos los snapshots en orden descendente."""
    try:
        con  = _db_con()
        rows = con.execute(
            "SELECT ts,periodo,tolerancia,n_listado,conciliados,fecha_proceso "
            "FROM conciliaciones ORDER BY ts DESC"
        ).fetchall()
        con.close()
        result = []
        for row in rows:
            ts      = row["ts"]
            periodo = str(row["periodo"] or "").strip()
            try:
                fecha_label = datetime.strptime(ts, "%Y%m%d_%H%M%S").strftime("%d/%m/%Y %H:%M")
            except Exception:
                fecha_label = ts
            label = f"{periodo}  ({fecha_label})" if periodo else fecha_label
            result.append({
                "ts":      ts,
                "label":   label,
                "periodo": periodo,
                "conc":    int(row["conciliados"] or 0),
                "n":       int(row["n_listado"] or 0),
                "tol":     float(row["tolerancia"] or 0),
            })
        return result
    except Exception:
        return []


def cargar_historico(ts: str):
    """Carga s1, s2, s3 de un snapshot por ts. Retorna (s1, s2, s3) o (None, None, None)."""
    try:
        con = _db_con()
        row = con.execute(
            "SELECT s1_json, s2_json, s3_json FROM conciliaciones WHERE ts=?", (ts,)
        ).fetchone()
        con.close()
        if row is None:
            return None, None, None
        s1 = _restore_bools(pd.read_json(StringIO(row["s1_json"]), orient="records", dtype=False))
        s2 = pd.read_json(StringIO(row["s2_json"]), orient="records", dtype=False)
        s3 = pd.read_json(StringIO(row["s3_json"]), orient="records", dtype=False)
        return s1, s2, s3
    except Exception:
        return None, None, None


# ── Aliases semánticos aprendidos ─────────────────────────────────────────────

def guardar_alias(canonical: str, synonym: str, direction: str = "both") -> bool:
    """Registra o incrementa un alias semántico aprendido del usuario.

    canonical: nombre de campo semántico (ej: "Razón Social")
    synonym:   nombre real encontrado en el archivo (ej: "Proveedor")
    direction: 'left' | 'right' | 'both'
    """
    con = None
    try:
        con = _db_con()
        con.execute(
            """
            INSERT INTO semantic_aliases (canonical, synonym, direction, use_count, last_seen)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(canonical, synonym) DO UPDATE SET
                use_count = use_count + 1,
                last_seen = excluded.last_seen,
                direction = excluded.direction
            """,
            (canonical, synonym, direction, datetime.now().isoformat()),
        )
        con.commit()
        return True
    except Exception as e:
        print(f"[ERROR guardar_alias] {e}", file=sys.stderr)
        return False
    finally:
        if con:
            con.close()


def cargar_aliases() -> dict[str, list[str]]:
    """Lee todos los aliases semánticos guardados.

    Retorna {canonical: [synonym, ...]} ordenados por use_count descendente.
    """
    try:
        con  = _db_con()
        rows = con.execute(
            "SELECT canonical, synonym FROM semantic_aliases ORDER BY use_count DESC"
        ).fetchall()
        con.close()
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row["canonical"], []).append(row["synonym"])
        return result
    except Exception:
        return {}


# ── Perfiles semánticos completos ─────────────────────────────────────────────

def guardar_perfil(profile) -> bool:
    """Persiste un MappingProfile completo (incluye fingerprints y rules).

    Guarda en mapping_profiles (tabla nueva) y también en mapeos para
    compatibilidad con el panel de perfiles de la UI existente.
    """
    con = None
    try:
        d    = profile.to_dict() if hasattr(profile, "to_dict") else profile
        name = d.get("name", "")
        pid  = d.get("id", name)
        now  = datetime.now().isoformat()

        con = _db_con()
        con.execute(
            """
            INSERT INTO mapping_profiles (id, name, profile_json, use_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name         = excluded.name,
                profile_json = excluded.profile_json,
                use_count    = use_count + 1,
                updated_at   = excluded.updated_at
            """,
            (pid, name, json.dumps(d, ensure_ascii=False), d.get("use_count", 0),
             d.get("created_at", now), now),
        )
        # Compat: guardar también en tabla mapeos con columnas_l/a para UI legacy
        rules_by_lc = {r["l_campo"]: r["left_columns"] for r in d.get("rules", []) if r.get("l_campo")}
        rules_by_ac = {r["a_campo"]: r["right_columns"] for r in d.get("rules", []) if r.get("a_campo")}
        cols_l = {lc: (v[0] if len(v) == 1 else v) for lc, v in rules_by_lc.items() if v}
        cols_a = {ac: (v[0] if len(v) == 1 else v) for ac, v in rules_by_ac.items() if v}
        compat_config = {"columnas_l": cols_l, "columnas_a": cols_a, "profile_id": pid}
        con.execute(
            "INSERT OR REPLACE INTO mapeos (nombre, config_json, actualizado) VALUES (?,?,?)",
            (name, json.dumps(compat_config, ensure_ascii=False), now),
        )
        con.commit()
        return True
    except Exception as e:
        print(f"[ERROR guardar_perfil] {e}", file=sys.stderr)
        return False
    finally:
        if con:
            con.close()


def cargar_perfiles() -> list:
    """Carga todos los MappingProfile guardados, ordenados por uso reciente."""
    try:
        from .models import MappingProfile
        con  = _db_con()
        rows = con.execute(
            "SELECT profile_json FROM mapping_profiles ORDER BY updated_at DESC"
        ).fetchall()
        con.close()
        result = []
        for row in rows:
            try:
                result.append(MappingProfile.from_dict(json.loads(row["profile_json"])))
            except Exception:
                pass
        return result
    except Exception:
        return []


def registrar_uso_perfil(profile_id: str):
    """Incrementa el contador de uso de un perfil para priorizar sugerencias."""
    try:
        con = _db_con()
        con.execute(
            "UPDATE mapping_profiles SET use_count = use_count + 1, updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), profile_id),
        )
        con.commit()
        con.close()
    except Exception:
        pass
