from functools import lru_cache

from app.core.config import settings
from app.services.llm.base import LLMProvider
from app.services.llm.gemini import GeminiProvider
from app.services.llm.ollama import OllamaProvider
from app.services.llm.retry import build_provider


@lru_cache
def get_llm() -> LLMProvider:
    if settings.llm_provider == "ollama":
        return build_provider(OllamaProvider())
    return build_provider(GeminiProvider())
