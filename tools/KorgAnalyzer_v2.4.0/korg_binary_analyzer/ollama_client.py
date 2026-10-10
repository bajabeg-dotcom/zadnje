"""Minimal Ollama client.

Talks to a local Ollama server over its HTTP API using nothing but the standard
library, so the analyser keeps its "no third-party dependencies" promise.  No
cloud service is contacted: the host defaults to ``127.0.0.1:11434`` and the
only way to point it elsewhere is an explicit argument or ``OLLAMA_HOST``.

The client is deliberately small.  It exposes exactly what the analyser needs:
version, installed models, a streaming chat call and a streaming pull.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

DEFAULT_HOST = "http://127.0.0.1:11434"
USER_AGENT = "KorgBinaryAnalyzer"

#: How long to wait for the server to accept a connection, in seconds.  A local
#: Ollama answers instantly; a long timeout here would only hide "not running".
CONNECT_TIMEOUT = 5

#: Generation can legitimately take minutes on CPU, so no read timeout is set
#: on streaming calls -- the caller can cancel instead.


class OllamaError(RuntimeError):
    """Raised when the server is unreachable or returns an error."""


@dataclass
class ModelInfo:
    name: str
    size: int = 0
    family: str = ""
    parameters: str = ""
    quantization: str = ""
    modified: str = ""

    @property
    def human_size(self) -> str:
        if not self.size:
            return "-"
        if self.size < 1024 ** 3:
            return f"{self.size / 1024 ** 2:.0f} MB"
        return f"{self.size / 1024 ** 3:.1f} GB"

    @property
    def label(self) -> str:
        bits = [self.name, self.human_size]
        if self.parameters:
            bits.append(self.parameters)
        if self.quantization:
            bits.append(self.quantization)
        return "  ·  ".join(bits)


def normalize_host(host: str | None = None) -> str:
    """Accept ``localhost:11434``, ``http://host:port`` or an ``OLLAMA_HOST`` value."""
    value = (host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).strip()
    if not value:
        value = DEFAULT_HOST
    if "://" not in value:
        value = "http://" + value
    return value.rstrip("/")


class OllamaClient:
    def __init__(self, host: str | None = None, timeout: float = 600.0):
        self.host = normalize_host(host)
        self.timeout = timeout

    # ------------------------------------------------------------------ core
    def _request(self, path: str, payload: dict[str, Any] | None = None, method: str = "GET") -> Any:
        url = f"{self.host}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", "replace")[:400]
            except Exception:  # noqa: BLE001
                pass
            raise OllamaError(f"Ollama returned HTTP {error.code}: {detail or error.reason}") from error
        except urllib.error.URLError as error:
            raise OllamaError(
                f"Cannot reach Ollama at {self.host} ({error.reason}). "
                "Start it with `ollama serve`, or pass --host."
            ) from error
        except (TimeoutError, OSError) as error:
            raise OllamaError(f"Ollama request failed: {type(error).__name__}: {error}") from error

    def _stream(self, path: str, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        url = f"{self.host}{path}"
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "Accept": "application/x-ndjson",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", "replace")[:400]
            except Exception:  # noqa: BLE001
                pass
            raise OllamaError(f"Ollama returned HTTP {error.code}: {detail or error.reason}") from error
        except urllib.error.URLError as error:
            raise OllamaError(
                f"Cannot reach Ollama at {self.host} ({error.reason}). Is `ollama serve` running?"
            ) from error
        except (TimeoutError, OSError) as error:
            raise OllamaError(f"Ollama stream failed: {type(error).__name__}: {error}") from error

    # ---------------------------------------------------------------- status
    def version(self) -> str:
        payload = self._request("/api/version")
        return str(payload.get("version", "unknown"))

    def is_available(self) -> tuple[bool, str]:
        """Return ``(available, detail)`` without raising."""
        try:
            return True, self.version()
        except OllamaError as error:
            return False, str(error)

    def models(self) -> list[ModelInfo]:
        payload = self._request("/api/tags")
        result: list[ModelInfo] = []
        for item in payload.get("models", []):
            details = item.get("details") or {}
            result.append(ModelInfo(
                name=item.get("name", "?"),
                size=int(item.get("size") or 0),
                family=str(details.get("family") or ""),
                parameters=str(details.get("parameter_size") or ""),
                quantization=str(details.get("quantization_level") or ""),
                modified=str(item.get("modified_at") or ""),
            ))
        return sorted(result, key=lambda model: model.name)

    def running(self) -> list[str]:
        try:
            payload = self._request("/api/ps")
        except OllamaError:
            return []
        return [item.get("name", "?") for item in payload.get("models", [])]

    def has_model(self, name: str) -> bool:
        wanted = name.casefold()
        for model in self.models():
            if model.name.casefold() == wanted or model.name.casefold().split(":")[0] == wanted:
                return True
        return False

    def resolve_model(self, preferred: str | None = None) -> str | None:
        """Pick the model to use.

        Order: the exact preference, the same family as the preference, then the
        best installed Qwen by the recommended ladder, then any Qwen, then
        anything at all.  Plain alphabetical order would happily choose
        qwen2.5:3b over qwen2.5:7b, which is the wrong default for this
        workload -- a full report needs the roomier model.
        """
        available = self.models()
        if not available:
            return None
        by_name = {model.name.casefold(): model.name for model in available}
        if preferred:
            wanted = preferred.casefold()
            if wanted in by_name:
                return by_name[wanted]
            # The requested size is not installed, but its family may be.  Ask
            # for qwen2.5:14b on a machine holding 3b and 7b and the answer must
            # be 7b -- picking whichever tag sorts first would silently degrade
            # the assistant to the smallest model.
            base = wanted.split(":")[0]
            family = [model for model in available if model.name.casefold().split(":")[0] == base]
            if family:
                return suggest_model(family) or family[0].name
        return suggest_model(available) or available[0].name

    # ----------------------------------------------------------------- chat
    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        options: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Stream a chat completion.

        ``on_token`` is called for every fragment as it arrives.  ``cancel`` is
        polled between fragments so the caller can stop generation; the partial
        text is still returned along with ``cancelled=True``.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": options or {},
        }
        chunks: list[str] = []
        final: dict[str, Any] = {}
        for event in self._stream("/api/chat", payload):
            if cancel and cancel():
                return {
                    "content": "".join(chunks),
                    "model": model,
                    "cancelled": True,
                }
            if event.get("error"):
                raise OllamaError(str(event["error"]))
            piece = (event.get("message") or {}).get("content")
            if piece:
                chunks.append(piece)
                if on_token:
                    on_token(piece)
            if event.get("done"):
                final = event
        return {
            "content": "".join(chunks),
            "model": model,
            "cancelled": False,
            "done_reason": final.get("done_reason"),
            "total_duration": final.get("total_duration"),
            "eval_count": final.get("eval_count"),
            "prompt_eval_count": final.get("prompt_eval_count"),
        }

    # ----------------------------------------------------------------- pull
    def pull(
        self,
        model: str,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for event in self._stream("/api/pull", {"model": model, "stream": True}):
            if cancel and cancel():
                return {"status": "CANCELLED", "model": model}
            if event.get("error"):
                return {"status": "FAILED", "model": model, "error": str(event["error"])}
            last = event
            if on_progress:
                on_progress(event)
        return {"status": last.get("status", "DONE"), "model": model}


SUGGESTED_MODELS: tuple[tuple[str, str, str], ...] = (
    # (tag, approximate download, what it is for)
    ("qwen2.5:1.5b", "~1.0 GB", "fastest; small machines, short answers"),
    ("qwen2.5:3b", "~1.9 GB", "good balance on 8 GB RAM"),
    ("qwen2.5:7b", "~4.7 GB", "recommended: 32K context, handles a full report"),
    ("qwen2.5:14b", "~9.0 GB", "better reasoning if you have 16 GB+ RAM"),
    ("qwen2.5:32b", "~20 GB", "best quality; needs 32 GB RAM or a GPU"),
    ("qwen2.5-coder:7b", "~4.7 GB", "code-shaped answers"),
)


#: Preference ladder, best first.  The recommended size for a full report is
#: tried before the small ones, so a machine with several Qwen models installed
#: gets the capable one rather than merely the quickest one.
MODEL_PREFERENCE: tuple[str, ...] = (
    "qwen2.5:7b",
    "qwen2.5:14b",
    "qwen2.5:3b",
    "qwen2.5:32b",
    "qwen2.5-coder:7b",
    "qwen2.5:1.5b",
    "qwen2.5",
)


def suggest_model(installed: list[ModelInfo]) -> str:
    """Pick a sensible default from what is actually installed."""
    by_name = {model.name.casefold(): model.name for model in installed}
    for preference in MODEL_PREFERENCE:
        if preference in by_name:
            return by_name[preference]
    for preference in MODEL_PREFERENCE:
        base = preference.split(":")[0]
        for key, original in by_name.items():
            if key.split(":")[0] == base:
                return original
    for key, original in by_name.items():
        if "qwen" in key:
            return original
    return installed[0].name if installed else ""
