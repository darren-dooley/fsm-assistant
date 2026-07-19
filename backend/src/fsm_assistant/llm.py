"""The injectable LLM client interface and its OpenAI implementation.

The concrete client is chosen at app construction (PRD story 33): production
injects OpenAIClient, tests inject a scripted fake. Nothing else in the
backend imports the openai package.
"""

from typing import Protocol


class LLMUnavailableError(Exception):
    """The LLM provider could not be reached or returned a provider error."""


class LLMClient(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str:
        """Send a chat-shaped message list, return the assistant's text.

        Raises LLMUnavailableError on any provider failure.
        """
        ...


class OpenAIClient:
    def __init__(self, model: str):
        from openai import OpenAI

        self._client = OpenAI()
        self._model = model

    def complete(self, messages: list[dict[str, str]]) -> str:
        from openai import OpenAIError

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
            )
        except OpenAIError as exc:
            raise LLMUnavailableError(str(exc)) from exc
        content = response.choices[0].message.content
        if content is None:
            raise LLMUnavailableError("The model returned an empty response.")
        return content
