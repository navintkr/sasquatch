"""End-to-end migration pipeline: parse → transpile → (LLM escalate) → emit."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .emitters import emit as emit_target
from .ir import FormatStep, MacroDef, Program, Step
from .llm import Model, NullProvider, Orchestrator
from .llm.orchestrator import ConversionRequest, LLMProvider
from .macros import convert_macro
from .parser import parse
from .transpilers import dispatch


@dataclass
class StepReport:
    name: str
    kind: str
    engine: str
    confidence: float
    needs_review: bool
    notes: list[str]
    start_line: int
    end_line: int


@dataclass
class MigrationResult:
    """Outcome of a migration run."""

    target: str
    model: Model
    filename: str
    code: str
    program: Program
    reports: list[StepReport] = field(default_factory=list)
    llm_requests: list[ConversionRequest] = field(default_factory=list)
    source_path: str = ""

    @property
    def review_count(self) -> int:
        return sum(1 for r in self.reports if r.needs_review)

    def report_markdown(self) -> str:
        lines = [
            f"# Migration report — {Path(self.source_path).name or 'in-memory'}",
            "",
            f"- Target: **{self.target}**",
            f"- Model: **{self.model.value}**",
            f"- Steps: **{len(self.reports)}**  |  Needs review: **{self.review_count}**",
            f"- LLM escalations: **{len(self.llm_requests)}**",
            "",
            "| Step | Kind | Engine | Conf | Review | SAS lines |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in self.reports:
            flag = "⚠️ yes" if r.needs_review else "ok"
            lines.append(
                f"| `{r.name}` | {r.kind} | {r.engine} | {r.confidence:.2f} | {flag} "
                f"| {r.start_line}-{r.end_line} |"
            )
        notes = [r for r in self.reports if r.notes]
        if notes:
            lines += ["", "## Notes"]
            for r in notes:
                lines.append(f"- **{r.name}**: " + "; ".join(r.notes))
        return "\n".join(lines) + "\n"

    def report_html(self) -> str:
        """Self-contained HTML migration report (no external assets)."""
        import html

        def esc(value: object) -> str:
            return html.escape(str(value))

        rows = []
        for r in self.reports:
            cls = "review" if r.needs_review else "ok"
            badge = "needs review" if r.needs_review else "ok"
            note = esc("; ".join(r.notes)) if r.notes else ""
            rows.append(
                f'<tr class="{cls}"><td><code>{esc(r.name)}</code></td>'
                f"<td>{esc(r.kind)}</td><td>{esc(r.engine)}</td>"
                f"<td>{r.confidence:.2f}</td><td>{badge}</td>"
                f"<td>{r.start_line}-{r.end_line}</td><td>{note}</td></tr>"
            )
        title = esc(Path(self.source_path).name or "in-memory")
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>sas2databricks report - {title}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1b1b1b; }}
 h1 {{ font-size: 1.4rem; }}
 .meta span {{ display: inline-block; margin-right: 1.5rem; }}
 table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; font-size: .9rem; }}
 th, td {{ border: 1px solid #ddd; padding: .4rem .6rem; text-align: left; vertical-align: top; }}
 th {{ background: #f4f4f4; }}
 tr.review {{ background: #fff6f6; }}
 tr.ok td:nth-child(5) {{ color: #157f3b; }}
 tr.review td:nth-child(5) {{ color: #b3261e; font-weight: 600; }}
 code {{ background: #f0f0f0; padding: 0 .2rem; border-radius: 3px; }}
</style></head><body>
<h1>SAS to Databricks migration report</h1>
<div class="meta">
 <span>Source: <strong>{title}</strong></span>
 <span>Target: <strong>{esc(self.target)}</strong></span>
 <span>Model: <strong>{esc(self.model.value)}</strong></span>
 <span>Steps: <strong>{len(self.reports)}</strong></span>
 <span>Needs review: <strong>{self.review_count}</strong></span>
 <span>LLM escalations: <strong>{len(self.llm_requests)}</strong></span>
</div>
<table><thead><tr><th>Step</th><th>Kind</th><th>Engine</th><th>Conf</th>
<th>Review</th><th>SAS lines</th><th>Notes</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>
"""


def _build_program(text: str, *, source_path: str) -> Program:
    parsed = parse(text, source_path=source_path)
    prog = Program(macro_vars=parsed.macro_vars)
    for raw in parsed.steps:
        for step in dispatch(raw):
            if isinstance(step, FormatStep):
                prog.formats[step.format_name] = step
            if isinstance(step, MacroDef):
                convert_macro(step)
            prog.steps.append(step)
    return prog


def migrate(
    text: str,
    *,
    target: str = "pyspark",
    model: str | Model = Model.OPUS_4_8,
    source_path: str = "",
    provider: LLMProvider | None = None,
    threshold: float = 0.8,
    **options,
) -> MigrationResult:
    """Migrate SAS ``text`` to a Databricks ``target``.

    ``provider`` defaults to :class:`NullProvider` (offline stubs). Pass
    :class:`CopilotProvider` when running inside GitHub Copilot / MCP so low-confidence
    steps are delegated to the host model. ``options`` may include ``catalog``/``schema``
    (DLT Unity Catalog target) or ``ref_base`` (validation reference path).
    """
    resolved_model = Model.parse(model) if isinstance(model, str) else model
    prog = _build_program(text, source_path=source_path)

    provider = provider or NullProvider()
    orch = Orchestrator(provider, model=resolved_model, target=target, threshold=threshold)

    converted: list[str] = []
    for step in prog.steps:
        orch.maybe_escalate(step, context=converted)
        converted.append(step.name)

    filename, code = emit_target(prog, target, source_path=source_path, **options)

    reports = [_step_report(s, threshold) for s in prog.steps]
    return MigrationResult(
        target=target,
        model=resolved_model,
        filename=filename,
        code=code,
        program=prog,
        reports=reports,
        llm_requests=orch.requests,
        source_path=source_path,
    )


def migrate_file(path: str | Path, **kwargs) -> MigrationResult:
    p = Path(path)
    return migrate(p.read_text(encoding="utf-8"), source_path=str(p), **kwargs)


def _step_report(step: Step, threshold: float) -> StepReport:
    p = step.prov
    return StepReport(
        name=step.name,
        kind=step.kind,
        engine=p.engine.value,
        confidence=p.confidence,
        needs_review=p.needs_review(threshold),
        notes=p.notes,
        start_line=p.start_line,
        end_line=p.end_line,
    )
