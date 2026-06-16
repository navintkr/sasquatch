"""SAS lexer / preprocessor.

SAS is not cleanly context-free, so rather than a single grammar we preprocess the
source (strip comments, expand ``%let`` macro variables) and split it into *steps*
(``DATA ...; run;``, ``PROC ...; run/quit;``, ``%macro ...; %mend;``). Each step is then
handed to a construct-specific transpiler.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---- comment stripping ----------------------------------------------------------------

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# `* ... ;` statement comments (only when `*` starts a statement)
_STAR_COMMENT = re.compile(r"(?m)^\s*\*[^;]*;")
_MACRO_COMMENT = re.compile(r"%\*[^;]*;")


def strip_comments(text: str) -> str:
    text = _BLOCK_COMMENT.sub(" ", text)
    text = _MACRO_COMMENT.sub(" ", text)
    text = _STAR_COMMENT.sub(" ", text)
    return text


# ---- macro variable expansion ---------------------------------------------------------

_LET = re.compile(r"%let\s+([A-Za-z_]\w*)\s*=\s*([^;]*);", re.IGNORECASE)


def extract_macro_vars(text: str) -> dict[str, str]:
    """Collect ``%let`` assignments. Returns {name: value} (last write wins)."""
    out: dict[str, str] = {}
    for m in _LET.finditer(text):
        out[m.group(1).lower()] = m.group(2).strip()
    return out


def expand_macro_vars(text: str, variables: dict[str, str]) -> str:
    """Resolve ``&name`` / ``&name.`` references using ``variables`` (iteratively)."""
    if not variables:
        return text
    prev = None
    cur = text
    # iterate to resolve nested references, bounded to avoid infinite loops
    for _ in range(10):
        if cur == prev:
            break
        prev = cur
        for name, value in variables.items():
            cur = re.sub(rf"&{name}\.?", value, cur, flags=re.IGNORECASE)
    return cur


# ---- macro definition + invocation expansion ------------------------------------------

_MACRO_DEF = re.compile(
    r"(?is)%macro\s+([A-Za-z_]\w*)\s*(?:\((.*?)\))?\s*;(.*?)%mend(?:\s+[A-Za-z_]\w*)?\s*;"
)


@dataclass
class MacroSpec:
    """A captured %MACRO definition (name, ordered params with defaults, body)."""

    name: str
    params: list[tuple[str, str]]  # (name, default)
    body: str


def extract_macros(text: str) -> dict[str, MacroSpec]:
    """Collect ``%macro``/``%mend`` definitions keyed by lowercased name."""
    out: dict[str, MacroSpec] = {}
    for m in _MACRO_DEF.finditer(text):
        name = m.group(1)
        params = _parse_macro_params(m.group(2) or "")
        out[name.lower()] = MacroSpec(name=name, params=params, body=m.group(3).strip())
    return out


def _parse_macro_params(raw: str) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, default = part.split("=", 1)
            params.append((key.strip(), default.strip()))
        else:
            params.append((part, ""))
    return params


def _split_call_args(raw: str) -> tuple[list[str], dict[str, str]]:
    positional: list[str] = []
    keyword: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, val = part.split("=", 1)
            keyword[key.strip().lower()] = val.strip()
        else:
            positional.append(part)
    return positional, keyword


def expand_macro_calls(text: str, macros: dict[str, MacroSpec], *, depth: int = 0) -> str:
    """Inline ``%name(args)`` invocations by substituting the macro body.

    Recursion is bounded so mutually recursive macros cannot loop forever. The
    ``%macro``/``%mend`` definitions themselves are left untouched.
    """
    if not macros or depth > 10:
        return text

    names = "|".join(re.escape(s.name) for s in macros.values())
    call_re = re.compile(rf"(?is)%({names})\b\s*(?:\((.*?)\))?\s*;")

    def _inline(m: re.Match) -> str:
        # don't expand the definition header itself
        if re.match(r"(?is)%macro\b", m.group(0)):
            return m.group(0)
        spec = macros[m.group(1).lower()]
        positional, keyword = _split_call_args(m.group(2) or "")
        values: dict[str, str] = {}
        for i, (pname, default) in enumerate(spec.params):
            if pname.lower() in keyword:
                values[pname] = keyword[pname.lower()]
            elif i < len(positional):
                values[pname] = positional[i]
            else:
                values[pname] = default
        return expand_macro_vars(spec.body, values)

    # avoid matching inside %macro ... %mend definitions
    spans = [(d.start(), d.end()) for d in _MACRO_DEF.finditer(text)]

    def _guarded(m: re.Match) -> str:
        pos = m.start()
        if any(s <= pos < e for s, e in spans):
            return m.group(0)
        return _inline(m)

    expanded = call_re.sub(_guarded, text)
    if expanded != text:
        return expand_macro_calls(expanded, macros, depth=depth + 1)
    return expanded


# ---- step splitting -------------------------------------------------------------------


@dataclass
class RawStep:
    """A raw SAS step (pre-transpilation)."""

    text: str
    kind: str  # data | proc_sql | proc_means | proc_format | proc_report | proc | macro | unknown
    proc: str = ""  # PROC name when kind == proc*
    start_line: int = 0
    end_line: int = 0
    extra: dict = field(default_factory=dict)


_STEP_START = re.compile(
    r"(?im)^[ \t]*(data\b|proc\s+\w+|%macro\b)",
)
_BOUNDARY = re.compile(r"(?im)\b(run|quit|%mend)\s*;")


def _classify(header: str) -> tuple[str, str]:
    h = header.strip().lower()
    if h.startswith("data"):
        return "data", ""
    if h.startswith("%macro"):
        return "macro", ""
    m = re.match(r"proc\s+(\w+)", h)
    if m:
        proc = m.group(1)
        mapping = {
            "sql": "proc_sql",
            "means": "proc_means",
            "summary": "proc_means",
            "freq": "proc_means",
            "tabulate": "proc_means",
            "format": "proc_format",
            "report": "proc_report",
            "print": "proc_report",
            "corr": "proc_stat",
            "univariate": "proc_stat",
            "reg": "proc_model",
            "logistic": "proc_model",
            "glm": "proc_model",
            "genmod": "proc_model",
        }
        return mapping.get(proc, "proc"), proc
    return "unknown", ""


def split_steps(text: str) -> list[RawStep]:
    """Split preprocessed SAS text into a list of :class:`RawStep`.

    ``%macro``/``%mend`` definitions are treated as a single atomic step so the inner
    ``run;``/``quit;`` boundaries don't split the body apart.
    """
    line_index = _line_starts(text)
    macro_spans = [(m.start(), m.end(), m.group(0)) for m in _MACRO_DEF.finditer(text)]

    # blank out macro bodies (preserving length/newlines) before generic splitting
    masked = list(text)
    for s, e, _ in macro_spans:
        for i in range(s, e):
            if masked[i] != "\n":
                masked[i] = " "
    masked_text = "".join(masked)

    collected: list[tuple[int, RawStep]] = []
    for s, _e, body in macro_spans:
        start_line = _line_of(line_index, s)
        collected.append(
            (
                s,
                RawStep(
                    text=body.strip(),
                    kind="macro",
                    start_line=start_line,
                    end_line=start_line + body.count("\n"),
                ),
            )
        )

    starts = [m.start() for m in _STEP_START.finditer(masked_text)]
    starts.append(len(masked_text))

    for i in range(len(starts) - 1):
        chunk = masked_text[starts[i] : starts[i + 1]]
        boundary = _BOUNDARY.search(chunk)
        if boundary:
            chunk = chunk[: boundary.end()]
        if not chunk.strip():
            continue
        # use original text for the chunk content (mask only guided splitting)
        original = text[starts[i] : starts[i] + len(chunk)]
        header = original.strip().splitlines()[0]
        kind, proc = _classify(header)
        start_line = _line_of(line_index, starts[i])
        collected.append(
            (
                starts[i],
                RawStep(
                    text=original.strip(),
                    kind=kind,
                    proc=proc,
                    start_line=start_line,
                    end_line=start_line + original.count("\n"),
                ),
            )
        )

    collected.sort(key=lambda t: t[0])
    return [step for _, step in collected]


def _line_starts(text: str) -> list[int]:
    idx = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            idx.append(i + 1)
    return idx


def _line_of(line_starts: list[int], pos: int) -> int:
    lo, hi = 0, len(line_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_starts[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1
