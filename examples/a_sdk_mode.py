"""a — SDK mode.

Use envoyai as a library from a single Python process. The gateway runs as
a background subprocess (via ``gw.local()``), and calls go through
``gw.complete()``. No separate server terminal, no ``openai`` SDK import.

Good for: scripts, notebooks, single-machine prototypes, CI jobs.

If you want the gateway to run as a persistent service that many clients
(any language) can hit, see ``b_proxy_mode/`` next to this file.
"""
from __future__ import annotations

import envoyai as ea

gw = ea.Gateway()
gw.model("chat").route(
    primary=ea.OpenAI(api_key=ea.env("OPENAI_KEY"))("gpt-4o-mini")
)

gw.local()  # background gateway on http://localhost:1975

resp = gw.complete("chat", "Say hi in one word.")
print(resp.choices[0].message.content)
