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
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        try:
            # First attempt chat completions standard
            try:
                chat_resp = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    timeout=self.timeout,
                )
                output_text = chat_resp.choices[0].message.content or ""
            except Exception as chat_err:
                # Fallback to responses API if supported
                if hasattr(self.client, "responses"):
                    resp_obj = self.client.responses.create(
                        model=self.model_name,
                        input=prompt,
                        timeout=self.timeout,
                    )
                    output_text = getattr(resp_obj, "output_text", str(resp_obj))
                else:
                    raise chat_err

        except TimeoutError:
            raise
        except Exception as exc:
            # Mask API keys from exception representation
            err_msg = str(exc)
            if self.api_key and self.api_key in err_msg:
                err_msg = err_msg.replace(self.api_key, "[REDACTED_API_KEY]")
            raise RuntimeError(f"Generic model provider generation failed ({self.model_name} @ {self.base_url}): {err_msg}") from exc

        return AURAResponse(
            request_id=request_id,
            content=output_text,
            metadata={
                "provider": "generic",
                "model": self.model_name,
                "base_url": self.base_url,
            },
        )
