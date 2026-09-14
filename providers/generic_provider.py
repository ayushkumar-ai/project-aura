import ipaddress
import json
import logging
import socket
import urllib.parse
from typing import Any
from uuid import UUID

from openai import OpenAI

from app.config import settings
from core.models import AURAResponse
from interfaces.model import ModelInterface

logger = logging.getLogger("aura.generic_provider")

FORBIDDEN_METADATA_IPS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "100.100.100.200",  # Alibaba cloud metadata
})


def validate_endpoint_url(base_url: str, allow_local: bool = True) -> str:
    """Validate endpoint URL against SSRF attacks, cloud metadata exfiltration, and invalid schemes."""
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("base_url must be a non-empty string.")

    cleaned = base_url.strip()
    parsed = urllib.parse.urlparse(cleaned)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme '{parsed.scheme}'. Only http:// and https:// are supported.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("Invalid base_url: Missing hostname.")

    if hostname in FORBIDDEN_METADATA_IPS:
        raise ValueError(f"SSRF violation: Access to cloud metadata endpoint '{hostname}' is strictly forbidden.")

    # Check if IP address
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_link_local:
            raise ValueError(f"SSRF violation: Link-local IP addresses ({ip}) are forbidden.")
        if ip.is_loopback:
            if not allow_local:
                raise ValueError("SSRF violation: Local loopback endpoints are disabled by policy.")
        elif ip.is_private:
            if not allow_local:
                raise ValueError(f"SSRF violation: Private network address ({ip}) is disabled by policy.")
    except ValueError as e:
        # Not a raw IP, check hostname string
        if "forbidden" in str(e) or "disabled" in str(e):
            raise
        if hostname in ("localhost", "127.0.0.1", "::1"):
            if not allow_local:
                raise ValueError("SSRF violation: Localhost access is disabled by policy.")

    # Return normalized base URL without trailing slash
    return cleaned.rstrip("/")


class GenericOpenAICompatibleProvider(ModelInterface):
    """Universal model provider supporting standard OpenAI-compatible HTTP inference endpoints."""

    def __init__(
        self,
        base_url: str,
        model_name: str,
        api_key: str = "",
        custom_headers: dict[str, str] | None = None,
        timeout: float | None = None,
        allow_local_endpoints: bool = True,
        client: OpenAI | None = None,
    ):
        self.allow_local_endpoints = allow_local_endpoints
        self.base_url = validate_endpoint_url(base_url, allow_local=self.allow_local_endpoints)

        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name must be a non-empty string.")
        self.model_name = model_name.strip()

        self.api_key = api_key.strip() if isinstance(api_key, str) else ""
        self.custom_headers = {str(k): str(v) for k, v in (custom_headers or {}).items()}
        self.timeout = float(timeout) if timeout is not None else float(getattr(settings, "aura_model_request_timeout_seconds", 30.0))

        if client is not None:
            self.client = client
        else:
            eff_key = self.api_key if self.api_key else "not-provided"
            self.client = OpenAI(
                base_url=self.base_url,
                api_key=eff_key,
                default_headers=self.custom_headers if self.custom_headers else None,
                timeout=self.timeout,
            )

    def generate(
        self,
        prompt: str,
        request_id: UUID,
    ) -> AURAResponse:
        """Execute a text generation call through the OpenAI-compatible endpoint."""
        import time
        from core.metrics import get_metrics_registry
        from core.tracing import Tracer
        from core.trace_types import SpanKind, SpanStatus

        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        tracer = Tracer(service_name="aura.llm")
        metrics = get_metrics_registry()
        start_time = time.time()

        with tracer.start_span(
            f"llm_generate:{self.model_name}",
            kind=SpanKind.CLIENT,
            attributes={
                "llm.provider": "generic",
                "llm.model": self.model_name,
                "llm.base_url": self.base_url,
            },
        ) as span:
            try:
                usage_dict: dict[str, int] | None = None
                prompt_tokens = 0
                completion_tokens = 0
                total_tokens = 0

                def _safe_int(v: Any) -> int:
                    if isinstance(v, int) and not isinstance(v, bool):
                        return v
                    if isinstance(v, float):
                        return int(v)
                    return 0

                # First attempt chat completions standard
                try:
                    chat_resp = self.client.chat.completions.create(
                        model=self.model_name,
                        messages=[{"role": "user", "content": prompt}],
                        timeout=self.timeout,
                    )
                    output_text = chat_resp.choices[0].message.content or ""
                    if hasattr(chat_resp, "usage") and chat_resp.usage is not None:
                        usage = chat_resp.usage
                        prompt_tokens = _safe_int(getattr(usage, "prompt_tokens", None))
                        completion_tokens = _safe_int(getattr(usage, "completion_tokens", None))
                        total_tokens = _safe_int(getattr(usage, "total_tokens", None)) or (prompt_tokens + completion_tokens)
                        if prompt_tokens > 0 or completion_tokens > 0 or total_tokens > 0:
                            usage_dict = {
                                "prompt_tokens": prompt_tokens,
                                "completion_tokens": completion_tokens,
                                "total_tokens": total_tokens,
                            }
                except Exception as chat_err:
                    # Fallback to responses API if supported
                    if hasattr(self.client, "responses"):
                        resp_obj = self.client.responses.create(
                            model=self.model_name,
                            input=prompt,
                            timeout=self.timeout,
                        )
                        output_text = getattr(resp_obj, "output_text", str(resp_obj))
                        if hasattr(resp_obj, "usage") and resp_obj.usage is not None:
                            usage = resp_obj.usage
                            prompt_tokens = _safe_int(getattr(usage, "prompt_tokens", None))
                            completion_tokens = _safe_int(getattr(usage, "completion_tokens", None))
                            total_tokens = _safe_int(getattr(usage, "total_tokens", None)) or (prompt_tokens + completion_tokens)
                            if prompt_tokens > 0 or completion_tokens > 0 or total_tokens > 0:
                                usage_dict = {
                                    "prompt_tokens": prompt_tokens,
                                    "completion_tokens": completion_tokens,
                                    "total_tokens": total_tokens,
                                }
                    else:
                        raise chat_err

                duration = time.time() - start_time
                span.set_status(SpanStatus.OK)
                span.record_resource_usage(
                    tokens=total_tokens,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cpu_ms=round(duration * 1000.0, 2),
                )

                try:
                    metrics.get_counter("aura_llm_requests_total").inc(
                        labels={"provider": "generic", "model": self.model_name, "status": "success"}
                    )
                    if total_tokens > 0:
                        metrics.get_counter("aura_llm_tokens_total").inc(
                            prompt_tokens, labels={"provider": "generic", "model": self.model_name, "type": "prompt"}
                        )
                        metrics.get_counter("aura_llm_tokens_total").inc(
                            completion_tokens, labels={"provider": "generic", "model": self.model_name, "type": "completion"}
                        )
                        metrics.get_counter("aura_llm_tokens_total").inc(
                            total_tokens, labels={"provider": "generic", "model": self.model_name, "type": "total"}
                        )
                    metrics.get_histogram("aura_llm_duration_seconds").observe(
                        duration, labels={"provider": "generic", "model": self.model_name}
                    )
                except Exception:
                    pass

                meta: dict[str, Any] = {
                    "provider": "generic",
                    "model": self.model_name,
                    "base_url": self.base_url,
                    "duration_seconds": round(duration, 4),
                }
                if usage_dict:
                    meta["usage"] = usage_dict

                return AURAResponse(
                    request_id=request_id,
                    content=output_text,
                    metadata=meta,
                )

            except TimeoutError as te:
                duration = time.time() - start_time
                span.record_exception(te)
                try:
                    metrics.get_counter("aura_llm_requests_total").inc(
                        labels={"provider": "generic", "model": self.model_name, "status": "timeout"}
                    )
                except Exception:
                    pass
                raise
            except Exception as exc:
                duration = time.time() - start_time
                span.record_exception(exc)
                try:
                    metrics.get_counter("aura_llm_requests_total").inc(
                        labels={"provider": "generic", "model": self.model_name, "status": "error"}
                    )
                except Exception:
                    pass
                # Mask API keys from exception representation
                err_msg = str(exc)
                if self.api_key and self.api_key in err_msg:
                    err_msg = err_msg.replace(self.api_key, "[REDACTED_API_KEY]")
                raise RuntimeError(f"Generic model provider generation failed ({self.model_name} @ {self.base_url}): {err_msg}") from exc

