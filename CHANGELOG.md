# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - One-command migration

### Added
- One-command migration: `s2db migrate` now accepts `--target all` and **defaults to it**,
  emitting PySpark + Spark SQL + DLT in a single run under `out/<target>/`, with a combined
  top-level `index.md` (and `index.html` with `--html`) linking each per-target report.
  `--bundle` produces a deployable Databricks Asset Bundle per target. Public helper
  `migrate_all_targets()` and `ALL_TARGETS` added to `project.py`.

### Changed
- `s2db migrate <project>` with no `--target` now produces all three notebook formats
  instead of PySpark only. Pass `--target pyspark` (or `sparksql`/`dlt`) to keep a single
  format. `s2db convert` (single file) is unchanged.

## [0.3.1] - Packaging & publishing

### Added
- `py.typed` marker so downstream projects pick up the package's type hints (PEP 561).
- Automated PyPI publishing workflow (`.github/workflows/publish.yml`) using GitHub
  Trusted Publishing (OIDC): builds + `twine check` on every run, publishes to PyPI on a
  GitHub release, and supports a manual TestPyPI dry run via `workflow_dispatch`.

### Changed
- Rebranded the project and repository to **sas2databricks**; all in-repo URLs updated.
  Replaced em dashes with hyphens in the README.

## [0.3.0] — End-to-end deployment & roadmap features

### Added
- **Databricks Asset Bundle target** (`--target bundle`): emits a `databricks.yml` that wraps
  the migrated steps in a multi-task job (reusing the Workflows task graph) with `dev`/`prod`
  deployment targets.
- **`s2db migrate --bundle`**: assembles a deployable bundle directory — `databricks.yml`,
  `src/` notebooks (one per migrated `.sas` file), and a `reports/` folder.
- **Project report index** (`index.md` + `index.html`): a roll-up across every migrated file
  with portfolio totals (steps, needs-review, LLM escalations) and links to each per-file
  report. Written by `s2db migrate` in both flat and bundle layouts.
- **Macro control flow**: `%IF/%THEN/%DO/%ELSE/%END` and iterative `%DO i = a %TO b [%BY s]`
  loops in `%MACRO` bodies now lower deterministically to Python `if`/`elif`/`else`/`for`.
  Bare-word condition operands are correctly treated as SAS literal text (quoted).
- **DATA-step arrays + iterative `DO` loops**: `array x{n} v1-vn` declarations and integer
  `DO` loops are deterministically unrolled into per-column assignments.
- **CI workflow** (`.github/workflows/ci.yml`): ruff + mypy + pytest on Python 3.10–3.12.
- New examples: `sample6_arrays_doloop.sas`, `sample7_macro_controlflow.sas`.
- New module `project.py` (shared project-migration core for the CLI and MCP server) and
  `report_index.py` (project index rendering).

### Changed
- `migrate_project` MCP tool now supports `bundle=True` and reports `validate`/`bundle` in its
  target documentation; CLI and MCP share one project-migration implementation.
- Workflows/Bundle task dependencies now resolve on both the full dataset name and its
  unqualified tail, so a `PROC SQL` reading `enriched` links to a DATA step named `work.enriched`.
- Project status bumped to Beta.

### Fixed
- DATA-step assignment parsing dropped every other `;`-separated assignment in unrolled
  (single-line) step bodies; fixed with a zero-width lookbehind in the assignment regex.
- Resolved 4 pre-existing `mypy` errors (added `Step.llm_code`/`Step.llm_fulfilled`, a typed
  local in the PySpark stat emitter, and a corrected CLI `type: ignore`); `mypy src` is clean.

## [0.2.0] — DATA step depth & macro facility

### Added
- DATA step BY-group processing (`FIRST.`/`LAST.`), `RETAIN`, `LAG()`/`DIF()` via window
  functions, and `MERGE` → DataFrame join / `FULL JOIN USING`.
- `%MACRO`/`%MEND` definitions and macro-call expansion; descriptive PROCs (`CORR`,
  `UNIVARIATE`) and MLlib scaffolds for `REG`/`LOGISTIC`/`GLM`/`GENMOD`.
- `validate` target (row/schema/checksum parity notebook), DLT expectations, Workflows job
  graph, Unity Catalog naming, and HTML migration reports.
- Pluggable LLM providers (Anthropic, Azure OpenAI) behind `LLMProvider`.

## [0.1.0] — Foundation

### Added
- Project scaffold, packaging, `s2db` CLI, MCP server, and the Copilot agent + skill.
- Lexer/preprocessor, IR with provenance + confidence, and deterministic transpilers for
  PROC SQL, PROC MEANS/SUMMARY, PROC FORMAT, macro variables, and the core DATA step.
- PySpark, Spark SQL, DLT, and Workflows emitters; LLM orchestrator with model routing.

[0.4.0]: https://github.com/navintkr/sas2databricks/releases/tag/v0.4.0
[0.3.1]: https://github.com/navintkr/sas2databricks/releases/tag/v0.3.1
[0.3.0]: https://github.com/navintkr/sas2databricks/releases/tag/v0.3.0
[0.2.0]: https://github.com/navintkr/sas2databricks/releases/tag/v0.2.0
[0.1.0]: https://github.com/navintkr/sas2databricks/releases/tag/v0.1.0
