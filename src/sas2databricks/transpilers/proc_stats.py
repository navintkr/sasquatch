"""PROC CORR / UNIVARIATE / REG / LOGISTIC / GLM / GENMOD -> stats & MLlib IR.

Descriptive PROCs (CORR, UNIVARIATE) lower to deterministic Spark helpers. Modelling
PROCs (REG, LOGISTIC, GLM) lower to a Spark MLlib estimator *scaffold* -- the structure
is deterministic, but model tuning / diagnostics should be reviewed.
"""

from __future__ import annotations

import re

from ..ir import ModelStep, StatStep
from ..parser import RawStep
from .base import prov_for

_DATA = re.compile(r"(?is)proc\s+\w+\s+data\s*=\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)")
_VAR = re.compile(r"(?is)\bvar\s+(.*?);")
_WITH = re.compile(r"(?is)\bwith\s+(.*?);")
_MODEL = re.compile(r"(?is)\bmodel\s+([A-Za-z_]\w*)\s*=\s*(.*?)(?:/\s*(.*?))?;")
_CLASS = re.compile(r"(?is)\bclass\s+(.*?);")
_DIST = re.compile(r"(?is)\b(?:dist|distribution|d)\s*=\s*([A-Za-z]+)")

_ESTIMATOR = {
    "reg": "LinearRegression",
    "glm": "GeneralizedLinearRegression",
    "genmod": "GeneralizedLinearRegression",
    "logistic": "LogisticRegression",
}

_GLM_FAMILY = {
    "normal": "gaussian",
    "gaussian": "gaussian",
    "binomial": "binomial",
    "poisson": "poisson",
    "gamma": "gamma",
}


def transpile(raw: RawStep):
    if raw.kind == "proc_model":
        return _model(raw)
    return _stat(raw)


def _stat(raw: RawStep) -> StatStep:
    prov = prov_for(raw, confidence=0.85)
    dm = _DATA.search(raw.text)
    source = dm.group(1) if dm else ""
    op = "corr" if raw.proc.lower() == "corr" else "univariate"
    cols = _names(_VAR.search(raw.text))
    with_cols = _names(_WITH.search(raw.text))
    prov.notes.append(
        f"PROC {raw.proc.upper()} lowered to a deterministic Spark {op} helper; "
        "verify which columns/options are required"
    )
    return StatStep(
        name=f"{source or 'data'}_{op}",
        prov=prov,
        inputs=[source] if source else [],
        source=source,
        op=op,
        columns=cols,
        with_columns=with_cols,
    )


def _model(raw: RawStep) -> ModelStep:
    prov = prov_for(raw, confidence=0.7)
    dm = _DATA.search(raw.text)
    source = dm.group(1) if dm else ""
    estimator = _ESTIMATOR.get(raw.proc.lower(), "LinearRegression")

    label = ""
    features: list[str] = []
    mm = _MODEL.search(raw.text)
    if mm:
        label = mm.group(1)
        features = [w for w in re.split(r"[\s,]+", mm.group(2).strip()) if w]

    family = ""
    if estimator == "GeneralizedLinearRegression":
        dist = _DIST.search(raw.text)
        family = _GLM_FAMILY.get((dist.group(1).lower() if dist else ""), "gaussian")

    prov.notes.append(
        f"PROC {raw.proc.upper()} lowered to a Spark MLlib {estimator} scaffold "
        "(VectorAssembler + estimator); review feature engineering, regularization, "
        "and CLASS/dummy encoding"
    )
    if _CLASS.search(raw.text):
        prov.notes.append("CLASS variables present -> add StringIndexer/OneHotEncoder")

    return ModelStep(
        name=f"{source or 'data'}_{raw.proc.lower()}_model",
        prov=prov,
        inputs=[source] if source else [],
        source=source,
        estimator=estimator,
        family=family,
        label=label,
        features=features,
    )


def _names(m: re.Match | None) -> list[str]:
    if not m:
        return []
    return [w for w in re.split(r"[\s,]+", m.group(1).strip()) if w]
