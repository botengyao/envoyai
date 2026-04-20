"""03 — Failover chain.

Primary provider first. If it fails in a retryable way, the gateway walks
the fallback list in order. Each step gets ``attempts`` tries (default 1)
before moving on.
"""
from __future__ import annotations

import envoyai as ea

openai    = ea.OpenAI(api_key=ea.env("OPENAI_KEY"))
anthropic = ea.Anthropic(api_key=ea.env("ANTHROPIC_KEY"))
bedrock   = ea.Bedrock(region="us-east-1", credentials=ea.aws.irsa())

gw = ea.Gateway()
gw.model("chat").route(
    primary=openai("gpt-4o"),
    fallbacks=[
        anthropic("claude-sonnet-4"),
        bedrock("anthropic.claude-sonnet-4-20250514-v1:0"),
    ],
    retry=ea.RetryPolicy.rate_limit_tolerant(),  # preset: 5 attempts, 60s timeout
    timeout="60s",
)

gw.local()
