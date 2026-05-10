"""
Modelos de datos del dominio de conciliación.

  - ColumnFingerprint: perfil estadístico de una columna (para detección semántica)
  - MappingRule:       relación N:M entre columnas de dos fuentes con operador
  - MappingProfile:    comportamiento completo de conciliación persistible

El modelo de mapeo abandona la restricción 1:1 y permite expresar relaciones como:
    sum([IVA 21%, IVA 10.5%]) ≈ [Total IVA]
    concat([Sucursal, Letra, Número]) = [Comprobante]
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# ── Vocabulario de operadores ─────────────────────────────────────────────────

OPERATION_LABELS: dict[str, str] = {
    "identity":  "Igual (1↔1)",
    "sum":       "Suma (Σ)",
    "concat":    "Concatenar",
    "subtract":  "Diferencia",
    "coalesce":  "Primer no vacío",
}

OPERATION_SYMBOLS: dict[str, str] = {
    "identity":  "=",
    "sum":       "+",
    "concat":    "·",
    "subtract":  "−",
    "coalesce":  "??",
}

COMPARISON_LABELS: dict[str, str] = {
    "exact":    "Exacto",
    "approx":   "Aproximado (±tolerancia)",
    "contains": "Contiene",
}

COMPARISON_SYMBOLS: dict[str, str] = {
    "exact":    "=",
    "approx":   "≈",
    "contains": "⊃",
}

# ── Tipos semánticos reconocidos ──────────────────────────────────────────────

SEMANTIC_TYPES: dict[str, str] = {
    "importe":     "💰 Importe ($)",
    "comprobante": "🔑 Nro. Comprobante",
    "fecha":       "📅 Fecha",
    "cuit":        "🪪 CUIT/CUIL",
    "texto":       "📝 Texto",
    "booleano":    "✓ Booleano",
}

SEMANTIC_ICONS: dict[str, str] = {
    "importe":     "💰",
    "comprobante": "🔑",
    "fecha":       "📅",
    "cuit":        "🪪",
    "texto":       "📝",
    "booleano":    "✓",
}


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class ColumnFingerprint:
    """Perfil estadístico de una columna para detección semántica automática."""
    name: str
    dtype_kind: str           # 'numeric', 'datetime', 'text', 'boolean'
    null_ratio: float         # 0.0 – 1.0
    cardinality_ratio: float  # valores únicos / total filas
    avg_length: float         # longitud media (texto) o cifras (numérico)
    min_val: float | None     # solo numéricos
    max_val: float | None     # solo numéricos
    sample_values: list       # hasta 5 valores representativos (str)
    semantic_hint: str = ""   # 'cuit', 'fecha', 'importe', 'comprobante', 'texto'

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ColumnFingerprint":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class MappingRule:
    """Relación semántica N:M entre columnas de dos fuentes.

    Modela desde equivalencias simples (1:1) hasta fórmulas compuestas:
        sum([IVA 21%, IVA 10.5%]) ≈ [Total IVA]
        concat([Suc, Letra, Nro]) = [Comprobante]
    """
    id: str
    label: str
    left_columns: list[str]       # columnas del Listado (Colppy)
    right_columns: list[str]      # columnas de ARCA
    operation: str = "identity"   # 'identity', 'sum', 'concat', 'subtract', 'coalesce'
    comparison: str = "approx"    # 'exact', 'approx', 'contains'
    tolerance: float = 0.07       # para comparison='approx'
    semantic_type: str = "texto"  # 'importe', 'comprobante', 'fecha', 'cuit', 'texto'
    required: bool = True
    confidence: float = 1.0
    affects_status: bool = True   # False → solo informativa, no afecta Conciliado
    l_campo: str = ""             # campo semántico canónico izquierdo
    a_campo: str = ""             # campo semántico canónico derecho

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MappingRule":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def formula_str(self) -> str:
        """Fórmula legible para preview en UI."""
        sep_l = f" {OPERATION_SYMBOLS.get(self.operation, '+')} "
        sep_r = f" {OPERATION_SYMBOLS.get(self.operation, '+')} "
        cmp   = COMPARISON_SYMBOLS.get(self.comparison, "≈")
        left  = sep_l.join(self.left_columns)  if self.left_columns  else "—"
        right = sep_r.join(self.right_columns) if self.right_columns else "—"
        return f"{left} {cmp} {right}"

    @property
    def is_multi(self) -> bool:
        return len(self.left_columns) > 1 or len(self.right_columns) > 1


@dataclass
class MappingProfile:
    """Comportamiento completo de conciliación persistible.

    Almacena reglas, fingerprints estructurales y aliases aprendidos.
    Permite sugerir automáticamente el perfil correcto para nuevos archivos.
    """
    id: str
    name: str
    rules: list[MappingRule] = field(default_factory=list)
    left_fingerprint: dict = field(default_factory=dict)   # {col: ColumnFingerprint.to_dict()}
    right_fingerprint: dict = field(default_factory=dict)
    aliases: dict = field(default_factory=dict)            # {canonical: [synonym, ...]}
    created_at: str = ""
    updated_at: str = ""
    use_count: int = 0
    match_score: float = 0.0   # similitud con archivos actuales (calculado en runtime)

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "name":             self.name,
            "rules":            [r.to_dict() for r in self.rules],
            "left_fingerprint": self.left_fingerprint,
            "right_fingerprint": self.right_fingerprint,
            "aliases":          self.aliases,
            "created_at":       self.created_at,
            "updated_at":       self.updated_at,
            "use_count":        self.use_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MappingProfile":
        rules = [MappingRule.from_dict(r) for r in d.get("rules", [])]
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            rules=rules,
            left_fingerprint=d.get("left_fingerprint", {}),
            right_fingerprint=d.get("right_fingerprint", {}),
            aliases=d.get("aliases", {}),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            use_count=d.get("use_count", 0),
        )
