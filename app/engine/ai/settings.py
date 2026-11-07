"""Per-firm AI settings (stored in firms.ai_settings). Safe by default: the client chatbot is OFF and, once on,
client-visible answers wait for advocate approval (OPEN-QUESTIONS #9: default on until pilot firms say otherwise)."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AiSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client_chatbot_enabled: bool = False
    require_approval_for_client: bool = True
    reading_level: Literal["simple", "standard", "technical"] = "simple"
    firm_prompt: str = Field(default="", max_length=1000)  # style notes only; cannot override the safety rules

    @classmethod
    def from_firm(cls, raw: dict[str, Any] | None) -> "AiSettings":
        try:
            return cls.model_validate(raw or {})
        except ValueError:
            return cls()  # corrupt settings fall back to the safe defaults
