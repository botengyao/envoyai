"""07 — Embeddings.

Register an embedding-capable model on the gateway, then call
``client.embeddings.create`` as usual.
"""
from __future__ import annotations

import envoyai as ea
from openai import OpenAI

openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))

gw = ea.Gateway()
gw.model("embed-small").route(primary=openai("text-embedding-3-small"))
gw.model("embed-large").route(primary=openai("text-embedding-3-large"))
gw.local()

client = OpenAI(base_url="http://localhost:1975", api_key="unused")

resp = client.embeddings.create(
    model="embed-small",
    input=["hello world", "goodbye world"],
)
print(len(resp.data), "vectors,", len(resp.data[0].embedding), "dimensions each")
