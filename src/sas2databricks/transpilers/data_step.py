"""DATA step -> IR.

Handles the deterministic subset: ``set``, ``merge`` (join), assignments,
``if/then/else``, ``where``, ``keep``/``drop``/``rename``, ``by``, ``retain``, and the
order-sensitive constructs ``LAG()``/``DIF()`` and ``FIRST.``/``LAST.`` which are lowered
to Spark SQL window expressions. Anything still unrecognised lowers the confidence so the
orchestrator can escalate to the LLM.
"""

from __future__ import annotations

import re

from ..ir import Assignment, DataStep, Engine
from ..parser import RawStep
from .base import prov_for, translate_expr

_DATA_HEADER = re.compile(r"(?is)^\s*data\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*;")
_SET = re.compile(r"(?is)\bset\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)")
_MERGE = re.compile(r"(?is)\bmerge\s+(.*?);")
_WHERE = re.compile(r"(?is)\bwhere\s+(.*?);")
_KEEP = re.compile(r"(?is)\bkeep\s+(.*?);")
_DROP = re.compile(r"(?is)\bdrop\s+(.*?);")
_BY = re.compile(r"(?is)\bby\s+(.*?);")
_RENAME = re.compile(r"(?is)\brename\s+(.*?);")
_RETAIN = re.compile(r"(?is)\bretain\s+(.*?);")
_IF_THEN = re.compile(r"(?is)\bif\s+(.*?)\s+then\s+([A-Za-z_]\w*)\s*=\s*(.*?);")
_ASSIGN = re.compile(r"(?i)(?:^|;)\s*([A-Za-z_]\w*)\s*=\s*(.*?);")

# order-sensitive SAS constructs (need a synthetic row-order column on Spark)
_LAG = re.compile(r"(?i)\blag(\d*)\s*\(\s*([A-Za-z_]\w*)\s*\)")
_DIF = re.compile(r"(?i)\bdif(\d*)\s*\(\s*([A-Za-z_]\w*)\s*\)")
_FIRST = re.compile(r"(?i)\bfirst\.([A-Za-z_]\w*)")
_LAST = re.compile(r"(?i)\blast\.([A-Za-z_]\w*)")
_ORDER_SENSITIVE = re.compile(r"(?is)\b(lag\d*|dif\d*|first\.|last\.)")


def transpile(raw: RawStep) -> DataStep:
    text = raw.text
    header = _DATA_HEADER.search(text)
    name = header.group(1) if header else "result"

    prov = prov_for(raw)
    notes: list[str] = []

    merge_m = _MERGE.search(text)
    merge = _split_names(merge_m) if merge_m else []

    set_m = _SET.search(text)
    source = set_m.group(1) if set_m else (merge[0] if merge else "")
    inputs = merge if merge else ([source] if source else [])

    by = _split_names(_BY.search(text))
    retain = _split_names(_RETAIN.search(text))

    needs_order = bool(_ORDER_SENSITIVE.search(text)) or bool(retain)
    window = _window_clause(by) if needs_order else ""

    where = None
    wm = _WHERE.search(text)
    if wm:
        where, wnotes = translate_expr(wm.group(1))
        notes += wnotes

    keep = _split_names(_KEEP.search(text))
    drop = _split_names(_DROP.search(text))
    rename = _parse_rename(_RENAME.search(text))

    assignments: list[Assignment] = []
    consumed_spans: list[tuple[int, int]] = []

    for m in _IF_THEN.finditer(text):
        cond, cnotes = translate_expr(m.group(1))
        cond = _lower_order_ops(cond, window, by)
        expr, enotes = translate_expr(m.group(3))
        expr = _lower_order_ops(expr, window, by)
        assignments.append(Assignment(target=m.group(2), expr=expr, condition=cond))
        notes += cnotes + enotes
        consumed_spans.append(m.span())

    for m in _ASSIGN.finditer(text):
        if _overlaps(m.span(), consumed_spans):
            continue
        target = m.group(1)
        low = target.lower()
        if low in {"set", "by", "where", "keep", "drop", "rename", "merge", "retain"}:
            continue
        rhs = m.group(2)
        cum = _retain_accumulator(target, rhs, retain, window)
        if cum is not None:
            assignments.append(Assignment(target=target, expr=cum))
            notes.append(
                f"RETAIN `{target}` lowered to a cumulative window sum (ROWS UNBOUNDED "
                "PRECEDING) -- verify reset semantics across BY groups"
            )
            continue
        expr, enotes = translate_expr(rhs)
        expr = _lower_order_ops(expr, window, by)
        assignments.append(Assignment(target=target, expr=expr))
        notes += enotes

    confidence = 0.9
    if merge:
        notes.append(
            f"MERGE lowered to a join of {merge} on {by or '[no BY -> verify join keys]'}"
        )
        if not by:
            confidence = min(confidence, 0.7)
    if needs_order:
        notes.append(
            "order-sensitive logic (LAG/DIF/FIRST./LAST./RETAIN) uses a synthetic "
            "`_row_id` for ordering; confirm the intended row order"
        )
        confidence = min(confidence, 0.75)
    if _has_unknown_logic(text):
        confidence = min(confidence, 0.6)
        prov.engine = Engine.LLM
        notes.append(
            "DATA step contains constructs (array/do-while/do-until) not fully lowered "
            "deterministically -- flagged for LLM assist / review"
        )

    prov.confidence = confidence
    prov.notes += notes
    return DataStep(
        name=name,
        prov=prov,
        inputs=inputs,
        source=source,
        assignments=assignments,
        where=where,
        keep=keep,
        drop=drop,
        rename=rename,
        by=by,
        merge=merge,
        retain=retain,
        needs_row_order=needs_order,
    )


def _window_clause(by: list[str]) -> str:
    part = "PARTITION BY " + ", ".join(by) + " " if by else ""
    return f"OVER ({part}ORDER BY _row_id)"


def _lower_order_ops(expr: str, window: str, by: list[str]) -> str:
    """Rewrite LAG/DIF/FIRST./LAST. into Spark SQL window expressions."""
    if not window:
        return expr
    win = window

    def _lag(m: re.Match) -> str:
        offset = m.group(1) or "1"
        return f"lag({m.group(2)}, {offset}) {win}"

    def _dif(m: re.Match) -> str:
        offset = m.group(1) or "1"
        col = m.group(2)
        return f"({col} - lag({col}, {offset}) {win})"

    out = _LAG.sub(_lag, expr)
    out = _DIF.sub(_dif, out)
    part = "PARTITION BY " + ", ".join(by) + " " if by else ""
    if _FIRST.search(out):
        out = _FIRST.sub(lambda _m: f"(row_number() OVER ({part}ORDER BY _row_id) = 1)", out)
    if _LAST.search(out):
        out = _LAST.sub(
            lambda _m: f"(row_number() OVER ({part}ORDER BY _row_id DESC) = 1)", out
        )
    return out


def _retain_accumulator(target: str, rhs: str, retain: list[str], window: str) -> str | None:
    if target not in retain or not window:
        return None
    cum_win = window.replace(")", " ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)", 1)
    m = re.match(rf"(?is)^\s*{re.escape(target)}\s*\+\s*(.+)$", rhs.strip())
    if m:
        return f"sum({m.group(1).strip()}) {cum_win}"
    m = re.match(rf"(?is)^\s*(.+?)\s*\+\s*{re.escape(target)}\s*$", rhs.strip())
    if m:
        return f"sum({m.group(1).strip()}) {cum_win}"
    return None


def _split_names(m: re.Match | None) -> list[str]:
    if not m:
        return []
    body = m.group(1).strip()
    return [w for w in re.split(r"[\s,]+", body) if w and not _is_number(w)]


def _is_number(tok: str) -> bool:
    return bool(re.fullmatch(r"[-+]?\d+(\.\d+)?", tok))


def _parse_rename(m: re.Match | None) -> dict[str, str]:
    if not m:
        return {}
    out: dict[str, str] = {}
    for pair in re.split(r"[\s,]+", m.group(1).strip()):
        if "=" in pair:
            old, new = pair.split("=", 1)
            out[old.strip()] = new.strip()
    return out


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] >= s and span[1] <= e for s, e in spans)


def _has_unknown_logic(text: str) -> bool:
    return bool(re.search(r"(?is)\b(array|do\s+while|do\s+until|do\s+\w+\s*=)\b", text))
