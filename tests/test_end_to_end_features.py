"""Tests for the end-to-end feature sweep: arrays/DO loops, macro control flow,
the Databricks Asset Bundle target/assembler, and the project report index.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml
from click.testing import CliRunner

from sas2databricks import migrate
from sas2databricks.cli import main
from sas2databricks.emitters import TARGETS, emit
from sas2databricks.emitters.bundle_emitter import project_bundle
from sas2databricks.report_index import IndexEntry, build_index_html, build_index_markdown

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _macro(sas: str) -> str:
    result = migrate(sas)
    return next(s for s in result.program.steps if s.kind == "macro").generated


# ---- C2: arrays + iterative DO loops --------------------------------------------------


def test_array_with_do_loop_unrolls():
    sas = (EXAMPLES / "sample6_arrays_doloop.sas").read_text()
    result = migrate(sas, target="pyspark")
    code = result.code
    for col in ("s1", "s2", "s3"):
        assert f'withColumn("{col}"' in code
    assert "q1 * 10" in code and "q3 * 10" in code
    # the loop is fully unrolled -- no residual DO/END control flow
    assert "do i" not in code.lower()


def test_array_unroll_lowers_confidence_and_notes():
    sas = "data out; set inp; array q{3} q1-q3; do i = 1 to 3; q{i} = q{i} + 1; end; run;"
    result = migrate(sas, target="pyspark")
    step = next(s for s in result.program.steps if s.kind == "data")
    assert step.prov.confidence <= 0.85
    assert any("array" in n.lower() for n in step.prov.notes)


def test_consecutive_assignments_all_emitted():
    # regression: the _ASSIGN lookbehind fix must keep every ;-separated assignment
    sas = "data out; set s; a = 1; b = 2; c = 3; run;"
    code = migrate(sas, target="pyspark").code
    for col in ("a", "b", "c"):
        assert f'withColumn("{col}"' in code


# ---- C1: macro %IF/%DO control flow ---------------------------------------------------


def test_macro_if_else_lowers_to_python():
    sas = (
        "%macro rep(level=); "
        "%if &level = 1 %then %do; proc means data=sales sum; var revenue; run; %end; "
        "%else %do; proc means data=sales mean; var revenue; run; %end; %mend;"
    )
    gen = _macro(sas)
    assert "if (level == 1):" in gen
    assert "else:" in gen
    assert gen.count("spark.sql") == 2
    ast.parse(gen)  # must be valid Python


def test_macro_elif_chain_and_text_quoting():
    sas = (EXAMPLES / "sample7_macro_controlflow.sas").read_text()
    gen = _macro(sas)
    assert 'if (grain == "region"):' in gen  # bare word -> quoted text
    assert 'elif (grain == "product"):' in gen
    assert "else:" in gen
    ast.parse(gen)


def test_macro_do_loop_becomes_for():
    sas = "%macro g(n=); %do i = 1 %to &n; proc means data=s sum; var v; run; %end; %mend;"
    gen = _macro(sas)
    assert "for i in range(1, n + 1):" in gen
    ast.parse(gen)


def test_macro_descending_do_loop_counts_down():
    sas = "%macro g(); %do i = 5 %to 1 %by -1; proc means data=s sum; var v; run; %end; %mend;"
    gen = _macro(sas)
    # inclusive SAS bounds, descending -> range(5, 1 - 1, -1) == 5,4,3,2,1
    assert "for i in range(5, 1 - 1, -1):" in gen
    assert list(range(5, 1 - 1, -1)) == [5, 4, 3, 2, 1]
    ast.parse(gen)


def test_macro_without_control_flow_unchanged():
    sas = "%macro roll(ds=, v=); proc means data=&ds sum; var &v; run; %mend;"
    gen = _macro(sas)
    assert gen.startswith("def roll(")
    assert "if " not in gen and "for " not in gen
    ast.parse(gen)


# ---- A1: bundle emitter (single program) ----------------------------------------------


def test_bundle_target_registered_and_emits_yaml():
    assert "bundle" in TARGETS
    sas = (EXAMPLES / "sample1_proc_sql.sas").read_text()
    filename, content = emit(migrate(sas, source_path="x.sas").program, "bundle",
                             source_path="x.sas")
    assert filename == "databricks.yml"
    doc = yaml.safe_load(content)
    assert doc["bundle"]["name"]
    assert set(doc["targets"]) == {"dev", "prod"}
    assert doc["targets"]["dev"]["default"] is True


def test_bundle_wires_step_dependencies():
    sas = (
        "data work.enriched; set raw.sales; margin = revenue - cost; run;\n"
        "proc sql; create table work.summary as "
        "select region, sum(margin) as total from work.enriched group by region; quit;"
    )
    _, content = emit(migrate(sas).program, "bundle")
    job = next(iter(yaml.safe_load(content)["resources"]["jobs"].values()))
    by_key = {t["task_key"]: t for t in job["tasks"]}
    assert by_key["work_summary"]["depends_on"] == [{"task_key": "work_enriched"}]


# ---- A2: project bundle assembler -----------------------------------------------------


def test_project_bundle_one_task_per_file():
    content = project_bundle("myproj", ["sample1", "sample2"], src_dir="src")
    doc = yaml.safe_load(content)
    job = next(iter(doc["resources"]["jobs"].values()))
    paths = {t["notebook_task"]["notebook_path"] for t in job["tasks"]}
    assert paths == {"src/sample1", "src/sample2"}


def test_migrate_bundle_cli_layout(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.sas").write_text("data out; set s; x = a + b; run;")
    (project / "b.sas").write_text("proc sql; create table t as select * from s; quit;")
    out = tmp_path / "bundle_out"

    runner = CliRunner()
    res = runner.invoke(
        main, ["migrate", str(project), "-t", "pyspark", "--bundle", "--html", "--out", str(out)]
    )
    assert res.exit_code == 0, res.output
    assert (out / "databricks.yml").exists()
    assert (out / "src" / "a.py").exists() and (out / "src" / "b.py").exists()
    assert (out / "reports" / "index.md").exists()
    assert (out / "reports" / "index.html").exists()
    doc = yaml.safe_load((out / "databricks.yml").read_text())
    assert len(next(iter(doc["resources"]["jobs"].values()))["tasks"]) == 2


def test_migrate_bundle_rejects_non_notebook_target(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.sas").write_text("data out; set s; x = a + b; run;")
    runner = CliRunner()
    res = runner.invoke(main, ["migrate", str(project), "-t", "workflow", "--bundle"])
    assert res.exit_code != 0
    assert "notebook target" in res.output


# ---- A3: project report index ---------------------------------------------------------


def test_migrate_flat_writes_project_index(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.sas").write_text("data out; set s; x = a + b; run;")
    out = tmp_path / "flat_out"
    runner = CliRunner()
    res = runner.invoke(main, ["migrate", str(project), "-t", "pyspark", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert (out / "index.md").exists()


# ---- one-command happy path: --target all ---------------------------------------------


def test_migrate_defaults_to_all_targets(tmp_path):
    """`s2db migrate <proj>` with no -t emits PySpark + Spark SQL + DLT plus a combined index."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.sas").write_text("proc sql; create table t as select * from s; quit;")
    out = tmp_path / "out_all"
    runner = CliRunner()
    res = runner.invoke(main, ["migrate", str(project), "--out", str(out)])
    assert res.exit_code == 0, res.output

    assert (out / "pyspark" / "a_notebook.py").exists()
    assert (out / "sparksql" / "a_queries.sql").exists()
    assert (out / "dlt" / "a_dlt_pipeline.py").exists()
    assert (out / "pyspark" / "index.md").exists()

    combined = (out / "index.md").read_text(encoding="utf-8")
    assert "all targets" in combined.lower()
    for tgt in ("pyspark", "sparksql", "dlt"):
        assert tgt in combined
        assert f"{tgt}/index.md" in combined


def test_migrate_all_with_bundle_and_html_per_target(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.sas").write_text("data out; set s; x = a + b; run;")
    out = tmp_path / "out_all_bundle"
    runner = CliRunner()
    res = runner.invoke(
        main, ["migrate", str(project), "-t", "all", "--bundle", "--html", "--out", str(out)]
    )
    assert res.exit_code == 0, res.output

    for tgt in ("pyspark", "sparksql", "dlt"):
        assert (out / tgt / "databricks.yml").exists()
        assert (out / tgt / "reports" / "index.md").exists()
    # combined index (both formats) links the per-target bundle report indexes
    assert (out / "index.md").exists()
    assert (out / "index.html").exists()
    combined = (out / "index.md").read_text(encoding="utf-8")
    assert "pyspark/reports/index.md" in combined


def test_same_stem_files_do_not_overwrite(tmp_path):
    """Two ``load.sas`` files in different subdirs must produce distinct outputs."""
    project = tmp_path / "proj"
    (project / "etl").mkdir(parents=True)
    (project / "staging").mkdir(parents=True)
    (project / "etl" / "load.sas").write_text("data out; set s; x = a + b; run;")
    (project / "staging" / "load.sas").write_text("data out; set s; y = a - b; run;")
    out = tmp_path / "flat_out"

    runner = CliRunner()
    res = runner.invoke(main, ["migrate", str(project), "-t", "pyspark", "--out", str(out)])
    assert res.exit_code == 0, res.output

    # both notebooks survive -- the second is disambiguated, not overwritten
    notebooks = sorted(p.name for p in out.glob("*_notebook.py"))
    assert notebooks == ["load_2_notebook.py", "load_notebook.py"]
    assert "a + b" in (out / "load_notebook.py").read_text()
    assert "a - b" in (out / "load_2_notebook.py").read_text()

    # the index lists both, parent-qualified so they are distinguishable
    index = (out / "index.md").read_text()
    assert "etl/load.sas" in index and "staging/load.sas" in index


def test_bundle_same_stem_files_do_not_overwrite(tmp_path):
    project = tmp_path / "proj"
    (project / "etl").mkdir(parents=True)
    (project / "staging").mkdir(parents=True)
    (project / "etl" / "load.sas").write_text("data out; set s; x = a + b; run;")
    (project / "staging" / "load.sas").write_text("data out; set s; y = a - b; run;")
    out = tmp_path / "bundle_out"

    runner = CliRunner()
    res = runner.invoke(
        main, ["migrate", str(project), "-t", "pyspark", "--bundle", "--out", str(out)]
    )
    assert res.exit_code == 0, res.output
    assert (out / "src" / "load.py").exists()
    assert (out / "src" / "load_2.py").exists()
    doc = yaml.safe_load((out / "databricks.yml").read_text())
    job = next(iter(doc["resources"]["jobs"].values()))
    paths = {t["notebook_task"]["notebook_path"] for t in job["tasks"]}
    assert paths == {"src/load", "src/load_2"}


def test_report_index_totals_and_links():
    r1 = migrate("data out; set s; x = a + b; run;", source_path="a.sas")
    r2 = migrate((EXAMPLES / "sample4_data_advanced.sas").read_text(), source_path="b.sas")
    entries = [
        IndexEntry("a.sas", "a_report.md", "a_report.html", r1),
        IndexEntry("b.sas", "b_report.md", "b_report.html", r2),
    ]
    md = build_index_markdown(entries, target="pyspark")
    assert "Files migrated: **2**" in md
    assert "[report](a_report.md)" in md
    html = build_index_html(entries, target="pyspark")
    assert 'href="b_report.html"' in html
    assert "project migration index" in html.lower()
