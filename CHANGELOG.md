# Changelog

All notable changes to envoyai are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- **Cost Analysis Center.** Versioned price sheets under
  `_internal/prices/`, ingestion of per-request token counts from the
  gateway, aggregation by team / user / model / tag, CLI + HTTP query
  layer. Historical queries resolve against the price sheet in effect
  at request time. `Budget` alerts and enforcement become a thin layer
  on the same ledger.
- **xDS remote control plane.** `Gateway.serve_xds(host, port)` exposes
  envoyai as a gRPC ADS server (v3, SotW initially; delta later). Envoy
  and `aigw` data planes connect with a minimal bootstrap pointing at
  the control plane and receive Listener / Route / Cluster / Endpoint /
  Secret updates streamed from the typed Python config. Enables one
  central envoyai controlling a fleet of data planes, reconfigurable
  live without restarts.
- **Framework integrations — Google ADK first.** Run ADK agents through
  an envoyai-managed gateway by pointing ADK's OpenAI-compatible model
  wrapper at the local `:1975` listener; routing, fallback, retry, and
  cost attribution happen once at the gateway rather than per-agent.
  Recipe + a thin helper to build the ADK model from a running
  `Gateway` handle. LangGraph / LlamaIndex recipes to follow.

### Added
- Initial scaffold: `Gateway` builder, eight providers (OpenAI, AzureOpenAI,
  Bedrock, AWSAnthropic, Anthropic, Cohere, GCPVertex, GCPAnthropic), auth
  helpers (`env`, `secret`, `header`, `aws`, `azure`, `gcp`), policy types
  (`RetryPolicy`, `Budget`, `Timeouts`, `Privacy`), typed error hierarchy.
- Target outputs on `Gateway`: `.local()`, `.deploy()`, `.render_k8s()`,
  `.apply()`, `.diff()` — currently stubs raising `NotImplementedError`.
- `examples/` directory with hero examples for both modes plus 15 feature
  demos: SDK mode (`a_sdk_mode.py`), proxy mode (`b_proxy_mode/`), and
  numbered feature examples covering multi-provider setup, failover, canary
  splits, streaming, tool use, embeddings, vision, async, custom retry,
  per-team budgets, aliases, self-hosted upstreams, privacy, error handling.
- `Gateway.complete(model, messages, ...)` and `Gateway.acomplete(...)` —
  call the gateway in SDK mode without importing the OpenAI SDK. Sync vs
  async is by method name; return type is fixed regardless of kwargs.
- `Gateway.serve(...)` — blocking foreground entrypoint for running the
  gateway as a persistent proxy service. Counterpart to `local()`.
- `envoyai.complete(model=..., messages=...)` and `envoyai.acomplete(...)`
  — module-level shortcuts backed by a process-wide singleton Gateway,
  created lazily on first call and torn down via `atexit`. Bare model
  names auto-register through a small catalog (`gpt-*` → OpenAI,
  `claude-*` → Anthropic, `anthropic.*` / `amazon.*` / `meta.*` →
  Bedrock, `command-*` / `rerank-*` → Cohere); the explicit
  `"provider/model"` form is always accepted. Auto-configuration uses
  SDK-conventional env vars: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `COHERE_API_KEY`, plus the AWS default credential chain and
  `AWS_REGION` for Bedrock.
- `examples/00_two_liner.py` — the two-line quickstart.
- Examples renamed env vars to SDK conventions (`OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY`, `COHERE_API_KEY`).
- `Gateway.local()` and `Gateway.serve()` now actually run: they render
  the Gateway to an `aigw`-compatible multi-doc YAML, spawn
  `aigw run <path>`, probe readiness on the data-plane port, and (for
  `local()`) return a `LocalRun` handle whose `.stop()` cleans up the
  subprocess and temp config. `Gateway.complete()` / `.acomplete()`
  dispatch through the resulting port rather than calling providers
  directly, so provider translation, routing, and auth injection all
  happen in the Envoy subprocess.
- Private `envoyai._internal.render.aigw_standalone` renders the YAML.
  Current scope: single OpenAI provider per route, API keys supplied via
  `envoyai.env(...)` (rendered as `${VAR}` placeholders so `aigw`'s
  envsubst resolves them at startup). Fallbacks, Split, retry, budgets,
  and other providers raise `NotImplementedError` with a clear pointer.
- Private `envoyai._internal.aigw_process` wraps the subprocess
  lifecycle (`find_aigw`, `spawn_background`, `run_foreground`,
  `probe_ready`, `stop_background`).
- New tests: renderer resource shapes, `local()` glue (mocked
  subprocess), `serve()` foreground path, missing-`aigw` error, and a
  skipped-by-default end-to-end integration test that runs when
  `aigw` is installed and `OPENAI_API_KEY` is set.
- `aigw` auto-download: `Gateway.local()` / `Gateway.serve()` resolve
  the binary in the order `$ENVOYAI_AIGW_PATH` → `$PATH` →
  `~/.cache/envoyai/bin/aigw-<version>` → download-and-cache from
  GitHub releases. Users no longer need `go install` as a prerequisite.
- `envoyai` CLI with three subcommands:
  `envoyai download-aigw` (pre-fetch the pinned binary for CI /
  Dockerfile `RUN`), `envoyai where` (print the resolved path), and
  `envoyai version` (show the envoyai + pinned aigw versions).
- Pinned `aigw` version: `0.5.0`. The envoyai release cadence bumps this
  alongside schema / renderer changes.
- `aigw_standalone` renderer now supports the `envoyai.Anthropic`
  provider alongside `envoyai.OpenAI`. Emits `AIServiceBackend`
  (schema `Anthropic`), `BackendSecurityPolicy` (type
  `AnthropicAPIKey`, sub-field `anthropicAPIKey`), and a
  `Backend`+`BackendTLSPolicy` pair pointed at `api.anthropic.com`.
  Multiple providers coexist in one `Gateway`, each on its own backend.
  Renderer refactored to a provider-spec table so adding the next API-key
  family is a one-row change. Unsupported providers now surface the list
  of supported ones in the error message.
- Fallback chains and `RetryPolicy` now render through `aigw`. Routes
  with `fallbacks=[...]` emit prioritized `backendRefs` on the
  `AIGatewayRoute` rule (primary = `priority: 0`, each fallback gets the
  next index). A `BackendTrafficPolicy` carries the retry envelope:
  `attempts` → `numRetries = attempts - 1`, `attempts_per_step` →
  `numAttemptsPerPriority`, `per_retry_timeout` / `backoff_base` /
  `backoff_max` → the `perRetry` block. `RetryPolicy.on` reasons map to
  Envoy triggers + HTTP status codes (`rate_limit` → 429 +
  `envoy-ratelimited`, `server_error` → 500/502/503/504, `timeout` → 504,
  `connection_error` → `connect-failure`/`reset`/`refused-stream`).
  Declaring `fallbacks=[...]` without an explicit `RetryPolicy` injects
  a sane failover default (one attempt per priority, retry on connection
  errors and 5xx) so the chain actually walks. One `RetryPolicy` per
  `Gateway` today — differing policies across routes raise
  `NotImplementedError`. Flagship proxy-mode example
  (`examples/b_proxy_mode/server.py`) now renders and runs end-to-end.
- README reorganized: hero quick start, two-modes section, `Why envoyai`
  differentiators, and a flat index of `examples/`.
- Smoke tests covering the public surface and the README example.

### Changed
- `Gateway` no longer takes a required `namespace`; the `namespace` kwarg
  moved to `render_k8s()` / `apply()` / `deploy()` / `diff()` where the
  Kubernetes output actually uses it.
- `Gateway.listener_port` → `Gateway.port`.
- `RetryPolicy.attempts_per_priority` → `attempts_per_step`.
- `Timeouts.backend` → `Timeouts.provider`.
- `openai>=1.40` is now a core dependency; the `envoyai[client]` extra
  is gone. `pip install envoyai` is all that's needed for
  `Gateway.complete()` / `envoyai.complete()` and every documented
  example. Proxy-mode consumers that live in a separate process still
  install `openai` on the app side as they did before.

### Removed
- Envoy- and CRD-flavored vocabulary from user-visible docstrings and error
  messages (internal implementation names stay under `envoyai._internal`).

[Unreleased]: https://github.com/botengyao/envoyai/compare/main...HEAD
