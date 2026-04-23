"""06 — Function / tool calling.

Tool use is standard OpenAI SDK — envoyai forwards ``tools`` and
``tool_choice`` through to the provider unchanged.
"""
from __future__ import annotations

import json

import envoyai as ea
from openai import OpenAI

gw = ea.Gateway()
gw.model("chat").route(primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))("gpt-4o"))
gw.local()

client = OpenAI(base_url="http://localhost:1975/v1", api_key="unused")

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]

resp = client.chat.completions.create(
    model="chat",
    messages=[{"role": "user", "content": "What's the weather in Paris?"}],
    tools=tools,
    tool_choice="auto",
)

call = resp.choices[0].message.tool_calls[0]
print(call.function.name, json.loads(call.function.arguments))
