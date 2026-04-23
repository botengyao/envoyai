"""Client-side usage — standard OpenAI SDK, pointed at the running proxy.

No envoyai import on the client. Existing OpenAI-SDK code drops in with one
URL change.
"""
from __future__ import annotations

from openai import OpenAI

client = OpenAI(base_url="http://localhost:1975/v1", api_key="unused")

resp = client.chat.completions.create(
    model="chat",
    messages=[{"role": "user", "content": "Say hi in one word."}],
)
print(resp.choices[0].message.content)
