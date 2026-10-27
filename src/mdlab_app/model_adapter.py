"""Opt-in DeepSeek JSON transport; no environment proxies, redirects or SDK retries."""

import http.client
import json
import os
import re
import socket
import time
from dataclasses import dataclass, field

from .errors import ProcessingError
from .model_contracts import PLANNING_CONTRACT, SYSTEM_PROMPT

ENDPOINT = "https://api.deepseek.com/chat/completions"


@dataclass(frozen=True)
class ModelConfig:
    enabled: bool = False
    model: str = "deepseek-flash"
    api_key: str = field(default="", repr=False)
    timeout_seconds: int = 30

    def __post_init__(self):
        if (type(self.enabled) is not bool or self.model not in ("deepseek-flash", "deepseek-v4-pro")
                or type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 60
                or not isinstance(self.api_key, str) or (self.enabled and not self.api_key.strip())
                or "\n" in self.api_key or "\r" in self.api_key):
            raise ProcessingError("MODEL_CONFIGURATION_ERROR", "Invalid local model configuration")

    @classmethod
    def from_environment(cls, model="deepseek-flash"):
        # No dotenv loading, key search, client override, or fallback provider.
        return cls(enabled=True, model=model, api_key=os.environ.get("DEEPSEEK_API_KEY", ""))

    def profile(self):
        return {"enabled": self.enabled, "provider": "deepseek", "model": self.model,
                "endpoint": ENDPOINT, "timeout_seconds": self.timeout_seconds,
                "temperature": 0, "max_tokens": 400, "thinking": "disabled", "max_attempts": 1}


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Non-finite JSON number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


def request_body(config, question):
    return {"model": config.model, "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps({"question": question}, ensure_ascii=False)}],
        "response_format": {"type": "json_object"}, "stream": False,
        "thinking": {"type": "disabled"}, "temperature": 0, "max_tokens": 400}


def deepseek_request(config, body):
    """One bounded HTTPS response; errors never expose response bodies or headers."""
    if not config.enabled:
        raise ProcessingError("MODEL_DISABLED", "Model planning is disabled")
    deadline = time.monotonic() + config.timeout_seconds
    connection = http.client.HTTPSConnection("api.deepseek.com", timeout=config.timeout_seconds)
    try:
        connection.request("POST", "/chat/completions", json.dumps(body, ensure_ascii=False).encode("utf-8"),
                           headers={"Content-Type": "application/json", "Authorization": "Bearer " + config.api_key})
        if connection.sock:
            connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
        response = connection.getresponse()
        if response.status != 200:
            code = ("MODEL_AUTH_ERROR" if response.status in (401, 403) else
                    "MODEL_RATE_LIMIT" if response.status == 429 else "MODEL_UPSTREAM_ERROR")
            raise ProcessingError(code, "Model provider rejected the request; no draft was adopted")
        content = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if connection.sock:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(8192, PLANNING_CONTRACT["max_response_bytes"] + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > PLANNING_CONTRACT["max_response_bytes"]:
                raise ProcessingError("MODEL_RESPONSE_LIMIT", "Model response exceeded the configured limit")
        if time.monotonic() >= deadline:
            raise TimeoutError()
        return bytes(content)
    except (TimeoutError, socket.timeout) as exc:
        raise ProcessingError("MODEL_TIMEOUT", "Model request timed out; no draft was adopted") from exc
    except (http.client.HTTPException, OSError) as exc:
        raise ProcessingError("MODEL_NETWORK_ERROR", "Model provider is unavailable; no draft was adopted") from exc
    finally:
        connection.close()


def decode_response(raw):
    """JSON mode is only a transport hint; local strict validation remains required."""
    try:
        if not isinstance(raw, bytes) or len(raw) > PLANNING_CONTRACT["max_response_bytes"]:
            raise ValueError()
        value = strict_json(raw.decode("utf-8"))
        choices = value["choices"]
        if not isinstance(choices, list) or len(choices) != 1 or choices[0]["finish_reason"] != "stop":
            raise ValueError()
        message = choices[0]["message"]
        if (message.get("role") != "assistant" or message.get("tool_calls") or message.get("function_call")
                or message.get("refusal") or not isinstance(message["content"], str)):
            raise ValueError()
        draft = strict_json(message["content"])
        usage = value.get("usage", {})
        tokens = {key: usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        if any(type(number) is not int or number < 0 for number in tokens.values()):
            raise ValueError()
        actual_model = value.get("model")
        if not isinstance(actual_model, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", actual_model):
            raise ValueError()
        return draft, tokens, actual_model
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError) as exc:
        raise ProcessingError("MODEL_PROTOCOL_ERROR", "Model response failed the planning protocol") from exc
