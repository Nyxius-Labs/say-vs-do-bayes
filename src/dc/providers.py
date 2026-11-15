from __future__ import annotations

import asyncio
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_S = 800.0


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip('"').strip("'")
        if not os.environ.get(k):
            os.environ[k] = v


def env_key(*names: str) -> str | None:
    for n in names:
        if os.environ.get(n):
            return os.environ[n]
    return None


KEY_ENV = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
}


@dataclass
class CallResult:
    text: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int | None = None
    billed_cost_usd: float | None = None
    latency_s: float = 0.0
    request_id: str | None = None
    served_model: str | None = None
    quirks: list[str] = field(default_factory=list)
    finish_reason: str | None = None
    upstream_provider: str | None = None


class ProviderError(Exception):
    def __init__(self, status: int | None, message: str, param: str | None = None, retry_after: float | None = None):
        super().__init__(f"HTTP {status}: {message[:500]}")
        self.status, self.message, self.param, self.retry_after = status, message, param, retry_after

    @property
    def rate_limited(self) -> bool:
        return self.status == 429

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status in (402, 429) or self.status >= 500


def _int(x) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def _retry_after(headers) -> float | None:
    try:
        v = headers.get("retry-after")
        return None if v is None else float(v)
    except (TypeError, ValueError, AttributeError):
        return None


class OpenRouterBackend:
    name = "openrouter"

    def __init__(self, api_key: str, base_url: str = OPENROUTER_BASE_URL, timeout: float = TIMEOUT_S,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def list_models(self) -> list[dict]:
        try:
            r = await self._client.get(f"{self.base_url}/models", headers=self._headers)
        except httpx.HTTPError as e:
            raise ProviderError(None, f"{type(e).__name__}: {e}")
        if r.status_code != 200:
            raise ProviderError(r.status_code, r.text)
        return r.json().get("data", [])

    def body(self, model_id, messages, params, max_tokens, temperature) -> dict:
        body = {"model": model_id, "messages": messages, "max_tokens": max_tokens, "stream": False,
                "usage": {"include": True}}
        if temperature is not None:
            body["temperature"] = temperature
        body.update(params or {})
        rf = body.get("response_format")
        if str(model_id).startswith("anthropic/") and rf and rf.get("type") == "json_schema":
            schema, _ = _anthropic_schema(rf["json_schema"]["schema"])
            body["response_format"] = {**rf, "json_schema": {**rf["json_schema"], "schema": schema}}
        return body

    async def complete(self, model_id, messages, params=None, max_tokens=300, temperature=None) -> CallResult:
        body = self.body(model_id, messages, params, max_tokens, temperature)
        t0 = time.monotonic()
        try:
            r = await self._client.post(f"{self.base_url}/chat/completions", headers=self._headers, json=body)
        except httpx.HTTPError as e:
            raise ProviderError(None, f"{type(e).__name__}: {e}")
        dt = time.monotonic() - t0
        if r.status_code != 200:
            msg, param = r.text, None
            try:
                err = r.json().get("error", {})
                msg, param = err.get("message") or msg, err.get("param")
            except Exception:
                pass
            raise ProviderError(r.status_code, msg, param, _retry_after(r.headers))
        try:
            d = r.json()
        except ValueError:
            raise ProviderError(None, f"malformed 200 body ({len(r.content)} bytes, not JSON)")
        return self.parse(d, dt, r.headers)

    @staticmethod
    def parse(d: dict, dt: float = 0.0, headers=None) -> CallResult:
        if d.get("error") and not d.get("choices"):
            err = d["error"]
            code = err.get("code")
            raise ProviderError(code if isinstance(code, int) else 502, str(err.get("message") or err))
        choice = (d.get("choices") or [{}])[0]
        if choice.get("error"):
            err = choice["error"]
            code = err.get("code")
            raise ProviderError(code if isinstance(code, int) else 502, str(err.get("message") or err))
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        u = d.get("usage") or {}
        details = u.get("completion_tokens_details") or {}
        rt = details.get("reasoning_tokens")
        cost = u.get("cost")
        return CallResult(
            text=content, input_tokens=_int(u.get("prompt_tokens")), output_tokens=_int(u.get("completion_tokens")),
            reasoning_tokens=None if rt is None else _int(rt),
            billed_cost_usd=None if cost is None else float(cost), latency_s=dt, request_id=d.get("id"),
            served_model=d.get("model"), finish_reason=choice.get("finish_reason") or choice.get("native_finish_reason"),
            upstream_provider=d.get("provider"))

    async def key_usage(self) -> dict:
        r = await self._client.get(f"{self.base_url}/key", headers=self._headers)
        if r.status_code != 200:
            raise ProviderError(r.status_code, r.text)
        d = r.json().get("data", {})
        return {"usage_usd": float(d.get("usage") or 0.0), "limit_usd": d.get("limit"),
                "limit_remaining_usd": d.get("limit_remaining")}

    async def aclose(self):
        await self._client.aclose()


def openrouter_key_usage(api_key: str, base_url: str = OPENROUTER_BASE_URL, transport=None) -> dict:
    with httpx.Client(timeout=60, transport=transport) as c:
        r = c.get(f"{base_url}/key", headers={"Authorization": f"Bearer {api_key}"})
        if r.status_code != 200:
            raise ProviderError(r.status_code, r.text)
        d = r.json().get("data", {})
        return {"usage_usd": float(d.get("usage") or 0.0), "limit_usd": d.get("limit"),
                "limit_remaining_usd": d.get("limit_remaining")}


_STOP = {"end_turn": "stop", "max_tokens": "length", "stop_sequence": "stop", "refusal": "refusal"}


_ANTHROPIC_UNSUPPORTED_SCHEMA = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}


def _anthropic_schema(schema):
    dropped = set()

    def walk(x):
        if isinstance(x, dict):
            out = {}
            for k, v in x.items():
                if k in _ANTHROPIC_UNSUPPORTED_SCHEMA and not isinstance(v, (dict, list)):
                    dropped.add(k)
                    continue
                out[k] = walk(v)
            return out
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return walk(schema), dropped


def anthropic_request(model_id, messages, params, max_tokens) -> tuple[dict, list[str]]:
    quirks = []
    p = dict(params or {})
    kw = {"model": model_id, "max_tokens": max_tokens, "messages": messages}
    rf = p.pop("response_format", None)
    oc = dict(p.pop("output_config", {}) or {})
    if rf:
        schema = (rf.get("json_schema") or {}).get("schema") if rf.get("type") == "json_schema" else None
        if schema is None:
            raise ValueError(f"unsupported response_format for Anthropic: {rf}")
        schema, dropped = _anthropic_schema(schema)
        if dropped:
            quirks.append(f"JSON schema keywords {sorted(dropped)} removed (unsupported by Anthropic structured "
                          f"outputs, HTTP 400); the parser still rejects probabilities outside 0-100")
        oc["format"] = {"type": "json_schema", "schema": schema}
    if oc:
        kw["output_config"] = oc
    kw.update(p)
    return kw, quirks


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, api_key: str, timeout: float = TIMEOUT_S, http_client=None, base_url: str | None = None):
        import anthropic
        self._anthropic = anthropic
        kw = {"api_key": api_key, "max_retries": 0, "timeout": timeout}
        if http_client is not None:
            kw["http_client"] = http_client
        if base_url:
            kw["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kw)

    async def list_models(self) -> list[dict]:
        try:
            page = await self._client.models.list(limit=1000)
        except self._anthropic.APIStatusError as e:
            raise ProviderError(e.status_code, e.message)
        return [{"id": m.id, "display_name": getattr(m, "display_name", None)} for m in page.data]

    async def complete(self, model_id, messages, params=None, max_tokens=300, temperature=None) -> CallResult:
        a = self._anthropic
        kw, quirks = anthropic_request(model_id, messages, params, max_tokens)
        if temperature is not None:
            quirks.append("temperature not sent (Claude API rejects sampling parameters; API default is 1.0)")
        t0 = time.monotonic()
        try:
            msg = await self._client.messages.create(**kw)
        except a.APIStatusError as e:
            raise ProviderError(e.status_code, e.message, None, _retry_after(e.response.headers))
        except (a.APITimeoutError, a.APIConnectionError, a.APIResponseValidationError) as e:
            raise ProviderError(None, f"{type(e).__name__}: {e}")
        dt = time.monotonic() - t0
        text = "".join(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        details = getattr(u, "output_tokens_details", None)
        thinking = getattr(details, "thinking_tokens", None) if details is not None else None
        if thinking is None:
            thinking = None if any(b.type in ("thinking", "redacted_thinking") for b in msg.content) else 0
        inp = _int(u.input_tokens) + _int(getattr(u, "cache_read_input_tokens", 0)) + \
            _int(getattr(u, "cache_creation_input_tokens", 0))
        return CallResult(text=text, input_tokens=inp, output_tokens=_int(u.output_tokens), reasoning_tokens=thinking,
                          billed_cost_usd=None, latency_s=dt, request_id=msg.id, served_model=msg.model,
                          quirks=quirks, finish_reason=_STOP.get(msg.stop_reason, msg.stop_reason),
                          upstream_provider="anthropic")

    async def aclose(self):
        await self._client.close()


ANTHROPIC_REASONING_OFF = [
    {"label": "thinking.type=disabled", "params": {"thinking": {"type": "disabled"}}},
    {"label": "output_config.effort=low", "params": {"output_config": {"effort": "low"}}},
]
ANTHROPIC_REASONING_ON = {"label": "thinking.type=adaptive, output_config.effort=medium",
                          "params": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}}}


def make_provider(route: str):
    if route not in KEY_ENV:
        return None
    key = env_key(*KEY_ENV[route])
    if not key:
        return None
    if route == "openrouter":
        return OpenRouterBackend(key, os.environ.get("OPENROUTER_BASE_URL") or OPENROUTER_BASE_URL)
    return AnthropicBackend(key)


async def call_with_retry(provider, model_id, messages, params, max_tokens, temperature,
                          max_attempts=8, base_delay=1.0, max_delay=60.0, sleep=asyncio.sleep,
                          on_retry=None) -> CallResult:
    attempt = 0
    while True:
        try:
            return await provider.complete(model_id, messages, params, max_tokens, temperature)
        except ProviderError as e:
            attempt += 1
            if not e.retryable or attempt >= max_attempts:
                raise
            delay = random.uniform(0, min(max_delay, base_delay * 2 ** (attempt - 1)))
            if e.retry_after:
                delay = min(max_delay, max(delay, e.retry_after))
            if on_retry is not None:
                on_retry(e, attempt, delay)
            await sleep(delay)


_EXCLUDE = re.compile(r"(fast|batch|flex|preview|mini|nano|lite|thinking|reasoning|audio|search|realtime|vision|"
                      r"turbo|free|beta|exp|latest|pro|max|prime|highspeed|decisions|tts|transcribe|\d{4}-?\d{2}-?\d{2}|-\d{3,4}$|:)")


def resolve_model_id(pattern: str, catalog_ids: list[str]) -> tuple[str | None, list[str], str]:
    rx = re.compile(pattern)
    matches, clean = [], []
    for cid in catalog_ids:
        tail = cid.lower().replace("_", "-").rsplit("/", 1)[-1]
        m = rx.match(tail)
        if not m or tail[m.end():m.end() + 1].isalnum():
            continue
        matches.append(cid)
        rest = re.sub(r"-?instruct", "", tail[m.end():])
        if not _EXCLUDE.search(rest):
            clean.append(cid)
    if clean:
        chosen = min(clean, key=lambda s: (len(s), s))
        return chosen, matches, "plain variant" if len(clean) == 1 else f"shortest of {len(clean)} plain variants"
    if matches:
        return None, matches, "only non-standard variants matched; needs manual choice"
    return None, [], "no catalog match"


class MockProvider:
    def __init__(self, behavior, name="mock", input_tokens=700, output_tokens=20, reasoning_tokens=0,
                 billed_cost_usd=None, delay=0.0, catalog=None, usage_fn=None, reject_fn=None, full_fn=None):
        self.name = name
        self.behavior = behavior
        self.catalog = catalog or []
        self.usage_fn = usage_fn
        self.reject_fn = reject_fn
        self.full_fn = full_fn
        self.requests: list[dict] = []
        self.calls = 0
        self.it, self.ot, self.rt = input_tokens, output_tokens, reasoning_tokens
        self.billed = billed_cost_usd
        self.delay = delay

    async def complete(self, model_id, messages, params=None, max_tokens=300, temperature=None) -> CallResult:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        self.requests.append({"model_id": model_id, "params": params or {}, "max_tokens": max_tokens})
        if self.reject_fn and (err := self.reject_fn(model_id, params or {})):
            raise err
        if self.full_fn and (full := self.full_fn(model_id, messages, params or {}, max_tokens)) is not None:
            return CallResult(**{"finish_reason": "stop", "billed_cost_usd": self.billed, **full})
        out = self.behavior(messages)
        if isinstance(out, Exception):
            raise out
        u = {"input_tokens": self.it, "output_tokens": self.ot, "reasoning_tokens": self.rt}
        if self.usage_fn:
            u.update(self.usage_fn(model_id, params or {}))
        u.setdefault("finish_reason", "stop")
        return CallResult(text=out, **u, billed_cost_usd=self.billed, latency_s=self.delay,
                          request_id=f"mock-{self.calls}", served_model=model_id)

    async def list_models(self):
        return [{"id": c} if isinstance(c, str) else c for c in self.catalog]

    async def aclose(self):
        pass
