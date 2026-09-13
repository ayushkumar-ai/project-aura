from uuid import UUID

from openai import OpenAI

from core.models import AURAResponse
from interfaces.model import ModelInterface


class OpenAIProvider(ModelInterface):
    """OpenAI model provider for AURA."""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        client: OpenAI | None = None,
        timeout: float | None = None,
    ):
        if not model_name.strip():
            raise ValueError("OpenAI model name cannot be empty.")

        if client is None and not api_key.strip():
            raise ValueError("OpenAI API key cannot be empty.")

        self.model_name = model_name.strip()
        self.timeout = timeout
        if client is not None:
            self.client = client
        elif timeout is not None:
            try:
                self.client = OpenAI(api_key=api_key, timeout=timeout)
            except TypeError:
                self.client = OpenAI(api_key=api_key)
        else:
            self.client = OpenAI(api_key=api_key)

    def generate(
        self,
        prompt: str,
        request_id: UUID,
    ) -> AURAResponse:
        """Generate a response using the OpenAI Responses API."""

        try:
            response = self.client.responses.create(
                model=self.model_name,
                input=prompt,
            )
        except TimeoutError:
            raise
        except Exception as exc:
            raise RuntimeError(
                "OpenAI model generation failed."
            ) from exc

        return AURAResponse(
            request_id=request_id,
            content=response.output_text,
            metadata={
                "provider": "openai",
                "model": self.model_name,
            },
        )
