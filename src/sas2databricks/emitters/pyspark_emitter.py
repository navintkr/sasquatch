"""Emit a Databricks PySpark notebook (.py, Databricks `# COMMAND ----------` cells)."""

from __future__ import annotations

from ..ir import (
    AggStep,
    DataStep,
    FormatStep,
    MacroDef,
    ModelStep,
    Program,
    RawStep,
    ReportStep,
    SqlStep,
    StatStep,
    Step,
)
from .base import header_comment, safe_name, step_banner

_AGG_FUNC = {
    "avg": "F.avg",
    "sum": "F.sum",
    "count": "F.count",
    "min": "F.min",
    "max": "F.max",
    "stddev": "F.stddev",
    "variance": "F.variance",
    "percentile_approx_25": "lambda c: F.percentile_approx(c, 0.25)",
    "percentile_approx_50": "lambda c: F.percentile_approx(c, 0.50)",
    "percentile_approx_75": "lambda c: F.percentile_approx(c, 0.75)",
}


def emit(prog: Program, *, source_path: str = "") -> str:
    cells: list[str] = []
    cells.append("# Databricks notebook source\n" + header_comment(prog, "pyspark", source_path))
    cells.append("from pyspark.sql import functions as F, Window")

    if prog.macro_vars:
        kv = "\n".join(f"{k} = {v!r}" for k, v in prog.macro_vars.items())
        cells.append("# SAS macro variables (%let) -> Python constants\n" + kv)

    for step in prog.steps:
        cells.append(step_banner(step) + "\n" + _emit_step(step))

    return ("\n\n# COMMAND ----------\n\n").join(cells) + "\n"


def _emit_step(step: Step) -> str:
    code = getattr(step, "llm_code", None)
    if isinstance(step, RawStep):
        return code or _raw(step)
    if isinstance(step, SqlStep):
        return _sql(step)
    if isinstance(step, DataStep):
        # keep the deterministic translation unless a provider actually fulfilled it
        if code and getattr(step, "llm_fulfilled", False):
            return code
        return _data(step)
    if isinstance(step, AggStep):
        return _agg(step)
    if isinstance(step, FormatStep):
        return _format(step)
    if isinstance(step, ReportStep):
        return _report(step)
    if isinstance(step, StatStep):
        return _stat(step)
    if isinstance(step, ModelStep):
        return _model(step)
    if isinstance(step, MacroDef):
        return code or _macro(step)
    return f"# unsupported step kind: {step.kind}"


def _sql(step: SqlStep) -> str:
    var = safe_name(step.name)
    sql = step.sql.replace('"""', '\\"\\"\\"')
    return f'{var} = spark.sql("""\n{sql}\n""")\n{var}.createOrReplaceTempView("{var}")'


def _data(step: DataStep) -> str:
    var = safe_name(step.name)
    lines: list[str]
    if step.merge:
        parts = [safe_name(m) for m in step.merge]
        on = "[" + ", ".join(f'"{b}"' for b in step.by) + "]" if step.by else "None"
        lines = [f"{var} = {parts[0]}"]
        for other in parts[1:]:
            how = '"full"' if step.by else '"cross"'
            lines.append(f"{var} = {var}.join({other}, on={on}, how={how})")
    else:
        src = safe_name(step.source) if step.source else "spark.range(0)"
        lines = [f"{var} = {src}"]
    if step.where:
        lines.append(f'{var} = {var}.where("{step.where}")')
    if step.needs_row_order:
        lines.append(
            f'{var} = {var}.withColumn("_row_id", F.monotonically_increasing_id())'
        )
    for a in step.assignments:
        if a.condition:
            existing = (
                f'F.col("{a.target}") if "{a.target}" in {var}.columns else F.lit(None)'
            )
            lines.append(
                f'{var} = {var}.withColumn("{a.target}", '
                f'F.when(F.expr("{a.condition}"), F.expr("{a.expr}"))'
                f'.otherwise({existing}))'
            )
        else:
            lines.append(f'{var} = {var}.withColumn("{a.target}", F.expr("{a.expr}"))')
    for old, new in step.rename.items():
        lines.append(f'{var} = {var}.withColumnRenamed("{old}", "{new}")')
    if step.needs_row_order and not step.keep:
        lines.append(f'{var} = {var}.drop("_row_id")')
    if step.keep:
        cols = ", ".join(f'"{c}"' for c in step.keep)
        lines.append(f"{var} = {var}.select({cols})")
    if step.drop:
        cols = ", ".join(f'"{c}"' for c in step.drop)
        lines.append(f"{var} = {var}.drop({cols})")
    lines.append(f'{var}.createOrReplaceTempView("{var}")')
    return "\n".join(lines)


def _agg(step: AggStep) -> str:
    var = safe_name(step.name)
    src = safe_name(step.source) if step.source else "df"
    aggs = []
    for m in step.measures:
        fn = _AGG_FUNC.get(m.func, f"F.{m.func}")
        col = "F.lit(1)" if m.column == "*" else f'"{m.column}"'
        if fn.startswith("lambda"):
            aggs.append(f'({fn})({col}).alias("{m.alias}")')
        elif m.column == "*":
            aggs.append(f'{fn}("*").alias("{m.alias}")' if m.func == "count"
                        else f'{fn}(F.lit(1)).alias("{m.alias}")')
        else:
            aggs.append(f'{fn}("{m.column}").alias("{m.alias}")')
    agg_str = ",\n    ".join(aggs)
    if step.group_by:
        gb = ", ".join(f'"{g}"' for g in step.group_by)
        body = f"{src}.groupBy({gb}).agg(\n    {agg_str}\n)"
    else:
        body = f"{src}.agg(\n    {agg_str}\n)"
    return f'{var} = {body}\n{var}.createOrReplaceTempView("{var}")'


def _format(step: FormatStep) -> str:
    var = safe_name(step.name)
    items = ", ".join(f"{k!r}: {v!r}" for k, v in step.mapping.items())
    default = repr(step.default) if step.default is not None else "None"
    return (
        f"{var} = {{{items}}}  # SAS format {step.format_name}.\n"
        f"def apply_{var}(col):\n"
        f"    mapping = F.create_map([F.lit(x) for kv in {var}.items() for x in kv])\n"
        f"    return F.coalesce(mapping[col], F.lit({default}))"
    )


def _report(step: ReportStep) -> str:
    var = safe_name(step.name)
    src = safe_name(step.source) if step.source else "df"
    sel = (", ".join(f'"{c}"' for c in step.columns)) if step.columns else '"*"'
    lines = [f"{var} = {src}.select({sel})"]
    if step.group_by:
        gb = ", ".join(f'"{g}"' for g in step.group_by)
        lines.append(f"{var} = {var}.orderBy({gb})")
    if step.title:
        lines.append(f"print({step.title!r})")
    lines.append(f"display({var})")
    return "\n".join(lines)


def _macro(step: MacroDef) -> str:
    if step.generated:
        return step.generated
    params = ", ".join(step.params)
    body = "\n".join("    # " + line for line in step.body.splitlines())
    return (
        f"def {safe_name(step.name)}({params}):\n"
        f"    # MANUAL REVIEW: convert SAS macro body to parameterized PySpark.\n"
        f"{body}\n    pass"
    )


def _raw(step: RawStep) -> str:
    body = "\n".join("# " + line for line in step.raw.splitlines())
    return f"# MANUAL REVIEW: unsupported SAS construct\n{body}"


def _stat(step: StatStep) -> str:
    var = safe_name(step.name)
    src = safe_name(step.source) if step.source else "df"
    if step.op == "corr":
        cols = step.columns or []
        if step.with_columns:
            pairs = [(a, b) for a in cols for b in step.with_columns]
        else:
            pairs = [(cols[i], cols[j]) for i in range(len(cols)) for j in range(i + 1, len(cols))]
        rows = ", ".join(
            f'({a!r}, {b!r}, {src}.stat.corr({a!r}, {b!r}))' for a, b in pairs
        ) or "# no VAR columns parsed -> add column pairs"
        return (
            f"# PROC CORR -> pairwise Pearson correlations\n"
            f"{var} = spark.createDataFrame([{rows}], ['x', 'y', 'corr'])\n"
            f"display({var})"
        )
    cols = ", ".join(f'"{c}"' for c in step.columns) if step.columns else ""
    target = f"{src}.select({cols})" if cols else src
    return (
        f"# PROC UNIVARIATE -> descriptive statistics\n"
        f"{var} = {target}.summary()  # count/mean/stddev/min/quartiles/max\n"
        f"display({var})"
    )


def _model(step: ModelStep) -> str:
    var = safe_name(step.name)
    src = safe_name(step.source) if step.source else "df"
    features = step.features or ["feature1", "feature2"]
    feat_list = ", ".join(f'"{f}"' for f in features)
    label = step.label or "label"
    if step.estimator == "LogisticRegression":
        import_line = "from pyspark.ml.classification import LogisticRegression"
        family = ""
    elif step.estimator == "GeneralizedLinearRegression":
        import_line = "from pyspark.ml.regression import GeneralizedLinearRegression"
        family = f', family="{step.family or "gaussian"}"'
    else:
        import_line = "from pyspark.ml.regression import LinearRegression"
        family = ""
    return (
        "# MANUAL REVIEW: MLlib model scaffold -- verify features, encoding, regularization\n"
        "from pyspark.ml.feature import VectorAssembler\n"
        f"{import_line}\n"
        f"_assembler = VectorAssembler(inputCols=[{feat_list}], outputCol='features')\n"
        f"_train = _assembler.transform({src})\n"
        f'{var} = {step.estimator}(featuresCol="features", labelCol="{label}"{family})'
        ".fit(_train)\n"
        f"print({var}.coefficients, {var}.intercept)"
    )
