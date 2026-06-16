"""Real LLM providers behind the :class:`LLMProvider` interface.

These are optional. They lazily import their SDK so the core package has no hard
dependency on any model vendor, and they read credentials from environment variables
only -- never from arguments or files. If the SDK or credentials are missing, ``convert``
raises a clear error rather than failing silently.

Install extras::

    pip install "sas2databricks[llm]"        # anthropic + openai SDKs
"""

from __future__ import annotations

import os

from .orchestrator import ConversionRequest, LLMProvider, LLMResult


def _extract_code(text: str) -> str:
    """Pull a fenced code block out of a model response, or return the text as-is."""
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            block = parts[1]
            # drop an optional language hint on the first line
            return block.split("\n", 1)[1] if "\n" in block else block
    return text.strip()


class AnthropicProvider(LLMProvider):
    """Convert via the Anthropic Claude API (e.g. Opus 4.8).

    Reads ``ANTHROPIC_API_KEY`` from the environment.
    """

    def __init__(self, model: str = "claude-opus-4-8", max_tokens: int = 4000) -> None:
        self.model = model
        self.max_tokens = max_tokens

    def convert(self, req: ConversionRequest) -> LLMResult:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "AnthropicProvider requires the 'anthropic' package: "
                "pip install 'sas2databricks[llm]'"
            ) from exc
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("Set ANTHROPIC_API_KEY to use AnthropicProvider.")

        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=req.system,
            messages=[{"role": "user", "content": req.prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content)
        return LLMResult(
            code=_extract_code(text),
            model=req.model,
            fulfilled=True,
            notes=[f"converted by Anthropic {self.model}"],
        )


class AzureOpenAIProvider(LLMProvider):
    """Convert via Azure OpenAI chat completions.

    Reads ``AZURE_OPENAI_ENDPOINT`` and ``AZURE_OPENAI_API_KEY`` from the environment.
    ``deployment`` is the Azure deployment name of the model.
    """

    def __init__(self, deployment: str, api_version: str = "2024-10-21") -> None:
        self.deployment = deployment
        self.api_version = api_version

    def convert(self, req: ConversionRequest) -> LLMResult:
        try:
            from openai import AzureOpenAI
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "AzureOpenAIProvider requires the 'openai' package: "
                "pip install 'sas2databricks[llm]'"
            ) from exc
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        key = os.environ.get("AZURE_OPENAI_API_KEY")
        if not (endpoint and key):
            raise RuntimeError(
                "Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY to use "
                "AzureOpenAIProvider."
            )

        client = AzureOpenAI(api_key=key, azure_endpoint=endpoint, api_version=self.api_version)
        resp = client.chat.completions.create(
            model=self.deployment,
            messages=[
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.prompt},
            ],
        )
        text = resp.choices[0].message.content or ""
        return LLMResult(
            code=_extract_code(text),
            model=req.model,
            fulfilled=True,
            notes=[f"converted by Azure OpenAI deployment '{self.deployment}'"],
        )


def provider_from_env() -> LLMProvider | None:
    """Pick a provider from environment variables, or ``None`` if none configured."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider()
    if os.environ.get("AZURE_OPENAI_ENDPOINT") and os.environ.get("AZURE_OPENAI_API_KEY"):
        deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
        return AzureOpenAIProvider(deployment=deployment)
    return None
