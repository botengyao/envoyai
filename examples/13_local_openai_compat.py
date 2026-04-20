"""13 — Self-hosted / OpenAI-compatible upstreams.

Point an ``OpenAI`` provider at any OpenAI-compatible server: vLLM, Ollama,
text-generation-inference, a self-hosted Together endpoint, or a remote
proxy. Same provider class, different ``base_url``.
"""
from __future__ import annotations

import envoyai as ea

ollama = ea.OpenAI(
    api_key=ea.env("OLLAMA_KEY"),   # Ollama ignores this; still required by the SDK
    base_url="http://ollama.internal:11434",
)
vllm = ea.OpenAI(
    api_key=ea.env("VLLM_KEY"),
    base_url="http://vllm.internal:8000",
)
cloud = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))

gw = ea.Gateway()
# Prefer self-hosted for cheap traffic; spill over to cloud under load.
gw.model("chat").route(
    primary=ollama("llama3.2"),
    fallbacks=[vllm("meta-llama/Llama-3.1-70B-Instruct"), cloud("gpt-4o-mini")],
    retry=ea.RetryPolicy(on=["server_error", "connection_error"]),
)

gw.local()
