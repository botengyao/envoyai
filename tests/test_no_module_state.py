"""Regression guard: ``envoyai`` must never grow module-level mutable state.

This is the #1 LiteLLM pain point — ``litellm.api_key``, ``litellm.success_callback``,
``litellm.drop_params``, ``litellm.model_cost`` and ~100 other module-level
mutable variables that users are expected to reach in and poke. That pattern
makes the library thread-unsafe, impossible to use with multiple concurrent
configurations, untestable, and full of spooky action at a distance.

envoyai keeps all configuration on ``Gateway`` instances. The top-level package
surface exposes only classes, functions, and submodules — nothing that anyone
is expected to mutate.

If a contributor ever adds ``envoyai.default_api_key = None`` or
``envoyai.success_callback: list = []`` at module level, this test fails and
forces the config to move to the ``Gateway`` object instead.
"""
from __future__ import annotations

import inspect
import types

import envoyai


_ALLOWLIST = {
    # Version string — a literal, not config.
    "__version__",
    # Private dunder / sunder names.
}


def _public_attrs() -> list[str]:
    return [n for n in dir(envoyai) if not n.startswith("_") or n in _ALLOWLIST]


def test_no_module_level_mutable_state() -> None:
    """Top-level ``envoyai.*`` exposes only classes, functions, modules.

    Mutable containers (list, dict, set) and plain mutable scalars (str, int,
    bool, float) at module level are banned — they become de-facto global
    config knobs that cause the LiteLLM pain.
    """
    offenders: list[str] = []
    for name in _public_attrs():
        if name in _ALLOWLIST:
            continue
        value = getattr(envoyai, name)
        if isinstance(value, (types.ModuleType, type)):
            continue
        if inspect.isfunction(value) or inspect.isbuiltin(value) or inspect.ismethod(value):
            continue
        # Anything else at the top level is suspect: a plain list, dict, scalar,
        # or instance likely represents global config.
        offenders.append(f"envoyai.{name} = {type(value).__name__}({value!r:.60})")

    assert not offenders, (
        "envoyai top-level namespace must contain only classes, functions, and "
        "submodules — no mutable config. Offenders:\n  " + "\n  ".join(offenders)
    )


def test_no_top_level_config_names() -> None:
    """Belt-and-suspenders: specific names we never want to see at module level.

    These are the exact names LiteLLM uses for global config. Even if someone
    adds them as @property or similar, we want a failure.
    """
    banned = {
        "api_key",
        "api_base",
        "api_version",
        "success_callback",
        "failure_callback",
        "input_callback",
        "drop_params",
        "model_cost",
        "cache",
        "caching",
        "max_tokens",
        "headers",
        "model_alias_map",
    }
    leaked = banned & set(dir(envoyai))
    assert not leaked, (
        f"envoyai exposed LiteLLM-style global config names: {sorted(leaked)}. "
        "Move this configuration onto Gateway or a dedicated policy object."
    )
