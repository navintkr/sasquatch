"""Project-level migration report index.

Rolls up the per-file :class:`~sas2databricks.pipeline.MigrationResult` objects produced by
``s2db migrate`` into a single index (Markdown + self-contained HTML) with portfolio totals
and links to each per-file report.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .pipeline import MigrationResult


@dataclass
class IndexEntry:
    """One migrated file in the project index."""

    source_name: str
    report_md: str
    report_html: str | None
    result: MigrationResult


@dataclass
class _Totals:
    files: int
    steps: int
    review: int
    escalations: int

    @classmethod
    def of(cls, entries: list[IndexEntry]) -> _Totals:
        return cls(
            files=len(entries),
            steps=sum(len(e.result.reports) for e in entries),
            review=sum(e.result.review_count for e in entries),
            escalations=sum(len(e.result.llm_requests) for e in entries),
        )


def build_index_markdown(entries: list[IndexEntry], *, target: str) -> str:
    t = _Totals.of(entries)
    lines = [
        "# SAS to Databricks — project migration index",
        "",
        f"- Files migrated: **{t.files}**",
        f"- Target: **{target}**",
        f"- Total steps: **{t.steps}**  |  Needs review: **{t.review}**",
        f"- LLM escalations: **{t.escalations}**",
        "",
        "| File | Steps | Needs review | Escalations | Report |",
        "| --- | --- | --- | --- | --- |",
    ]
    for e in sorted(entries, key=lambda x: x.source_name):
        r = e.result
        flag = f"⚠️ {r.review_count}" if r.review_count else "0"
        link = e.report_md
        lines.append(
            f"| `{e.source_name}` | {len(r.reports)} | {flag} | {len(r.llm_requests)} "
            f"| [report]({link}) |"
        )
    if t.review:
        lines += [
            "",
            f"> {t.review} step(s) across {t.files} file(s) need review before you deploy.",
        ]
    return "\n".join(lines) + "\n"


def build_index_html(entries: list[IndexEntry], *, target: str) -> str:
    t = _Totals.of(entries)

    def esc(value: object) -> str:
        return html.escape(str(value))

    rows = []
    for e in sorted(entries, key=lambda x: x.source_name):
        r = e.result
        cls = "review" if r.review_count else "ok"
        link = e.report_html or e.report_md
        badge = str(r.review_count)
        rows.append(
            f'<tr class="{cls}"><td><a href="{esc(link)}">'
            f"<code>{esc(e.source_name)}</code></a></td>"
            f"<td>{len(r.reports)}</td><td>{badge}</td>"
            f"<td>{len(r.llm_requests)}</td>"
            f"<td>{r.model.value}</td></tr>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>sas2databricks - project migration index</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1b1b1b; }}
 h1 {{ font-size: 1.5rem; }}
 .meta span {{ display: inline-block; margin-right: 1.5rem; }}
 table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; font-size: .9rem; }}
 th, td {{ border: 1px solid #ddd; padding: .4rem .6rem; text-align: left; }}
 th {{ background: #f4f4f4; }}
 tr.review td:nth-child(3) {{ color: #b3261e; font-weight: 600; }}
 tr.ok td:nth-child(3) {{ color: #157f3b; }}
 code {{ background: #f0f0f0; padding: 0 .2rem; border-radius: 3px; }}
 a {{ color: #0b5fff; text-decoration: none; }}
</style></head><body>
<h1>SAS to Databricks &mdash; project migration index</h1>
<div class="meta">
 <span>Files: <strong>{t.files}</strong></span>
 <span>Target: <strong>{esc(target)}</strong></span>
 <span>Steps: <strong>{t.steps}</strong></span>
 <span>Needs review: <strong>{t.review}</strong></span>
 <span>LLM escalations: <strong>{t.escalations}</strong></span>
</div>
<table><thead><tr><th>File</th><th>Steps</th><th>Needs review</th>
<th>Escalations</th><th>Model</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>
"""


def write_index(
    entries: list[IndexEntry],
    out_dir: Path,
    *,
    target: str,
    html_report: bool,
    stem: str = "index",
) -> list[Path]:
    """Write ``index.md`` (and ``index.html`` when ``html_report``) into ``out_dir``."""
    written: list[Path] = []
    md_path = out_dir / f"{stem}.md"
    md_path.write_text(build_index_markdown(entries, target=target), encoding="utf-8")
    written.append(md_path)
    if html_report:
        html_path = out_dir / f"{stem}.html"
        html_path.write_text(build_index_html(entries, target=target), encoding="utf-8")
        written.append(html_path)
    return written


@dataclass
class TargetSummary:
    """One target's roll-up for the combined ``--target all`` index."""

    target: str
    files: int
    steps: int
    review: int
    escalations: int
    index_base: str  # relative path to that target's index, without extension


def build_multi_index_markdown(summaries: list[TargetSummary]) -> str:
    """Top-level index linking each target's per-target index (``--target all``)."""
    files = summaries[0].files if summaries else 0
    review = summaries[0].review if summaries else 0
    targets = ", ".join(s.target for s in summaries)
    lines = [
        "# SAS to Databricks - project migration (all targets)",
        "",
        f"- Files migrated: **{files}**",
        f"- Targets: **{targets}**",
        f"- Steps needing review: **{review}** (per file; identical across targets)",
        "",
        "| Target | Files | Steps | Needs review | Open |",
        "| --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        flag = f"⚠️ {s.review}" if s.review else "0"
        lines.append(
            f"| `{s.target}` | {s.files} | {s.steps} | {flag} | [index]({s.index_base}.md) |"
        )
    return "\n".join(lines) + "\n"


def build_multi_index_html(summaries: list[TargetSummary]) -> str:
    """Self-contained HTML twin of :func:`build_multi_index_markdown`."""
    files = summaries[0].files if summaries else 0
    review = summaries[0].review if summaries else 0
    targets = html.escape(", ".join(s.target for s in summaries))
    rows = []
    for s in summaries:
        cls = "review" if s.review else "ok"
        rows.append(
            f'<tr class="{cls}"><td><a href="{html.escape(s.index_base)}.html">'
            f"<code>{html.escape(s.target)}</code></a></td>"
            f"<td>{s.files}</td><td>{s.steps}</td><td>{s.review}</td>"
            f"<td>{s.escalations}</td></tr>"
        )
    body = "\n".join(rows)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>SAS to Databricks - all targets</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
 table {{ border-collapse: collapse; width: 100%; }}
 th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
 th {{ background: #f3f3f3; }}
 tr.review {{ background: #fff7e6; }}
</style></head><body>
<h1>SAS to Databricks - project migration (all targets)</h1>
<p>Files migrated: <strong>{files}</strong> &middot; Targets: <strong>{targets}</strong>
 &middot; Steps needing review: <strong>{review}</strong> (per file; identical across targets)</p>
<table>
<thead><tr>
<th>Target</th><th>Files</th><th>Steps</th><th>Needs review</th><th>Escalations</th>
</tr></thead>
<tbody>
{body}
</tbody></table>
</body></html>
"""


def write_multi_index(
    summaries: list[TargetSummary],
    out_dir: Path,
    *,
    html_report: bool,
    stem: str = "index",
) -> list[Path]:
    """Write the combined ``--target all`` index into ``out_dir``."""
    written: list[Path] = []
    md_path = out_dir / f"{stem}.md"
    md_path.write_text(build_multi_index_markdown(summaries), encoding="utf-8")
    written.append(md_path)
    if html_report:
        html_path = out_dir / f"{stem}.html"
        html_path.write_text(build_multi_index_html(summaries), encoding="utf-8")
        written.append(html_path)
    return written
