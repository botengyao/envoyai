"""Proxy mode — the gateway as a foreground service.

``gw.serve()`` blocks until SIGINT/SIGTERM. Any OpenAI-compatible client in
any language can hit http://localhost:1975 while this is running. Pair with
``client_python.py`` or ``client_curl.sh`` in this directory.
"""
from __future__ import annotations

import envoyai as ea

openai    = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
anthropic = ea.Anthropic(api_key=ea.env("ANTHROPIC_API_KEY"))

gw = ea.Gateway()
gw.model("chat").route(
    primary=openai("gpt-4o"),
    fallbacks=[anthropic("claude-sonnet-4")],
    retry=ea.RetryPolicy.rate_limit_tolerant(),
)
gw.model("fast").route(primary=openai("gpt-4o-mini"))

gw.serve()  # blocks; Ctrl-C to shut down
