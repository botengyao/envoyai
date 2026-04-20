"""15 — Handling errors.

Two error surfaces to know about:

* **Build-time**: ``Gateway._validate`` (called automatically from
  ``local()``, ``render_k8s()``, ``deploy()``) raises
  :class:`envoyai.errors.ConfigError` with *every* problem listed at once.
* **Request-time**: the OpenAI-compatible client raises its own exception
  types (``openai.RateLimitError``, ``openai.APIStatusError``, etc.). Those
  carry the upstream provider's status codes and headers directly.
"""
from __future__ import annotations

import envoyai as ea
from openai import APIStatusError, OpenAI, RateLimitError

# --- build time --------------------------------------------------------------

gw = ea.Gateway()
gw.model("chat")  # intentionally missing .route(primary=...)

try:
    gw._validate()
except ea.errors.ConfigError as e:
    print("Config problems:", e)

# --- request time ------------------------------------------------------------

gw.model("chat").route(primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))("gpt-4o"))
gw.local()

client = OpenAI(base_url="http://localhost:1975", api_key="unused")

try:
    resp = client.chat.completions.create(
        model="chat",
        messages=[{"role": "user", "content": "hi"}],
    )
except RateLimitError as e:
    # The provider returned 429. Retry-After, if set, is on the response headers.
    retry_after = e.response.headers.get("retry-after")
    print(f"rate limited; retry after {retry_after}s")
except APIStatusError as e:
    print(f"provider error {e.status_code}: {e.message}")
