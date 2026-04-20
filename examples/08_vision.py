"""08 — Vision / multi-modal inputs.

Message content blocks with image URLs are standard OpenAI format and pass
through the gateway unchanged.
"""
from __future__ import annotations

import envoyai as ea
from openai import OpenAI

gw = ea.Gateway()
gw.model("vision").route(primary=ea.OpenAI(api_key=ea.env("OPENAI_KEY"))("gpt-4o"))
gw.local()

client = OpenAI(base_url="http://localhost:1975", api_key="unused")

resp = client.chat.completions.create(
    model="vision",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in this image?"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://upload.wikimedia.org/wikipedia/commons/4/47/PNG_transparency_demonstration_1.png"
                    },
                },
            ],
        }
    ],
)
print(resp.choices[0].message.content)
