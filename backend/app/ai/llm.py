"""LLM client — vLLM behind its OpenAI-compatible API.

We speak HTTP to vLLM rather than importing it, so the API process never loads
a model into memory and the serving tier scales (or fails) independently.

The provider sits behind a narrow interface for one reason: the *grounding*
architecture matters far more than the vendor. Swapping models must never
require touching how facts are assembled.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings


class LlmUnavailable(RuntimeError):
    """Raised when generation cannot proceed.

    Callers must fall back to deterministic text rather than surfacing an
    error: a supervisor at 5:45 AM needs their three priorities, and 'the model
    is down' is not one of them.
    """


@dataclass(slots=True)
class Generation:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LlmProvider:
    """Thin async client. One method, one prompt, no agent framework.

    An agent framework here would add indirection over a call we deliberately
    keep boringly auditable — every generation is a single structured prompt
    over a pre-built fact bundle, logged in full.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout_seconds

    @property
    def enabled(self) -> bool:
        return settings.llm_enabled

    async def generate(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Generation:
        if not self.enabled:
            raise LlmUnavailable("LLM serving is disabled (LLM_ENABLED=false)")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Low temperature throughout: this model is rephrasing a fact
            # bundle into Tamil or English, not being creative.
            "temperature": temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise LlmUnavailable(str(exc)) from exc

        choice = body["choices"][0]["message"]["content"]
        usage = body.get("usage", {})
        return Generation(
            text=choice.strip(),
            model=body.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )


class OllamaProvider(LlmProvider):
    """The same OpenAI-compatible client, pointed at a local Ollama.

    Kaizen uses this rather than the shared vLLM tier for one reason that is not
    about cost: Step 2 sends a photograph of somebody's shop floor, and Step 1
    sends an operator's voice describing what is wrong with it. Keeping both on
    a machine inside the plant is the difference between a product a plant head
    will switch on and one their customer's IT team will not allow.

    Three capabilities, three models, each optional — every caller has a
    deterministic fallback, so a plant with no GPU still runs the whole journey.
    """

    def __init__(self, *, model: str | None = None) -> None:
        super().__init__(
            base_url=settings.ollama_base_url,
            api_key="ollama",
            model=model or settings.ollama_model,
            timeout=settings.ollama_timeout_seconds,
        )

    @property
    def enabled(self) -> bool:
        return settings.ollama_enabled

    async def see(
        self,
        *,
        system: str,
        user: str,
        images: Sequence[bytes],
        media_type: str = "image/jpeg",
        temperature: float = 0.1,
        max_tokens: int = 600,
    ) -> Generation:
        """Vision: read one photo and describe only what is in it.

        Images go as data URIs on an OpenAI-shaped ``image_url`` content part,
        which is what Ollama's /v1 surface accepts for a vision model.
        """
        if not self.enabled:
            raise LlmUnavailable("Ollama is disabled (OLLAMA_ENABLED=false)")
        if not images:
            raise LlmUnavailable("No image supplied")

        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for raw in images:
            encoded = base64.b64encode(raw).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                }
            )

        payload = {
            "model": settings.ollama_vision_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body = await self._post("/chat/completions", payload)
        message = body["choices"][0]["message"]["content"]
        usage = body.get("usage", {})
        return Generation(
            text=message.strip(),
            model=body.get("model", settings.ollama_vision_model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embeddings for the pattern detector's meaning fingerprints (§10).

        Clustering falls back to token overlap when this is unavailable, so a
        plant without the embedding model still gets pattern detection — just a
        blunter version of it.
        """
        if not self.enabled:
            raise LlmUnavailable("Ollama is disabled (OLLAMA_ENABLED=false)")
        if not texts:
            return []
        body = await self._post(
            "/embeddings",
            {"model": settings.ollama_embedding_model, "input": list(texts)},
        )
        rows = sorted(body.get("data", []), key=lambda row: row.get("index", 0))
        return [[float(value) for value in row["embedding"]] for row in rows]

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}{path}",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
            raise LlmUnavailable(f"{path}: {exc}") from exc


# The one system prompt the product uses. Its whole job is to stop the model
# doing arithmetic — an LLM that hallucinates a rupee figure into an owner's
# WhatsApp report destroys the credibility of every number in the product.
GROUNDING_SYSTEM_PROMPT = """You are FactoryManager AI, speaking to staff in an Indian MSME manufacturing plant.

ABSOLUTE RULES:
1. Every number you write MUST be copied verbatim from the FACTS given to you.
   Never calculate, never estimate, never round, never infer a number.
2. If the FACTS do not answer the question, say exactly what you do know and
   what you do not. Never fill a gap with a plausible guess.
3. Name machines, reasons, parts and people exactly as the FACTS spell them.
4. Never attribute a problem to a named individual. Talk about machines,
   reasons and processes. This plant runs a no-blame rule.
5. Be short. A supervisor reads this on a phone before a shift.

Write in {language}. Use the ₹ symbol for money exactly as shown in the FACTS."""
