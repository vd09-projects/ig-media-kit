"""Config loader: parse, $IG_MK_CONFIG override, per-call override merge."""

from __future__ import annotations

import textwrap

import pytest

from ig_media_kit.config import (
    CONFIG_PATH_ENV,
    load_config,
    merge_overrides,
    resolve_config_path,
)

SAMPLE = textwrap.dedent(
    """
    channels: [natgeo, nike]
    top_reels:
      count: 5
      sort_by: play_count
      min_play_count: 100000
    fetch:
      scan_depth: 90
      max_pages_per_call: 4
      page_pace_seconds: 1.5
    output:
      store_dir: ./store
      media_dir: ./media
    """
)


def _write(tmp_path, text=SAMPLE):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_parses_channels_and_filters(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert cfg.channels == ["natgeo", "nike"]
    assert cfg.top_reels.count == 5
    assert cfg.top_reels.min_play_count == 100000
    assert cfg.fetch.max_pages_per_call == 4
    assert cfg.output.store_dir == "./store"


def test_env_override_resolves_path(tmp_path, monkeypatch):
    p = _write(tmp_path)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(p))
    assert resolve_config_path() == p
    cfg = load_config()  # no explicit path -> uses env
    assert cfg.channels == ["natgeo", "nike"]


def test_explicit_path_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_PATH_ENV, "/does/not/exist.yaml")
    p = _write(tmp_path)
    assert resolve_config_path(p) == p  # explicit wins


def test_per_call_override_merges_and_wins(tmp_path):
    cfg = load_config(_write(tmp_path), overrides={"fetch": {"max_pages_per_call": 2}})
    assert cfg.fetch.max_pages_per_call == 2      # override won
    assert cfg.fetch.scan_depth == 90             # untouched key preserved
    assert cfg.top_reels.count == 5


def test_merge_overrides_on_loaded_config(tmp_path):
    cfg = load_config(_write(tmp_path))
    merged = merge_overrides(cfg, {"channels": ["only_this"], "fetch": {"scan_depth": 30}})
    assert merged.channels == ["only_this"]
    assert merged.fetch.scan_depth == 30
    assert merged.fetch.max_pages_per_call == 4   # deep-merge kept sibling key
    # original config object is untouched (frozen dataclass, new instance returned)
    assert cfg.channels == ["natgeo", "nike"]


def test_missing_file_raises_with_path(tmp_path):
    missing = tmp_path / "nope.yaml"
    with pytest.raises(FileNotFoundError) as exc:
        load_config(missing)
    assert str(missing) in str(exc.value)
