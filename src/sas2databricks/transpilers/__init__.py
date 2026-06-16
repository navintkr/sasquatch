"""Transpiler dispatch: RawStep → IR step(s)."""

from __future__ import annotations

from ..ir import Engine, Step
from ..ir import RawStep as IRRawStep
from ..parser import RawStep
from . import data_step, formats, macro, proc_means, proc_report, proc_sql, proc_stats
from .base import prov_for


def dispatch(raw: RawStep) -> list[Step]:
    """Route a raw SAS step to the right transpiler. Always returns a list."""
    kind = raw.kind
    if kind == "proc_sql":
        return [proc_sql.transpile(raw)]
    if kind == "data":
        return [data_step.transpile(raw)]
    if kind == "proc_means":
        return [proc_means.transpile(raw)]
    if kind == "proc_format":
        return list(formats.transpile(raw))
    if kind == "proc_report":
        return [proc_report.transpile(raw)]
    if kind in ("proc_stat", "proc_model"):
        return [proc_stats.transpile(raw)]
    if kind == "macro":
        return [macro.transpile(raw)]

    # unknown PROC or statement → preserve for LLM / manual review
    prov = prov_for(raw, engine=Engine.MANUAL, confidence=0.3)
    label = raw.proc or kind
    prov.notes.append(f"no deterministic transpiler for `{label}` — escalate to LLM / review")
    return [IRRawStep(name=f"{label}_raw", prov=prov, raw=raw.text)]


__all__ = ["dispatch"]
