# envoyai

**A production-grade LLM gateway, configured in Python.** Two lines for the quickstart, typed Python for anything beyond. Built on [Envoy AI Gateway](https://github.com/envoyproxy/ai-gateway): routing, retries, provider translation, and auth injection all happen in a real Envoy proxy — not inside your Python process. No YAML, no cluster knowledge, no client-side provider juggling.

## Quick start

```bash
pip install envoyai[client]
export OPENAI_API_KEY=sk-...
```

Two lines of Python:

```python
import envoyai as ea
resp = ea.complete(model="gpt-4o-mini",
                   messages=[{"role": "user", "content": "hi"}])
```

An implicit, process-wide gateway is marked active against `127.0.0.1:1975`; `gpt-*` / `claude-*` / `anthropic.*` / `command-*` model names auto-register to the right provider. For anything beyond the happy path, drop to the full builder:

```python
import envoyai as ea

gw = ea.Gateway()
gw.model("chat").route(
    primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))("gpt-4o-mini")
)
gw.local()                              # renders YAML, spawns `aigw run`,
                                        # waits for readiness on :1975

resp = gw.complete("chat", "hi")
print(resp.choices[0].message.content)
```

Any OpenAI-compatible client (any language) can hit the gateway too:

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:1975", api_key="unused")
client.chat.completions.create(model="chat",
                               messages=[{"role": "user", "content": "hi"}])
```

Your application keeps using the OpenAI SDK it already has. The real provider key stays server-side in the gateway's auth policy and is injected upstream — it never touches the Python client.

## Three entry points

| Style | File | When to use |
|---|---|---|
| **Two-liner** — [`examples/00_two_liner.py`](examples/00_two_liner.py) | `ea.complete(model=..., messages=...)` with an implicit singleton gateway | Quick scripts, trying things out |
| **SDK mode** — [`examples/a_sdk_mode.py`](examples/a_sdk_mode.py) | `gw = ea.Gateway()` + `gw.local()` + `gw.complete()` | Explicit gateway in the same process |
| **Proxy mode** — [`examples/b_proxy_mode/`](examples/b_proxy_mode/) | `gw.serve()` blocking + any OpenAI client (any language) | Multi-language stacks, shared dev servers |

Same `Gateway` config drives all three.

## Why envoyai

- **The proxy is Envoy, not Python.** Routing, retries, circuit breaking, and cost attribution run in an `aigw` subprocess that this library manages for you. Python clients point at `http://127.0.0.1:<port>` with a placeholder `api_key="unused"`; the real upstream key lives server-side.
- **Every language, one config.** Because the gateway is a real proxy, Python, Node, Go, Ruby, and curl clients all see identical behavior without a per-language SDK.
- **Typed Python config, not growing YAML.** Providers, routes, fallbacks, retries, and budgets are Pydantic-validated objects with IDE autocomplete. `Gateway._validate()` reports every problem in a single error before any subprocess starts.
- **No module-level mutable state.** All configuration lives on a `Gateway` instance; a regression test fails the build if anyone adds global config knobs at `envoyai.*`.
- **Cost from metrics, not hardcoded tables.** Spend will be computed from gateway-emitted token counts against [versioned price sheets](src/envoyai/_internal/prices/). Historical queries use the sheet in effect at the time of the request.
- **Safe-by-default privacy.** Auth headers redacted; prompt and response bodies stay out of logs and callbacks unless you opt in via `gw.privacy(...)`.
- **Structured, typed errors** with always-populated fields (`retry_after_s`, `provider`, `model`, `trace_id`) and a `.cause` attribute for underlying exceptions.

## Install

```bash
pip install envoyai              # library
pip install envoyai[client]      # + openai SDK for the calling side
pip install envoyai[admin]       # + admin UI backend (coming)
pip install envoyai[dev]         # + pytest, mypy, ruff for contributors
```

On the first call to `gw.local()` / `gw.serve()`, envoyai downloads the pinned [`aigw`](https://github.com/envoyproxy/ai-gateway) binary into `~/.cache/envoyai/bin/` and runs from there — no Go toolchain required. Subsequent calls reuse the cache.

Escape hatches for the auto-download:

| | |
|---|---|
| **Pre-fetch** | `envoyai download-aigw` (useful in CI, Dockerfile `RUN`, air-gapped installs) |
| **Bring your own binary** | `export ENVOYAI_AIGW_PATH=/path/to/aigw` |
| **Use a `$PATH` install** | `brew install aigw` or `go install github.com/envoyproxy/ai-gateway/cmd/aigw@latest` — anything on `PATH` wins over the cache |
| **Check what got resolved** | `envoyai where` |

Supported auto-download targets: `linux/amd64`, `linux/arm64`, `darwin/arm64`. Other platforms need a locally-built binary (set `ENVOYAI_AIGW_PATH`).

## Providers

Typed classes for every backend: `OpenAI`, `AzureOpenAI`, `Bedrock`, `AWSAnthropic`, `Anthropic`, `Cohere`, `GCPVertex`, `GCPAnthropic`, and any OpenAI-compatible endpoint (vLLM, Ollama, text-generation-inference, self-hosted proxies) by passing `base_url` to `OpenAI`.

**Today's runtime scope.** `gw.local()` / `gw.serve()` render and run for these configurations:

- **Providers**: `OpenAI`, `Anthropic` (native API). Multiple providers coexist in one `Gateway`; each logical model can point at either.
- **Auth**: `ea.env(...)` (rendered as `${VAR}` for `aigw`'s envsubst).
- **Routing**: one primary per route, plus an ordered `fallbacks=[...]` chain of additional providers.
- **Retry / failover**: `RetryPolicy` renders end-to-end (attempts, per-retry timeout, backoff, product-level retry reasons). One `RetryPolicy` per `Gateway` today — differing policies across routes are not yet supported.

Anything else the builder accepts (Bedrock, Azure, Vertex, Cohere, AWSAnthropic, GCPAnthropic, weighted splits, `Budget`, custom `Timeouts`) raises a clear `NotImplementedError` from the renderer and is landing release by release — see the [changelog](CHANGELOG.md).

## Examples

[`examples/`](examples/) has one short, self-contained script per task:

| # | File | What it shows |
|---|---|---|
| 00 | [two_liner](examples/00_two_liner.py) | `ea.complete()` with auto-registration |
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

The builder accepts every example's configuration today; the subset in **Today's runtime scope** above is what renders and runs through `aigw` right now. The rest will light up release by release — see the [changelog](CHANGELOG.md).

## Status

Alpha. Shipped today:

- **Builder API** — `Gateway`, eight provider classes, auth helpers (env/secret/header + `aws`/`azure`/`gcp` namespaces), `RetryPolicy` / `Budget` / `Privacy` / `Timeouts`, typed errors.
- **Runtime** — `Gateway.local()` (background subprocess), `Gateway.serve()` (foreground), `Gateway.complete()` / `acomplete()`, module-level `envoyai.complete()` / `acomplete()` backed by an implicit singleton.
- **Renderer** — `OpenAI` and `Anthropic` providers, primary + ordered `fallbacks=[...]` chains, `RetryPolicy` via `BackendTrafficPolicy` (product-level reasons → Envoy triggers + status codes), `ea.env(...)` auth. Designed so each additional API-key provider is a one-row registry addition.
- **Binary management** — first-call auto-download of the pinned `aigw` release, `envoyai download-aigw` / `where` / `version` CLI, `ENVOYAI_AIGW_PATH` escape hatch.

Coming in upcoming releases, roughly in order:

- **Bedrock renderer** (three AWS credential modes) — lights up the full failover example once AWS-credentialed backends can participate in the fallback chain.
- **Timeouts**, weighted Split, aliases.
- **Cost ledger** against versioned price sheets; then `Budget` alerts and enforcement.
- **Azure**, Cohere, GCP Vertex, AWSAnthropic, GCPAnthropic renderers.
- **Kubernetes output** — `render_k8s()` / `apply()` / `diff()` / `deploy()`.
- **Admin UI** backend + SPA.

Track the detailed roadmap and every release's additions in [`CHANGELOG.md`](CHANGELOG.md).

Developed at [github.com/botengyao/envoyai](https://github.com/botengyao/envoyai).
