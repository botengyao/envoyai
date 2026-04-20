# envoyai

Python SDK for [Envoy AI Gateway](https://github.com/envoyproxy/ai-gateway). Define an LLM gateway in Python — providers, routes, fallbacks, retries, budgets, privacy — and run it on your laptop with one line. No YAML, no cluster required.

```python
import envoyai as ea

openai    = ea.OpenAI(api_key=ea.env("OPENAI_KEY"))
anthropic = ea.Anthropic(api_key=ea.env("ANTHROPIC_KEY"))

gw = ea.Gateway()
gw.model("chat").route(
    primary=openai("gpt-4o"),
    fallbacks=[anthropic("claude-sonnet-4")],
    retry=ea.RetryPolicy.rate_limit_tolerant(),
)
gw.local()                                  # http://localhost:1975
```

Any OpenAI-compatible client now works against `localhost:1975`:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:1975", api_key="unused")
client.chat.completions.create(
    model="chat",
    messages=[{"role": "user", "content": "hi"}],
)
```

Your application keeps using the OpenAI SDK it already has. Failover, retries, streaming, tool use, cost tracking, and budgets come from envoyai without changing call sites.

## Install

```bash
pip install envoyai
```

Optional extras:

- `envoyai[client]` — pulls in the OpenAI SDK for the calling side
- `envoyai[admin]` — adds the admin UI backend
- `envoyai[dev]`    — pytest, mypy, ruff for contributors

## Features

- **Eight providers**: OpenAI, Azure OpenAI, AWS Bedrock, Anthropic (native and via Bedrock), Google Vertex (Gemini and Anthropic), Cohere, and any OpenAI-compatible endpoint (vLLM, Ollama, self-hosted).
- **Logical model names** decouple application code from vendor names; aliases let existing code on `model="gpt-4"` keep working.
- **Fallback chains** that walk a list of providers in order, with typed `RetryPolicy` that takes product reasons (`rate_limit`, `timeout`, `server_error`, `connection_error`) rather than wire triggers.
- **Weighted splits** for gradual rollouts and A/B testing.
- **Per-team budgets** driven by request headers (`x-team`, `x-user`), with alerts and optional hard enforcement.
- **Safe-by-default privacy**: auth headers redacted; prompt and response bodies kept out of logs unless you opt in.
- **Structured, typed errors** with `.cause` and always-populated fields (`retry_after_s`, `provider`, `model`, `trace_id`).
- **Ships to Kubernetes** with `gw.deploy()` when you're ready — but the laptop path requires no cluster knowledge.

## Examples

[`examples/`](examples/) has one short, self-contained script per task:

- [`01_hello_world.py`](examples/01_hello_world.py) — simplest setup
- [`02_multi_provider.py`](examples/02_multi_provider.py) — all eight providers
- [`03_failover.py`](examples/03_failover.py) — primary + fallback chain
- [`04_canary.py`](examples/04_canary.py) — 90/10 weighted split
- [`05_streaming.py`](examples/05_streaming.py) — streaming completions
- [`06_tool_use.py`](examples/06_tool_use.py) — function calling
- [`07_embeddings.py`](examples/07_embeddings.py) — embeddings
- [`08_vision.py`](examples/08_vision.py) — multi-modal inputs
- [`09_async.py`](examples/09_async.py) — async / concurrent
- [`10_custom_retry.py`](examples/10_custom_retry.py) — retry policy
- [`11_budget_per_team.py`](examples/11_budget_per_team.py) — per-team spend caps
- [`12_model_alias.py`](examples/12_model_alias.py) — vendor-agnostic names
- [`13_local_openai_compat.py`](examples/13_local_openai_compat.py) — vLLM / Ollama
- [`14_privacy.py`](examples/14_privacy.py) — logging and redaction
- [`15_handling_errors.py`](examples/15_handling_errors.py) — typed errors

## Status

Alpha. The builder API (providers, routes, policies, `Gateway`) is stable. The runtime that powers `gw.local()`, `gw.deploy()`, and `gw.render_k8s()` lands next — see [`CHANGELOG.md`](CHANGELOG.md).

Developed at [github.com/botengyao/envoyai](https://github.com/botengyao/envoyai).
