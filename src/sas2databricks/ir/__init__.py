"""Intermediate representation (IR) for SAS programs.

The IR is engine-agnostic: transpilers build it from SAS source and emitters turn it
into PySpark / Spark SQL / DLT / Workflows. Every node carries :class:`Provenance` so
generated code can be traced back to its SAS origin and reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Engine(str, Enum):
    """How an IR node was produced."""

    RULE = "rule"  # deterministic transpiler
    LLM = "llm"  # produced/assisted by the language model
    MANUAL = "manual"  # stub left for a human


@dataclass
class Provenance:
    """Traceability metadata attached to every IR node."""

    source: str  # original SAS snippet
    start_line: int = 0
    end_line: int = 0
    engine: Engine = Engine.RULE
    confidence: float = 1.0  # 0..1
    notes: list[str] = field(default_factory=list)

    def needs_review(self, threshold: float = 0.8) -> bool:
        return self.engine == Engine.MANUAL or self.confidence < threshold


@dataclass
class Step:
    """Base class for an IR step."""

    name: str  # target dataset / view name (output)
    prov: Provenance
    inputs: list[str] = field(default_factory=list)  # upstream dataset names

    kind: str = "step"


@dataclass
class SqlStep(Step):
    """A PROC SQL query, already translated to the Spark SQL dialect."""

    sql: str = ""
    creates_table: bool = True
    kind: str = "sql"


@dataclass
class Assignment:
    """A single column assignment inside a DATA step."""

    target: str
    expr: str  # already translated to a Spark SQL expression
    condition: str | None = None  # IF ... THEN target = expr


@dataclass
class DataStep(Step):
    """A SAS DATA step lowered to a sequence of column/filter operations."""

    source: str = ""  # SET / input dataset
    assignments: list[Assignment] = field(default_factory=list)
    where: str | None = None
    keep: list[str] = field(default_factory=list)
    drop: list[str] = field(default_factory=list)
    rename: dict[str, str] = field(default_factory=dict)
    by: list[str] = field(default_factory=list)
    merge: list[str] = field(default_factory=list)  # MERGE a b; -> join inputs
    retain: list[str] = field(default_factory=list)  # RETAIN cols (carried across rows)
    needs_row_order: bool = False  # uses LAG/DIF/FIRST./LAST./RETAIN -> needs an order col
    kind: str = "data"


@dataclass
class Aggregation:
    """One output measure for an aggregation step."""

    func: str  # mean, sum, n, min, max, std, ...
    column: str
    alias: str


@dataclass
class AggStep(Step):
    """PROC MEANS / SUMMARY / FREQ / TABULATE → group-by aggregation."""

    source: str = ""
    group_by: list[str] = field(default_factory=list)
    measures: list[Aggregation] = field(default_factory=list)
    kind: str = "agg"


@dataclass
class FormatStep(Step):
    """PROC FORMAT VALUE clause → a named mapping (value → label)."""

    format_name: str = ""
    mapping: dict[str, str] = field(default_factory=dict)
    default: str | None = None
    kind: str = "format"


@dataclass
class ReportStep(Step):
    """PROC REPORT / PRINT → projection + display."""

    source: str = ""
    columns: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    title: str | None = None
    kind: str = "report"


@dataclass
class MacroDef(Step):
    """A %MACRO definition captured for parameterization."""

    params: list[str] = field(default_factory=list)
    body: str = ""
    generated: str = ""  # deterministically converted body (target code), when available
    kind: str = "macro"


@dataclass
class ModelStep(Step):
    """PROC REG / LOGISTIC / GLM -> Spark MLlib estimator scaffold."""

    source: str = ""
    estimator: str = ""  # LinearRegression | LogisticRegression | GeneralizedLinearRegression
    family: str = ""  # for GLM (gaussian/binomial/poisson/...)
    label: str = ""  # response / dependent variable
    features: list[str] = field(default_factory=list)  # predictors
    kind: str = "model"


@dataclass
class StatStep(Step):
    """PROC CORR / UNIVARIATE -> descriptive-statistics helper."""

    source: str = ""
    op: str = ""  # corr | univariate
    columns: list[str] = field(default_factory=list)
    with_columns: list[str] = field(default_factory=list)  # PROC CORR `with` vars
    kind: str = "stat"


@dataclass
class RawStep(Step):
    """A step we could not lower — preserved verbatim for manual review / LLM."""

    raw: str = ""
    kind: str = "raw"


@dataclass
class Program:
    """A whole SAS program as an ordered list of IR steps."""

    steps: list[Step] = field(default_factory=list)
    macro_vars: dict[str, str] = field(default_factory=dict)
    formats: dict[str, FormatStep] = field(default_factory=dict)

    def review_items(self, threshold: float = 0.8) -> list[Step]:
        return [s for s in self.steps if s.prov.needs_review(threshold)]
