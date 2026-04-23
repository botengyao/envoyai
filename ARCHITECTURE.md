# Architecture

This document explains how a Python `Gateway` object ends up serving
HTTP traffic — what runs where, which files land on disk, and how a
single client request walks through the system.

Four views:

1. [Whole system (build + runtime)](#1-whole-system-build--runtime)
2. [Request path detail](#2-request-path-detail)
3. [Where every rendered resource lives](#3-where-every-rendered-resource-lives)
4. [Control plane: today and where it's going](#4-control-plane-today-and-where-its-going)

---

## 1. Whole system (build + runtime)

One Python process owns the configuration; one `aigw` subprocess (Envoy
+ controller) owns the data plane. envoyai is the bridge between them.

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                                                                                   │
│  YOUR APP PROCESS                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐      │
│  │                                                                         │      │
│  │   ┌─ your code ──────────────────────────────────────────────┐          │      │
│  │   │   import envoyai as ea                                   │          │      │
│  │   │   gw = ea.Gateway(port=1975)                             │          │      │
│  │   │   gw.model("chat").route(                                │          │      │
│  │   │       primary=ea.OpenAI(api_key=ea.env("OPENAI_KEY"))    │          │      │
│  │   │              ("gpt-4o"),                                 │          │      │
│  │   │       fallbacks=[ea.Anthropic(...)("claude-sonnet-4")],  │          │      │
│  │   │       retry=ea.RetryPolicy.rate_limit_tolerant(),        │          │      │
│  │   │   )                                                      │          │      │
│  │   │   gw.local()       ◀── triggers the pipeline ──────┐     │          │      │
│  │   └──────────────────────────────────────────────────────────┘          │      │
│  │                                                        │                │      │
│  │                                                        ▼                │      │
│  │   ┌─ envoyai._internal ─────────────────────────────────────┐           │      │
│  │   │  (1) Gateway._validate()   one-pass structural check    │           │      │
│  │   │  (2) render/aigw_standalone.py                          │           │      │
│  │   │        Gateway  →  list[dict]  →  multi-doc YAML        │           │      │
│  │   │        (12 CRDs across 4 API groups, see below)         │           │      │
│  │   │  (3) write_config() → /tmp/envoyai-XXXXX.yaml           │           │      │
│  │   │  (4) aigw_process.spawn_background(...)                 │           │      │
│  │   │  (5) probe_ready(:1975/v1/models)                       │           │      │
│  │   └─────────────────────────────┬───────────────────────────┘           │      │
│  │                                 │  subprocess.Popen                     │      │
│  │   ┌─ openai SDK (same process) ─┴──┐                                    │      │
│  │   │  openai.OpenAI(                │   used by gw.complete() /          │      │
│  │   │    base_url=run.base_url,      │   envoyai.complete() /             │      │
│  │   │    api_key="unused")           │   gw.acomplete()                   │      │
│  │   └────────────────┬───────────────┘                                    │      │
│  │                    │ HTTP /v1/chat/completions                          │      │
│  └────────────────────┼────────────────────────────────────────────────────┘      │
│                       │                                                           │
│  AIGW SUBPROCESS      ▼                                                           │
│  ┌────────────────────────────────────────────────────────────────────────┐       │
│  │  aigw run <tmp.yaml>                                                   │       │
│  │                                                                        │       │
│  │  ┌─ controller ───────────────────────┐                                │       │
│  │  │  parse YAML + envsubst ${VARS}     │ ◀── reads OPENAI_KEY /         │       │
│  │  │  build in-mem Gateway/AIGatewayRoute/…   ANTHROPIC_KEY from the     │       │
│  │  │  program Envoy via xDS snapshot    │     env the parent Python      │       │
│  │  └────────────────┬───────────────────┘     process exported           │       │
│  │                   │                                                    │       │
│  │  ┌─ Envoy data plane ────────────────┐                                 │       │
│  │  │  listener :1975                   │ ◀── HTTP clients hit here       │       │
│  │  │   ↓ HTTP route match              │     (OpenAI SDK / curl / Node)  │       │
│  │  │   ↓ ExtProc: OpenAI→target format │                                 │       │
│  │  │   ↓ BackendTrafficPolicy retry    │                                 │       │
│  │  │   ↓ priority-based failover       │                                 │       │
│  │  │   ↓ injects Authorization / x-api-key                               │       │
│  │  │  admin :1064  (stats, config dump)│                                 │       │
│  │  └────────────┬──────────────┬───────┘                                 │       │
│  └───────────────┼──────────────┼─────────────────────────────────────────┘       │
│                  │              │                                                 │
└──────────────────┼──────────────┼─────────────────────────────────────────────────┘
                   │  TLS :443    │  TLS :443
                   ▼              ▼
              ┌──────────┐  ┌───────────┐        ┌───────────────────────────┐
              │ OpenAI   │  │ Anthropic │   ...  │ Bedrock / Azure / Vertex  │
              │ api.     │  │ api.      │        │ (gated today — renderer   │
              │ openai   │  │ anthropic │        │  raises NotImplementedErr)│
              │ .com     │  │ .com      │        └───────────────────────────┘
              └──────────┘  └───────────┘
```

**On-disk artifacts**

| Path | What |
|---|---|
| `/tmp/envoyai-XXXXX.yaml` | Rendered config; auto-deleted on `LocalRun.stop()` or `serve()` exit |
| `~/.cache/envoyai/bin/` | Cached `aigw` binary (first-call download of the pinned release) |

**Secrets**

Env vars (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, …) live only in process
memory. They are envsubst'd into the YAML inside `aigw` at startup, then
streamed into upstream requests as headers. They are never written back
to disk and never appear in the rendered YAML on disk (which holds
`${VAR}` placeholders until `aigw` resolves them in memory).

---

## 2. Request path detail

One call walking through `aigw`'s data plane. Primary backend is OpenAI;
on rate-limit or 5xx the `BackendTrafficPolicy` retries across to the
Anthropic fallback.

```
  client                aigw / Envoy                                  upstream
  ──────                ─────────────                                 ────────
  POST /v1/             ┌───────────────────────────────────────┐
  chat/                 │ :1975 listener                        │
  completions    ─────▶ │                                       │
  {"model":             │  HTTP header extract:                 │
    "chat",             │    x-ai-eg-model: chat                │
    ...}                │                                       │
                        │  AIGatewayRoute rule matches          │
                        │  backendRefs (priority asc):          │
                        │    p0 → team-a-openai                 │
                        │    p1 → team-a-anthropic              │
                        │                                       │
                        │  ExtProc filter:                      │
                        │    request body JSON rewrite          │
                        │      OpenAI format  →  target format  │
                        │                                       │
                        │  ┌── try priority 0 ──────────────┐   │       ┌──────────┐
                        │  │ add: Authorization: Bearer $K  │ ──┼─TLS──▶│ OpenAI   │
                        │  │ SNI api.openai.com             │   │       │ gpt-4o   │
                        │  └──────┬─────────────────────────┘   │       └────┬─────┘
                        │         │                             │            │
                        │     200 OK ─▶ translate response ─────┼────────────┘
                        │     429/5xx│                          │
                        │         ▼                             │
                        │  BackendTrafficPolicy:                │
                        │    retryOn: [429, 5xx, envoy-ratelim] │
                        │    numAttemptsPerPriority: 1          │
                        │    numRetries: 4                      │
                        │    perRetry.timeout: 60s              │
                        │    backOff: 100ms..10s exp            │
                        │         │                             │
                        │         ▼ walk to priority 1          │
                        │  ┌── try priority 1 ──────────────┐   │       ┌──────────┐
                        │  │ add: x-api-key: $K             │ ──┼─TLS──▶│ Anthropic│
                        │  │ SNI api.anthropic.com          │   │       │ claude-  │
                        │  └──────┬─────────────────────────┘   │       │ sonnet-4 │
                        │         │                             │       └────┬─────┘
                        │     200 OK ─▶ translate response ─────┼────────────┘
                        │         │                             │
  {"id": "...",  ◀──────┼─────────┘                             │
   "choices": [...]}    │                                       │
                        └───────────────────────────────────────┘
```

Three things happen that would be hard to express in pure Python:

- **Wire-format translation** (ExtProc filter) — the client sends
  OpenAI format; the upstream may want Anthropic format. aigw's filter
  rewrites the JSON body and response.
- **Priority-based failover** (`BackendTrafficPolicy`) — Envoy picks
  the next backend when a retry trigger fires, without the client ever
  knowing.
- **Server-side auth injection** — the upstream API key is added to
  the outbound request by the security policy, never by the client.

---

## 3. Where every rendered resource lives

```
Python Gateway config
        │
        │ render/aigw_standalone.py
        ▼
YAML multi-doc     which is 12 CRDs across 4 API groups:
────────────
core/v1                       Secret                (the ${VAR} placeholder)
gateway.networking.k8s.io/v1  Gateway               (:1975 listener)
  ...v1alpha3                 BackendTLSPolicy      (upstream SNI + trust)
gateway.envoyproxy.io/v1alpha1
                              Backend               (upstream FQDN)
                              BackendTrafficPolicy  (retry + failover)
aigateway.envoyproxy.io/v1beta1
                              AIGatewayRoute        (match header, list backends)
                              AIServiceBackend      (provider schema: OpenAI/Anthropic)
                              BackendSecurityPolicy (where the key lives, how to inject)
        │
        │ aigw run <tmp.yaml>
        ▼
aigw in-mem controller state  ─── programs ───▶  embedded Envoy data plane  ─▶  you
```

**API-group ownership**

| Group | Project | What it owns |
|---|---|---|
| `v1` | Core Kubernetes | `Secret` |
| `gateway.networking.k8s.io` | [Gateway API](https://gateway-api.sigs.k8s.io/) | `Gateway`, `BackendTLSPolicy`, internally-generated `HTTPRoute` |
| `gateway.envoyproxy.io` | [Envoy Gateway](https://gateway.envoyproxy.io/) | `Backend`, `BackendTrafficPolicy` |
| `aigateway.envoyproxy.io` | [Envoy AI Gateway](https://aigateway.envoyproxy.io/) | `AIGatewayRoute`, `AIServiceBackend`, `BackendSecurityPolicy` |

AI-specific concerns (provider schema, API-key injection, model-based
matching) live in the AI Gateway CRDs. Generic transport concerns
(FQDN, TLS, retry, port listener) reuse Envoy Gateway and Gateway API
primitives. Nothing is invented for envoyai — the Python SDK is a typed
front-end over these four established projects.

---

## 4. Control plane: today and where it's going

"Control plane" here means two things bundled: **where config comes
from**, and **how it reaches the data plane**. The K8s-native story
and envoyai's story share the same data plane (Envoy + aigw's ExtProc
translation filter), but differ on the control-plane side.

### Reference shape — aigw as a K8s controller

When aigw is deployed in a cluster, its controller **watches the K8s
API server** for CRD changes and reconciles them into Envoy xDS:

```
   ┌──────────────┐    watch + list    ┌────────────────┐   xDS    ┌──────────┐
   │ K8s API      │ ◀────────────────── │ aigw           │ ──────▶ │ Envoy    │
   │ (CRDs+etcd)  │                     │ controller     │         │ data     │
   └──────────────┘                     │   reconciler   │         │ plane    │
     ▲                                  │   xDS translator│        └──────────┘
     │ kubectl apply                    │   ExtProc       │
     │                                  └────────────────┘
   your CI / GitOps
```

That's the K8s-native control plane. `kubectl apply` writes CRDs → aigw
sees them via watch → Envoy gets a new xDS snapshot. Source of truth
lives in etcd; propagation is event-driven via the K8s watch stream.

### Today in envoyai — (A) file snapshot + respawn

envoyai already **bypasses the K8s API watch** by using aigw's
standalone mode. Python is the source of truth; file is the hand-off.

```
   ┌──────────────┐   render + write   ┌──────────────┐   aigw run  ┌──────────┐
   │ envoyai      │ ────────────────▶ │ /tmp/xxx.yaml│ ──────────▶ │ aigw     │
   │ Gateway (Py) │                    └──────────────┘              │ (Envoy)  │
   │  (your code) │                                                  └──────────┘
   └──────────────┘
   on reconfigure:  LocalRun.stop() → re-render → spawn_background()
```

Pros: zero new infrastructure, works offline, no K8s dep. Cons: config
updates are **snapshot-reload** — brief drop during respawn. Fine for
dev and many prod shapes; not ideal for fleets or for sub-second
reconfigure.

### Planned — (B) gRPC `ConfigSource` upstream in aigw

The cleanest long-term shape is to abstract aigw's source of config so
K8s, file, and gRPC are interchangeable. Upstream design candidate:

```
                                   ┌──────────────────────────────────────┐
                                   │ aigw                                 │
                                   │   ┌──ConfigSource (interface)───┐    │
                                   │   │  • K8sSource    (today)      │   │
                                   │   │  • FileSource   (standalone) │   │
   ┌──────────────┐   gRPC stream  │   │  • GrpcSource  ← envoyai ──┐ │   │
   │ envoyai      │ ─────────────▶ │   └──────────────────────────────┘   │
   │ Gateway (Py) │  pushed        │   reconciler + xDS + ExtProc         │
   │ (control     │  snapshots     │   unchanged across all sources       │
   │  plane)      │                │                                      │
   └──────────────┘                └────────────┬─────────────────────────┘
                                                │ xDS
                                                ▼
                                             Envoy data plane
```

Why this is the right endgame: aigw keeps owning wire-format
translation (ExtProc), envoyai becomes the typed, version-controlled
control plane, and the **K8s-native path is unchanged** for users who
want it. Multi-quarter effort; requires a design proposal upstream at
[envoyproxy/ai-gateway](https://github.com/envoyproxy/ai-gateway).

### Alternative — (C) envoyai as its own xDS server

The `Gateway.serve_xds(host, port)` roadmap item is a second control
plane shape that skips aigw entirely:

```
   ┌──────────────┐      xDS (ADS, v3)      ┌──────────────────┐
   │ envoyai      │ ──────────────────────▶ │ plain Envoy      │
   │ Gateway (Py) │                         │   (no ExtProc)   │
   │ + xDS server │                         │                  │
   │              │                         │  OpenAI-compat   │
   │              │                         │  upstreams only  │
   └──────────────┘                         └──────────────────┘
```

Pros: sub-second pushed reconfigure, one Python process, no subprocess.
Cons: **no wire-format translation** — without aigw's ExtProc filter,
upstreams must all speak OpenAI format (OpenAI proper, `OpenAI(base_url=...)`
for vLLM / Ollama / self-hosted, Azure OpenAI in compat mode). Right
shape for homogeneous-fleet / edge deployments; wrong shape if you need
Anthropic / Bedrock / Vertex / Cohere in the same gateway.

### Comparison at a glance

|  | source of truth | how the runtime learns | propagation latency | multi-replica | upstream set |
|---|---|---|---|---|---|
| aigw in K8s (reference) | CRDs in etcd | K8s watch | event-driven | yes (shared API server) | full (via ExtProc) |
| envoyai today (A) | Python `Gateway` in memory | one-shot YAML + respawn | respawn cycle | no | full (via aigw ExtProc) |
| envoyai + gRPC `ConfigSource` (B, planned) | Python `Gateway` | gRPC push to aigw | sub-second | yes | full (via aigw ExtProc) |
| envoyai xDS direct (C, planned) | Python `Gateway` | gRPC xDS to Envoy | sub-second | yes | OpenAI-compat only |

### The honest endgame

A hybrid: **envoyai as a gRPC control plane, aigw as the runtime**,
where aigw accepts either its K8s watch or envoyai's gRPC stream as
interchangeable `ConfigSource`s. Typed Python stays the source of
truth; aigw keeps owning data-plane translation. (C) remains on the
board as the lean option for OpenAI-compat-only deployments. (A) is
the ladder rung we're on today.

---

## Three principles the architecture bets on

- **Python is a builder, not a data plane.** Your `Gateway` object is
  compiled *once* into CRDs and handed off. No per-request Python.
- **aigw is the runtime.** Controller + Envoy in the same subprocess;
  request translation (OpenAI ↔ Anthropic ↔ Bedrock wire formats) is
  an Envoy ExtProc filter, not Python code.
- **Secrets never touch disk unencrypted.** Env vars → `${VAR}`
  placeholders in rendered YAML → envsubst in `aigw` memory at startup
  → never written back.

## Further reading

- Renderer source: [`src/envoyai/_internal/render/aigw_standalone.py`](src/envoyai/_internal/render/aigw_standalone.py)
- Subprocess lifecycle: [`src/envoyai/_internal/aigw_process.py`](src/envoyai/_internal/aigw_process.py)
- `aigw` binary resolver: [`src/envoyai/_internal/aigw_bootstrap.py`](src/envoyai/_internal/aigw_bootstrap.py)
- Runtime handle: [`src/envoyai/_internal/runtime.py`](src/envoyai/_internal/runtime.py)
