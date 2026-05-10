"""
Detección y sugerencia automática de columnas.

Incluye:
  - _MULTI_PREFIJOS: prefijos semánticos para detección de columnas de alícuota
  - _detectar_cols_multi: retorna todas las columnas que coinciden con un prefijo
  - sugerir_mapeo: pipeline de 4 pasos (exact → norm → alias → fuzzy) para mapear
                   columnas reales a campos semánticos esperados
  - build_mapping_rules: convierte res_l + res_a + GRUPOS_PAREJAS en lista de MappingRule
  - Fallbacks de columnas conocidas para Listado y ARCA

El pipeline de sugerencia evita asignaciones duplicadas (una columna solo puede
mapearse a un campo), y distingue la confianza del match para mostrar alertas en
la UI sin bloquear el procesamiento.

build_mapping_rules formaliza el modelo N:M: cada campo del GRUPOS_PAREJAS genera
una MappingRule con sus columnas reales, operador y tipo de comparación. Esto permite
al motor de conciliación y a la UI trabajar con un modelo semántico explícito en lugar
de convenciones implícitas.
"""
import difflib

from .constants import (
    ALIASES_ARCA,
    ALIASES_LISTADO,
    CAMPOS_ARCA,
    CAMPOS_LISTADO,
)
from .utils import _normalizar_col

# Fallbacks de columnas conocidas para ubicar el encabezado
_LISTADO_FALLBACKS = ["CUIT/DNI", "Fecha Factura", "Fecha", "Neto", "Total"]
_ARCA_FALLBACKS    = ["Número Desde", "Fecha", "Neto Gravado"]

# Prefijos semánticos para detección automática de columnas de alícuota.
# Clave = nombre del campo semántico (l_campo/a_campo en GRUPOS_PAREJAS).
# Valor = lista de prefijos normalizados que identifican columnas de alícuota.
_MULTI_PREFIJOS: dict[str, list[str]] = {
    "neto":      ["neto gravado"],    # Listado: "Neto gravado 21%", "Neto gravado 10,5%", …
    "iva":       ["iva "],            # Listado: "IVA 21%", "IVA 10.5%", … (espacio excluye "Total IVA")
    "total_iva": ["iva "],            # ARCA:    "IVA 10,5%", "IVA 21%", …
}


def _detectar_cols_multi(cols_archivo: list, campo: str) -> list[str]:
    """Retorna todas las columnas del archivo cuyo nombre empieza con un prefijo semántico.

    Ej: campo='iva' con cols ["IVA 21%","IVA 10.5%","Neto gravado 21%","Total IVA"]
        → ["IVA 21%", "IVA 10.5%"]  (excluye "Total IVA" por el espacio en el prefijo "iva ")
    """
    prefijos = [_normalizar_col(p) for p in _MULTI_PREFIJOS.get(campo, [])]
    if not prefijos:
        return []
    return [
        col for col in cols_archivo
        if any(_normalizar_col(col).startswith(p) for p in prefijos)
    ]


def sugerir_mapeo(cols_archivo: list, campos_esperados: dict,
                  aliases: dict | None = None) -> tuple[dict, dict]:
    """Mapea columnas del archivo a campos semánticos con pipeline de 4 pasos.

    Pasos en orden de confianza decreciente:
      1. Exact (case-insensitive)
      2. Normalizado (sin acentos, sin puntuación)
      3. Alias conocidos del diccionario
      4. Fuzzy (difflib, cutoff 0.45)

    Retorna (sugerencias, confianzas):
      sugerencias: {campo: col_nombre}
      confianzas:  {campo: "exact" | "norm" | "alias" | "fuzzy" | "none"}

    Garantiza unicidad: una columna no puede asignarse a dos campos distintos.
    """
    cols_lower = {c.lower().strip(): c for c in cols_archivo}
    cols_norm  = {_normalizar_col(c): c for c in cols_archivo}
    usados: set[str] = set()

    def _claim(col: str) -> str | None:
        if col not in usados:
            usados.add(col)
            return col
        return None

    sugerencias: dict[str, str] = {}
    confianzas:  dict[str, str] = {}

    for campo, default_col in campos_esperados.items():
        col_enc = None
        nivel   = "none"

        # 1. Exact (case-insensitive)
        k = default_col.lower().strip()
        if k in cols_lower:
            col_enc = _claim(cols_lower[k])
            if col_enc:
                nivel = "exact"

        # 2. Normalizado (sin acentos, sin puntuación)
        if not col_enc:
            kn = _normalizar_col(default_col)
            if kn in cols_norm:
                col_enc = _claim(cols_norm[kn])
                if col_enc:
                    nivel = "norm"

        # 3. Alias conocidos
        if not col_enc and aliases and campo in aliases:
            for alias in aliases[campo]:
                an = _normalizar_col(alias)
                if an in cols_norm:
                    col_enc = _claim(cols_norm[an])
                    if col_enc:
                        nivel = "alias"
                        break

        # 4. Fuzzy
        if not col_enc:
            libre   = [n for n in cols_norm if cols_norm[n] not in usados]
            matches = difflib.get_close_matches(
                _normalizar_col(default_col), libre, n=1, cutoff=0.45
            )
            if matches:
                col_enc = _claim(cols_norm[matches[0]])
                if col_enc:
                    nivel = "fuzzy"

        sugerencias[campo] = col_enc or (cols_archivo[0] if cols_archivo else "")
        confianzas[campo]  = nivel if col_enc else "none"

    return sugerencias, confianzas


# ── Construcción formal de MappingRules ──────────────────────────────────────

def build_mapping_rules(
    res_l: dict,
    res_a: dict,
    grupos: list | None = None,
    tolerancia: float = 0.07,
) -> list:
    """Convierte el mapeo usuario en una lista de MappingRule formales.

    Cada campo de GRUPOS_PAREJAS se convierte en una MappingRule que captura:
      - Las columnas reales seleccionadas por el usuario (N izquierda, M derecha)
      - El operador semántico del campo (sum, concat, identity…)
      - El modo de comparación (exact, approx)
      - El tipo semántico (importe, cuit, comprobante…)

    Esto permite al motor de conciliación y a la UI trabajar con un modelo
    explícito en lugar de convenciones implícitas esparcidas por el código.

    Retorna lista de conciliacion.models.MappingRule.
    """
    from .models import MappingRule
    from .constants import GRUPOS_PAREJAS as _GRUPOS_DEFAULT

    if grupos is None:
        grupos = _GRUPOS_DEFAULT

    rules: list[MappingRule] = []
    for grupo in grupos:
        for campo in grupo["campos"]:
            lc  = campo.get("l_campo")
            ac  = campo.get("a_campo")
            op  = campo.get("operation",     "identity")
            cmp = campo.get("comparison",    "approx")
            sem = campo.get("semantic_type", "texto")
            req = campo.get("required",      True)

            left_val  = res_l.get(lc, "") if lc else None
            right_val = res_a.get(ac, "") if ac else None

            # Normalizar a lista (el usuario puede haber seleccionado múltiples)
            left_cols  = left_val  if isinstance(left_val,  list) else ([left_val]  if left_val  else [])
            right_cols = right_val if isinstance(right_val, list) else ([right_val] if right_val else [])

            # Columnas ARCA implícitas (ej: punto_venta + numero para Comprobante)
            for impl in campo.get("a_implícitos", []):
                impl_val = res_a.get(impl, "")
                if impl_val and impl_val not in right_cols:
                    right_cols.append(impl_val)

            if not left_cols and not right_cols:
                continue

            tol = tolerancia if (cmp == "approx" and sem == "importe") else 0.0

            rule = MappingRule(
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
            )
            rules.append(rule)

    return rules
