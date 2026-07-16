"""Config loader — parses config.yaml (mirroring yt-media-kit) into a typed
object, resolves the ``$IG_MK_CONFIG`` path override, and supports per-call
override merge (call args shallow-merge over config defaults, call args win).

Task T1.1.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

# Env var that overrides the default config path.
CONFIG_PATH_ENV = "IG_MK_CONFIG"
DEFAULT_CONFIG_PATH = "config.yaml"


@dataclass(frozen=True)
class TopReelsFilter:
    """Selection filters applied over the accumulated reel pool (T2.8 ranking).

    ``min_play_count`` backs the ``min_views`` call arg; ``min_duration`` (seconds)
    and ``max_age_days`` back the like-named call args. All are optional and
    skipped when unset."""

    count: int = 5
    sort_by: str = "play_count"
    min_play_count: int = 0
    min_duration: float | None = None
    max_age_days: int | None = None


@dataclass(frozen=True)
class FetchSettings:
    """Politeness / effort knobs. See CLAUDE.md — these are load-bearing.

    ``page_pace_seconds`` is honoured ONLY off the synchronous path; the sync
    window call never sleeps.
    """

    scan_depth: int = 90
    max_pages_per_call: int = 4
    page_pace_seconds: float = 1.5


@dataclass(frozen=True)
class OutputSettings:
    store_dir: str = "./store"
    media_dir: str = "./media"


@dataclass(frozen=True)
class BatchSettings:
    """Async batch-runner knobs (T4). The batch runner is the system's ONLY
    sleeper; these size its cooldown waits + callback retries. Defaults are
    conservative-with-margin per CLAUDE.md's measured rate-limit behaviour
    (~6.6-min window escalating to ~13 min) — tune from ``probe/probe_batch.py``.

      * ``retries`` — bounded callback POST attempts before giving up (result
        stays durable + queryable regardless). It ALSO bounds the per-handle
        hard-block wait: the fill loop's stall guard is ``retries + 2``, so a
        permanently rate-limited handle sleeps out at most ~``retries`` escalating
        cooldowns (up to ``cooldown_cap_s`` each) before giving up — raising
        ``retries`` raises total block-wait, not just callback attempts.
      * ``backoff_base_s`` / ``backoff_cap_s`` — callback retry exponential
        backoff: ``min(cap, base * 2**attempt)`` (+ jitter).
      * ``cooldown_base_s`` — first metered-stop cooldown sleep (~6.6 min ≈ 396 s;
        400 with margin). NEVER polled — the FetchGate sleeps it out.
      * ``cooldown_escalation_factor`` — multiplier per successive metered stop
        (``base * factor**(escalation-1)``); IG escalates ~6.6→13 min ⇒ ~2.0.
      * ``cooldown_cap_s`` — hard upper bound on any single cooldown sleep.
      * ``per_job_page_budget`` — max feed pages one handle's fill may spend in a
        job (effort ceiling; the store is never destructively capped).
      * ``heartbeat_stale_s`` — a non-terminal job silent longer than this reads
        as ``dead-worker`` in ``get_batch_status``. MUST exceed the longest
        expected cooldown so a legitimately-sleeping job is not misreported."""

    retries: int = 5
    backoff_base_s: float = 2.0
    backoff_cap_s: float = 60.0
    cooldown_base_s: float = 400.0
    cooldown_escalation_factor: float = 2.0
    cooldown_cap_s: float = 1800.0
    per_job_page_budget: int = 200
    heartbeat_stale_s: float = 3600.0


@dataclass(frozen=True)
class Config:
    channels: list[str] = field(default_factory=list)
    top_reels: TopReelsFilter = field(default_factory=TopReelsFilter)
    fetch: FetchSettings = field(default_factory=FetchSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    batch: BatchSettings = field(default_factory=BatchSettings)
    # The raw parsed mapping, kept so per-call override merge can reach any key.
    raw: dict[str, Any] = field(default_factory=dict)


def resolve_config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve which config file to load.

    Priority: explicit arg > ``$IG_MK_CONFIG`` > ``./config.yaml``.
    """
    if explicit is not None:
        return Path(explicit)
    env_path = os.environ.get(CONFIG_PATH_ENV)
    if env_path:
        return Path(env_path)
    return Path(DEFAULT_CONFIG_PATH)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` onto ``base``. Override wins on scalar/list keys;
    nested dicts merge recursively. Returns a new dict (inputs untouched)."""
    out = copy.deepcopy(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def _config_from_mapping(data: dict[str, Any]) -> Config:
    top = data.get("top_reels") or {}
    fetch = data.get("fetch") or {}
    output = data.get("output") or {}
    batch = data.get("batch") or {}
    _batch_defaults = BatchSettings()
    return Config(
        channels=list(data.get("channels") or []),
        top_reels=TopReelsFilter(
            count=top.get("count", 5),
            sort_by=top.get("sort_by", "play_count"),
            min_play_count=top.get("min_play_count", 0),
            min_duration=top.get("min_duration"),
            max_age_days=top.get("max_age_days"),
        ),
        fetch=FetchSettings(
            scan_depth=fetch.get("scan_depth", 90),
            max_pages_per_call=fetch.get("max_pages_per_call", 4),
            page_pace_seconds=fetch.get("page_pace_seconds", 1.5),
        ),
        output=OutputSettings(
            store_dir=output.get("store_dir", "./store"),
            media_dir=output.get("media_dir", "./media"),
        ),
        batch=BatchSettings(
            retries=batch.get("retries", _batch_defaults.retries),
            backoff_base_s=batch.get("backoff_base_s", _batch_defaults.backoff_base_s),
            backoff_cap_s=batch.get("backoff_cap_s", _batch_defaults.backoff_cap_s),
            cooldown_base_s=batch.get("cooldown_base_s", _batch_defaults.cooldown_base_s),
            cooldown_escalation_factor=batch.get(
                "cooldown_escalation_factor", _batch_defaults.cooldown_escalation_factor),
            cooldown_cap_s=batch.get("cooldown_cap_s", _batch_defaults.cooldown_cap_s),
            per_job_page_budget=batch.get(
                "per_job_page_budget", _batch_defaults.per_job_page_budget),
            heartbeat_stale_s=batch.get(
                "heartbeat_stale_s", _batch_defaults.heartbeat_stale_s),
        ),
        raw=copy.deepcopy(data),
    )


def load_config(
    path: str | os.PathLike[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> Config:
    """Load config from disk and optionally shallow/deep-merge per-call overrides.

    ``overrides`` is a mapping shaped like the YAML (e.g.
    ``{"fetch": {"max_pages_per_call": 2}}``); it is deep-merged over the file
    contents with call args winning. Raises ``FileNotFoundError`` (with the
    resolved path in the message) if the file is absent.
    """
    resolved = resolve_config_path(path)
    if not resolved.exists():
        raise FileNotFoundError(
            f"loading config: no config file at {resolved} "
            f"(set ${CONFIG_PATH_ENV} or pass an explicit path)"
        )
    with resolved.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"loading config from {resolved}: top level must be a mapping")
    if overrides:
        data = _merge(data, overrides)
    return _config_from_mapping(data)


def merge_overrides(config: Config, overrides: dict[str, Any]) -> Config:
    """Return a new Config with ``overrides`` deep-merged over an already-loaded
    Config (call args win). Used for per-call overrides that arrive after load."""
    merged_raw = _merge(config.raw, overrides or {})
    return _config_from_mapping(merged_raw)
