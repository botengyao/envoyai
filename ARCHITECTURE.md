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

#### Extending (C) — pure Envoy at full scope

(C) is scoped to OpenAI-compatible upstreams *because* it drops aigw's
ExtProc filter. Lift that constraint — assume every aigw-owned piece
is provided some other way — and you get a fully aigw-free
architecture on top of pure Envoy. What "provided some other way"
actually means, piece by piece:

| What aigw owns today | Replacement without aigw |
|---|---|
| Format translation (OpenAI ↔ Anthropic ↔ Bedrock wire formats) | (a) an Envoy **WASM filter** doing the rewrite in-proc; (b) an **ExtProc sidecar** microservice; (c) restrict scope to OpenAI-compat upstreams — the filter becomes identity. |
| Model-aware routing (today: `x-ai-eg-model` extracted from the JSON body) | Client sends the model as an HTTP header and Envoy does a **native header match**. Or a small WASM / Lua filter peeks at the body. |
| Per-provider auth injection (`BackendSecurityPolicy`) | `request_headers_to_add` on the route (native). Rotating keys use **Envoy SDS** — envoyai pushes secrets as xDS `Secret` resources. |
| Retry + priority failover | **Native** `retry_policy` on the route + cluster priorities. Or Envoy Gateway's `BackendTrafficPolicy` if present. |
| Per-request token / cost logging | Structured access logs, optionally enriched by the same WASM filter that does translation. |

With all of that assumed, the architecture collapses to one control
plane and one data plane, with a filter chain that envoyai programs
via xDS:

```
      ┌──────────────┐           xDS (ADS v3, Secret xDS)
      │ envoyai      │ ───────────────────────────────────┐
      │ typed Gateway│                                    │
      │ + xDS server │                                    ▼
      └──────────────┘                          ┌──────────────────┐
                                                │ pure Envoy       │
                                                │  listener :1975  │
                                                │  filter chain:   │
                                                │   • model header │ native match
                                                │   • auth inject  │ native
                                                │   • translate    │ ← WASM / ExtProc
                                                │   • router       │
                                                │  clusters:       │
                                                │   - openai       │
                                                │   - anthropic    │
                                                └────────┬─────────┘
                                                         │ TLS per cluster
                                                         ▼
                                                      upstreams
```

When this becomes the right architecture:

- **Edge / on-device inference fleets** — one envoyai control plane
  pushing xDS to many Envoys on edge hardware, where pulling a second
  binary (aigw) is inconvenient and updates need to be near real-time.
- **Pure OpenAI-compat deployments** — vLLM / Ollama / Azure OpenAI in
  compat mode / self-hosted proxies all speak the same wire format, so
  the translation slot collapses to identity.
- **Orgs with WASM engineering** — a translation WASM module replaces
  a subprocess dependency with a versioned, hot-swappable in-Envoy
  filter that can be pushed via xDS itself.

When aigw remains the right choice:

- **Heterogeneous upstreams today, no WASM bandwidth** — aigw's
  ExtProc filter is already battle-tested for OpenAI ↔ Anthropic ↔
  Bedrock translation. Rebuilding it in WASM or a sidecar is a
  quarters-of-engineering commitment, not a week.
- **You want aigw's ops surface out of the box** — token counters,
  admin port, stat prefixes, ready-made observability. Reimplementing
  these in pure Envoy plus custom filters is more work than it looks.

Honest read: the scoped (C) can land first with a real use case
(OpenAI-compat fleets). The full-scope extension is the right
long-term architecture *if* envoyai invests in a translation WASM
module or adopts ExtProc-as-a-microservice. Until then, aigw is
load-bearing — it's doing the work we'd otherwise have to do ourselves.

### Comparison at a glance

|  | source of truth | how the runtime learns | propagation latency | multi-replica | upstream set |
|---|---|---|---|---|---|
| aigw in K8s (reference) | CRDs in etcd | K8s watch | event-driven | yes (shared API server) | full (via ExtProc) |
| envoyai today (A) | Python `Gateway` in memory | one-shot YAML + respawn | respawn cycle | no | full (via aigw ExtProc) |
| envoyai + gRPC `ConfigSource` (B, planned) | Python `Gateway` | gRPC push to aigw | sub-second | yes | full (via aigw ExtProc) |
| envoyai xDS direct (C, planned) | Python `Gateway` | gRPC xDS to Envoy | sub-second | yes | OpenAI-compat only |

### Deployment topologies — where the pieces physically live

The control-plane shapes above describe *how* config reaches the
runtime. Where the pieces live is a separate axis — and a useful one.
Envoy's native xDS client makes "many data planes, one control plane"
a standard deployment even when each data plane runs on-device.

**Topology 1 — single process (envoyai today).** Python control plane
and aigw / Envoy runtime on the same machine. No network for config;
the listener is local.

**Topology 2 — central control plane, on-device runtime.** One
envoyai instance exposes gRPC xDS (or a gRPC `ConfigSource` inside
aigw, once (B) lands) and serves many edge / laptop / node runtimes.
Each runtime still handles requests locally — translation via aigw's
ExtProc filter and upstream TLS both happen next to whoever is
calling — but policy is owned centrally.

```
                           edge / laptop / node
                           ┌──────────────────┐
   ┌──────────────┐  xDS   │ aigw (Envoy)     │ ─▶ OpenAI / Anthropic / …
   │ envoyai      │ ─────▶ └──────────────────┘
   │ central      │  xDS   ┌──────────────────┐
   │ control      │ ─────▶ │ aigw (Envoy)     │ ─▶ local vLLM / Ollama
   │ plane        │  xDS   └──────────────────┘
   │              │ ─────▶ ┌──────────────────┐
   │              │        │ aigw (Envoy)     │ ─▶ Bedrock in this region
   └──────────────┘        └──────────────────┘
   one Python               N on-device runtimes; each translates
   source of truth          OpenAI↔native formats next to the upstream
```

This is the shape that gets interesting on-device: each node runs
aigw locally, but config comes from one place. "Use model X in eu-west,
model Y everywhere else" or "rotate the Anthropic key fleet-wide" is a
single Python edit pushed down as an xDS snapshot — no per-node deploy,
no staggered restarts. Envoy's xDS subscription makes this a runtime
primitive; the application layer needs no polling or reload logic.

Mixed fleets work on the same control plane: aigw nodes where
upstreams include Anthropic / Bedrock / Vertex, plain-Envoy nodes
where the upstream set is OpenAI-compatible only and the ExtProc
filter isn't needed. (B) enables the aigw side; (C) enables the plain
Envoy side.

**Topology 3 — in-cluster (Kubernetes-native).** aigw runs as a
cluster controller; envoyai's `render_k8s()` / `apply()` / `deploy()`
(roadmap) writes CRDs into etcd; aigw watches them. Compatible with
GitOps; standard Kubernetes operational shape for teams that already
live there.

**Topology 4 — third-party xDS control plane (e.g., Traffic Director,
istiod).** Organizations that already run a service mesh — Google
Cloud [Traffic Director](https://cloud.google.com/traffic-director),
Istio (istiod), HashiCorp Consul Connect, Kuma — have a production
xDS control plane already configuring Envoys across the fleet.
envoyai integrates *with* that control plane rather than replacing it.
Two integration patterns, complementary:

**(4a) envoyai as a mesh-native backend.** The existing control plane
doesn't touch the aigw Envoy directly; instead it treats envoyai as a
service in the mesh. Mesh-sidecar Envoys on the client side get the
usual routing / mTLS / authz from the mesh control plane; envoyai
sits at the edge of that flow and owns AI-specific concerns.

```
   mesh control plane             mesh sidecar              envoyai gateway
   (Traffic Director / istiod)    (client-side Envoy)
   ┌───────────────┐              ┌───────────┐             ┌──────────────┐
   │ mesh xDS      │── ──▶ ───────│ app Envoy │── mTLS ───▶ │ aigw listener│ ─▶ OpenAI /
   │  routes       │              └───────────┘             │ :1975        │    Anthropic
   │  clusters     │                                        └──────────────┘    /…
   │  mTLS, RBAC   │
   └───────────────┘                                        service registered
                                                            in the mesh
```

Cleanest brownfield story — the mesh keeps owning north-south / east-west
transport (mTLS, authz, org telemetry, SRE runbooks), envoyai keeps
owning format translation, per-model routing, and upstream auth injection.

**(4b) Dual-source xDS into the aigw Envoy.** The Envoy inside aigw
subscribes to **two** xDS sources at once: the mesh control plane (for
listeners, clusters, endpoints, mTLS, rate limits, access logs) and
aigw's own in-process xDS (for the AI-specific ExtProc filter state
and the `BackendSecurityPolicy`-driven auth-header injection). Envoy
supports multiple xDS sources; what it doesn't support is two sources
asserting ownership over the *same* resource, so the split has to be
clean — the mesh owns transport, aigw owns AI filters.

```
   Traffic Director / istiod                 aigw in-process xDS
   (routes, clusters, mTLS, RBAC)            (ExtProc filter, BSP auth)
        │                                         │
        └──────────────────┬──────────────────────┘
                           ▼
                    ┌──────────────────┐
                    │ aigw Envoy       │ ← on-device runtime, two xDS feeds
                    └──────────────────┘
                           │ upstream
                           ▼
                    OpenAI / Anthropic / local vLLM / …
```

More operationally demanding; worth it when the organization wants
the LLM gateway to inherit *every* mesh concern — zero-trust identity,
org-wide telemetry, quota enforcement — without envoyai reimplementing
any of it. aigw still adds the AI-layer value; Traffic Director keeps
its job.

**Topology 5 — cloud control plane + on-device Envoy.** The
combination worth naming explicitly: the control plane is a
cloud-managed xDS service (Google Cloud
[Traffic Director](https://cloud.google.com/traffic-director),
managed Istio — Anthos Service Mesh / Google Cloud Service Mesh, HCP
Consul, managed Kuma) and Envoy runs *on the device* — laptop,
desktop app, edge appliance, IoT node, on-prem customer hardware.
The xDS connection is device-to-cloud over the internet, typically a
long-lived gRPC stream authenticated with mTLS or short-lived OIDC
tokens.

```
             cloud
   ┌─────────────────────────────┐
   │ xDS control plane (managed) │
   │  e.g. Traffic Director,     │
   │  managed istiod             │
   └──────────────┬──────────────┘
                  │  xDS (ADS v3) over mTLS, long-lived gRPC
                  │
       ┌──────────┼───────────┬───────────────┐
       ▼          ▼           ▼               ▼
     laptop    desktop     edge node      on-prem appliance
   ┌────────┐ ┌────────┐  ┌────────┐     ┌────────┐
   │ Envoy  │ │ Envoy  │  │ Envoy  │ ... │ Envoy  │   ← runs on the device
   │ [aigw?]│ │ [aigw?]│  │ [aigw?]│     │ [aigw?]│      aigw only if
   └────┬───┘ └────┬───┘  └────┬───┘     └────┬───┘      heterogeneous
        ▼          ▼           ▼              ▼          upstreams
    upstream   upstream    upstream       upstream
```

Envoy's xDS client is built to survive network hiccups — on
disconnect it keeps serving from the last-known config — so this
topology is resilient even from unreliable device networks.

On-device variants (same choice as extended (C) above):

- **aigw sidecar on each device.** Required when upstreams include
  Anthropic, Bedrock, or Vertex. aigw does the format translation
  locally via its ExtProc filter, configured by the cloud control
  plane via xDS resources that reference it. Heavier device
  footprint; full provider support.
- **Pure Envoy on each device.** OpenAI-compat upstreams only, or a
  WASM translation module loaded via xDS. Smaller footprint, simpler
  operations; narrower provider set unless the WASM module is
  invested in.

Where this topology fits:

- **LLM calls from a client app.** A desktop or mobile app embeds a
  tiny Envoy; every LLM request goes through it. Routing, auth,
  quota, per-region failover, and key rotation all come from the
  cloud control plane — one xDS update and every user gets the new
  policy. Keys never live in the app.
- **Edge inference gateways.** Many regional edge nodes, each with a
  local Envoy deciding which local vs. cloud model to call, managed
  from one place.
- **Hybrid cloud-to-customer-site.** Your service ships an on-prem
  appliance with Envoy inside; the control plane stays in your cloud
  and the customer never configures anything.

What envoyai's role looks like in topology 5: either **generate the
xDS resources** the cloud control plane serves (today: render YAML
that gets loaded into Traffic Director / imported into the mesh) or
— once `Gateway.serve_xds(...)` lands — **be** the cloud control plane
itself, running as a hosted service. The choice depends on whether
the org wants a managed third-party control plane or an envoyai-owned
one.

Load-bearing question: who owns device enrollment, cert lifecycle,
and observability shipping. That work is the same whether the control
plane is Traffic Director or envoyai-hosted; the Envoy data plane
handles the wire protocol for free.

### The honest endgame

A hybrid: **envoyai as a gRPC control plane, aigw as the runtime**,
where aigw accepts either its K8s watch or envoyai's gRPC stream as
interchangeable `ConfigSource`s. Typed Python stays the source of
truth; aigw keeps owning data-plane translation. (C) remains on the
board as the lean option for OpenAI-compat-only deployments. (A) is
the ladder rung we're on today. Topology 2 is what that endgame
unlocks — a central envoyai controlling a fleet of on-device runtimes
with no K8s in the middle.

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
