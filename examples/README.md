# Examples

Small, self-contained scripts showing common envoyai tasks. Each file is
runnable once the envoyai runtime lands (`gw.local()` is a stub for now).

| # | File | What it shows |
|---|---|---|
| 01 | [hello_world](01_hello_world.py) | Single provider, one logical model |
| 02 | [multi_provider](02_multi_provider.py) | All eight providers in one gateway |
| 03 | [failover](03_failover.py) | Primary + fallback chain with retries |
| 04 | [canary](04_canary.py) | 90/10 weighted split for gradual rollouts |
| 05 | [streaming](05_streaming.py) | Streaming completions, passthrough |
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
python examples/01_hello_world.py
```

Environment variables referenced across examples: `OPENAI_KEY`,
`ANTHROPIC_KEY`, `AZURE_KEY`, `COHERE_KEY`, `OLLAMA_KEY`, `VLLM_KEY`.
