# envoyai

**A production-grade LLM gateway, configured in Python.** Define providers, routes, fallbacks, retries, budgets, and privacy in one typed Python file — run it on your laptop with one line, or ship it to Kubernetes with one line. Built on [Envoy AI Gateway](https://github.com/envoyproxy/ai-gateway). No YAML, no cluster knowledge, no client-side provider juggling.

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

Point any OpenAI-compatible client at it:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:1975", api_key="unused")
client.chat.completions.create(
    model="chat",
    messages=[{"role": "user", "content": "hi"}],
)
```

Your application keeps using the OpenAI SDK it already has. Failover, retries, streaming, tool use, cost tracking, budgets, and privacy come from envoyai without changing call sites.

## Why envoyai

If you're coming from an in-process LLM router like LiteLLM, the shift is: **the gateway is a real proxy, not code running inside your application.** That changes what you get:

- **Typed Python config, not growing YAML.** Providers, routes, fallbacks, retries, and budgets are Pydantic-validated objects with IDE autocomplete. Build-time validation catches misconfiguration before any request runs; `Gateway._validate()` reports every problem in a single error, not one-at-a-time at request time.
- **Every language, one config.** Routing, retries, circuit breaking, and cost attribution run in Envoy — so Python, Node, Go, Ruby, and curl clients all get identical behavior without a per-language SDK.
- **No module-level mutable state.** All configuration lives on a `Gateway` instance. Multiple gateways coexist in one process. A regression test in this repo fails the build if anyone adds global config knobs.
- **Cost from metrics, not hardcoded tables.** Spend is computed at query time from gateway-emitted token counts against [versioned price sheets](src/envoyai/_internal/prices/). Historical queries use the sheet in effect at the time of the request.
- **Safe-by-default privacy.** Auth headers redacted; prompt and response bodies stay out of logs and observability callbacks unless you explicitly opt in via `gw.privacy(...)`.
- **Structured, typed errors** with always-populated fields (`retry_after_s`, `provider`, `model`, `trace_id`) and a `.cause` attribute for underlying exceptions.
- **One object, three outputs.** The same `Gateway` can `local()` on your laptop, `render_k8s()` for GitOps, or `deploy()` to a cluster with readiness polling. No separate YAML schema for "proxy mode."

## Install

```bash
pip install envoyai
```

Optional extras:

- `envoyai[client]` — the OpenAI SDK for the calling side
- `envoyai[admin]` — the admin UI backend
- `envoyai[dev]`    — pytest, mypy, ruff for contributors

## Providers

OpenAI, Azure OpenAI, AWS Bedrock, Anthropic (native and via Bedrock), Google Vertex AI (Gemini and Anthropic), Cohere, and any OpenAI-compatible endpoint — vLLM, Ollama, text-generation-inference, self-hosted proxies.

## Examples

[`examples/`](examples/) has one short, self-contained script per task:

| # | File | What it shows |
|---|---|---|
| 01 | [hello_world](examples/01_hello_world.py) | Single provider, one logical model |
| 02 | [multi_provider](examples/02_multi_provider.py) | All eight providers in one gateway |
| 03 | [failover](examples/03_failover.py) | Primary + fallback chain with retries |
| 04 | [canary](examples/04_canary.py) | 90/10 weighted split for gradual rollouts |
| 05 | [streaming](examples/05_streaming.py) | Streaming completions |
| 06 | [tool_use](examples/06_tool_use.py) | Function / tool calling |
| 07 | [embeddings](examples/07_embeddings.py) | Embedding endpoints |
| 08 | [vision](examples/08_vision.py) | Multi-modal (image) inputs |
| 09 | [async](examples/09_async.py) | Async / concurrent requests |
| 10 | [custom_retry](examples/10_custom_retry.py) | Retry on product-level reasons |
| 11 | [budget_per_team](examples/11_budget_per_team.py) | Per-team spend caps |
| 12 | [model_alias](examples/12_model_alias.py) | Vendor-agnostic model names |
| 13 | [local_openai_compat](examples/13_local_openai_compat.py) | vLLM / Ollama / self-hosted |
| 14 | [privacy](examples/14_privacy.py) | Redaction defaults and overrides |
| 15 | [handling_errors](examples/15_handling_errors.py) | Build-time and request-time errors |

## Status

Alpha. The builder API is stable; the runtime that powers `gw.local()`, `gw.deploy()`, and `gw.render_k8s()` lands next — see [`CHANGELOG.md`](CHANGELOG.md).

Developed at [github.com/botengyao/envoyai](https://github.com/botengyao/envoyai).
