"""
Fingerprinting estadístico de columnas para detección semántica automática.

Permite identificar el tipo semántico de una columna (CUIT, fecha, importe,
comprobante, etc.) sin depender únicamente del nombre — lo que es crítico para
archivos con encabezados variables o nombres no estándar.

Pipeline de detección (orden de prioridad):
  1. Nombre de columna → heurísticas por palabras clave
  2. dtype pandas (datetime, numeric, bool)
  3. Estadísticas: cardinalidad, longitud media, rango de valores
  4. Muestras: patrones de formato (CUIT de 11 dígitos, fecha parseable)

score_profile_match: compara la distribución de tipos semánticos entre los
archivos actuales y un perfil guardado para sugerir el perfil más adecuado.
"""
from __future__ import annotations

import re

import pandas as pd

from .models import ColumnFingerprint


# ── Fingerprint de columna ────────────────────────────────────────────────────

def fingerprint_column(series: pd.Series, name: str) -> ColumnFingerprint:
    """Computa el fingerprint estadístico de una columna.

    Analiza dtype, cardinalidad, longitud media, rango y muestras para
    inferir el tipo semántico sin depender del nombre.
    """
    n_total = max(len(series), 1)
    s_valid = series.dropna()
    n_valid = len(s_valid)
    null_ratio = 1.0 - n_valid / n_total

    dtype_kind = "text"
    is_numeric  = False
    is_datetime = False
    min_val = max_val = None
    avg_length = 0.0

    # ── Paso 1: numérico ──────────────────────────────────────────────────────
    num_rate = pd.to_numeric(s_valid, errors="coerce").notna().mean() if n_valid else 0
    if num_rate > 0.8:
        is_numeric = True
        snum = pd.to_numeric(s_valid, errors="coerce").dropna()
        dtype_kind = "numeric"
        min_val    = float(snum.min()) if len(snum) else None
        max_val    = float(snum.max()) if len(snum) else None
        avg_length = float(snum.abs().apply(lambda x: len(str(int(abs(x))))).mean()) if len(snum) else 0.0

    # ── Paso 2: fecha ─────────────────────────────────────────────────────────
    if not is_numeric and n_valid > 0:
        s_str = s_valid.astype(str)
        avg_length = float(s_str.str.len().mean())
        try:
            parsed = pd.to_datetime(s_str, format="mixed", dayfirst=True, errors="coerce")
            if parsed.notna().mean() > 0.7:
                is_datetime = True
                dtype_kind  = "datetime"
        except Exception:
            pass

    cardinality_ratio = s_valid.nunique() / n_total if n_total > 0 else 0.0
    sample = [str(v)[:50] for v in s_valid.head(5).tolist()]

    fp = ColumnFingerprint(
        name=name,
        dtype_kind=dtype_kind,
        null_ratio=null_ratio,
        cardinality_ratio=cardinality_ratio,
        avg_length=avg_length,
        min_val=min_val,
        max_val=max_val,
        sample_values=sample,
        semantic_hint="",
    )
    fp.semantic_hint = detect_semantic_type(fp, name)
    return fp


# ── Detección semántica ───────────────────────────────────────────────────────

def detect_semantic_type(fp: ColumnFingerprint, name: str = "") -> str:
    """Infiere el tipo semántico de una columna.

    Prioridad: nombre → dtype → estadísticas → muestras.
    """
    n = name.lower().strip()

    # ── Por nombre (más confiable) ────────────────────────────────────────────
    if any(x in n for x in ["cuit", "cuil", "doc emisor", "nrodoc", "nro doc"]):
        return "cuit"
    if any(x in n for x in ["fecha", "date", "fec "]):
        return "fecha"
    if any(x in n for x in ["comprobante", "nro comp", "numero desde", "num desde"]):
        return "comprobante"
    if any(x in n for x in [
        "neto", "iva", "total", "importe", "monto", "gravado",
        "tributo", "exenta", "trib", "imp ", "no gravado",
    ]):
        return "importe"

    # ── Por dtype ─────────────────────────────────────────────────────────────
    if fp.dtype_kind == "datetime":
        return "fecha"

    if fp.dtype_kind == "numeric":
        # CUIT: longitud ~11, baja cardinalidad relativa (muchos comprobantes por proveedor)
        if fp.avg_length and 9 <= fp.avg_length <= 12 and fp.cardinality_ratio < 0.4:
            return "cuit"
        # Comprobante puro numérico: alta cardinalidad, no decimal
        if fp.cardinality_ratio > 0.8 and fp.min_val and fp.min_val >= 0:
            return "comprobante"
        return "importe"

    # ── Por muestras (texto) ──────────────────────────────────────────────────
    if fp.dtype_kind == "text" and fp.sample_values:
        # Detectar patrón CUIT: solo dígitos después de limpiar, longitud 10-11
        cuit_like = sum(
            1 for v in fp.sample_values
            if re.sub(r"[^0-9]", "", str(v)) and 9 <= len(re.sub(r"[^0-9]", "", str(v))) <= 12
        )
        if cuit_like >= len(fp.sample_values) * 0.7:
            return "cuit"
        # Detectar patrón comprobante XXXXX-YYYYYYYY
        comp_like = sum(
            1 for v in fp.sample_values
            if re.match(r"^\d{1,5}-\d{5,8}$", str(v).strip())
        )
        if comp_like >= len(fp.sample_values) * 0.5:
            return "comprobante"
        # Detectar fecha textual
        if fp.avg_length and 8 <= fp.avg_length <= 12:
            date_like = sum(
                1 for v in fp.sample_values
                if re.search(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", str(v))
            )
            if date_like >= len(fp.sample_values) * 0.5:
                return "fecha"

    return "texto"


# ── Fingerprint de DataFrame ──────────────────────────────────────────────────

def fingerprint_dataframe(df: pd.DataFrame) -> dict[str, ColumnFingerprint]:
    """Computa fingerprints de todas las columnas de un DataFrame.

    Retorna {nombre_columna: ColumnFingerprint}.
    """
    return {col: fingerprint_column(df[col], col) for col in df.columns}


# ── Similitud entre archivos y un perfil guardado ─────────────────────────────

def score_profile_match(
    left_fps: dict[str, ColumnFingerprint],
    right_fps: dict[str, ColumnFingerprint],
    profile_left_fp: dict,
    profile_right_fp: dict,
) -> float:
    """Puntaje 0.0–1.0 de similitud estructural entre archivos actuales y un perfil.

    Compara la distribución de tipos semánticos (importe, cuit, fecha…)
    entre los fingerprints actuales y los almacenados en el perfil.
    No requiere que los nombres de columna coincidan — detecta estructura.
    """
    def _type_vector(fps: dict) -> dict[str, int]:
        counts: dict[str, int] = {}
        for fp in fps.values():
            hint = fp.get("semantic_hint", "texto") if isinstance(fp, dict) else fp.semantic_hint
            counts[hint] = counts.get(hint, 0) + 1
        return counts

    def _jaccard(a: dict, b: dict) -> float:
        keys = set(a) | set(b)
        if not keys:
            return 0.0
        hits  = sum(min(a.get(k, 0), b.get(k, 0)) for k in keys)
        total = sum(max(a.get(k, 0), b.get(k, 0)) for k in keys)
        return hits / total if total > 0 else 0.0

    if not profile_left_fp and not profile_right_fp:
        return 0.0

    cur_left_vec  = _type_vector(left_fps)
    cur_right_vec = _type_vector(right_fps)

    score_l = _jaccard(cur_left_vec,  _type_vector(profile_left_fp))  if profile_left_fp  else None
    score_r = _jaccard(cur_right_vec, _type_vector(profile_right_fp)) if profile_right_fp else None

    if score_l is None:
        return score_r or 0.0
    if score_r is None:
        return score_l
    return (score_l + score_r) / 2


def suggest_best_profile(
    left_fps: dict[str, ColumnFingerprint],
    right_fps: dict[str, ColumnFingerprint],
    profiles: list,
    min_score: float = 0.60,
) -> tuple | None:
    """Retorna (MappingProfile, score) del perfil más compatible, o None.

    Usa score_profile_match para comparar la distribución estructural.
    Solo sugiere si el score supera min_score.
    """
    best = None
    best_score = min_score - 0.001

    for profile in profiles:
        score = score_profile_match(
            left_fps, right_fps,
            profile.left_fingerprint,
            profile.right_fingerprint,
        )
        if score > best_score:
            best_score = score
            best = profile

    if best is None:
        return None
    return best, best_score
