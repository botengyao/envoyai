"""01 — Hello world.

Single OpenAI model, laptop-only. Shortest possible envoyai setup.

Requires OPENAI_API_KEY in the environment. ``pip install envoyai``
already includes the OpenAI SDK used below.

Runtime note: ``gw.local()`` is a stub until the envoyai runtime lands.
"""
from __future__ import annotations

import envoyai as ea
from openai import OpenAI

gw = ea.Gateway()
gw.model("chat").route(
    primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))("gpt-4o-mini")
)

gw.local()  # gateway on http://localhost:1975

client = OpenAI(base_url="http://localhost:1975", api_key="unused")
resp = client.chat.completions.create(
    model="chat",
    messages=[{"role": "user", "content": "Say hi in one word."}],
)
print(resp.choices[0].message.content)
