"""Tests for the roadmap features: DATA-step depth, macros, stats/models, validation."""

from __future__ import annotations

from pathlib import Path

from sas2databricks import migrate
from sas2databricks.llm import AnthropicProvider, AzureOpenAIProvider, provider_from_env

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _code(sas: str, target: str = "pyspark", **kw) -> str:
    return migrate(sas, target=target, **kw).code


# ---- v0.2 DATA step depth -------------------------------------------------------------


def test_merge_lowers_to_join():
    sas = "data out; merge a b; by id; run;"
    code = _code(sas)
    assert ".join(" in code
    sql = _code(sas, target="sparksql")
    assert "FULL JOIN" in sql and "USING (id)" in sql


def test_retain_becomes_cumulative_window():
    sas = "data out; set s; by region; retain total 0; total = total + revenue; run;"
    code = _code(sas, target="sparksql")
    assert "sum(revenue)" in code
    assert "UNBOUNDED PRECEDING" in code
    assert "_row_id" in code


def test_lag_and_dif_become_window_functions():
    sas = "data out; set s; prev = lag(revenue); delta = dif(revenue); run;"
    code = _code(sas, target="sparksql")
    assert "lag(revenue, 1) OVER" in code
    assert "_row_id" in code


def test_first_last_become_row_number_flags():
    sas = (
        "data out; set s; by region; "
        "if first.region then a = 1; if last.region then b = 1; run;"
    )
    code = _code(sas, target="sparksql")
    assert "row_number() OVER" in code
    assert "ORDER BY _row_id DESC" in code  # last.


def test_advanced_example_marks_review():
    sas = (EXAMPLES / "sample4_data_advanced.sas").read_text()
    result = migrate(sas, target="pyspark")
    # merge + order-sensitive logic -> at least one step flagged for review
    assert result.review_count >= 1
    assert "monotonically_increasing_id" in result.code


# ---- v0.3 macro facility --------------------------------------------------------------


def test_macro_invocation_is_expanded():
    sas = (EXAMPLES / "sample5_stats_macro.sas").read_text()
    result = migrate(sas, target="sparksql")
    # the %region_rollup call expands to a PROC MEANS -> group-by aggregation
    assert any(r.kind == "agg" for r in result.reports)


def test_macro_definition_becomes_function():
    sas = "%macro roll(ds=, v=); proc means data=&ds sum; var &v; run; %mend;"
    result = migrate(sas)
    macro = next(s for s in result.program.steps if s.kind == "macro")
    assert macro.generated.startswith("def roll(")
    assert "{ds}" in macro.generated or "{v}" in macro.generated


# ---- v0.5 statistics & models ---------------------------------------------------------


def test_proc_corr_emits_correlations():
    sas = "proc corr data=d; var x y z; run;"
    code = _code(sas, target="sparksql")
    assert "corr(" in code


def test_proc_reg_emits_mllib_scaffold():
    sas = "proc reg data=d; model y = a b c; run;"
    code = _code(sas)
    assert "LinearRegression" in code
    assert "VectorAssembler" in code


def test_proc_logistic_uses_classification():
    sas = "proc logistic data=d; model churn = a b; run;"
    code = _code(sas)
    assert "LogisticRegression" in code
    assert "pyspark.ml.classification" in code


# ---- v0.6 orchestration & validation --------------------------------------------------


def test_validation_target_builds_parity_notebook():
    sas = (EXAMPLES / "sample2_data_step.sas").read_text()
    code = _code(sas, target="validate")
    assert "compare_output(" in code
    assert "row_count" in code


def test_dlt_expectations_from_where():
    sas = "data out; set s; where region <> 'TEST'; x = a + b; run;"
    code = _code(sas, target="dlt")
    assert "@dlt.expect_or_drop" in code


def test_dlt_unity_catalog_header():
    sas = (EXAMPLES / "sample1_proc_sql.sas").read_text()
    code = migrate(sas, target="dlt", catalog="main", schema="sales").code
    assert "Unity Catalog target: main.sales" in code


# ---- cross-cutting: HTML report + providers -------------------------------------------


def test_html_report_renders():
    sas = (EXAMPLES / "sample2_data_step.sas").read_text()
    html = migrate(sas).report_html()
    assert "<table" in html and "migration report" in html.lower()


def test_provider_from_env_returns_none_without_keys(monkeypatch):
    for var in (
        "ANTHROPIC_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    assert provider_from_env() is None


def test_real_providers_are_constructable():
    # construction must not require the SDK or network (lazy import on convert)
    assert AnthropicProvider().model.startswith("claude")
    assert AzureOpenAIProvider(deployment="gpt-4o").deployment == "gpt-4o"
