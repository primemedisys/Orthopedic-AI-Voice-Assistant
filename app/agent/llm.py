"""
app/agent/llm.py
────────────────
One shared LLM instance for the whole app.
A 15s timeout and 2 retries keep the caller from waiting in silence if OpenAI is slow.
"""

from functools import lru_cache
from langchain_openai import ChatOpenAI # type: ignore
from app.config import settings


# @lru_cache
# def get_llm() -> ChatOpenAI:
#     """LLM for live replies: same model, but the reply length is capped so speech stays short and fast."""
#     return ChatOpenAI(
#         model=settings.OPENAI_MODEL,
#         api_key=settings.OPENAI_API_KEY,
#         temperature=0.5,
#         timeout=15,
#         max_retries=2,
#         max_tokens=settings.REPLY_MAX_TOKENS,
#     )

def _base_kwargs(temperature: float) -> dict:
    """Settings shared by both clients."""
    kwargs = {
        "model": settings.OPENAI_MODEL,
        "api_key": settings.OPENAI_API_KEY,
        "timeout": 15,
        "max_retries": 2,
    }
    if not settings.OPENAI_MODEL.startswith("gpt-5"):
        kwargs["temperature"] = temperature
    return kwargs


@lru_cache
def get_llm() -> ChatOpenAI:
    """General client: extraction (name, date of birth) and call summaries."""
    return ChatOpenAI(**_base_kwargs(0.3))


@lru_cache
def get_agent_llm() -> ChatOpenAI:
    """Live replies: same model, but the length is capped so speech stays short and fast."""
    return ChatOpenAI(**_base_kwargs(0.5), max_tokens=settings.REPLY_MAX_TOKENS)