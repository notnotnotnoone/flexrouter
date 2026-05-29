from __future__ import annotations
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import click
import httpx
import yaml

STANDARD_RL_HEADERS = {
    "rpm": "x-ratelimit-limit-requests",
    "tpm": "x-ratelimit-limit-tokens",
}


@dataclass
class ProviderDef:
    name: str
    base_url: str
    signup_url: str
    free: bool
    free_filter: Callable[[dict], bool]
    default_rpm: int
    default_tpm: int
    rate_limit_headers: dict = field(default_factory=lambda: dict(STANDARD_RL_HEADERS))
    ollama: bool = False


def _all_free(_model: dict) -> bool:
    return True


def _openrouter_free(model: dict) -> bool:
    pricing = model.get("pricing", {})
    try:
        return float(pricing.get("prompt", "1")) == 0.0
    except (ValueError, TypeError):
        return ":free" in model.get("id", "")


def _googleai_free(model: dict) -> bool:
    mid = model.get("id", "").lower()
    return "flash" in mid


PROVIDERS: list[ProviderDef] = [
    ProviderDef(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        signup_url="https://cloud.cerebras.ai",
        free=True,
        free_filter=_all_free,
        default_rpm=30,
        default_tpm=60_000,
    ),
    ProviderDef(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        signup_url="https://console.groq.com/keys",
        free=True,
        free_filter=_all_free,
        default_rpm=30,
        default_tpm=6_000,
    ),
    ProviderDef(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        signup_url="https://openrouter.ai/keys",
        free=True,
        free_filter=_openrouter_free,
        default_rpm=20,
        default_tpm=100_000,
    ),
    ProviderDef(
        name="googleai",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        signup_url="https://aistudio.google.com/apikey",
        free=True,
        free_filter=_googleai_free,
        default_rpm=15,
        default_tpm=1_000_000,
    ),
    ProviderDef(
        name="ollama",
        base_url="http://localhost:11434/v1",
        signup_url="https://ollama.com",
        free=True,
        free_filter=_all_free,
        default_rpm=600,
        default_tpm=10_000_000,
        ollama=True,
    ),
    ProviderDef(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        signup_url="https://platform.deepseek.com",
        free=False,
        free_filter=_all_free,
        default_rpm=60,
        default_tpm=200_000,
    ),
    ProviderDef(
        name="siliconflow",
        base_url="https://api.siliconflow.cn/v1",
        signup_url="https://cloud.siliconflow.cn",
        free=False,
        free_filter=_all_free,
        default_rpm=20,
        default_tpm=100_000,
    ),
    ProviderDef(
        name="sambanova",
        base_url="https://api.sambanova.ai/v1",
        signup_url="https://cloud.sambanova.ai",
        free=False,
        free_filter=_all_free,
        default_rpm=20,
        default_tpm=50_000,
    ),
]
