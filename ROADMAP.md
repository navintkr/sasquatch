# Roadmap

Status legend: ✅ working · 🚧 partial · 🔭 planned

## v0.1 — Foundation
- ✅ Project scaffold, packaging, CLI, MCP server, Copilot agent + skill
- ✅ Lexer/preprocessor (comments, step splitting, `%let` expansion)
- ✅ IR model with provenance + confidence
- ✅ PROC SQL → Spark SQL / PySpark (deterministic, sqlglot)
- ✅ PROC MEANS/SUMMARY → group-by aggregations
- ✅ PROC FORMAT → mapping tables / CASE
- ✅ Macro variables (`%LET`)
- ✅ DATA step (assignments, IF/THEN/ELSE, WHERE, KEEP/DROP/RENAME)
- ✅ LLM orchestrator (Copilot + Null providers, model routing)
- ✅ PySpark + Spark SQL emitters; ✅ DLT + Workflows emitters

## v0.2 — DATA step depth
- ✅ BY-group processing, `FIRST.`/`LAST.`, `RETAIN` (window functions)
- ✅ `LAG()`/`DIF()` via window functions
- ✅ `MERGE` (SAS join semantics) → DataFrame joins / `FULL JOIN USING`
- 🚧 Arrays + `DO` loops (flagged for LLM/manual review)
- 🔭 Informats/formats applied inside DATA steps

## v0.3 — Macro facility
- ✅ `%MACRO`/`%MEND` with positional + keyword params → Python functions
- ✅ Macro-call expansion (inlining) before parsing
- 🔭 `%IF/%DO/%END` control flow, `%DO` iterative loops

## v0.4 — Reporting
- ✅ PROC REPORT → notebook scaffold (basic COLUMN/GROUP)
- 🔭 PROC TABULATE → pivot tables
- 🔭 ODS output → notebook/dashboard scaffolds

## v0.5 — Statistics & ML
- ✅ PROC REG/LOGISTIC/GLM/GENMOD → MLlib scaffold (VectorAssembler + estimator)
- ✅ PROC UNIVARIATE/CORR → descriptive stats / correlation helpers

## v0.6 — Orchestration & validation
- ✅ DLT pipeline generation with expectations (`@dlt.expect_or_drop`)
- ✅ Databricks Workflows job graph from step dependencies
- ✅ Data-parity validation harness (`validate` target: row/schema/checksum diff)
- ✅ Unity Catalog naming (`--catalog`/`--schema` on DLT target)

## Cross-cutting
- ✅ Web report (HTML) for migration results (`report --html`)
- ✅ Pluggable LLM providers (Azure OpenAI, Anthropic) behind `LLMProvider`
- 🔭 VS Code extension wrapper around the MCP server
- 🔭 Local/offline model provider
