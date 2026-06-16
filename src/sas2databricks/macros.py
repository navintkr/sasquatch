"""Deterministic %MACRO body conversion.

Runs the normal parse -> transpile -> Spark SQL pipeline over a macro body (with
``&param`` references rewritten to Python ``{param}`` f-string slots) and wraps the
result in a runnable parameterized Python function. Kept in its own module (rather than
in ``transpilers/macro.py``) so it can import the emitters without creating an import
cycle.
"""

from __future__ import annotations

import re

from .emitters import sparksql_emitter
from .ir import Engine, MacroDef
from .parser import parse
from .parser.lexer import _parse_macro_params  # noqa: PLC2701 - internal reuse
from .transpilers import dispatch

_MACRO_HEADER = re.compile(r"(?is)%macro\s+([A-Za-z_]\w*)\s*(?:\((.*?)\))?\s*;(.*)%mend", re.DOTALL)


def convert_macro(step: MacroDef) -> None:
    """Populate ``step.generated`` with a parameterized Python function for the body.

    Mutates ``step`` in place: sets ``generated`` and adjusts provenance confidence /
    notes based on how cleanly the body lowered.
    """
    header = _MACRO_HEADER.search(step.prov.source or "")
    body = header.group(3).strip() if header else step.body
    params = _parse_macro_params(header.group(2) or "") if header else []
    param_names = [p for p, _ in params]

    # rewrite &param / &param. -> {param} for f-string interpolation
    templated = body
    for name in param_names:
        templated = re.sub(rf"&{name}\.?", "{" + name + "}", templated, flags=re.IGNORECASE)

    parsed = parse(templated, expand_macros=False, expand_calls=False)
    sql_stmts: list[str] = []
    clean = True
    for raw in parsed.steps:
        for sub in dispatch(raw):
            stmt = sparksql_emitter._emit_step(sub)
            if not stmt or stmt.lstrip().startswith("--"):
                clean = False
            sql_stmts.append(stmt)

    sig = ", ".join(p if not d else f"{p}={d!r}" for p, d in params) if params else ""
    lines = [f"def {step.name}({sig}):"]
    lines.append(f'    """Converted from SAS macro %{step.name}."""')
    if sql_stmts:
        for stmt in sql_stmts:
            esc = stmt.replace("\\", "\\\\")
            lines.append(f'    spark.sql(f"""{esc}""")')
    else:
        lines.append("    pass  # MANUAL REVIEW: empty macro body")

    step.generated = "\n".join(lines)

    if clean and sql_stmts:
        step.prov.engine = Engine.RULE
        step.prov.confidence = max(step.prov.confidence, 0.85)
        step.prov.notes.append(
            f"macro `{step.name}` body converted deterministically to a parameterized "
            "Python function (Spark SQL); verify %IF/%DO control flow if present"
        )
    else:
        step.prov.notes.append(
            f"macro `{step.name}` body partially converted; review generated function"
        )
