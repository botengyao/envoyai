"""04 — Canary / weighted split.

Serve 90% of traffic on the stable model, 10% on a new one. Good for gradual
rollouts. Same logical name; the client code doesn't change.
"""
from __future__ import annotations

import envoyai as ea

openai = ea.OpenAI(api_key=ea.env("OPENAI_KEY"))

gw = ea.Gateway()
gw.model("chat").route(
    primary={
        openai("gpt-4o-2024-08-06"): 9,   # stable, 90%
        openai("gpt-4o-2024-11-20"): 1,   # new, 10%
    },
)

gw.local()
