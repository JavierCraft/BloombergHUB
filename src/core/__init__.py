"""Shared plumbing: error taxonomy, cache, frame normalisation, source registry, health."""
from . import cache, frames, health, registry
from .errors import (
    BadRequest,
    HubError,
    Meta,
    MissingCredential,
    MissingDependency,
    NetworkBlocked,
    RateLimited,
    Timer,
    TradingDisabled,
    UpstreamError,
    fail,
    ok,
)
from .frames import json_safe, to_records
from .registry import SOURCES, Source

__all__ = [
    "cache", "frames", "health", "registry",
    "HubError", "MissingDependency", "MissingCredential", "NetworkBlocked",
    "UpstreamError", "RateLimited", "BadRequest", "TradingDisabled",
    "Meta", "Timer", "ok", "fail",
    "to_records", "json_safe", "SOURCES", "Source",
]
