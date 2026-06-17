"""Project-level migration: migrate many ``.sas`` files into a flat or bundle layout.

Shared by the ``s2db migrate`` CLI command and the ``migrate_project`` MCP tool so both
produce the same on-disk layout, per-file reports, and project report index.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .emitters.bundle_emitter import project_bundle
from .llm.orchestrator import LLMProvider
from .pipeline import MigrationResult, migrate_file
from .report_index import IndexEntry, TargetSummary, write_index, write_multi_index

# Targets whose output is a runnable notebook (valid inside a deployable --bundle layout).
BUNDLE_NOTEBOOK_TARGETS = frozenset({"pyspark", "sparksql", "dlt", "validate"})

# Code-generation targets emitted together by ``--target all`` (the one-command happy path).
ALL_TARGETS: tuple[str, ...] = ("pyspark", "sparksql", "dlt")

OnFile = Callable[[Path, MigrationResult, str], None]


@dataclass
class _PlannedFile:
    """A source file paired with a collision-free output stem and a display name."""

    path: Path
    stem: str
    display: str


def _plan_files(files: list[Path]) -> list[_PlannedFile]:
    """Assign each input a unique output stem so same-named files never overwrite each other.

    ``a/load.sas`` and ``b/load.sas`` both have stem ``load``; the second becomes ``load_2``.
    The display name shown in the report index is parent-qualified when names collide.
    """
    name_counts = Counter(f.name for f in files)
    used: dict[str, int] = {}
    planned: list[_PlannedFile] = []
    for f in files:
        n = used.get(f.stem, 0) + 1
        used[f.stem] = n
        stem = f.stem if n == 1 else f"{f.stem}_{n}"
        display = f.name if name_counts[f.name] == 1 else f"{f.parent.name}/{f.name}"
        planned.append(_PlannedFile(f, stem, display))
    return planned


@dataclass
class ProjectResult:
    """Outcome of a project migration."""

    out: Path
    target: str
    bundle: bool
    entries: list[IndexEntry] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.entries)

    @property
    def review_count(self) -> int:
        return sum(e.result.review_count for e in self.entries)


def migrate_project(
    files: list[Path],
    out: str | Path,
    *,
    target: str = "pyspark",
    model: str = "opus-4.8",
    provider: LLMProvider | None = None,
    threshold: float = 0.8,
    html: bool = False,
    bundle: bool = False,
    on_file: OnFile | None = None,
    **options: str,
) -> ProjectResult:
    """Migrate ``files`` into ``out``.

    With ``bundle=False`` (default) each file is written flat as ``<stem>_<filename>`` with a
    sibling ``<stem>_report.md`` and a project ``index.md``. With ``bundle=True`` a deployable
    Databricks Asset Bundle is assembled: ``databricks.yml`` + ``src/<stem>.<ext>`` notebooks +
    ``reports/`` (per-file reports and ``index.md``).

    Raises ``ValueError`` if ``bundle`` is requested with a non-notebook ``target``.
    """
    out = Path(out)
    if bundle and target not in BUNDLE_NOTEBOOK_TARGETS:
        raise ValueError(
            f"--bundle needs a notebook target ({', '.join(sorted(BUNDLE_NOTEBOOK_TARGETS))}); "
            f"got '{target}'."
        )
    if bundle:
        return _bundle_layout(files, out, target, model, provider, threshold, html, on_file,
                              options)
    return _flat_layout(files, out, target, model, provider, threshold, html, on_file, options)


@dataclass
class MultiProjectResult:
    """Outcome of a ``--target all`` migration (one :class:`ProjectResult` per target)."""

    out: Path
    targets: list[str]
    bundle: bool
    results: list[ProjectResult] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return self.results[0].file_count if self.results else 0

    @property
    def review_count(self) -> int:
        # Review flags come from transpile confidence, which is target-independent, so any
        # target's count is representative of the whole run.
        return self.results[0].review_count if self.results else 0


def migrate_all_targets(
    files: list[Path],
    out: str | Path,
    *,
    targets: tuple[str, ...] = ALL_TARGETS,
    model: str = "opus-4.8",
    provider: LLMProvider | None = None,
    threshold: float = 0.8,
    html: bool = False,
    bundle: bool = False,
    on_file: OnFile | None = None,
    on_target: Callable[[str], None] | None = None,
    **options: str,
) -> MultiProjectResult:
    """Migrate ``files`` to every target in ``targets``.

    Each target is written under ``out/<target>/`` (flat, or a deployable bundle when
    ``bundle=True``) and a combined ``out/index.md`` (plus ``index.html`` when ``html``)
    links each per-target index. This is the one-command happy path behind ``s2db migrate``.
    """
    out = Path(out)
    results: list[ProjectResult] = []
    for tgt in targets:
        if on_target is not None:
            on_target(tgt)
        results.append(
            migrate_project(
                files, out / tgt, target=tgt, model=model, provider=provider,
                threshold=threshold, html=html, bundle=bundle, on_file=on_file, **options,
            )
        )
    summaries = [
        TargetSummary(
            target=r.target,
            files=r.file_count,
            steps=sum(len(e.result.reports) for e in r.entries),
            review=r.review_count,
            escalations=sum(len(e.result.llm_requests) for e in r.entries),
            index_base=f"{r.out.name}/reports/index" if r.bundle else f"{r.out.name}/index",
        )
        for r in results
    ]
    write_multi_index(summaries, out, html_report=html)
    return MultiProjectResult(out=out, targets=list(targets), bundle=bundle, results=results)


def _flat_layout(
    files: list[Path], out: Path, target: str, model: str, provider: LLMProvider | None,
    threshold: float, html: bool, on_file: OnFile | None, options: dict[str, str],
) -> ProjectResult:
    out.mkdir(parents=True, exist_ok=True)
    entries: list[IndexEntry] = []
    for pf in _plan_files(files):
        result = migrate_file(pf.path, target=target, model=model, provider=provider,
                              threshold=threshold, **options)
        dest = out / f"{pf.stem}_{result.filename}"
        dest.write_text(result.code, encoding="utf-8")
        report_md = f"{pf.stem}_report.md"
        (out / report_md).write_text(result.report_markdown(), encoding="utf-8")
        report_html = _maybe_html(out, pf.stem, result, html)
        entries.append(IndexEntry(pf.display, report_md, report_html, result))
        if on_file:
            on_file(pf.path, result, dest.name)
    write_index(entries, out, target=target, html_report=html)
    return ProjectResult(out=out, target=target, bundle=False, entries=entries)


def _bundle_layout(
    files: list[Path], out: Path, target: str, model: str, provider: LLMProvider | None,
    threshold: float, html: bool, on_file: OnFile | None, options: dict[str, str],
) -> ProjectResult:
    src_dir = out / "src"
    reports_dir = out / "reports"
    src_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    entries: list[IndexEntry] = []
    stems: list[str] = []
    for pf in _plan_files(files):
        result = migrate_file(pf.path, target=target, model=model, provider=provider,
                              threshold=threshold, **options)
        ext = Path(result.filename).suffix
        (src_dir / f"{pf.stem}{ext}").write_text(result.code, encoding="utf-8")
        report_md = f"{pf.stem}_report.md"
        (reports_dir / report_md).write_text(result.report_markdown(), encoding="utf-8")
        report_html = _maybe_html(reports_dir, pf.stem, result, html)
        entries.append(IndexEntry(pf.display, report_md, report_html, result))
        stems.append(pf.stem)
        if on_file:
            on_file(pf.path, result, f"src/{pf.stem}{ext}")

    bundle_name = out.resolve().name or "sas_migration"
    (out / "databricks.yml").write_text(
        project_bundle(bundle_name, stems, src_dir="src"), encoding="utf-8"
    )
    write_index(entries, reports_dir, target=target, html_report=html)
    return ProjectResult(out=out, target=target, bundle=True, entries=entries)


def _maybe_html(
    folder: Path, stem: str, result: MigrationResult, html: bool
) -> str | None:
    if not html:
        return None
    name = f"{stem}_report.html"
    (folder / name).write_text(result.report_html(), encoding="utf-8")
    return name
