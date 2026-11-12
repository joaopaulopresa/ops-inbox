"""The model client: one narrow interface, three implementations.

* :class:`OpenRouterClient` calls the provider.
* :class:`RecordingClient` wraps any client and keeps every response on disk, keyed by a hash of
  the request, so a paid call is never made twice and the evaluation replays without a key.
* :class:`FakeModelClient` answers from a function, for unit and workflow tests.

The model is only ever asked for text or for JSON that matches a schema. It is given no tools.
"""

import base64
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from opsinbox.errors import ModelError, RecordingMissingError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImageInput:
    """An image handed to a model that accepts images."""

    data: bytes
    content_type: str


@dataclass(frozen=True)
class ModelRequest:
    """Everything that determines a model response."""

    model: str
    system: str
    user: str
    images: tuple[ImageInput, ...] = ()
    schema_name: str | None = None
    json_schema: dict[str, Any] | None = None
    temperature: float = 0.0
    max_tokens: int = 2000
    #: False switches the model's hidden reasoning off (cheaper, faster). None sends nothing.
    reasoning: bool | None = None
    #: Distinguishes otherwise identical requests (for example a second extraction pass).
    tag: str = ""

    def cache_key(self) -> str:
        """Stable digest of the request, used to store and find its recorded response."""
        canonical = {
            "model": self.model,
            "system": self.system,
            "user": self.user,
            "images": [hashlib.sha256(i.data).hexdigest() for i in self.images],
            "schema_name": self.schema_name,
            "json_schema": self.json_schema,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "reasoning": self.reasoning,
            "tag": self.tag,
        }
        blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class ModelResponse:
    """A model answer with what it cost."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_seconds: float = 0.0
    replayed: bool = False

    def json(self) -> dict[str, Any]:
        """Parse the answer as a JSON object.

        Raises:
            ModelError: when the answer is not a JSON object.

        """
        text = self.text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[4:] if text.startswith("json") else text
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ModelError(f"model did not return JSON: {self.text[:200]!r}") from exc
        if not isinstance(value, dict):
            raise ModelError("model returned JSON that is not an object")
        return value


class ModelClient(Protocol):
    """What the application needs from a model."""

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return the model answer for ``request`` or raise :class:`ModelError`."""


class OpenRouterClient:
    """Chat completions through OpenRouter."""

    def __init__(self, api_key: str, base_url: str, timeout: float, max_retries: int) -> None:
        if not api_key:
            raise ModelError("OPENROUTER_API_KEY is not set")
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "X-Title": "Ops Inbox"},
        )
        self._max_retries = max_retries

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Call the provider, retrying on transport errors and 5xx/429."""
        content: list[dict[str, Any]] = [{"type": "text", "text": request.user}]
        for image in request.images:
            encoded = base64.b64encode(image.data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image.content_type};base64,{encoded}"},
                }
            )
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": content if request.images else request.user},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "usage": {"include": True},
        }
        if request.reasoning is not None:
            body["reasoning"] = {"enabled": request.reasoning}
        if request.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name or "answer",
                    "strict": True,
                    "schema": request.json_schema,
                },
            }

        last_error = "no attempt made"
        for attempt in range(self._max_retries + 1):
            started = time.monotonic()
            try:
                response = self._client.post("/chat/completions", json=body)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    payload = response.json()
                    if payload.get("choices"):
                        usage = payload.get("usage") or {}
                        return ModelResponse(
                            text=payload["choices"][0]["message"].get("content") or "",
                            prompt_tokens=int(usage.get("prompt_tokens", 0)),
                            completion_tokens=int(usage.get("completion_tokens", 0)),
                            cost_usd=float(usage.get("cost", 0.0) or 0.0),
                            latency_seconds=time.monotonic() - started,
                        )
                    last_error = f"no choices in response: {str(payload)[:300]}"
                else:
                    last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                    if response.status_code not in {408, 429, 500, 502, 503, 504}:
                        break
            if attempt < self._max_retries:
                time.sleep(1.5 * (attempt + 1))
        raise ModelError(last_error)


RecordingMode = Literal["auto", "replay", "record"]


class RecordingClient:
    """Keep model responses on disk and answer from there when possible.

    Modes: ``auto`` replays when a recording exists and calls the inner client otherwise;
    ``replay`` never calls out (missing recording is an error); ``record`` always calls and
    overwrites.
    """

    def __init__(
        self,
        inner: ModelClient | None,
        directory: Path,
        mode: RecordingMode = "auto",
        read_only: tuple[Path, ...] = (),
    ):
        self._inner = inner
        self._directory = directory
        self._mode = mode
        #: Further directories searched for recordings (never written to).
        self._read_only = read_only
        self.calls = 0
        self.replays = 0

    def _path(self, key: str) -> Path:
        return self._directory / key[:2] / f"{key}.json"

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Answer from the recording, or call the inner client and record."""
        key = request.cache_key()
        path = self._path(key)
        if not path.exists():
            for directory in self._read_only:
                candidate = directory / key[:2] / f"{key}.json"
                if candidate.exists():
                    path = candidate
                    break
        if self._mode != "record" and path.exists():
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.replays += 1
            return ModelResponse(
                text=stored["text"],
                prompt_tokens=stored["prompt_tokens"],
                completion_tokens=stored["completion_tokens"],
                cost_usd=stored["cost_usd"],
                latency_seconds=stored["latency_seconds"],
                replayed=True,
            )
        if self._mode == "replay" or self._inner is None:
            raise RecordingMissingError(
                f"no recorded response for {request.schema_name or 'text'} request {key[:12]}"
            )
        response = self._inner.complete(request)
        self.calls += 1
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "model": request.model,
                    "schema_name": request.schema_name,
                    "tag": request.tag,
                    "text": response.text,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "cost_usd": response.cost_usd,
                    "latency_seconds": round(response.latency_seconds, 3),
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        return response


@dataclass
class FakeModelClient:
    """Answers from a function. Records the requests it received."""

    responder: Callable[[ModelRequest], str | dict[str, Any]]
    requests: list[ModelRequest] = field(default_factory=list)

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return what the responder returns, JSON-encoding dictionaries."""
        self.requests.append(request)
        answer = self.responder(request)
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return ModelResponse(text=text)
