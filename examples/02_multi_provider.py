"""02 — Multiple providers, one gateway.

Register every provider once, then give each its own logical model name.
Callers use the logical names; you can swap providers underneath without
touching application code.
"""
from __future__ import annotations

import envoyai as ea

openai    = ea.OpenAI    (api_key=ea.env("OPENAI_KEY"))
anthropic = ea.Anthropic (api_key=ea.env("ANTHROPIC_KEY"))
cohere    = ea.Cohere    (api_key=ea.env("COHERE_KEY"))
azure     = ea.AzureOpenAI(
    resource="myresource",
    api_version="2025-01-01-preview",
    credentials=ea.azure.api_key(ea.env("AZURE_KEY")),
)
bedrock   = ea.Bedrock   (region="us-east-1", credentials=ea.aws.irsa())
vertex    = ea.GCPVertex (
    project_id="my-gcp-project",
    region="us-central1",
    credentials=ea.gcp.workload_identity(
        project_id="my-gcp-project", pool="my-pool", provider="my-provider",
    ),
)

gw = ea.Gateway()
gw.model("fast"      ).route(primary=openai   ("gpt-4o-mini"))
gw.model("smart"     ).route(primary=anthropic("claude-sonnet-4"))
gw.model("rerank"    ).route(primary=cohere   ("command-r-plus"))
gw.model("azure-chat").route(primary=azure    ("gpt-4", override="my-deployment"))
gw.model("bedrock"   ).route(primary=bedrock  ("anthropic.claude-sonnet-4-20250514-v1:0"))
gw.model("gemini"    ).route(primary=vertex   ("gemini-2.5-flash"))

gw.local()
# Clients now see six logical models and don't care which vendor is behind each.
