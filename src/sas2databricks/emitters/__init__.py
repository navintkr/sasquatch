"""IR → Databricks target emitters."""

from __future__ import annotations

from ..ir import Program
from . import (
    dlt_emitter,
    pyspark_emitter,
    sparksql_emitter,
    validation_emitter,
    workflow_emitter,
)

TARGETS = {
    "pyspark": ("notebook.py", pyspark_emitter.emit),
    "sparksql": ("queries.sql", sparksql_emitter.emit),
    "dlt": ("dlt_pipeline.py", dlt_emitter.emit),
    "workflow": ("job.json", workflow_emitter.emit),
    "validate": ("validation.py", validation_emitter.emit),
}


def emit(prog: Program, target: str, *, source_path: str = "", **options) -> tuple[str, str]:
    """Return (filename, content) for the given target.

    ``options`` may include ``catalog``/``schema`` (DLT) or ``ref_base`` (validate);
    they are forwarded only to emitters that accept them.
    """
    if target not in TARGETS:
        raise ValueError(f"Unknown target '{target}'. Choose from: {', '.join(TARGETS)}")
    filename, fn = TARGETS[target]
    kwargs = {"source_path": source_path}
    if target == "dlt":
        for key in ("catalog", "schema"):
            if options.get(key):
                kwargs[key] = options[key]
    if target == "validate" and options.get("ref_base"):
        kwargs["ref_base"] = options["ref_base"]
    return filename, fn(prog, **kwargs)


__all__ = ["emit", "TARGETS"]
