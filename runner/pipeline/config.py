"""配置加载：config.toml + 环境变量覆盖（零第三方依赖，用标准库 tomllib）。"""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

DEFAULTS: dict = {
    "llm": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DS_API_KEY",
        "temperature": 0.7,
        "max_tokens": 8000,
        "timeout": 180,
    },
    "paths": {"prompts_dir": ".", "sample_dir": "sample", "out_dir": "runner/out"},
    "verify": {
        "min_dialogue_samples": 20,
        "min_narration_samples": 5,
        "fuzzy_threshold": 0.90,
    },
}


def _merge(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(repo_root: Path, config_path: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(config_path) if config_path else repo_root / "runner" / "config.toml"
    if path.exists():
        _merge(cfg, tomllib.loads(path.read_text(encoding="utf-8")))
    cfg["_config_path"] = str(path) if path.exists() else None

    for env, (sec, key) in {
        "PERSONA_BASE_URL": ("llm", "base_url"),
        "PERSONA_MODEL": ("llm", "model"),
        "PERSONA_API_KEY_ENV": ("llm", "api_key_env"),
        "PERSONA_TIMEOUT": ("llm", "timeout"),
    }.items():
        if os.environ.get(env):
            val = os.environ[env]
            cfg[sec][key] = int(val) if key == "timeout" else val
    return cfg


def resolve_key(cfg: dict) -> tuple[str, str]:
    """返回 (key, 来源说明)。兼容 DS_API_KEY / PERSONA_API_KEY 两种写法。"""
    names = [cfg["llm"]["api_key_env"], "DS_API_KEY", "PERSONA_API_KEY",
             "DEEPSEEK_API_KEY", "OPENAI_API_KEY"]
    for n in names:
        if n and os.environ.get(n, "").strip():
            return os.environ[n].strip(), f"环境变量 {n}"
    return "", "未找到"


def repo_root_from(start: Path) -> Path:
    p = start.resolve()
    for _ in range(6):
        if (p / "01-input-spec.md").exists() or (p / ".git").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return start.resolve()
