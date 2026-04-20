"""12 — Vendor-agnostic model aliases.

Give an internal model a stable name, then alias vendor-specific names to it
so existing code that says ``model="gpt-4"`` or ``model="claude-sonnet-4"``
keeps working while you migrate between providers.
"""
from __future__ import annotations

import envoyai as ea

openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))

gw = ea.Gateway()
gw.model("chat").route(primary=openai("gpt-4o"))

# All three of these client-facing names route to the same internal "chat".
gw.alias("gpt-4",           target="chat")
gw.alias("gpt-4-turbo",     target="chat")
gw.alias("claude-sonnet-4", target="chat")

gw.local()

# Application code can stay on its current model name; swap vendors by
# changing what "chat" routes to, not by editing every call site.
