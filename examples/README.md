# Examples

envoyai runs in two modes. Pick the one that matches how you want to run the
gateway; the configuration is identical.

## Two modes

| Mode | Start with | Good for |
|---|---|---|
| **SDK mode** — [`a_sdk_mode.py`](a_sdk_mode.py) | `gw.local()` + `gw.complete()` | Scripts, notebooks, single-process prototypes, CI jobs |
| **Proxy mode** — [`b_proxy_mode/`](b_proxy_mode/) | `gw.serve()` (blocks) + any OpenAI client | Multi-language stacks, shared dev servers, production |

## Feature examples

The numbered examples below demonstrate specific features. They work in
either mode — each focuses on the configuration and uses the OpenAI SDK
against `localhost:1975` to show the call shape.

| # | File | What it shows |
|---|---|---|
| 01 | [hello_world](01_hello_world.py) | Single provider, one logical model |
| 02 | [multi_provider](02_multi_provider.py) | All eight providers in one gateway |
| 03 | [failover](03_failover.py) | Primary + fallback chain with retries |
| 04 | [canary](04_canary.py) | 90/10 weighted split for gradual rollouts |
| 05 | [streaming](05_streaming.py) | Streaming completions |
| 06 | [tool_use](06_tool_use.py) | Function calling / tool definitions |
| 07 | [embeddings](07_embeddings.py) | Embedding endpoints |
| 08 | [vision](08_vision.py) | Multi-modal (image) inputs |
| 09 | [async](09_async.py) | Async calls, concurrent requests |
| 10 | [custom_retry](10_custom_retry.py) | Retry on product-level reasons |
| 11 | [budget_per_team](11_budget_per_team.py) | Per-team spend caps and alerts |
| 12 | [model_alias](12_model_alias.py) | Vendor-agnostic logical names |
| 13 | [local_openai_compat](13_local_openai_compat.py) | vLLM / Ollama / self-hosted upstreams |
| 14 | [privacy](14_privacy.py) | Redaction defaults and overrides |
| 15 | [handling_errors](15_handling_errors.py) | Build-time and request-time errors |

## Running

```bash
pip install envoyai[client]
export OPENAI_KEY=sk-...              # or whichever vars each example uses
python examples/a_sdk_mode.py
```

Environment variables referenced across examples: `OPENAI_KEY`,
`ANTHROPIC_KEY`, `AZURE_KEY`, `COHERE_KEY`, `OLLAMA_KEY`, `VLLM_KEY`.
