"""What a client writes in `model`, and what the router needs, are not the
same vocabulary. This is the only place that translates between them.

The owner's word for a group of models is "bucket" and settings files write
`buckets:`, but the internal field is still `FlexConfig.tiers` and the public
parameter is still `tier` (see the Bucket entry in CONTEXT.md and ADR 0009).
That split is deliberately confined to this module: everything outward-facing
here says bucket, and the one string handed back is called a tier by its
caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Target:
    kind: Literal["bucket", "pin"]
    name: str


def parse_model(model: str) -> Target:
    """Work out what a client meant by the string it put in `model`.

    A name containing "/" is a specific provider's model and pins it; bucket
    names never contain "/". Anything else names a bucket. The two older
    spellings this service used to advertise ("auto-smart", and
    "smart::groq/llama") are still understood, because chat clients save the
    model name in their own settings and would otherwise break on upgrade.
    """
    model = (model or "").strip()
    if not model:
        return Target("bucket", "auto")
    if "::" in model:
        # Legacy discovery id. The bucket half is dropped: the model half is
        # more specific, and pinning is now a real behaviour rather than the
        # no-op it used to be.
        _, _, rest = model.partition("::")
        if "/" in rest:
            return Target("pin", rest)
        return Target("bucket", rest or "auto")
    if "/" in model:
        return Target("pin", model)
    if model.startswith("auto-") and len(model) > len("auto-"):
        return Target("bucket", model[len("auto-"):])
    return Target("bucket", model)


def resolve(target: Target, bucket_names: list[str], best_bucket: str) -> str:
    """The string to hand the router as its `tier` argument.

    Raises KeyError naming the buckets that do exist, because that message is
    forwarded to whoever sent the request.
    """
    if target.kind == "pin":
        return target.name
    if target.name == "auto":
        return best_bucket
    if target.name in bucket_names:
        return target.name
    raise KeyError(
        f"There is no bucket named {target.name!r}. "
        f"Buckets you have: {', '.join(sorted(bucket_names)) or 'none yet'}.")


def bucket_id(name: str) -> str:
    return name


def model_id(provider: str, model: str) -> str:
    return f"{provider}/{model}"
