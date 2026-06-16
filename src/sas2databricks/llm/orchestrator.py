"""LLM orchestration: escalate low-confidence IR nodes to the chosen model.

The orchestrator is provider-agnostic. Two providers ship by default:

* :class:`CopilotProvider` — a *delegating* provider. When sas2databricks runs inside a
  GitHub Copilot session (via the MCP server or the custom agent), the host already holds
  an LLM with the user-selected model. This provider emits a structured *conversion
  request* that the host fulfils, so we never ship API keys and we honour the user's model
  picker (Opus 4.8 / Codex / Auto).
* :class:`NullProvider` — used for offline/CLI runs with no model configured. It returns a
  ``MANUAL REVIEW`` stub so the deterministic pipeline still produces runnable output.

Real API providers (Anthropic, Azure OpenAI) can be added by subclassing
:class:`LLMProvider` — see CONTRIBUTING.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ir import Engine, Step
from .models import Model, route
from .prompts import CONVERT_PROMPT, SYSTEM_PROMPT


@dataclass
class ConversionRequest:
    """A unit of work for the LLM: convert one IR node's SAS source to target code."""

    node_kind: str
    model: Model
    system: str
    prompt: str
    sas: str
    output_name: str


@dataclass
class LLMResult:
    code: str | None
    model: Model
    notes: list[str] = field(default_factory=list)
    fulfilled: bool = False


class LLMProvider:
    """Base class for LLM providers."""

    def convert(self, req: ConversionRequest) -> LLMResult:  # pragma: no cover - interface
        raise NotImplementedError


class NullProvider(LLMProvider):
    """Offline provider: emits a reviewable stub instead of calling a model."""

    def convert(self, req: ConversionRequest) -> LLMResult:
        stub = (
            f"# MANUAL REVIEW ({req.model.value}): no LLM provider configured.\n"
            f"# Convert the following SAS for `{req.output_name}` by hand or rerun with a\n"
            f"# model provider / inside GitHub Copilot.\n"
            + "\n".join(f"# {line}" for line in req.sas.splitlines())
        )
        return LLMResult(code=stub, model=req.model, fulfilled=False,
                         notes=["emitted manual-review stub (no provider)"])


class CopilotProvider(LLMProvider):
    """Delegates conversion to the host Copilot model.

    The host (MCP client / VS Code agent) is responsible for actually running the model
    and writing the result back. When used purely in-process, it records the request so
    the caller (e.g. the MCP server) can hand it to Copilot.
    """

    def __init__(self) -> None:
        self.requests: list[ConversionRequest] = []

    def convert(self, req: ConversionRequest) -> LLMResult:
        self.requests.append(req)
        # The host will fill this in; for now, surface the request as a TODO marker.
        marker = (
            f"# COPILOT[{req.model.value}]: convert step `{req.output_name}` "
            f"(kind={req.node_kind}). See migration report for the prompt."
        )
        return LLMResult(code=marker, model=req.model, fulfilled=False,
                         notes=["delegated to host Copilot model"])


class Orchestrator:
    """Escalates low-confidence IR nodes to the configured provider."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: Model = Model.OPUS_4_8,
        target: str = "pyspark",
        threshold: float = 0.8,
    ) -> None:
        self.provider = provider
        self.model = model
        self.target = target
        self.threshold = threshold
        self.requests: list[ConversionRequest] = []

    def maybe_escalate(self, step: Step, *, context: list[str]) -> Step:
        """If ``step`` is below the confidence threshold, ask the LLM to convert it."""
        if not step.prov.needs_review(self.threshold):
            return step

        resolved = route(self.model, step.kind)
        req = ConversionRequest(
            node_kind=step.kind,
            model=resolved,
            system=SYSTEM_PROMPT.format(target=self.target),
            prompt=CONVERT_PROMPT.format(
                target=self.target,
                start=step.prov.start_line,
                end=step.prov.end_line,
                sas=step.prov.source,
                context=", ".join(context) or "(none)",
                output_name=step.name,
            ),
            sas=step.prov.source,
            output_name=step.name,
        )
        self.requests.append(req)
        result = self.provider.convert(req)

        step.prov.engine = Engine.LLM
        step.prov.notes.append(
            f"escalated to {resolved.value}; "
            + ("fulfilled" if result.fulfilled else "pending host fulfillment")
        )
        step.prov.notes += result.notes
        # stash the generated/marker code on the step for emitters to surface
        step.llm_code = result.code
        step.llm_fulfilled = result.fulfilled
        if result.fulfilled:
            step.prov.confidence = max(step.prov.confidence, 0.85)
        return step
