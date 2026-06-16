"""End-to-end pipeline tests across constructs, targets, and model routing."""

from __future__ import annotations

from pathlib import Path

from sas2databricks import Model, migrate
from sas2databricks.llm import CopilotProvider
from sas2databricks.llm.models import route

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_data_step_and_means(tmp_path):
    sas = (EXAMPLES / "sample2_data_step.sas").read_text()
    result = migrate(sas, target="pyspark", source_path="sample2")
    # DATA step assignments become withColumn / expr
    assert "withColumn" in result.code
    # PROC MEANS becomes a groupBy aggregation
    assert "groupBy" in result.code
    assert any(r.kind == "agg" for r in result.reports)


def test_format_and_macro_converted():
    sas = (EXAMPLES / "sample3_macro_report.sas").read_text()
    result = migrate(sas, target="pyspark", provider=CopilotProvider())
    kinds = {r.kind for r in result.reports}
    assert "format" in kinds
    assert "macro" in kinds
    # the macro body now converts deterministically to a parameterized function
    macro = next(s for s in result.program.steps if s.kind == "macro")
    assert macro.generated.startswith("def ")
    assert "spark.sql" in macro.generated
    assert not macro.prov.needs_review()


def test_auto_router_picks_opus_for_macros():
    assert route(Model.AUTO, "macro") == Model.OPUS_4_8
    assert route(Model.AUTO, "sql") == Model.CODEX
    assert route(Model.CODEX, "macro") == Model.CODEX  # explicit choice wins


def test_all_targets_emit_something():
    sas = (EXAMPLES / "sample1_proc_sql.sas").read_text()
    for target in ("pyspark", "sparksql", "dlt", "workflow", "validate"):
        result = migrate(sas, target=target)
        assert result.code.strip()
        assert result.filename


def test_report_markdown_contains_table():
    sas = (EXAMPLES / "sample1_proc_sql.sas").read_text()
    result = migrate(sas)
    md = result.report_markdown()
    assert "Migration report" in md
    assert "| Step |" in md
