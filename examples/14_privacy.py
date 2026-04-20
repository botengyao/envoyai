"""14 — Logging / privacy policy.

Defaults are safe: auth headers redacted, prompt and response bodies kept
out of logs and observability callbacks. Flip the toggles only when you have
a specific need (e.g. debugging a provider issue, regulated audit logging).
"""
from __future__ import annotations

import envoyai as ea

gw = ea.Gateway()
gw.model("chat").route(
    primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))("gpt-4o")
)

# Default: equivalent to ea.Privacy(redact_auth=True, log_prompts=False, log_responses=False)

# Example override: allow prompt logging in a dev environment
gw.privacy(ea.Privacy(
    redact_auth=True,      # keep auth redacted — never disable in production
    log_prompts=True,      # record prompts for debugging
    log_responses=False,   # keep responses out of logs
))

gw.local()
