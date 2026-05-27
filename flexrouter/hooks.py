from __future__ import annotations
from dataclasses import dataclass, field
import tiktoken


@dataclass
class HookContext:
    messages: list[dict]
    hooks: list[str]
    token_counts: dict = field(default_factory=dict)
    vision: bool = False
    estimated_tokens: int = 0


class HookRunner:
    _HOOKS = {"detect_vision", "estimate_tokens"}

    def run(self, ctx: HookContext) -> HookContext:
        for hook in ctx.hooks:
            if hook not in self._HOOKS:
                raise ValueError(f"Unknown hook: {hook!r}. Available: {sorted(self._HOOKS)}")
            getattr(self, f"_hook_{hook}")(ctx)
        return ctx

    def _hook_detect_vision(self, ctx: HookContext) -> None:
        for msg in ctx.messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        ctx.vision = True
                        return

    def _hook_estimate_tokens(self, ctx: HookContext) -> None:
        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for msg in ctx.messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(enc.encode(content))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += len(enc.encode(part.get("text", "")))
        ctx.estimated_tokens = total
