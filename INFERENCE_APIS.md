# LLM inference requests: a field guide

**What an inference request actually looks like on the wire** — every major
provider, every OpenAI-compatible vendor, every self-hosted server, and every
gateway that sits between them. Verified against official documentation and
source, August 2026.

This document exists because envoyai's whole job is translating between these
shapes. `ea.OpenAI(...)`, `ea.Anthropic(...)`, `ea.Bedrock(...)` and friends are
typed Python names for the wire formats catalogued here; the `_schema` string on
each [provider class](src/envoyai/providers/) — `OpenAI`, `Anthropic`,
`AzureOpenAI`, `AWSBedrock`, `AWSAnthropic`, `GCPVertexAI`, `GCPAnthropic`,
`Cohere` — is exactly the set of dialects Envoy AI Gateway can translate to.
See [ARCHITECTURE.md](ARCHITECTURE.md) for how that translation is wired.

## Contents

**Native dialects**
1. [The five dialects](#1-the-five-dialects)
2. [OpenAI — Chat Completions and Responses](#2-openai)
3. [Anthropic — Messages](#3-anthropic-messages)
4. [Google Gemini — generateContent](#4-google-gemini)
5. [AWS Bedrock — InvokeModel and Converse](#5-aws-bedrock)
6. [Microsoft Azure — Azure OpenAI and Foundry](#6-microsoft-azure)
7. [Cohere v2 and the rerank family](#7-cohere-and-rerank)

**OpenAI-compatible surfaces**
8. [Hosted vendors: Groq, Together, Fireworks, Mistral, xAI, DeepSeek…](#8-openai-compatible-hosted-vendors)
9. [OpenRouter](#9-openrouter)
10. [Local and self-hosted: Ollama, llama.cpp, vLLM, SGLang, TGI, NIM](#10-local-and-self-hosted)

**Gateways**
11. [LiteLLM](#11-litellm)
12. [agentgateway](#12-agentgateway)
13. [Envoy AI Gateway](#13-envoy-ai-gateway)
14. [Other gateways: Kong, Portkey, Cloudflare, Helicone, Databricks…](#14-other-gateways)

**Cross-cutting**
15. [Streaming, side by side](#15-streaming-side-by-side)
16. [Tool calling, side by side](#16-tool-calling-side-by-side)
17. [Multimodal content parts](#17-multimodal-content-parts)
18. [Structured output, reasoning, caching](#18-structured-output-reasoning-caching)
19. [Usage, errors, rate limits](#19-usage-errors-rate-limits)
20. [Batch and async](#20-batch-and-async)
21. [Non-chat protocols: MCP, A2A, KServe, InferencePool](#21-non-chat-protocols)
22. [What bites a gateway](#22-what-bites-a-gateway)

---

## 1. The five dialects

Almost every inference request on the internet is one of five shapes. Everything
else is a dialect of one of them, or a gateway pretending to be one.

| Dialect | Canonical request | Message container | System prompt | Tool result turn | Streaming |
|---|---|---|---|---|---|
| **OpenAI Chat Completions** | `POST /v1/chat/completions` | `messages[]` of `{role, content}` | a `developer`/`system` message | `{"role":"tool","tool_call_id":…}` | data-only SSE, `data: [DONE]` |
| **OpenAI Responses** | `POST /v1/responses` | `input` (string or typed items) | top-level `instructions` | `{"type":"function_call_output","call_id":…}` | typed SSE, no `[DONE]` |
| **Anthropic Messages** | `POST /v1/messages` | `messages[]` of typed content blocks | top-level `system` | `tool_result` block in a **user** message | named SSE events, no `[DONE]` |
| **Google Gemini** | `POST …/models/{m}:generateContent` | `contents[]` of `{role, parts[]}` | top-level `systemInstruction` | `functionResponse` part in a **user** turn | separate endpoint + `?alt=sse` |
| **AWS Bedrock Converse** | `POST /model/{id}/converse` | `messages[]` of untagged unions | top-level `system[]` | `toolResult` block in a **user** message | AWS event-stream frames |

Four structural facts explain most of the pain:

- **Only OpenAI has a `tool` role.** Anthropic, Gemini and Bedrock all send tool
  results back inside a *user* turn. A gateway translating OpenAI→anything must
  re-parent those messages.
- **Only OpenAI and Anthropic put the model in the body.** Gemini and Bedrock put
  it in the URL path. Azure puts a *deployment name* in the path and ignores the
  body's `model`. This is why model-based routing needs body parsing.
- **Nobody agrees on the token cap.** `max_tokens` (deprecated on Chat
  Completions, required on Anthropic), `max_completion_tokens`,
  `max_output_tokens`, `maxOutputTokens`, `maxTokens`, `maxTokenCount`,
  `max_gen_len`.
- **Streaming is not one protocol.** SSE with bare `data:` lines; SSE with
  `event:` names; a separate HTTP endpoint; NDJSON; WebSocket; and AWS's binary
  event-stream framing. Six framings in all — see [§15](#15-streaming-side-by-side).

```
                        ┌──────────────────────────────┐
   client speaks   ───▶ │  gateway                     │ ───▶  upstream speaks
                        │                              │
   OpenAI Chat          │   parse body → extract model │       OpenAI Chat
   OpenAI Responses     │   route on model name        │       Anthropic Messages
   Anthropic Messages   │   inject upstream credential │       Gemini generateContent
   Gemini generateCont. │   rewrite body to target     │       Bedrock Converse / Invoke
   Cohere rerank        │   rewrite response back      │       Azure deployments
                        │   extract token usage        │
                        └──────────────────────────────┘
```

---

## 2. OpenAI

Base URL `https://api.openai.com/v1`. Auth is `Authorization: Bearer $OPENAI_API_KEY`.
There is **no dated version header** — versioning is per-model (`gpt-5.4`) and
per-tool (`web_search_2025_08_26`). Optional scoping headers:
`OpenAI-Organization: org_…`, `OpenAI-Project: proj_…`,
`OpenAI-Safety-Identifier: <hashed-user-id>`.

OpenAI ships **two** generation surfaces, and they are not interchangeable.

### 2.1 Chat Completions — the industry baseline

```bash
curl https://api.openai.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "gpt-5.4",
    "messages": [
      {"role": "developer", "content": "You are a helpful assistant."},
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

Response:

```json
{
  "id": "chatcmpl-B9MBs8CjcvOU2jLn4n570S5qMJKcT",
  "object": "chat.completion",
  "created": 1741569952,
  "model": "gpt-5.4",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Hello! How can I assist you today?", "refusal": null, "annotations": []},
    "logprobs": null,
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 19, "completion_tokens": 10, "total_tokens": 29,
    "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
    "completion_tokens_details": {"reasoning_tokens": 0, "audio_tokens": 0,
      "accepted_prediction_tokens": 0, "rejected_prediction_tokens": 0}
  },
  "service_tier": "default"
}
```

Fields worth knowing, because every compatible vendor picks a subset:

| Field | Notes |
|---|---|
| `max_completion_tokens` | current cap. `max_tokens` is **deprecated** and "not compatible with o-series models" |
| `reasoning_effort` | flat string: `none\|minimal\|low\|medium\|high\|xhigh\|max`, default `medium` |
| `verbosity` | `low\|medium\|high` |
| `response_format` | `{"type":"json_schema","json_schema":{"name":…,"schema":…,"strict":true}}` — **nested** |
| `modalities` + `audio` | `["text","audio"]` with `{"voice":"alloy","format":"wav"}` |
| `prediction` | Predicted Outputs: `{"type":"content","content":"…"}` |
| `logprobs` (bool) + `top_logprobs` (0–20) | note: on legacy `/v1/completions`, `logprobs` is an **integer** |
| `stream_options` | `{include_usage, include_obfuscation}` |
| `service_tier` | `auto\|default\|flex\|scale\|priority\|fast` |
| `n`, `logit_bias`, `seed` | no Responses equivalent; `seed` is deprecated |
| `prompt_cache_key`, `safety_identifier` | both replace the deprecated `user` field |
| `store` | enables `GET /v1/chat/completions/{id}` and `/messages` retrieval |

Streaming is **data-only SSE** — no `event:` lines, terminated by a literal sentinel:

```
data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1694268190,"model":"gpt-4o-mini","system_fingerprint":"fp_44709d6fcb","choices":[{"index":0,"delta":{"role":"assistant","content":""},"logprobs":null,"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk",…,"choices":[{"index":0,"delta":{"content":"Hello"},"logprobs":null,"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk",…,"choices":[{"index":0,"delta":{},"logprobs":null,"finish_reason":"stop"}]}

data: [DONE]
```

Usage is **not** emitted by default. With `stream_options.include_usage: true`
one extra chunk arrives before `data: [DONE]` carrying `usage` and an empty
`choices` array — and, per the spec, "if the stream is interrupted, you may not
receive the final usage chunk." This is why gateways that bill on tokens force
that flag on.

### 2.2 Responses — the stateful surface

```bash
curl https://api.openai.com/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "gpt-5.4",
    "input": "Tell me a three sentence bedtime story about a unicorn."
  }'
```

With reasoning, built-in tools, and background execution:

```json
{"model": "o3-mini", "input": "How much wood would a woodchuck chuck?", "reasoning": {"effort": "high"}}

{"model": "gpt-5.4", "tools": [{"type": "web_search_preview"}], "input": "What was a positive news story from today?"}

{"model": "gpt-5.4", "tools": [{"type": "file_search", "vector_store_ids": ["vs_1234567890"], "max_num_results": 20}],
 "input": "What are the attributes of an ancient brown dragon?"}

{"model": "gpt-5.6", "tools": [{"type": "code_interpreter", "container": {"type": "auto", "memory_limit": "4g"}}],
 "instructions": "You are a personal math tutor…", "input": "I need to solve 3x + 11 = 14."}

{"model": "gpt-5.6", "input": "Write a very long novel about otters in space.", "background": true}
```

The `mcp` tool type lets the *server* connect to an MCP server on your behalf:

```json
{
  "type": "mcp",
  "server_label": "deepwiki",
  "server_url": "https://mcp.deepwiki.com/mcp",
  "require_approval": {"never": {"tool_names": ["ask_question", "read_wiki_structure"]}}
}
```

Streaming is **typed SSE** — both an `event:` name and a `data:` payload whose
`type` repeats the name, with a `sequence_number` on every event and **no
`[DONE]` sentinel**; the terminal frame is `response.completed` (or
`.failed` / `.incomplete`):

```
event: response.created
data: {"type":"response.created","response":{"id":"resp_67c9fdce…","object":"response","status":"in_progress",…}}

event: response.output_item.added
data: {"type":"response.output_item.added","output_index":0,"item":{"id":"msg_67c9fdcf…","type":"message","status":"in_progress","role":"assistant","content":[]}}

event: response.output_text.delta
data: {"type":"response.output_text.delta","item_id":"msg_67c9fdcf…","output_index":0,"content_index":0,"delta":"Hi"}

event: response.output_text.done
data: {"type":"response.output_text.done",…,"text":"Hi there! How can I assist you today?"}

event: response.completed
data: {"type":"response.completed","response":{…,"usage":{"input_tokens":37,"output_tokens":11,"output_tokens_details":{"reasoning_tokens":0},"total_tokens":48}}}
```

The full union is 58 event types, including
`response.function_call_arguments.delta/.done`,
`response.reasoning_text.delta/.done`, `response.mcp_call.in_progress/.completed/.failed`,
`response.image_generation_call.partial_image`, and
`response.code_interpreter_call_code.delta`.

### 2.3 Responses vs Chat Completions — every difference that 400s

This is the single most common source of translation bugs, so it is worth
spelling out in full.

| Concern | Responses | Chat Completions |
|---|---|---|
| Envelope | `input` (string or typed items) + `instructions` | `messages[]` |
| Token cap | `max_output_tokens` | `max_completion_tokens` (`max_tokens` deprecated) |
| Reasoning | `reasoning: {effort, summary, mode, context}` | `reasoning_effort: "high"` (flat) |
| Structured output | `text.format` — **flat** `{type, name, schema, strict}` | `response_format` — **nested** under `json_schema` |
| Function tool | **flat** `{type, name, parameters, strict}` | **nested** `{type, function: {name, parameters}}` |
| Tool result | `{"type":"function_call_output","call_id":…,"output":…}` | `{"role":"tool","tool_call_id":…,"content":…}` |
| Image part | `{"type":"input_image","image_url":"<bare URL string>"}` | `{"type":"image_url","image_url":{"url":"…"}}` |
| Usage names | `input_tokens` / `output_tokens` | `prompt_tokens` / `completion_tokens` |
| Streaming | typed events, no `[DONE]` | bare `data:` chunks, `data: [DONE]` |
| State | `store` defaults **true**; `previous_response_id`, `conversation`, `background` | stateless |
| Server tools | web_search, file_search, code_interpreter, computer, mcp, image_generation, local_shell, apply_patch | only `web_search_options` (much narrower) |
| Chat-only | `n`, `logit_bias`, `prediction`, `modalities`+`audio`, `input_audio` parts, `seed` | — |
| `service_tier` | adds `ultrafast` | stops at `fast` |

### 2.4 The rest of the platform surface

| Endpoint | Content type | Shape |
|---|---|---|
| `POST /v1/embeddings` | JSON | `{"input": "…", "model": "text-embedding-ada-002", "encoding_format": "float"}`; `input` also takes arrays of strings/tokens; `dimensions` truncates |
| `POST /v1/images/generations` | JSON | `{"model":"gpt-image-1.5","prompt":"A cute baby sea otter","n":1,"size":"1024x1024"}`; `stream:true` emits partial images |
| `POST /v1/images/edits` | **multipart** | repeated `-F "image[]=@file.png"` parts + `-F prompt=…` |
| `POST /v1/audio/speech` | JSON in, **binary out** | `{"model":"gpt-4o-mini-tts","input":"…","voice":"alloy"}`; `stream_format:"sse"` switches to an event stream |
| `POST /v1/audio/transcriptions` | **multipart** | `-F file=@audio.mp3 -F model=gpt-4o-transcribe`; `response_format` adds `diarized_json` |
| `POST /v1/moderations` | JSON | `{"input": "…"}` or an array of `{type:"text"}` / `{type:"image_url"}` parts. Free |
| `POST /v1/batches` | JSON | `{"input_file_id":"file-abc123","endpoint":"/v1/chat/completions","completion_window":"24h"}` |
| `GET /v1/models` | — | `{"object":"list","data":[{"id":…,"owned_by":…,"shutdown_date":null}]}` |
| `POST /v1/completions` | JSON | legacy; `max_tokens` still current here, `logprobs` is an integer |
| `wss://api.openai.com/v1/realtime?model=…` | WebSocket | event protocol, not request/response |

Batch input is JSONL, one request per line — note that **output order is not
input order**, so correlate on `custom_id`:

```jsonl
{"custom_id": "request-1", "method": "POST", "url": "/v1/chat/completions", "body": {"model": "gpt-3.5-turbo-0125", "messages": [{"role":"user","content":"Hello world!"}], "max_tokens": 1000}}
```

```jsonl
{"id": "batch_req_123", "custom_id": "request-1", "response": {"status_code": 200, "request_id": "req_789", "body": {…}}, "error": null}
```

Realtime opens with a `session.update` and streams audio in with
`input_audio_buffer.append`:

```json
{"type": "session.update", "session": {"type": "realtime", "instructions": "Be extra nice today!"}}
```

Browsers get an ephemeral key instead of the API key:

```bash
curl -X POST https://api.openai.com/v1/realtime/client_secrets \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"expires_after": {"anchor": "created_at", "seconds": 600},
       "session": {"type": "realtime", "model": "gpt-realtime", "instructions": "You are a friendly assistant."}}'
```

→ `{"value": "ek_68af296e…", "expires_at": 1756310470, "session": {…}}`, used as
`Authorization: Bearer ek_…`. WebRTC clients then `POST /v1/realtime/calls` with
`Content-Type: application/sdp` and get an SDP answer back.

---

## 3. Anthropic Messages

Base URL `https://api.anthropic.com`. Everything routes through one endpoint —
tools, thinking, structured outputs, prompt caching, MCP connections and
server-side tools are all *parameters of* `POST /v1/messages`, not separate APIs.

```
x-api-key: $ANTHROPIC_API_KEY        # or Authorization: Bearer <WIF token>
anthropic-version: 2023-06-01        # REQUIRED on every request
content-type: application/json
anthropic-beta: feature-2025-11-20[,feature2]   # optional, comma-separated
anthropic-workspace-id: wrkspc_…     # required for multi-workspace keys
```

```bash
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-opus-4-5",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Hello, Claude"}
    ]
  }'
```

Four structural differences from OpenAI, all of which a translator must handle:

1. **`max_tokens` is required.** (`max_tokens: 0` is legal — it populates the
   prompt cache without generating.)
2. **`system` is a top-level parameter**, a string or an array of
   `TextBlockParam` — not a message role.
3. **`content` is a string or an array of typed blocks**: `text`, `image`,
   `document`, `tool_use`, `tool_result`, `thinking`, `redacted_thinking`,
   `container_upload`.
4. **Tool results go in a `user` message.** There is no `tool` role.

Response:

```json
{
  "id": "msg_1234567890",
  "type": "message",
  "role": "assistant",
  "content": [{"type": "text", "text": "Response text here…"}],
  "model": "claude-opus-4-5",
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 50,
    "output_tokens": 503,
    "cache_creation_input_tokens": 5120,
    "cache_read_input_tokens": 100000,
    "cache_creation": {"ephemeral_5m_input_tokens": 5120, "ephemeral_1h_input_tokens": 0}
  }
}
```

`stop_reason` ∈ `end_turn | max_tokens | stop_sequence | tool_use | pause_turn | refusal`.
`stop_details` is populated only on `refusal`.

**Total input tokens = `cache_read_input_tokens` + `cache_creation_input_tokens` + `input_tokens`.**
This trips up naive cost extraction: `input_tokens` counts only tokens *after
the last cache breakpoint*.

### 3.1 Thinking, effort, structured outputs

```json
{"model": "claude-opus-4-8", "max_tokens": 16000,
 "thinking": {"type": "adaptive", "display": "summarized"},
 "messages": [{"role": "user", "content": "What is the GCD of 1071 and 462?"}]}
```

```json
{"model": "claude-sonnet-4-6", "max_tokens": 16000,
 "thinking": {"type": "enabled", "budget_tokens": 10000},
 "messages": [{"role": "user", "content": "Are there infinitely many primes n mod 4 == 3?"}]}
```

`thinking` is one of `ThinkingConfigAdaptive {type:"adaptive", display?}`,
`ThinkingConfigEnabled {type:"enabled", budget_tokens, display?}` (min 1024, must
be < `max_tokens`), or `ThinkingConfigDisabled`. `display` is `summarized` or
`omitted` and is invalid with `disabled`. The manual `budget_tokens` form is
deprecated on the 4.6 generation and **rejected with a 400 on 4.7 and later**.

Structured output is `output_config`, not `response_format`:

```json
{
  "model": "claude-opus-5", "max_tokens": 1024,
  "messages": [{"role": "user", "content": "Extract the key information from this email: …"}],
  "output_config": {
    "format": {
      "type": "json_schema",
      "schema": {"type": "object",
                 "properties": {"name": {"type": "string"}, "email": {"type": "string"}},
                 "required": ["name", "email"], "additionalProperties": false}
    }
  }
}
```

`output_config` also carries `effort: "low"|"medium"|"high"|"xhigh"|"max"`.

Prompt caching is explicit and placeable at three levels:

```json
{"cache_control": {"type": "ephemeral"}}
{"system": [{"type": "text", "text": "Long system prompt…", "cache_control": {"type": "ephemeral", "ttl": "1h"}}]}
{"tools": [{"name": "tool1"}, {"name": "tool2", "cache_control": {"type": "ephemeral"}}]}
```

The MCP connector requires a beta header, and `mcp_servers` alone is rejected —
it must be paired with an `mcp_toolset` entry in `tools[]`:

```bash
curl https://api.anthropic.com/v1/messages \
  -H "X-API-Key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: mcp-client-2025-11-20" -H "Content-Type: application/json" \
  -d '{
    "model": "claude-opus-5", "max_tokens": 1000,
    "messages": [{"role": "user", "content": "What tools do you have available?"}],
    "mcp_servers": [{"type": "url", "url": "https://example-server.modelcontextprotocol.io/sse",
                     "name": "example-mcp", "authorization_token": "YOUR_TOKEN"}],
    "tools": [{"type": "mcp_toolset", "mcp_server_name": "example-mcp"}]
  }'
```

Other notable top-level fields: `container` (Agent Skills, requires the code
execution tool), `context_management`, `metadata: {user_id}`, `inference_geo`,
`service_tier: "auto"|"standard_only"`, `stop_sequences`. `temperature`,
`top_p` and `top_k` are documented as **deprecated** on current models —
`temperature` only accepts 1.0 and `top_p` only ≥ 0.99, for backwards
compatibility.

### 3.2 Streaming — named events, no `[DONE]`

The `data: [DONE]` sentinel was removed in `anthropic-version: 2023-06-01`.

```
event: message_start
data: {"type": "message_start", "message": {"id": "msg_1nZdL29xx5MUA1yADyHTEsnR8uuvGzszyY", "type": "message", "role": "assistant", "content": [], "model": "claude-opus-5", "stop_reason": null, "stop_sequence": null, "usage": {"input_tokens": 25, "output_tokens": 1}}}

event: content_block_start
data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}

event: ping
data: {"type": "ping"}

event: content_block_delta
data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}

event: content_block_stop
data: {"type": "content_block_stop", "index": 0}

event: message_delta
data: {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence":null}, "usage": {"output_tokens": 15}}

event: message_stop
data: {"type": "message_stop"}
```

Delta types inside `content_block_delta`:

```
{"type": "content_block_delta","index": 1,"delta": {"type": "input_json_delta","partial_json": "{\"location\": \"San Fra"}}
{"type": "content_block_delta","index": 0,"delta": {"type": "thinking_delta","thinking": "I need to find the GCD…"}}
{"type": "content_block_delta","index": 0,"delta": {"type": "signature_delta","signature": "EqQBCgIYAhIM1gbcDa9GJwZA2b3hGgxBdjrkzLoky3dl1pki…"}}
```

Two gotchas: tool arguments stream as **partial JSON strings** in
`input_json_delta.partial_json` while the final `tool_use.input` is an object;
and `message_delta.usage` counts are **cumulative**. `ping` events can appear
anywhere, any number of times. Errors arrive in-band:
`{"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}`.

### 3.3 Tool calling

```json
{
  "model": "claude-opus-5", "max_tokens": 1024,
  "tools": [{
    "name": "get_weather",
    "description": "Get the current weather for a given location.",
    "input_schema": {"type": "object",
      "properties": {"location": {"type": "string", "description": "City and state, e.g. San Francisco, CA"}},
      "required": ["location"]}
  }],
  "tool_choice": {"type": "auto", "disable_parallel_tool_use": true},
  "messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]
}
```

The model returns `stop_reason: "tool_use"` and a `tool_use` block:

```json
{"type": "tool_use", "id": "toolu_01A09q90qw90lq917835lq9", "name": "get_weather",
 "input": {"location": "New York, NY", "unit": "fahrenheit"}}
```

You reply with a `tool_result` block **in a user message**:

```json
{
  "messages": [
    {"role": "user", "content": "What's the weather in San Francisco?"},
    {"role": "assistant", "content": [ /* the assistant's full content array, tool_use included */ ]},
    {"role": "user", "content": [
      {"type": "tool_result", "tool_use_id": "toolu_01A09q90qw90lq917835lq9", "content": "15 degrees Celsius, partly cloudy"}
    ]}
  ]
}
```

- A failed tool is `{"type":"tool_result", …, "is_error": true}` — don't drop it.
- Parallel calls: one assistant message may hold several `tool_use` blocks;
  return **all** matching `tool_result` blocks in a **single** user message.
- `strict: true` is a **top-level field on the tool definition**, not a
  `tool_choice` option.
- Server tools carry a versioned `type` and no `input_schema`:
  `{"type": "web_search_20260209", "name": "web_search"}`. Anthropic executes
  them; results come back in the same response.
- MCP tools surface as `mcp_tool_use` / `mcp_tool_result` blocks.

### 3.4 Multimodal

```json
{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "$BASE64_IMAGE_DATA"}}
{"type": "image", "source": {"type": "url", "url": "https://platform.claude.com/docs/images/vision-example.jpg"}}
{"type": "image", "source": {"type": "file", "file_id": "file_011CPMxVD3fHLUhvTqtsQA5w"}}
```

```json
{
  "type": "document",
  "source": {"type": "file", "file_id": "file_011CNha8iCJcU1wXNR6q4V8w"},
  "title": "Document Title",
  "context": "Context about the document",
  "citations": {"enabled": true}
}
```

Constraints: `image/jpeg|png|gif|webp`; max 8000×8000 px; 10 MB base64 per image
on the first-party API (5 MB on Bedrock/Google Cloud); 600 images per request
(100 on 200k-context models) inside a 32 MB request cap. **On Bedrock and Google
Cloud only base64 sources work** — no `url`, no `file_id`.

### 3.5 Supporting endpoints

| Endpoint | Shape |
|---|---|
| `POST /v1/messages/count_tokens` | same body minus `max_tokens` → `{"input_tokens": 2095}` |
| `POST /v1/messages/batches` | `{"requests":[{"custom_id":"my-first-request","params":{…full Messages body…}}]}`, up to 100,000 entries / 256 MB |
| `GET /v1/messages/batches/{id}` | poll `processing_status` until `"ended"`, then stream `.jsonl` from `results_url` |
| `POST /v1/files` | multipart `file=@…`; optional `expires_in_seconds` 3600–7776000. No beta header needed any more |
| `GET /v1/models` | pagination is `after_id`/`before_id` + `has_more`/`first_id`/`last_id` |

Batch results come back **in arbitrary order** — key by `custom_id`:

```jsonl
{"custom_id":"my-second-request","result":{"type":"succeeded","message":{"id":"msg_014VwiXbi91y3JMjcpyGBHX5",…}}}
{"custom_id":"my-first-request","result":{"type":"succeeded","message":{"id":"msg_01FqfsLoHwgeFbguDgpz48m7",…}}}
```

### 3.6 The same model, three request formats

Claude is reachable three ways, and the version negotiation differs each time.
This is the single most confusing thing about the Anthropic wire format.

| Platform | Path | Where `model` lives | Where the version lives |
|---|---|---|---|
| First-party | `POST https://api.anthropic.com/v1/messages` | body | header `anthropic-version: 2023-06-01` |
| Bedrock (current) | `POST https://bedrock-mantle.{region}.api.aws/anthropic/v1/messages` | body, `anthropic.`-prefixed | header `anthropic-version: 2023-06-01` |
| Bedrock (InvokeModel) | `POST /model/{modelId}/invoke` | **URL path** | **body** `"anthropic_version": "bedrock-2023-05-31"` |
| Google Cloud | `POST …/publishers/anthropic/models/{MODEL_ID}:rawPredict` | **URL path** | **body** `"anthropic_version": "vertex-2023-10-16"` |

```bash
# Google Cloud — model is in the path, version is in the body, no Anthropic key
curl https://aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/global/publishers/anthropic/models/claude-opus-5:rawPredict \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -d '{
    "anthropic_version": "vertex-2023-10-16",
    "messages": [{"role": "user", "content": "Hey Claude!"}],
    "max_tokens": 100
  }'
```

```bash
# Bedrock Mantle — SigV4 with signing name bedrock-mantle
curl https://bedrock-mantle.us-east-1.api.aws/anthropic/v1/messages \
  --aws-sigv4 "aws:amz:us-east-1:bedrock-mantle" \
  --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" \
  -H "content-type: application/json" -H "anthropic-version: 2023-06-01" \
  -d '{"model": "anthropic.claude-opus-5", "max_tokens": 1024,
       "messages": [{"role": "user", "content": "Hello, Claude"}]}'
```

Model IDs differ too: bare (`claude-opus-5`) on the first-party API,
`anthropic.`-prefixed on Bedrock, and on Vertex dated snapshots use an `@`
separator — `claude-opus-4-5@20251101`, **not** `claude-opus-4-5-20251101`.

Feature coverage is *not* the same. Not supported on Bedrock or Google Cloud:
Files API, Agent Skills, MCP connector, Message Batches, Models API, programmatic
tool calling, server-side `fallbacks`, and URL image/document sources. Structured
outputs and server-side tools are additionally unavailable on Bedrock.

### 3.7 The OpenAI compatibility shim

`POST https://api.anthropic.com/v1/chat/completions` exists, with
`base_url="https://api.anthropic.com/v1/"`. Anthropic scopes it to "testing and
comparing model capabilities" and explicitly not "a long-term or production-ready
solution for most use cases." What it does silently:

- All `system`/`developer` messages are hoisted out and concatenated with a
  single newline into one initial system prompt.
- `choices[]` always has length 1; `temperature` is capped at 1; `n` must be 1.
- **Silently ignored, not rejected**: `logprobs`, `top_logprobs`, `metadata`,
  `response_format`, `prediction`, `presence_penalty`, `frequency_penalty`,
  `seed`, `service_tier`, `audio`, `logit_bias`, `store`, `user`, `modalities`,
  **`reasoning_effort`**, `tools[n].function.strict`, message `name` fields,
  `image_url.detail`, `input_audio` and `file` parts.
- Prompt caching, PDF processing and citations are unavailable.
- `thinking` can be smuggled through `extra_body`.

---

## 4. Google Gemini

Gemini has **two deployment surfaces** speaking one schema, plus two OpenAI shims,
plus a newer stateful API. The container is `contents[]` of `Content{role, parts[]}`,
posted to an RPC-style path with a colon verb.

| Surface | Base | Auth |
|---|---|---|
| Developer API | `https://generativelanguage.googleapis.com/{v1\|v1beta}/models/{model}:{verb}` | `x-goog-api-key: …` **or** `?key=…` |
| Vertex AI (regional) | `https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{p}/locations/{l}/publishers/google/models/{m}:{verb}` | `Authorization: Bearer $(gcloud auth print-access-token)` |
| Vertex AI (global) | `https://aiplatform.googleapis.com/v1/projects/{p}/locations/global/publishers/google/models/{m}:{verb}` | same |
| OpenAI shim (Developer) | `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` | `Authorization: Bearer $GEMINI_API_KEY` |
| OpenAI shim (Vertex) | `https://{LOC}-aiplatform.googleapis.com/v1/projects/{p}/locations/{l}/endpoints/openapi/chat/completions` | OAuth token as the `api_key` |

There is no version header — the version is a path segment (`/v1` GA vs
`/v1beta`). The GenAI SDKs default to `v1beta`.

```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=$GEMINI_API_KEY" \
    -H 'Content-Type: application/json' -X POST \
    -d '{
      "contents": [{
        "parts":[{"text": "Write a story about a magic backpack."}]
        }]
       }'
```

With a system instruction and generation config — note that Google's own docs mix
`snake_case` and `camelCase` freely, and **both are accepted**:

```json
{
  "system_instruction": {"parts": {"text": "You are a cat. Your name is Neko."}},
  "contents": [{"parts": [{"text": "Explain how AI works"}]}],
  "generationConfig": {
    "stopSequences": ["Title"],
    "temperature": 1.0,
    "maxOutputTokens": 800,
    "topP": 0.8,
    "topK": 10
  },
  "safetySettings": [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
  ]
}
```

Everything Gemini-shaped that has no OpenAI equivalent:

| Field | Meaning |
|---|---|
| role values | `user` and **`model`** — there is no `assistant`, no `system`, no `tool` role |
| `systemInstruction` | top-level `Content`; "currently, text only" |
| `generationConfig.thinkingConfig` | `{includeThoughts, thinkingBudget, thinkingLevel}`; `thinkingLevel` ∈ `MINIMAL\|LOW\|MEDIUM\|HIGH`, Gemini 3+ only (error on earlier models) |
| `generationConfig.responseMimeType` | `text/plain` \| `application/json` \| `text/x.enum` |
| `generationConfig.responseSchema` | marked **deprecated** in the current reference, alongside `_responseJsonSchema`; `responseJsonSchema` is the replacement |
| `generationConfig.responseModalities` | `TEXT \| IMAGE \| AUDIO` |
| `safetySettings[]` | `{category, threshold}`; the response carries `promptFeedback` and can block at prompt *or* candidate stage |
| `cachedContent` | a pre-created immutable cache resource name (`cachedContents/{id}`) |
| `Part.thought` / `Part.thoughtSignature` | opaque reasoning signature to replay on later turns |
| `Part.mediaResolution` | enum `MEDIA_RESOLUTION_LOW\|_MEDIUM\|_HIGH` (bare `LOW`/`HIGH` are rejected) |
| `serviceTier`, `store` | Developer API only |
| `labels`, `modelArmorConfig` | Vertex only; `modelArmorConfig` is mutually exclusive with `safetySettings` |

Response:

```json
{"candidates": [{…}], "promptFeedback": {…}, "usageMetadata": {…}, "modelVersion": "…", "responseId": "…", "modelStatus": {…}}
```

Text lives at `candidates[].content.parts[].text`. The reference notes the API
"returns either all requested candidates or none of them."

### 4.1 Streaming is a different endpoint

```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:streamGenerateContent?alt=sse&key=${GEMINI_API_KEY}" \
        -H 'Content-Type: application/json' \
        --no-buffer \
        -d '{ "contents":[{"parts":[{"text": "Write a story about a magic backpack."}]}]}'
```

Not `"stream": true` in the body — a **separate verb**, `:streamGenerateContent`,
with `?alt=sse` to get Server-Sent Events. Every official streaming example uses
`alt=sse` (and `--no-buffer`); omitting it yields a JSON array of
`GenerateContentResponse` objects rather than an event stream. There is no
`[DONE]` sentinel, and each SSE event is a **complete `GenerateContentResponse`**,
not a delta-shaped chunk. The Vertex reference documents only that the response
is "a stream of GenerateContentResponse instances" and does not describe
`?alt=sse` at all.

### 4.2 Tool calling

Declarations are an **array inside one Tool object**, not one entry per tool:

```json
{
  "tools": [{
    "function_declarations": [
      {"name": "enable_lights", "description": "Turn on the lighting system."},
      {"name": "set_light_color",
       "description": "Set the light color. Lights must be enabled for this to work.",
       "parameters": {"type": "object",
         "properties": {"rgb_hex": {"type": "string", "description": "6-digit hex, e.g. ff0000"}},
         "required": ["rgb_hex"]}}
    ]
  }],
  "tool_config": {"function_calling_config": {"mode": "auto"}},
  "contents": {"role": "user", "parts": {"text": "Turn on the lights please."}}
}
```

The round trip — `functionCall` in a `model` turn, `functionResponse` in a
**user** turn:

```json
{
  "contents": [
    {"role": "user",  "parts": {"text": "What is the weather in Boston?"}},
    {"role": "model", "parts": [{"functionCall": {"name": "get_current_weather", "args": {"location": "Boston, MA"}}}]},
    {"role": "user",  "parts": [{"functionResponse": {"name": "get_current_weather",
                                                      "response": {"temperature": 20, "unit": "C"}}}]}
  ],
  "tools": [{"function_declarations": [{"name": "get_current_weather", "description": "…", "parameters": {…}}]}]
}
```

Two surprises: **`tools` must be resent on every turn**, and correlation uses an
*optional* `id` field — the official examples match on `name`.

`toolConfig.functionCallingConfig.mode` is `AUTO | ANY | NONE | VALIDATED`
(+ `allowedFunctionNames[]`, only valid with `ANY`/`VALIDATED`). `VALIDATED` has
no OpenAI analogue: the model may answer in natural language *or* call a
function, but function calls are validated with constrained decoding.

Server-side built-ins are declared as empty objects in the same `tools` array:

```json
"tools": [{"googleSearch": {}}]
"tools": [{"codeExecution": {}}]
"tools": [{"urlContext": {}}]
```

The full `Tool` union also carries `googleSearchRetrieval`, `computerUse`,
`fileSearch`, `mcpServers[]`, and `googleMaps`. On Vertex, `googleSearch` accepts
`exclude_domains`; the Developer API reference's `GoogleSearch` type has only
`timeRangeFilter` and `searchTypes`.

### 4.3 Multimodal

```json
{
  "contents": [{
    "parts": [
      {"text": "Tell me about this instrument"},
      {"inline_data": {"mime_type": "image/jpeg", "data": "<base64>"}}
    ]
  }]
}
```

By reference instead of inline — and video with a time window:

```json
{"fileData": {"mimeType": "video/mp4", "fileUri": "gs://bucket/clip.mp4"},
 "videoMetadata": {"startOffset": {"seconds": 60}, "endOffset": {"seconds": 70}, "fps": 10.0}}
```

The `Part` union in full: `text`, `inlineData`, `functionCall`,
`functionResponse`, `fileData`, `executableCode`, `codeExecutionResult`,
`toolCall`, `toolResponse` — plus the metadata siblings `thought`,
`thoughtSignature`, `partMetadata`, `mediaResolution`, `mediaProcessing`,
`videoMetadata`.

### 4.4 The other Gemini endpoints

| Endpoint | Shape |
|---|---|
| `:countTokens` | `contents[]` **xor** `generateContentRequest` — "never both" → `{"totalTokens": n}` |
| `:embedContent` | takes `content` (a `Content` object), not `input`; options under `embedContentConfig` (`taskType`, `title`, `autoTruncate`, `outputDimensionality`, `documentOcr`) — the top-level versions are deprecated |
| `:batchEmbedContents` | `{"requests": [{model, content}, …]}`; each entry repeats `model` |
| `:predict` (Imagen) | **completely different envelope**: `{"instances": [{"prompt": "…"}], "parameters": {"sampleCount": 4}}` — no `contents`, no `generationConfig` |
| `POST /v1beta/cachedContents` | creates an immutable cache; `expireTime` xor `ttl`; only the TTL can be patched afterwards |
| `wss://…/BidiGenerateContent` | Live API; first frame must be `setup`, then exactly one of `clientContent`/`realtimeInput`/`toolResponse` per message |

Live API frames:

```json
{"setup": {"model": "models/gemini-3.1-flash-live-preview", "responseModalities": ["AUDIO"],
           "systemInstruction": {"parts": [{"text": "You are a helpful assistant."}]}}}
{"realtimeInput": {"text": "Hello, how are you?"}}
{"realtimeInput": {"audio": {"data": "<base64 PCM>", "mimeType": "audio/pcm;rate=16000"}}}
```

Audio must be raw 16-bit PCM, 16 kHz, little-endian. Configuration cannot be
changed while the connection is open.

### 4.5 The OpenAI shims — and what they can't carry

```bash
curl "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GEMINI_API_KEY" \
  -d '{"model": "gemini-3.7-flash", "reasoning_effort": "low",
       "messages": [{"role": "user", "content": "Explain to me how AI works"}]}'
```

Gemini-only knobs ride in `extra_body.google`:

```json
{"model": "gemini-3.7-flash", "messages": [{"role": "user", "content": "…"}],
 "extra_body": {"google": {"thinking_config": {"thinking_level": "low", "include_thoughts": true}}}}
```

Sibling shim routes: `/v1beta/openai/embeddings`, `/models`, `/images/generations`,
`/videos`. The shim is documented as "still in beta while we extend feature
support," and has **no representation** for `googleSearch`/`codeExecution`/
`urlContext`, `safetySettings`, `cachedContent`, or `thoughtSignature`
round-tripping.

The two shims are not interchangeable with each other: the Developer shim takes an
API key as a bearer token and bare model IDs (`gemini-3.7-flash`); the Vertex shim
takes a short-lived OAuth token as the `api_key` and publisher-prefixed IDs
(`google/gemini-2.0-flash-001`), which must be refreshed.

> **Currency note.** As of August 2026 the Gemini Developer API *guide* pages have
> moved to a newer stateful **Interactions API** — `POST /v1beta/interactions`
> with flat `model`/`input`, `previous_interaction_id`,
> `generation_config.thinking_level`, and built-ins declared with a type
> discriminator (`tools=[{"type":"google_search"}]`, **not**
> `{"googleSearch":{}}`). `:generateContent` remains fully documented in the API
> reference and remains the Vertex surface, but new guide examples will not match
> the shapes above.

---

## 5. AWS Bedrock

Bedrock is the widest surface in this document: four runtime API families across
two endpoints, and on the `InvokeModel` path **one request shape per model
vendor**.

| Host | Serves |
|---|---|
| `bedrock-runtime.{region}.amazonaws.com` | `InvokeModel`, `Converse`, `/openai/v1/*`, `/anthropic/v1/messages` |
| `bedrock-mantle.{region}.api.aws` | `/v1/*` (OpenAI Responses + Chat Completions), `/anthropic/v1/messages` |

Auth is SigV4 (signing name `bedrock`) **or** a Bedrock API key as a bearer token:

```
--aws-sigv4 "aws:amz:us-east-1:bedrock" --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY"
Authorization: Bearer $AWS_BEARER_TOKEN_BEDROCK
x-api-key: $AWS_BEARER_TOKEN_BEDROCK   +  anthropic-version: 2023-06-01   # on /anthropic routes
```

There is **no `/v1` segment** on native paths, and **no `model` body field** — the
model or inference-profile id is the path segment.

### 5.1 Converse — the unified API

```bash
POST /model/anthropic.claude-3-sonnet-20240229-v1:0/converse
Content-type: application/json

{
    "messages": [
        {"role": "user", "content": [{"text": "Write an article about impact of high inflation to GDP of a country"}]}
    ],
    "system": [{"text": "You are an economist with access to lots of data"}],
    "inferenceConfig": {"maxTokens": 1000, "temperature": 0.5}
}
```

```json
{
    "output": {"message": {"role": "assistant", "content": [{"text": "<text generated by the model>"}]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 30, "outputTokens": 628, "totalTokens": 658},
    "metrics": {"latencyMs": 1275}
}
```

The distinguishing feature: `ContentBlock` is an **untagged union** — exactly one
member per block, with no `"type"` discriminator. That is the opposite of both
OpenAI and Anthropic:

```
text | image | document | video | audio | toolUse | toolResult
    | guardContent | reasoningContent | cachePoint | citationsContent | searchResult
```

`inferenceConfig` is deliberately minimal — **only** `maxTokens`,
`stopSequences`, `temperature`, `topP`. Everything else goes through the escape
hatches:

| Field | Purpose |
|---|---|
| `additionalModelRequestFields` | free-form JSON merged into the model-native body (`top_k`, Anthropic `thinking`, Nova `reasoningConfig`) |
| `additionalModelResponseFieldPaths` | up to 10 **RFC 6901 JSON Pointers**, e.g. `["/stop_sequence"]`, selecting model-native response fields |
| `guardrailConfig` | `{guardrailIdentifier, guardrailVersion, trace}`; per-block targeting via `guardContent` |
| `promptVariables` | only with a Prompt-management ARN as `modelId` — and then `additionalModelRequestFields`/`inferenceConfig`/`system`/`toolConfig` are **forbidden** |
| `requestMetadata` | ≤16 key/value pairs for filtering invocation logs |
| `serviceTier` | `{type: reserved\|priority\|default\|flex}` |
| `performanceConfig` | `{latency: standard\|optimized}` |
| `outputConfig.textFormat.structure` | structured outputs |

`stopReason` ∈ `end_turn | tool_use | max_tokens | stop_sequence |
guardrail_intervened | content_filtered | malformed_model_output |
malformed_tool_use | model_context_window_exceeded`.

Tool calling, with the same "no `tool` role" pattern:

```json
{"toolConfig": {"tools": [{"toolSpec": {
    "name": "top_song",
    "description": "Get the most popular song played on a radio station.",
    "inputSchema": {"json": {"type": "object",
        "properties": {"sign": {"type": "string", "description": "The call sign, e.g. WZPZ or WKRP."}},
        "required": ["sign"]}}
}}]}}
```

```json
{"output": {"message": {"role": "assistant", "content": [
    {"toolUse": {"toolUseId": "tooluse_abc123", "name": "top_song", "input": {"sign": "WZPZ"}}}]}},
 "stopReason": "tool_use"}
```

```json
{"role": "user", "content": [
  {"toolResult": {"toolUseId": "tooluse_abc123",
                  "content": [{"json": {"song": "Elemental Hotel", "artist": "8 Storey Hike"}}]}}]}
```

Failures set `"status": "error"` (supported only by Nova and Claude 3/4).
`toolChoice` is a union object — `{"auto":{}}` / `{"any":{}}` /
`{"tool":{"name":"top_song"}}` — where `any` fills the role of OpenAI's
`required`.

Multimodal blocks take bytes **or** an S3 location, and a `DocumentBlock`
*requires* an accompanying text block in the same message:

```json
{"image": {"format": "png", "source": {"bytes": "{image in bytes}"}}}
{"image": {"format": "png", "source": {"s3Location": {"uri": "s3://amzn-s3-demo-bucket/myImage", "bucketOwner": "111122223333"}}}}
{"document": {"format": "pdf", "name": "MyDocument", "source": {"bytes": "{document in bytes}"}}}
{"video": {"format": "mp4", "source": {"s3Location": {"uri": "s3://amzn-s3-demo-bucket/myVideo", "bucketOwner": "111122223333"}}}}
{"cachePoint": {"type": "default"}}
```

AWS warns that a document `name` is prompt-injectable — use a neutral one.

### 5.2 ConverseStream — typed AWS event-stream frames

Not SSE. `application/vnd.amazon.eventstream`, no `data:` lines, no `[DONE]`:

```
{'messageStart': {'role': 'assistant'}}
{'contentBlockDelta': {'delta': {'text': ''}, 'contentBlockIndex': 0}}
{'contentBlockDelta': {'delta': {'text': ' Title'}, 'contentBlockIndex': 0}}
{'messageStop': {'stopReason': 'max_tokens'}}
{'metadata': {'usage': {'inputTokens': 47, 'outputTokens': 20, 'totalTokens': 67}, 'metrics': {'latencyMs': 100.0}}}
```

Order is `messageStart` → per block (`contentBlockStart` for tool use →
`contentBlockDelta`* → `contentBlockStop`) → `messageStop` → `metadata`. Blocks
can interleave, so reassemble on `contentBlockIndex`. Terminal errors arrive as
**in-stream union members**, not HTTP statuses: `modelStreamErrorException` (424),
`modelTimeoutException` (408), `throttlingException` (429),
`serviceUnavailableException` (503), `validationException` (400),
`internalServerException` (500).

### 5.3 InvokeModel — one body shape per vendor

`POST /model/{modelId}/invoke` wraps the model's own native body. Bedrock adds
nothing but the envelope.

**Anthropic Claude** — Messages format with the version moved into the body:

```json
{
    "anthropic_version": "bedrock-2023-05-31",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello world"}]}]
}
```

Also accepts `anthropic_beta: ["computer-use-2024-10-22"]` and
`output_config.effort`. Tools use `{"type":"custom", "name", "description", "input_schema"}`.

**Amazon Nova** — nearly Converse-shaped, but `bytes` is a base64 *string* here
and a byte array on Converse:

```json
{
  "system": [{"text": "string"}],
  "messages": [{"role": "user", "content": [
      {"text": "string"},
      {"image": {"format": "jpeg", "source": {"bytes": "<base64 for Invoke API>"}}}]}],
  "inferenceConfig": {"maxTokens": 512, "temperature": 0.7, "topP": 0.9, "topK": 50,
                      "reasoningConfig": {"type": "enabled", "maxReasoningEffort": "medium"}},
  "toolConfig": {"tools": [{"toolSpec": {"name": "…", "inputSchema": {"json": {…}}}}]},
  "toolChoice": {"auto": {}}
}
```

**Amazon Titan Text** — no messages at all:

```json
{"inputText": "string",
 "textGenerationConfig": {"temperature": 0.7, "topP": 0.9, "maxTokenCount": 3072, "stopSequences": []}}
```

**Meta Llama** — you supply the chat template yourself; Converse applies it for you, Invoke does not:

```json
{"prompt": "<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\nWhat can you help me with?<|eot_id|><|start_header_id|>assistant<|end_header_id|>",
 "temperature": 0.5, "top_p": 0.9, "max_gen_len": 512}
```

**Mistral (text completion)** — returns `outputs[]`, not `choices[]`:

```json
{"prompt": "<s>[INST] What is your favourite condiment? [/INST]",
 "max_tokens": 400, "stop": ["string"], "temperature": 0.7, "top_p": 0.7, "top_k": 50}
```

**Mistral (chat completion)** — OpenAI-shaped, with `tool_choice` accepting
`"any"` where OpenAI says `"required"`, and `stop_reason` inside each choice
rather than `finish_reason`.

**Cohere Command** — top-p is `p` and top-k is `k`:

```json
{"prompt": "string", "temperature": 0.6, "p": 1, "k": 0, "max_tokens": 200,
 "stop_sequences": ["string"], "return_likelihoods": "GENERATION", "stream": false,
 "num_generations": 2, "truncate": "END"}
```

**Cohere Command R/R+** — a third shape again, with uppercase chat-history roles,
grounding documents, and `parameter_definitions` instead of JSON Schema:

```json
{"message": "What are some skills I should have?",
 "chat_history": [{"role": "USER", "message": "Who discovered gravity?"},
                  {"role": "CHATBOT", "message": "…Sir Isaac Newton"}],
 "documents": [{"title": "Tall penguins", "snippet": "Emperor penguins are the tallest."}],
 "tools": [{"name": "top_song", "description": "…",
            "parameter_definitions": {"sign": {"description": "…", "type": "str", "required": true}}}],
 "tool_results": [{"call": {"name": "top_song", "parameters": {"sign": "WZPZ"}},
                   "outputs": [{"song": "Elemental Hotel"}]}],
 "p": 0.5, "k": 250, "prompt_truncation": "OFF"}
```

**AI21 Jamba** — plain string content per message, `n` 1–16.

**Embeddings** are also `InvokeModel`:

```json
// amazon.titan-embed-text-v2:0
{"inputText": "What are the different services that you offer?", "dimensions": 1024, "normalize": true, "embeddingTypes": ["binary"]}
// cohere.embed-english-v3
{"input_type": "search_document", "texts": ["hello world"], "truncate": "END", "embedding_types": ["int8", "float"]}
```

Titan V2 omits the top-level `embedding` field when `embeddingTypes` is
`["binary"]` only. Cohere Embed accepts `images` **xor** `texts`, max 1 image per
call, and Bedrock does not stream Cohere embeddings.

### 5.4 InvokeModelWithResponseStream — the double wrapper

```
POST /model/amazon.titan-text-express-v1/invoke-with-response-stream
x-amzn-bedrock-accept: */*          # note: NOT the Accept header
content-type: application/json
```

Response frames are a union, and the success member is opaque:

```json
{"chunk": {"bytes": "<blob>"},
 "internalServerException": {}, "modelStreamErrorException": {}, "modelTimeoutException": {},
 "serviceUnavailableException": {}, "throttlingException": {}, "validationException": {}}
```

You must **decode `chunk.bytes` yourself** to reach the provider's own chunk JSON
(boto3: `json.loads(event['chunk']['bytes'].decode())`). ConverseStream, by
contrast, gives you already-parsed typed events. The real payload media type is
echoed in the `x-amzn-bedrock-content-type` response header; the HTTP
`Content-Type` is always `application/vnd.amazon.eventstream`.

Also note: **the AWS CLI cannot perform any Bedrock streaming operation.**

### 5.5 Inference profiles, and the OpenAI surface

An inference profile is substituted for a model id in the same path:

```
POST /model/us.anthropic.claude-3-5-sonnet-20240620-v1:0/converse
```

Prefixes `us.` / `eu.` / `apac.` / `global.` mark system-defined cross-region
profiles. Application inference profiles must be passed as a **full ARN**
(`arn:aws:bedrock:{region}:{account}:application-inference-profile/{id}`). The
same `modelId` field also accepts foundation-model, custom-model, imported-model,
provisioned-model, prompt, prompt-router and **SageMaker endpoint** ARNs.

The OpenAI-compatible route is prefixed on the runtime endpoint and bare on mantle:

```bash
curl -X POST "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AWS_BEARER_TOKEN_BEDROCK" \
  -d '{"model": "openai.gpt-oss-120b", "messages": [{"role": "user", "content": "Hello"}]}'
```

Coverage is partial: per AWS's own compatibility matrix, `openai.gpt-oss-*` and
z.ai GLM models support Chat Completions, while **Claude models are marked
not-supported there** and are reached via Invoke / Converse / the Anthropic
Messages route. Guardrails on this path ride out-of-band, in headers
(`X-Amzn-Bedrock-GuardrailIdentifier`, `-GuardrailVersion`, `-Trace`) plus
`extra_body: {"amazon-bedrock-guardrailConfig": {"tagSuffix": "xyz"}}`.

Endpoint-level feature gaps worth memorising: bedrock-mantle has **no
Guardrails, no cross-region inference, no prompt routing**, and rejects
`output_config.format` on its Messages route; bedrock-runtime's Responses API
rejects `background: true` and application inference profiles.

### 5.6 SageMaker, for completeness

```
POST /endpoints/{EndpointName}/invocations     host: runtime.sagemaker.{region}.amazonaws.com
X-Amzn-SageMaker-Target-Model / -Target-Variant / -Inference-Component / -Session-Id / …
```

The body format is **entirely defined by your container** — there is no provider
schema at all. 6 MB request and response cap, 60-second container timeout,
SigV4 only. `ModelError` returns HTTP 424 with `LogStreamArn` and
`OriginalStatusCode`.

---

## 6. Microsoft Azure

Azure speaks OpenAI's *body* across two Azure-specific *URL* shapes, and the
"model" is always a **deployment name**, not a model id. A mismatched `model`
yields **404, not 400** — the deployment doesn't exist.

| Surface | URL | `api-version` |
|---|---|---|
| **v1 (current GA)** | `POST https://{resource}.openai.azure.com/openai/v1/{openai-path}` | optional; only `v1` or `preview` |
| **Classic / dated** | `POST https://{resource}.openai.azure.com/openai/deployments/{deployment-id}/chat/completions?api-version=YYYY-MM-DD` | **required**, dated |
| **Foundry `/models`** (legacy) | `POST https://{resource}.services.ai.azure.com/models/chat/completions?api-version=2025-04-01` | required |

Auth is one of:

```
api-key: $AZURE_OPENAI_API_KEY
Authorization: Bearer $AZURE_OPENAI_AUTH_TOKEN      # Entra ID
```

Entra tokens must be issued with scope **`https://ai.azure.com/.default`** (the
generated OpenAPI blocks on some reference pages still show the older
`https://cognitiveservices.azure.com/.default`; the migration guide's action item
is to change to the former). Entra auth also requires the resource to have a
custom subdomain — a regional endpoint returns 401. Responses carry an
`apim-request-id` header, because API Management sits in front.

```bash
# v1 surface — deployment name goes in the body's `model` field
curl -X POST https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AZURE_OPENAI_AUTH_TOKEN" \
  -d '{
      "model": "MAI-DS-R1",
      "messages": [
        {"role": "developer", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain what the bitter lesson is?"}
      ]
  }'
```

```bash
# Classic surface — deployment in the path, no `model` in the body
POST https://YOUR_RESOURCE_NAME.openai.azure.com/openai/deployments/YOUR_DEPLOYMENT_NAME/chat/completions?api-version=2024-06-01

{"messages": [{"role": "system", "content": "You are an AI assistant…"},
              {"role": "user", "content": "Does Azure OpenAI support customer managed keys?"}]}
```

### 6.1 What Azure adds to the OpenAI shape

**Content filtering, injected into every response** — OpenAI never returns this:

```json
{
  "choices": [{
    "index": 0, "finish_reason": "stop",
    "message": {"role": "assistant", "content": "…"},
    "content_filter_results": {
      "hate": {"filtered": false, "severity": "safe"},
      "self_harm": {"filtered": false, "severity": "safe"},
      "sexual": {"filtered": false, "severity": "safe"},
      "violence": {"filtered": false, "severity": "safe"}
    }
  }],
  "prompt_filter_results": [{"prompt_index": 0, "content_filter_results": {…}}]
}
```

Extended Azure-only categories on the v1 schema: `custom_blocklists`,
`custom_topics`, `personally_identifiable_information` (with `redacted_text`),
`protected_material_text`, `protected_material_code` (with
`citation.URL`/`citation.license`), `ungrounded_material`, `profanity`, plus
prompt-side `jailbreak` and `indirect_attack`.

**Azure-only request fields:**

- `user_security_context` — `{application_name, end_user_id, end_user_tenant_id, source_ip}`, feeds Microsoft Defender for Cloud.
- `data_sources` — On Your Data / RAG, **classic path only** (see below).

**Azure-only endpoints:** `POST /openai/v1/responses/compact`, and the video
generation job family `/openai/v1/video/generations/jobs` + `/{id}/content/video`
and `/content/thumbnail`.

Azure also documents its own `reasoning_effort` matrix with per-model defaults —
`gpt-5.1` defaults to `none`; models before 5.1 default to `medium` and don't
support `none`; `gpt-5-pro` only supports `high`.

### 6.2 On Your Data — the `data_sources` extension

Exactly one element, mutually exclusive with `logprobs`/`top_logprobs`, available
**only on the dated `/openai/deployments/` path**, and **deprecated with a
retirement date of 2026-10-14**:

```bash
az rest --method POST \
 --uri $AzureOpenAIEndpoint/openai/deployments/$ChatCompletionsDeploymentName/chat/completions?api-version=2024-05-01-preview \
 --resource https://cognitiveservices.azure.com/ \
 --body '
{
    "data_sources": [{
        "type": "azure_search",
        "parameters": {
            "endpoint": "'$SearchEndpoint'",
            "index_name": "'$SearchIndex'",
            "authentication": {"type": "system_assigned_managed_identity"}
        }
    }],
    "messages": [{"role": "user", "content": "Who is DRI?"}]
}'
```

The assistant message gains a `context` property carrying `citations[]`,
`intent`, and `all_retrieved_documents[]`, and the content contains `[doc1]`,
`[doc2]` markers indexing into `context.citations`. From a Python OpenAI client
this rides in `extra_body`, since `data_sources` is not an OpenAI field.

### 6.3 The Foundry `/models` surface and its `extra-parameters` header

```
POST https://<resource>.services.ai.azure.com/models/chat/completions?api-version=2025-04-01
Authorization: Bearer <bearer-token>
Content-Type: application/json
extra-parameters: pass-through

{"messages": [{"role": "user", "content": "Explain Riemann's conjecture in 1 paragraph"}],
 "temperature": 0, "top_p": 1, "response_format": {"type": "text"}, "safe_prompt": true}
```

`extra-parameters` has no analogue anywhere else and is genuinely useful: `error`
(default — reject unknown fields), `pass-through` (forward them to the model, which
is how `safe_prompt` above reaches Mistral), or `drop`.

Its errors are **flat**, not OpenAI's nested `{"error": {…}}`:

```json
{"status": 422, "code": "parameter_not_supported",
 "detail": {"loc": ["body", "response_format"], "input": "json_object"},
 "message": "One of the parameters contain invalid values."}
```

> **Deprecation landscape.** The Azure AI Inference beta SDK retired 2026-08-26;
> On Your Data retires 2026-10-14; **GitHub Models was fully retired
> 2026-07-30** (`models.github.ai` is gone — do not build against it). Only
> `/openai/v1/` is forward-looking. Azure API Management models the split as
> three client-compatibility modes: "Azure OpenAI"
> (`/openai/deployments/{d}/chat/completions`), "Azure AI"
> (`/{model}/models/chat/completions`), and "Azure OpenAI v1"
> (`/openai/v1/{model}/chat/completions`).

---

## 7. Cohere and rerank

Cohere's native v2 API takes an OpenAI-*like* `messages[]` but returns a
**completely different envelope** — a singular `message` object with typed content
blocks, no `choices[]`:

```bash
curl -X POST https://api.cohere.com/v2/chat \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{
    "stream": false,
    "model": "command-a-plus-05-2026",
    "messages": [{"role": "user", "content": "Tell me about LLMs"}]
  }'
```

```json
{
  "id": "…",
  "finish_reason": "COMPLETE",
  "message": {
    "role": "assistant",
    "content": [{"type": "text", "text": "…"}, {"type": "thinking", "thinking": "…"}],
    "tool_calls": [{"id": "…", "type": "function", "function": {"name": "…", "arguments": "…"}}],
    "tool_plan": "…",
    "citations": []
  },
  "usage": {
    "billed_units": {"input_tokens": 0, "output_tokens": 0, "search_units": 0, "classifications": 0},
    "tokens": {"input_tokens": 0, "output_tokens": 0},
    "cached_tokens": 0
  }
}
```

`finish_reason` ∈ `COMPLETE | STOP_SEQUENCE | MAX_TOKENS | TOOL_CALL | ERROR | TIMEOUT`.

Cohere-specific fields on the request:

| Field | Notes |
|---|---|
| `k` / `p` | **not** `top_k` / `top_p` |
| `stop_sequences` | not `stop` |
| `documents[]` | native grounded generation — plain strings or `{data, id}` objects; produces `citations` |
| `citation_options.mode` | `FAST \| ACCURATE \| OFF` (v2 spellings; `ENABLED`/`DISABLED` are legacy) |
| `safety_mode` | `CONTEXTUAL \| STRICT \| OFF` |
| `thinking` | `{type: "enabled"\|"disabled", token_budget: int}` |
| `strict_tools` | v2-only, experimental; requires ≥1 `required` parameter per tool, max 200 fields across all tools |
| `tool_choice` | `REQUIRED \| NONE` (uppercase) |
| `priority` | integer; lower = higher priority |
| `stream` | **required** on `/v2/chat` — set it explicitly |
| `assistant.tool_plan` | Cohere-only; the model's plan text before tool calls, **must be echoed back** on multi-turn tool conversations |

`usage.billed_units` is separate from `usage.tokens` — billed vs actual.

Legacy `/v1/chat` is structurally different again: one `message` string plus
`chat_history[]`, a `preamble` instead of a system message, and `connectors`.
`connectors` and `search_queries_only` were deprecated 2025-09-15, along with
`/v1/generate`, `/v1/summarize`, `/v1/connectors` and `/v1/classify`. **There is
no `/v2/generate`** — `/v2/chat` is the only v2 generation endpoint.

### 7.1 Embeddings — `input_type` is mandatory and changes the vector

```json
{"model": "embed-v4.0", "input_type": "search_document",
 "texts": ["hello", "goodbye"], "embedding_types": ["float"], "truncate": "END"}
```

`input_type` ∈ `search_document | search_query | classification | clustering | image`.
Mismatching query and document types measurably degrades retrieval. The response
keys embeddings **by type** (`{"embeddings": {"float": [[…]]}}`), unlike OpenAI's
flat `data[].embedding`. `output_dimension` (256/512/1024/1536 on v4) does
Matryoshka truncation.

### 7.2 Rerank — the shape everyone copies

```json
{
  "model": "rerank-v4.0-pro",
  "query": "What is the capital of the United States?",
  "documents": ["Carson City is the capital city of…", "Washington, D.C. … is the capital of the United States.", "…"],
  "top_n": 3
}
```

```json
{"results": [{"index": 3, "relevance_score": 0.999071}, {"index": 4, "relevance_score": 0.7867867}],
 "id": "07734bd2-…", "meta": {"billed_units": {"search_units": 1}}}
```

This `{query, documents[], top_n}` body is the de-facto rerank standard — LiteLLM,
Envoy AI Gateway, agentgateway, vLLM, Jina and Voyage all speak a variant of it.
Note Cohere v2 has **no `return_documents`** parameter (Voyage and Jina do), and
billing is in `search_units`, not tokens.

Cohere's OpenAI shim lives at a **different host**:
`https://api.cohere.ai/compatibility/v1/chat/completions`. Unsupported there:
`store`, `metadata`, `logit_bias`, `top_logprobs`, `n`, `modalities`,
`prediction`, `audio`, `service_tier`, `parallel_tool_calls`; on embeddings,
`dimensions` and `user`. `reasoning_effort` accepts only `none` and `high`. None
of `connectors`, `documents`, `citation_options`, `input_type` or `images` is
reachable through the shim.

**Other embedding/rerank specialists**, for completeness:

```json
// Voyage AI — POST https://api.voyageai.com/v1/embeddings
{"input": ["Sample text 1", "Sample text 2"], "model": "voyage-4-large",
 "input_type": null, "truncation": true, "output_dimension": null,
 "output_dtype": "float", "encoding_format": null}
```

`output_dtype` ∈ `float | int8 | uint8 | binary | ubinary`;
`output_dimension` ∈ `2048 | 1024 | 512 | 256`.

---

## 8. OpenAI-compatible hosted vendors

All of these take `Authorization: Bearer <KEY>` + `Content-Type: application/json`
— no custom auth header, no version header — and all stream OpenAI-identical SSE
chunks terminated by `data: [DONE]`. What differs is the base URL, the sampler
extensions, and (most dangerously) what they silently ignore.

### 8.1 Base URLs — the first thing that breaks

| Vendor | Base URL |
|---|---|
| Groq | `https://api.groq.com/openai/v1` — **not** `/v1`; a bare `/v1` 404s |
| Together AI | `https://api.together.ai/v1` |
| Fireworks AI | `https://api.fireworks.ai/inference/v1` |
| DeepSeek | `https://api.deepseek.com` — **no version segment at all** |
| xAI (Grok) | `https://api.x.ai/v1` |
| Mistral AI | `https://api.mistral.ai/v1` |
| Perplexity | `https://api.perplexity.ai/v1/sonar` (canonical); `/chat/completions` is an SDK alias |
| Cerebras | `https://api.cerebras.ai/v1` |
| Nebius Token Factory | `https://api.tokenfactory.nebius.com/v1` (was `api.studio.nebius.com`) |
| DeepInfra | `https://api.deepinfra.com/v1/openai` |
| Hyperbolic | `https://api.hyperbolic.xyz/v1` |
| SambaNova | `https://api.sambanova.ai/v1` |
| Ray Serve LLM | `http://<host>:8000/v1` — **no auth by default** |

Model-id format also differs: **Fireworks requires fully-qualified resource
paths** (`accounts/fireworks/models/deepseek-v3p1`); everyone else takes bare
slugs or HuggingFace-style `org/model`.

### 8.2 Silent ignores — the dangerous class

These fail *open*, not loud:

- **Together AI**: "`service_tier`, `store`, `metadata`, and `prediction` are
  accepted but ignored"; on vision, "the `detail` field is accepted but ignored";
  `seed` is "best-effort. Determinism is not guaranteed across replicas."
- **Groq**: silently converts `temperature: 0` to `1e-8`.
- **Mistral**: the seed field is `random_seed`, **not** `seed` — passing OpenAI's
  `seed` is a silent no-op. This is the classic Mistral porting bug.

Hard rejects, by contrast, are at least visible:

- **Groq** does not support `logprobs`, `logit_bias`, `top_logprobs`, or
  `messages[].name`, and requires `n == 1`.
- **DeepSeek** has **removed** `frequency_penalty` and `presence_penalty` —
  "This parameter is no longer supported."
- **Cerebras** `gpt-oss-120b` rejects `tools` and `response_format` in the same
  request, and rejects `json_object` response format when `stream` is true.

### 8.3 Sampler extensions

| Field | Vendors |
|---|---|
| `top_k` | Together, Fireworks, SambaNova, Reka |
| `min_p` | Together, Fireworks |
| `repetition_penalty` | Together, Fireworks |
| `mirostat_target` / `mirostat_lr` | **Fireworks only** |
| `do_sample` | **SambaNova only** (a HuggingFace-generate-ism) |
| `extra_body` escape hatch | **Nebius** — the full vLLM param set goes here, not top-level |

Groq, DeepSeek, xAI, Mistral, Cerebras and Perplexity expose no `top_k` at all.

Context-overflow policy shares a field name with **opposite defaults**:
`context_length_exceeded_behavior` defaults to `"error"` on Together and
`"truncate"` on Fireworks. Fireworks adds `prompt_truncate_len`, which evicts
earlier user/assistant messages first.

### 8.4 Reasoning — four incompatible spellings

```json
{"reasoning_effort": "high"}                          // Groq, xAI, Cerebras, Perplexity, DeepSeek
{"thinking": {"type": "enabled"}}                     // DeepSeek
{"reasoning": {"enabled": true}}                      // Together
{"chat_template_kwargs": {"enable_thinking": true}}   // SambaNova
{"reasoning_format": "parsed"}                        // Groq — output format, not effort
```

Enum values disagree too: Groq `none|default|low|medium|high`, xAI
`none|low|medium|high`, Cerebras `low|medium|high|none`, Perplexity
`minimal|low|medium|high`, DeepSeek `low|high|max` — DeepSeek is the only one
with `max`.

**Reasoning output is not OpenAI-shaped anywhere.** DeepSeek, Fireworks and
Together all return a flat `reasoning_content` (or `reasoning`) sibling field on
the assistant message/delta rather than OpenAI's nested structure.

### 8.5 Web search — three mutually incompatible designs

```json
// Groq — a top-level field, NOT a tool
{"model": "groq/compound-mini", "messages": [{"role": "user", "content": "What is the latest in AI?"}],
 "search_settings": {"include_domains": ["*.org"], "exclude_domains": ["wikipedia.org"]}}
```

```json
// xAI Chat Completions — search_parameters, dates as ISO-8601 YYYY-MM-DD
{"search_parameters": {"mode": "auto", "from_date": "2026-01-01", "max_search_results": 10, "return_citations": true}}
```

```json
// Perplexity — a flat family, dates as MM/DD/YYYY
{"search_mode": "academic", "search_domain_filter": ["arxiv.org"],
 "search_recency_filter": "month", "search_after_date_filter": "01/01/2026"}
```

Note the date-format clash: xAI `YYYY-MM-DD` vs Perplexity `MM/DD/YYYY`. On xAI's
newer Responses API, search moves from a request field to a **server-side tool**:
`{"type": "web_search", "enable_image_search": true}`.

### 8.6 Vendor-specific oddities worth knowing

- **xAI `deferred: true`** returns `{request_id}` instead of a completion; poll
  `GET /v1/chat/deferred-completion/{request_id}`. Nothing else here has an
  async-polling variant *on the chat endpoint*. xAI also now documents Chat
  Completions as "offered as a legacy endpoint" — new features land on
  `/v1/responses` first.
- **DeepInfra `service_tier`** is an explicit price multiplier: `priority` (+50%)
  or `flex` (−20%). Plus `fail_fast: true` to get a 429 instead of being queued.
- **Mistral** has the widest non-OpenAI surface: `/v1/fim/completions` (with
  `prompt` + `suffix` + `min_tokens` — fill-in-the-middle has no OpenAI analogue),
  `/v1/ocr`, `/v1/agents/completions`, `/v1/conversations`, `/v1/moderations`.

```json
// Mistral FIM — the suffix is the whole point
{"model": "codestral-2404", "prompt": "def", "suffix": "    return result", "min_tokens": 10}
```

```json
// Mistral OCR — outside the OpenAI surface entirely
{"model": "mistral-ocr-latest", "document": {"type": "document_url", "document_url": "https://arxiv.org/pdf/2201.04234"}}
```

- **Hyperbolic**'s media paths are singular and non-OpenAI:
  `/v1/image/generation` and `/v1/audio/generation`, not `/v1/images/generations`.
  A generic OpenAI client will not find them.
- **Perplexity** also ships `/v1/agent`, which takes a flat `input` string and a
  `preset` — not OpenAI-compatible at all.
- **Anyscale Endpoints** shut down its self-serve multi-tenant API on 2024-08-01.
  The "Anyscale-style" pattern survives only self-hosted via Ray Serve LLM's
  `OpenAiIngress` — which documents **no authentication at all** and must be
  fronted by your own auth proxy.

---

## 9. OpenRouter

`https://openrouter.ai/api/v1` — an OpenAI Chat Completions **superset**, with a
routing layer bolted on. Auth is `Authorization: Bearer $OPENROUTER_API_KEY`, plus
optional attribution headers `HTTP-Referer: <YOUR_SITE_URL>` and
`X-Title: <YOUR_SITE_NAME>`.

```json
{
  "models": ["~anthropic/claude-sonnet-latest", "gryphe/mythomax-l2-13b"],
  "messages": [{"role": "user", "content": "What is the meaning of life?"}]
}
```

`models[]` is an ordered fallback array — it fires on context-length validation
errors, moderation flags, rate-limiting and downtime.

The `provider{}` object is the real differentiator:

```json
{"provider": {"order": ["openai", "together"]}}
{"provider": {"order": ["together"], "allow_fallbacks": false}}
{"provider": {"require_parameters": true}}
{"provider": {"data_collection": "deny"}}
{"provider": {"quantizations": ["fp8"]}}
{"provider": {"sort": "throughput"}}
{"provider": {"sort": {"by": "price", "partition": "none"}}}
{"provider": {"zdr": true}}
{"provider": {"max_price": {"prompt": 1, "completion": 2}}}
{"provider": {"preferred_max_latency": {"p50": 1, "p90": 3}}}
```

`require_parameters: true` is the one to remember: without it, providers that
don't support a parameter you sent **silently ignore it**.
`quantizations` ∈ `int4, int8, fp4, mxfp4, nvfp4, fp6, fp8, mxfp8, fp16, bf16, fp32, unknown`.

Reasoning and plugins:

```json
{"reasoning": {"effort": "high", "max_tokens": 2000, "exclude": false, "enabled": true}}
```

```json
{"plugins": [{"id": "web", "engine": "exa", "mode": "auto", "max_results": 5,
              "search_prompt": "A web search was conducted on `date`. …"}]}
{"plugins": [{"id": "file-parser", "enabled": true, "pdf": {"engine": "cloudflare-ai"}}]}
{"plugins": [{"id": "context-compression", "enabled": true, "engine": "middle-out"}]}
```

Plugin ids in the live spec: `auto-router`, `auto-beta-router`,
`context-compression`, `file-parser`, `fusion`, `moderation`, `pareto-router`,
`response-healing`, `web`, `web-fetch`. The web-search shorthand is a model
suffix: `{"model": "openai/gpt-5.2:online"}`.

> **Three widely-repeated OpenRouter facts are now stale.**
> `transforms: ["middle-out"]` is **gone** from the live OpenAPI spec — use the
> `context-compression` plugin. `usage: {include: true}` and
> `stream_options: {include_usage: true}` are **deprecated with no effect** —
> usage is always returned. `route: "fallback"` is **deprecated** in favour of
> `provider.sort.partition` (`"fallback"` → `"model"`, `"sort"` → `"none"`).

The response carries two OpenRouter-only blocks — `usage.cost` /
`usage.cost_details.upstream_inference_cost`, and an `openrouter_metadata` object
naming the model actually selected:

```json
{"openrouter_metadata": {"requested": "openai/gpt-4", "strategy": "direct", "region": "iad",
  "summary": "available=1, selected=OpenAI", "attempt": 1, "is_byok": false,
  "endpoints": {"total": 1, "available": [{"model": "openai/gpt-4", "provider": "OpenAI", "selected": true}]}}}
```

Requests are priced using the model **ultimately used**, returned in the response
`model` field. `GET /api/v1/generation?id={GENERATION_ID}` returns post-hoc true
cost, native token counts, latency breakdown and which provider served it.

OpenRouter also exposes `/api/v1/messages` (Anthropic-shaped) and
`/api/v1/responses` (OpenAI Responses-shaped, carrying the same routing layer —
but **stateless only**: `store: true` or `previous_response_id` are rejected with
400 despite appearing in the schema). Note `/api/v1/completions` is **absent from
the live spec** — treat it as removed.

---

## 10. Local and self-hosted

This family speaks **three** wire formats, not one: genuinely native shapes that
share nothing with OpenAI; OpenAI-compatible surfaces with vendor extensions
merged into the same body; and thin passthroughs. Auth is almost universally
absent or a token the server ignores.

| Server | Default port | Native route | OpenAI route |
|---|---|---|---|
| Ollama | 11434 | `/api/generate`, `/api/chat`, `/api/embed` | `/v1/*` |
| llama.cpp `llama-server` | 8080 | `/completion`, `/infill`, `/embedding` | `/v1/*` |
| vLLM | 8000 | `/pooling`, `/score`, `/rerank`, `/tokenize` | `/v1/*` |
| SGLang | 30000 | `/generate`, `/encode`, `/v1/score` | `/v1/*` |
| TGI | 8080 | `POST /`, `/generate`, `/generate_stream` | `/v1/*` |
| LM Studio | 1234 | `/api/v0/*`, `/api/v1/chat` | `/v1/*` |
| LocalAI | 8080 | — | `/v1/*` (+ `/v1/edits`, `/v1/messages`) |
| NVIDIA NIM | 8000 | `/generative_scoring`, `/inference/v1/generate` | `/v1/*` |
| Triton | 8000 | `/v2/models/{m}/infer`, `/generate` | — |
| text-generation-webui | 5000 | `/v1/internal/*` | `/v1/*` |

### 10.1 Ollama — sampler knobs live in `options{}`

```bash
curl http://localhost:11434/api/generate -d '{
  "model": "llama3.2",
  "prompt": "Why is the sky blue?",
  "stream": false,
  "options": {
    "num_keep": 5, "seed": 42, "num_predict": 100, "top_k": 20, "top_p": 0.9,
    "min_p": 0.0, "typical_p": 0.7, "repeat_last_n": 33, "temperature": 0.8,
    "repeat_penalty": 1.2, "presence_penalty": 1.5, "frequency_penalty": 1.0,
    "penalize_newline": true, "stop": ["\n", "user:"],
    "num_ctx": 1024, "num_batch": 2, "num_gpu": 1, "num_thread": 8
  }
}'
```

Three things to internalise:

1. **Streaming is NDJSON, not SSE.** Default is `stream: true`; each chunk is a
   complete JSON object on its own line, no `data:` prefix, no `[DONE]`
   sentinel. The final object has `done: true` plus timing stats.
2. **`num_predict`, not `max_tokens`** — and it lives inside `options`.
3. **`format` is overloaded**: the string `"json"` for JSON mode, or a bare JSON
   Schema object *as the value* — no `{type:"json_schema", json_schema:{…}}`
   envelope.

```json
{"model": "llama3.1:8b", "prompt": "…", "stream": false,
 "format": {"type": "object",
            "properties": {"age": {"type": "integer"}, "available": {"type": "boolean"}},
            "required": ["age", "available"]}}
```

`/api/chat` keeps the same envelope with a `messages` array. Note that
**images hang off the message** as an array of base64 strings — no content-parts,
no `image_url` — `thinking` is its own message field rather than embedded in
content, and a tool-result message uses `tool_name` where OpenAI uses
`tool_call_id`. Returned `tool_calls[].function.arguments` is a **JSON object**,
not an escaped string.

Ollama also overloads `/api/generate` for residency control: omit `prompt` to
load a model; add `"keep_alive": 0` to evict it.

Its OpenAI shim publishes an explicit ignore-list. **Not supported**:
`tool_choice`, `logit_bias`, `user`, `n`, `logprobs`, and **image URLs** (base64
only). `/v1/responses` is non-stateful only — no `previous_response_id`, no
`conversation`. `api_key` is "required but ignored."

### 10.2 llama.cpp — flat samplers, `n_predict`, GBNF grammars

```bash
curl --request POST --url http://localhost:8080/completion \
    --header "Content-Type: application/json" \
    --data '{"prompt": "Building a website can be done in 10 simple steps:","n_predict": 128}'
```

`n_predict`, not `max_tokens` (default `-1` = infinity; `0` evaluates the prompt
into cache without generating). `prompt` is unusually polymorphic: a string, a
token array `[12,34,56]`, a **mixed** array `[12,34,"string",56]`, an object
`{"prompt_string": "…", "multimodal_data": ["<base64>"]}`, or an array of any of
those for batch (the response then becomes an array).

Samplers with no OpenAI analogue: `dynatemp_range`, `dynatemp_exponent`, `min_p`,
`typical_p`, `mirostat`/`mirostat_tau`/`mirostat_eta`, `xtc_probability`,
`xtc_threshold`, `dry_multiplier`, `dry_base`, `dry_allowed_length`,
`dry_sequence_breakers`, `top_k`, `min_keep`. A `samplers` array controls sampler
**order**, defaulting to
`["dry","top_k","typ_p","top_p","min_p","xtc","temperature"]`.

`logit_bias` takes **pairs**, not a map: `[[15043, 1.0]]`, `[[15043, false]]` to
ban, or `[["Hello, World!", -0.5]]` to bias a whole string's tokens.

Constrained decoding is `grammar` (a GBNF string) or `json_schema`. On the OpenAI
route the schema sits **directly under `json_object`**, which is not the OpenAI
shape:

```json
{"response_format": {"type": "json_object", "schema": {"type": "string", "minLength": 10, "maxLength": 100}},
 "chat_template_kwargs": {"enable_thinking": false},
 "reasoning_format": "none",
 "mirostat": 2}
```

FIM has its own endpoint with its own field names:

```json
{"input_prefix": "def add(a, b):\n    ", "input_suffix": "\n\nprint(add(1,2))",
 "input_extra": [{"filename": "utils.py", "text": "# helpers"}], "n_predict": 64}
```

Native `/embedding` takes **`content`**, not `input`, and `embd_normalize`
(`-1` none, `0` max-abs, `1` taxicab, `2` L2). Slot control (`id_slot`,
`cache_prompt` — **default true**, which the docs warn can cause nondeterministic
results — `n_cache_reuse`) and `POST /slots/{id}?action=save|restore|erase` have
no equivalent anywhere else.

### 10.3 vLLM — `guided_*` is gone

**The single most important currency correction in this section.** vLLM
**removed** `guided_json`, `guided_regex`, `guided_choice`, `guided_grammar`,
`guided_whitespace_pattern`, `structural_tag` and `guided_decoding_backend` in
**v0.12.0**, replacing them with a nested `structured_outputs` object:

```json
{
  "model": "NousResearch/Meta-Llama-3-8B-Instruct",
  "messages": [{"role": "user", "content": "Classify this sentiment: vLLM is wonderful!"}],
  "structured_outputs": {"choice": ["positive", "negative"]}
}
```

| Old | New |
|---|---|
| `guided_json` | `structured_outputs.json` |
| `guided_regex` | `structured_outputs.regex` |
| `guided_choice` | `structured_outputs.choice` |
| `guided_grammar` | `structured_outputs.grammar` (EBNF) |
| `guided_whitespace_pattern` | `structured_outputs.whitespace_pattern` |
| `structural_tag` | `structured_outputs.structural_tag` |
| `guided_decoding_backend` | **remove the field entirely** |

Other vLLM-only body fields: `vllm_xargs` (the sanctioned custom-extension
passthrough), `cache_salt` (salts the prefix cache so co-tenants can't guess
prompts — 1–1024 chars, ~43 base64 chars recommended), `kv_transfer_params` /
`ec_transfer_params` (disaggregated serving), `chat_template` / `chat_template_kwargs`,
`mm_processor_kwargs`, `truncate_prompt_tokens`, `truncation_side`,
`allowed_token_ids`, `bad_words`, `prompt_logprobs`, `priority`, `session_id`,
`stream_interval`. `user` is **ignored**, `image_url.detail` is unsupported, and
`/v1/completions` does **not** support `suffix`.

> **Security note worth flagging.** vLLM's `--api-key` only authenticates paths
> under the `/v1`, `/v2` and `/inference` prefixes — **not** other endpoints on
> the same server, most notably `/invocations`, which exposes the same inference
> capability unauthenticated.

The pooling family is genuinely native:

```json
// POST /pooling — task has no OpenAI counterpart
{"model": "…", "input": ["Hello, my name is", "The capital of France is"], "task": "embed"}
```

`task` ∈ `embed | classify | token_embed | token_classify | reward`, and the
response `data` can be an arbitrarily nested list.

```json
// POST /score — note `queries` (plural), cross-product semantics
{"model": "BAAI/bge-reranker-v2-m3", "encoding_format": "float",
 "queries": ["What is the capital of Brazil?", "What is the capital of France?"],
 "documents": ["The capital of Brazil is Brasilia.", "The capital of France is Paris."]}
```

```json
// POST /rerank (also /v1/rerank, /v2/rerank) — Jina- AND Cohere-compatible; `query` singular
{"model": "BAAI/bge-reranker-base", "query": "What is the capital of France?",
 "documents": ["The capital of Brazil is Brasilia.", "The capital of France is Paris.", "Horses and cows are both animals"]}
```

`/tokenize` accepts **either** a completion body (`prompt`) or a chat body
(`messages`), and `add_special_tokens` defaults **differently** between them:
`true` for `prompt`, `false` for `messages`.

### 10.4 SGLang — `text` plus nested `sampling_params`

```json
{"text": "The capital of France is", "sampling_params": {"temperature": 0, "max_new_tokens": 32}}
```

`max_new_tokens` (default 128), not `max_tokens`, and **everything** lives inside
`sampling_params`. Exactly one of `text`, `input_ids` or `input_embeds`.

**Streaming gotcha: each chunk's `text` is cumulative, not a delta.** The
documented client tracks a `prev` offset and prints `output[prev:]`.

Constrained decoding also lives inside `sampling_params`, and `json_schema` is a
**JSON-encoded string**, not an object:

```json
{"sampling_params": {"json_schema": "{\"type\":\"object\",…}"}}   // Outlines and XGrammar
{"sampling_params": {"regex": "(France|England)"}}                // Outlines only
{"sampling_params": {"ebnf": "root ::= \"Hello\" | \"Hi\" | \"Hey\""}}  // XGrammar only
```

On the OpenAI route, `separate_reasoning` defaults to **true** (reasoning comes
back in `reasoning_content`), and LoRA is selected by a `model` string of the form
`"base-model:adapter-name"`.

### 10.5 TGI — `inputs` plus nested `parameters`

```bash
curl 127.0.0.1:8080/generate -X POST \
    -d '{"inputs":"What is Deep Learning?","parameters":{"max_new_tokens":20}}' \
    -H 'Content-Type: application/json'
```

Only `inputs` is required; everything else is in `parameters{}`. Two defaults
that surprise people: **`do_sample` defaults to `false`** (sampling is off unless
you ask), and **`details` defaults to `true`** (you get generation metadata unless
you turn it off). `watermark` implements the "A Watermark for Large Language
Models" scheme — no analogue anywhere else.

Constrained generation is a discriminated union inside `parameters`, and it is
neither `response_format` nor a top-level field:

```json
{"inputs": "I saw a puppy a cat and a raccoon during my bike ride in the park",
 "parameters": {"repetition_penalty": 1.3,
   "grammar": {"type": "json", "value": {"properties": {"location": {"type": "string"},
                                                        "animals_seen": {"type": "integer", "minimum": 1, "maximum": 5}}}}}}
```

`grammar.type` ∈ `json | regex | json_schema`. `POST /` is a compat route taking
`{inputs, parameters, stream}` and switching between `/generate` and
`/generate_stream` on the boolean. `/chat_tokenize` templates *and* tokenizes
without generating — handy for seeing the rendered prompt. `/invocations` is the
SageMaker entry point.

### 10.6 Triton / KServe v2 — named typed tensors

The most alien body in this document. **There is no prompt field at all.**

```json
POST /v2/models/mymodel/infer
{
  "id": "42",
  "inputs": [
    {"name": "input0", "shape": [2, 2], "datatype": "UINT32", "data": [1, 2, 3, 4]},
    {"name": "input1", "shape": [3], "datatype": "BOOL", "data": [true]}
  ],
  "outputs": [{"name": "output0"}]
}
```

`data` is a **flat** array whose length must equal the product of `shape`.
Datatypes are protocol-level: `BOOL`, `UINT8/16/32/64`, `INT8/16/32/64`,
`FP16/FP32/FP64`, `BYTES`. Text models take their prompt as a `BYTES` tensor, so
the **tensor names are entirely model-defined** (`text_input`, `INPUT0`, …).

The `generate` extension is the LLM-friendly shortcut (flagged **provisional** in
the spec):

```bash
curl -X POST localhost:8000/v2/models/mymodel/generate \
  -d '{"id": "42", "text_input": "client input", "parameters": {"stream": false, "temperature": 0}}'
```

`parameters` values may only be string, number or boolean, and are
**model-specific**. Unknown top-level keys are *meaningful* — they're passed as
parameters or tensors per the model spec, not rejected.

Two sharp edges: on `/generate_stream` the **HTTP status is set by the first SSE
event**, so a later error arrives as an error object while the status still reads
200 — check every event. And the TensorRT-LLM backend flattens sampling knobs into
`sampling_param_`-prefixed top-level keys, with cancellation done by sending a
second request carrying `"stop": true` and the **same `triton-request-id`
header**:

```bash
curl -X POST localhost:8000/v2/models/tensorrt_llm/generate \
    -d '{"text_input": "The future of AI is", "sampling_param_max_tokens": 50}'
```

### 10.7 The rest, briefly

- **LM Studio** `/api/v0/*` is OpenAI-shaped on the way in, but the response adds
  `stats{tokens_per_second, time_to_first_token, generation_time, stop_reason}`,
  `model_info{arch, quant, format, context_length}` and `runtime{}`. The newer
  `/api/v1/chat` genuinely diverges: `input` replaces `messages`, content blocks
  are `{"type":"text","content":…}` / `{"type":"image","data_url":…}` (note
  `content` and `data_url`, not `text` and `image_url`), plus first-class MCP
  `integrations[]` and a per-request `context_length`.

```json
{"model": "ibm/granite-4-micro", "input": "…",
 "integrations": [{"type": "ephemeral_mcp", "server_label": "huggingface",
                   "server_url": "https://huggingface.co/mcp", "allowed_tools": ["model_search"]},
                  {"type": "plugin", "id": "mcp/playwright", "allowed_tools": ["browser_navigate"]}],
 "context_length": 8000, "temperature": 0}
```

- **LocalAI** is an OpenAI drop-in whose one notable extension is a **top-level
  `grammar` string** (BNF), not nested in `response_format`. It also revives
  `/v1/edits`, which OpenAI retired.

```json
{"model": "gpt-4", "messages": [{"role": "user", "content": "Do you like apples?"}],
 "grammar": "root ::= (\"yes\" | \"no\")"}
```

- **llamafile** is a llama.cpp repackage — it has no wire format of its own.
- **NVIDIA NIM for LLMs** is now explicitly **vLLM-backed**, so vLLM's shapes
  apply. Its own additions are `/generative_scoring`
  (`{model, query, items, label_token_ids}` — log-probability scoring, same idea
  as SGLang's `/v1/score`) and `/inference/v1/generate`. *(The `nvext` object
  often cited for NIM is **not** in the current API reference — check the running
  container's `/docs` before relying on it.)*
- **trtllm-serve** is plain OpenAI. Its `/metrics` is a **POP queue**, not a
  gauge: statistics are removed once retrieved, so poll after each request and
  store them yourself.
- **text-generation-webui** merges an enormous sampler set into the OpenAI body —
  `dry_multiplier`, `xtc_probability`, `smoothing_factor`, `top_n_sigma`,
  `mirostat_mode`, `sampler_priority` (an array ordering the sampler stack),
  `grammar_string` — and explicitly ignores `model`, `function_call`,
  `functions`, `n` and `user`. Its `/v1/internal/model/load` and
  `/v1/internal/logits` have no OpenAI analogue.

---

## 11. LiteLLM

Two faces sharing one config vocabulary: a Python SDK and an OpenAI-wire-compatible
proxy (default `http://0.0.0.0:4000`). The `/v1` prefix is optional throughout —
`/v1/chat/completions` and `/chat/completions` both resolve.

**Model naming is the core idea.** In the SDK, `model` is
`<provider>/<model>`. On the **proxy**, `model` is a `config.yaml` `model_name`
alias — a *model group* — and the namespaced string lives in that group's
`litellm_params.model`:

```
openai/gpt-4o · anthropic/claude-… · azure/<deployment_name> · gemini/gemini-2.0-flash
vertex_ai/gemini-2.5-pro · vertex_ai/claude-3-5-sonnet-20241022 · vertex_ai/meta/llama-3-70b-instruct
bedrock/<model-id> · bedrock/converse/<id> · bedrock/invoke/<id> · bedrock/arn:aws:bedrock:…
ollama/<model> (→ /api/generate) · ollama_chat/<model> (→ /api/chat, recommended)
openrouter/<org>/<model> · huggingface/<provider>/<hf_org>/<hf_model> · litellm_proxy/<model>
```

Auth **varies by route**, which surprises people:

| Route | Header |
|---|---|
| OpenAI-shaped routes | `Authorization: Bearer sk-1234` (a LiteLLM *virtual key*) |
| `/v1/messages`, `/anthropic/*` | `x-api-key: $LITELLM_API_KEY` + `anthropic-version: 2023-06-01` |
| `/vertex_ai/*` | `x-litellm-api-key: Bearer sk-1234` (the page's own Quick Start also shows plain `Authorization`) |
| `/gemini/*` | `?key=sk-anything` query param |
| `/cohere/*` | `Authorization: bearer sk-anything` |

### 11.1 Proxy-only body fields

```bash
curl -L -X POST 'http://0.0.0.0:4000/chat/completions' \
-H 'Content-Type: application/json' -H 'Authorization: Bearer sk-1234' \
--data '{"model": "zephyr-beta", "messages": [{"role":"user","content":"what llm are you"}],
         "fallbacks": ["gpt-3.5-turbo"]}'
```

`fallbacks` also accepts objects carrying their own prompt:

```json
{"fallbacks": [{"model": "claude-3-haiku", "messages": [{"role": "user", "content": "What is LiteLLM?"}]}]}
```

Other body fields OpenAI has no concept of: `disable_fallbacks`, `num_retries`,
`timeout`, `mock_response` (short-circuits the provider entirely — free guardrail
and pipeline testing), `guardrails`, `metadata`, and clientside
`api_key`/`api_base`/`api_version` (only for deployments that opt in via
`litellm_params.configurable_clientside_auth_params`).

```json
{"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "hi my email is ishaan@berri.ai"}],
 "mock_response": "This is a mock response",
 "guardrails": ["aporia-pre-guard", "aporia-post-guard"]}
```

Tags for routing and spend attribution work **three ways** — a top-level `tags`
field, `metadata.tags`, or a header:

```json
{"model": "gpt-4", "messages": [{"role": "user", "content": "Hello, Claude gm!"}], "tags": ["free"]}
{"model": "chat", "messages": [...], "metadata": {"tags": ["paid", "!provider:anthropic"]}}
```

```
x-litellm-tags: free,my-custom-tag
```

`metadata.tags` supports negation (`!provider:anthropic`) and required (`&`)
prefixes; routing on them needs `router_settings.enable_tag_filtering: True`,
though they always work for spend tracking.

`metadata` is a first-class object: `generation_name`, `generation_id`,
`trace_id`, `trace_user_id`, `session_id`, `trace_metadata`, `spend_logs_metadata`.
Note `user` stays a **top-level** body field.

**Unmapped body params are not rejected** — LiteLLM assumes anything it doesn't
recognise is provider-specific and forwards it into the upstream body.

### 11.2 Headers

Request headers with no OpenAI analogue: `x-litellm-timeout`,
`x-litellm-stream-timeout`, `x-litellm-num-retries`, `x-litellm-tags`,
`x-litellm-enable-message-redaction`, `x-litellm-keepalive-seconds` (emits SSE
`: ping` comment frames), `x-litellm-spend-logs-metadata`,
`x-litellm-customer-id`, `x-litellm-end-user-id`, `x-litellm-trace-id`,
`x-litellm-session-id`.

Response headers are where the operational value is:

```
x-litellm-call-id, x-litellm-model-id, x-litellm-model-group, x-litellm-model-api-base
x-litellm-response-duration-ms, x-litellm-overhead-duration-ms
x-litellm-attempted-retries, x-litellm-attempted-fallbacks, x-litellm-max-fallbacks
x-litellm-response-cost, -cost-input, -cost-output, -cost-cache-read,
  -cost-cache-creation, -cost-reasoning, -cost-tool-usage, x-litellm-key-spend
```

### 11.3 The endpoint surface

| Endpoint | Notes |
|---|---|
| `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings` | stock OpenAI |
| `/v1/responses` | Responses bridged to **all** providers |
| **`/v1/messages`** | Anthropic wire format backed by **any** provider — this is how Claude Code and Anthropic SDKs drive an OpenAI or Bedrock deployment |
| `/v1/rerank` | `{model, query, documents[], top_n}` |
| `/v1/audio/transcriptions`, `/v1/audio/speech`, `/v1/images/generations`, `/v1/moderations` | stock OpenAI |
| `/v1/files`, `/v1/batches`, `/v1/assistants`, `/v1/vector_stores` | stock OpenAI |
| `/v1/models` | documented with `include_metadata=true` and `fallback_type` query params |
| `/model/info`, `/model/new`, `/model/update`, `/model/delete` | management (needs `store_model_in_db`) |
| `/health`, `/health/liveliness`, `/health/readiness`, `/health/services`, `/health/drain` | `/health` runs real test requests against configured models |

```bash
# Anthropic format, any provider behind it
curl -L -X POST 'http://0.0.0.0:4000/v1/messages' \
-H 'content-type: application/json' \
-H 'x-api-key: $LITELLM_API_KEY' \
-H 'anthropic-version: 2023-06-01' \
-d '{"model": "anthropic-claude",
     "messages": [{"role": "user", "content": "Hello, can you tell me a short joke?"}],
     "max_tokens": 100}'
```

### 11.4 Pass-through prefixes

These forward **untranslated** to the provider's native API, while still doing
LiteLLM auth, spend tracking and budgets — so admins never hand out upstream keys:

```
/anthropic/*  /vertex_ai/*  /gemini/*  /bedrock/*  /cohere/*  /openai_passthrough/*
/vllm/*  /azure/*  /assemblyai/*  /eu.assemblyai/*  /mistral/*  /langfuse/*
/gigachat/*  /comprehend_medical/*  /cursor/*
```

```bash
# Vertex, native shape, LiteLLM key
curl http://localhost:4000/vertex_ai/v1/projects/${PROJECT_ID}/locations/us-central1/publishers/google/models/gemini-1.5-flash-001:generateContent \
  -H "Content-Type: application/json" \
  -H "x-litellm-api-key: Bearer sk-1234" \
  -d '{"contents":[{"role": "user", "parts":[{"text": "hi"}]}]}'
```

```bash
# Bedrock, native Converse shape
curl -X POST 'http://0.0.0.0:4000/bedrock/model/my-bedrock-model/converse' \
-H 'Authorization: Bearer sk-1234' -H 'Content-Type: application/json' \
-d '{"messages": [{"role": "user", "content": [{"text": "Hello, how are you?"}]}],
     "inferenceConfig": {"maxTokens": 100}}'
```

Note the Azure prefix is `/azure` — the `/azure/openai/...` you'll see in examples
is just because Azure's *own* native path starts with `/openai/deployments`. Use
`/openai_passthrough` rather than a bare `/openai`, which collides with the
proxy's own OpenAI-shaped routes.

### 11.5 Routing

The client never sees it. One `model` name can front many deployments;
`router_settings.routing_strategy` picks among them: `simple-shuffle` (default,
weighted by configured rpm/tpm), `latency-based-routing`, `least-busy`,
`usage-based-routing` / `-v2` (Redis-backed for cross-replica state),
`cost-based-routing`, or a `CustomRoutingStrategy` plugin.
`router_settings.enable_pre_call_checks: true` filters deployments before
dispatch; `allowed_fails`/`cooldown_time` govern cooldowns.

---

## 12. agentgateway

A single Rust proxy ([agentgateway.dev](https://agentgateway.dev), Apache-2.0)
that terminates ordinary HTTP/gRPC plus three AI protocol families: LLM inference,
MCP, and A2A. It is a Linux Foundation project (donated 2025, accepted into the
Agentic AI Foundation in 2026), with solo.io as commercial vendor.

**It is not one wire format.** It natively terminates several, each mapped to a
named `RouteType`, and converts between them:

| Path | RouteType |
|---|---|
| `POST /v1/chat/completions` | `completions` |
| `POST /v1/responses` | `responses` |
| `POST /v1/embeddings` | `embeddings` |
| `POST /v1/messages` | `messages` |
| `POST /v1/messages/count_tokens` | `anthropicTokenCount` |
| `POST /v1beta/models/{m}:generateContent` / `:streamGenerateContent` | `generateContent` |
| `POST /v1beta/models/{m}:countTokens` | `geminiCountTokens` |
| `POST /v2/rerank` (also `/v1/rerank`) | `rerank` |
| `GET /v1/models` | `models` |
| `GET /v1/realtime` (WebSocket) | `realtime` |
| any path | `passthrough` / `detect` |

```bash
curl 'http://localhost:4000/v1/chat/completions' \
--header 'Content-Type: application/json' \
--data '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Tell me a story"}]}'
```

```bash
curl -X POST http://localhost:4000/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-opus-4-6", "max_tokens": 100, "messages": [{"role": "user", "content": "Hello!"}]}'
```

Client auth defaults to **none** for the LLM APIs — the docs' own SDK examples
pass `api_key="anything"`. The gateway injects the provider credential itself
(`params.apiKey`, `backendAuth.key`, AWS SigV4, GCP ADC), and for Anthropic
"automatically adds the `x-api-key` authorization and `anthropic-version`
headers." Optional inbound policies: `jwtAuth`, `apiKey` (whose `location` field
defaults to reading header `authorization` with prefix `Bearer `), `basicAuth`,
`oidc`, `extAuthz`. `backendAuth` also has a **`passthrough`** mode that forwards
the validated incoming JWT to the backend.

### 12.1 `model` is a routing selector

It is glob-matched against `llm.models[].name` (`*`, `us.anthropic*`,
`claude-platform/*`), and `params.model` / `ai.modelAliases` can replace what
actually goes upstream — so the response `model` can differ from the request
`model` (CEL exposes both as `llm.requestModel` and `llm.responseModel`).

The `ai` route policy carries `defaults` (fill only if the client omitted the
field), `overrides` (replace client values), `prompts` (append/prepend messages),
`routes` (path → RouteType), `promptGuard`, `modelAliases`, `promptCaching`, plus
`transformations` / `finalTransformations` (CEL body-setting; the *final* ones run
**after** conversion to the provider format). On a per-model entry the equivalent
keys are **singular** — `transformation` / `finalTransformation` — and the plural
spellings will not validate there.

### 12.2 Conversion, and what it loses

Conversion order is fixed and provider-driven, not client-selectable:
`messages → completions → responses → Bedrock Converse`. Because `completions`
precedes `responses`, a provider advertising both never takes the lossy Responses
path.

Two things worth knowing before you rely on it:

- **Anthropic Messages → OpenAI Responses silently drops `stop_sequences` and
  `top_k`.** The docs say it plainly: agentgateway "accepts both fields and drops
  them, with no error and no warning to the client."
- Some features **hard-fail** instead of degrading: thinking / redacted-thinking
  history, document / search-result / server-tool content blocks, and non-text
  tool results return HTTP 400 `unsupported conversion` when the provider only
  advertises `responses`.

**Bedrock does not accept native Converse or Invoke bodies on the normal path** —
"Directly sending `Converse` or `Invoke` request shapes are not directly
supported." You send OpenAI or Anthropic shapes and agentgateway translates
internally *via* Converse, or you opt into passthrough and lose translation and
guardrails:

```yaml
llm:
  models:
  - name: us.anthropic*
    provider: bedrock
    params:
      awsRegion: us-west-2
    passthrough: detect       # or: opaque
```

`passthrough: opaque` = no telemetry; `detect` = telemetry and rate limits, no
guardrails. In simplified config, unmatched paths default to passthrough.

Gemini streaming **requires** `?alt=sse` on `:streamGenerateContent` — omitting it
is a validation error, unlike OpenAI's `"stream": true` body flag.

### 12.3 Token accounting and rate limits

Normalised, so it will **not** match provider invoices field-for-field:
`llm.inputTokens` / `llm.totalTokens` **add back** the prompt-cache tokens that
Anthropic and Bedrock exclude; `llm.providerInputTokens` /
`llm.providerTotalTokens` hold the provider's own numbers. Token rate limits use
the normalised value, so cache-heavy requests debit more than the provider
reports.

Rate limiting is two-phase — checked at request time and again at response time,
"because the provider reports the completion token count only in the response."
`tokenize` defaults to **false**, and the docs are sharp about what that means:
"When `tokenize: true` is not set or is set to false on the AI backend, the number
of tokens that are used for the request cannot be calculated. Because of this, the
request is always allowed, unless the rate limit is set to 0 tokens."

### 12.4 Guardrail webhook — the one place agentgateway defines its own wire format

```yaml
guardrails:
  request:
  - webhook:
      target:
        host: content-safety-webhook.example.com:8000
      headers:
        ":path": '"/api/guardrails/request"'
        x-user: jwt.sub
        x-tenant: request.headers["x-tenant"]
        x-model: llmRequest.model
```

The gateway POSTs to `/request` and `/response` (overridable via the `:path`
pseudo-header; the `headers` values are CEL expressions evaluated against the
**original** incoming request):

```json
// → POST /request
{"body": {"messages": [{"role": "user", "content": "…"}]}}
// → POST /response
{"body": {"choices": [ … ]}}
```

Your webhook returns exactly one action (the union is serde-untagged, so it is
discriminated by which fields are present):

```json
{"action": {"reason": "looks fine"}}                                     // PassAction
{"action": {"body": {"messages": [...]}, "reason": "masked PII"}}        // MaskAction, request phase
{"action": {"body": {"choices": [...]}, "reason": "masked"}}             // MaskAction, response phase
{"action": {"body": "blocked", "status_code": 403, "reason": "policy"}}  // RejectAction — REQUEST PHASE ONLY
```

Two constraints from the published OpenAPI contract that are easy to miss:
`RejectAction` is **not** valid on the `/response` phase, and for a `MaskAction`
"the number of choices inside `ResponseChoices` MUST be the same as in the
request" (likewise message count on the request phase). `failureMode` defaults to
`failClosed`, and `action` defaults to `reject`.

Guardrails are **off for streaming by default** (`promptGuard.streaming` defaults
to `Disabled`) and never apply under `passthrough`/`detect`. A schema warning
worth heeding: on `toolInput` scope, "in APIs that send tool arguments as opaque
JSON, such as Completions, the arguments are masked as a single string, meaning a
prompt guard has the potential to rewrite the arguments into invalid JSON."

### 12.5 MCP and A2A

MCP is served at `/mcp` (streamable HTTP) and `/sse` (legacy), multiplexing N
targets behind one server:

```yaml
gateways:
- port: 3000
  listeners:
  - routes:
    - backends:
      - mcp:
          targets:
          - name: time
            stdio: {cmd: uvx, args: ["--with", "mcp<2", "mcp-server-time"]}
          - name: everything
            stdio: {cmd: npx, args: ["@modelcontextprotocol/server-everything"]}
```

**Multiplexing renames tools**: with multiple targets the default
`prefixMode: conditional` rewrites every tool to `<target>_<name>` (the delimiter
is `_`, so `time_get_current_time`), which breaks client-side allowlists written
against the raw server. `failureMode` defaults to `failClosed` — one target that
fails to initialise fails `initialize` for the *entire* multiplexed backend.

Version negotiation is real: older clients send `initialize`; MCP revision
2026-07-28 clients send `server/discover` instead, and agentgateway forwards it to
each target and intersects `supportedVersions`.

Authorization is CEL over tool calls:

```yaml
mcpAuthorization:
  rules:
  - 'mcp.tool.name == "echo"'
  - 'jwt.sub == "test-user" && mcp.tool.name == "add"'
  - 'mcp.tool.name == "printEnv" && jwt.nested.key == "value"'
```

A2A is a **marker policy** — `A2aPolicy` is literally an empty schema object,
written `a2a: {}`. agentgateway does not whitelist methods; it reads whatever is
in the JSON-RPC `method` field and records it (`a2a.method=message/stream`,
`a2a.response.outcome=success`, `a2a.result.kind=task`, `a2a.task.state=completed`).
It **does** rewrite the agent card in flight, so clients keep talking to the
gateway rather than bypassing it:

```bash
$ curl http://localhost:3000/.well-known/agent.json | jq
{"description": "Just a hello world agent", "url": "http://localhost:3000"}
```

Both `/.well-known/agent.json` (older) and `/.well-known/agent-card.json` (v0.3+)
are matched; v1.0 rewrites `url` inside each `supportedInterfaces[]` entry.

### 12.6 Two config dialects, and a smaller provider list than advertised

Simplified `llm:`/`mcp:` uses **flat** params (`provider: bedrock`,
`params.awsRegion`, `params.vertexProject`, `params.baseUrl`). Routing-based
`gateways:`/`routes:`/`backends:` uses **nested** provider objects
(`ai.provider.bedrock.region`). `binds:` is a deprecated predecessor to
`gateways:` and still appears in most repo examples — as does a deprecated `port:`
inside the feature sections.

The `AIProvider` schema has exactly **8 native kinds**: `openAI`, `gemini`,
`vertex`, `anthropic`, `bedrock`, `azure`, `copilot`, `custom`. The other 13
advertised providers (cohere, ollama, baseten, cerebras, deepinfra, deepseek,
groq, huggingface, mistral, openrouter, togetherai, xai, fireworks) are
`ProviderPreset` strings that expand to a custom provider. The docs advise
`provider: custom` over `provider: openai` + `baseUrl`, because "using a specific
vendor's provider may introduce semantics specific to that provider."

On Kubernetes, config arrives over Envoy's **xDS transport** carrying
agentgateway's **own** resource types (`agentgateway.dev.resource`: `Bind`,
`Listener`, `Route`, `Backend`, `Policy`, `TCPRoute`, `RouteGroup`, `ModelRoute`)
— there is no LDS/RDS/CDS/EDS here, and an Envoy control plane will not drive it.

---

## 13. Envoy AI Gateway

The upstream project this repo drives. It is **not its own inference API**: it is
an Envoy Gateway extension exposing a client-facing OpenAI surface plus a native
Anthropic Messages surface, and translating each request into the selected
backend's native protocol.

Two independent choices:

- **Client-facing schema** is chosen by **path prefix**, configured at install
  time via Helm `endpointConfig` (`openai: ""`, `cohere: "/cohere"`,
  `anthropic: "/anthropic"`, with `rootPrefix` default `/` prepended). There is
  **no input-schema field on the route**.
- **Backend schema** is chosen by `AIServiceBackend.spec.schema`, whose enum is
  exactly: `OpenAI | Cohere | AWSBedrock | AzureOpenAI | GCPVertexAI | GCPAnthropic | Anthropic | AWSAnthropic`.

Those eight names are precisely the `_schema` values envoyai's
[provider classes](src/envoyai/providers/) emit.

### 13.1 The endpoint surface

| Endpoint | Backend schemas accepted |
|---|---|
| `POST /v1/chat/completions` | OpenAI, AWSBedrock, AWSAnthropic, AzureOpenAI, GCPVertexAI, GCPAnthropic |
| `POST /anthropic/v1/messages` | Anthropic, GCPAnthropic, AWSAnthropic, OpenAI, AWSBedrock |
| `POST /anthropic/v1/messages/count_tokens` | Anthropic, GCPAnthropic, AWSAnthropic, AWSBedrock |
| `POST /v1/completions` | OpenAI only — no cross-provider translation |
| `POST /v1/embeddings` | OpenAI, AzureOpenAI, GCPVertexAI, AWSBedrock (Titan only) |
| `POST /v1/responses`, `/v1/responses/input_tokens` | OpenAI, Azure OpenAI |
| `POST /v1/images/generations` | OpenAI-compatible |
| `POST /v1/audio/transcriptions`, `/v1/audio/translations` | multipart; the only multipart parser in the codebase |
| `POST /cohere/v2/rerank` | Cohere — the only use of the `Cohere` schema |
| `POST /tokenize` | vLLM-compatible; translates to Gemini CountTokens / Anthropic MessageCountTokens / Bedrock CountTokens |
| `GET /v1/models` | served by the gateway itself from route config, **not** proxied |
| `POST /mcp` | MCPRoute — aggregate many MCP servers behind one endpoint |

```bash
curl -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello, how are you?"}]}' \
  $GATEWAY_URL/v1/chat/completions
```

```bash
curl -H "Content-Type: application/json" \
  -d '{"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "Hello, how are you?"}], "max_tokens": 100}' \
  $GATEWAY_URL/anthropic/v1/messages
```

`/tokenize` returns a normalised shape for translated backends and passes native
extras through for vLLM:

```json
{"count": 15}
{"count": 15, "max_model_len": 131072, "tokens": [1234, 5678, 9012], "token_strs": ["Hello", " world", "!"]}
```

> **One upstream contradiction worth knowing before you copy a tutorial.**
> `getting-started/connect-providers/anthropic.md` shows a
> `$GATEWAY_URL/v1/chat/completions` curl against a backend configured
> `schema: {name: Anthropic}`, and the compatibility table marks that combination
> ✅. But `ChatCompletionsEndpointSpec.GetTranslator` in
> `internal/endpointspec/endpointspec.go` — on both `main` and tag `v1.1.0` —
> accepts only OpenAI, AWSBedrock, AWSAnthropic, AzureOpenAI, GCPVertexAI and
> GCPAnthropic, returning `unsupported API schema` for `Anthropic`. Use
> `$GATEWAY_URL/anthropic/v1/messages` for a `name: Anthropic` backend, or
> configure Anthropic's OpenAI-compatible surface as `{"name":"OpenAI", …}`.

### 13.2 How routing actually works

```
client body ──▶ ROUTER-level ext_proc filter
                  parse body, extract `model`
                  set  x-ai-eg-model: <model>     (name is the DEFAULT; it is configurable)
                  ClearRouteCache
                     │
                     ▼
                AIGatewayRoute.rules[].matches[].headers matches on that header
                     │
                     ▼
                UPSTREAM-level ext_proc filter
                  backendRefs[].modelNameOverride rewrites the model per backend
                  translate body to the backend schema
                  inject upstream credential (BackendSecurityPolicy)
```

Two ext_proc phases exist by design, and the rationale is worth quoting: "in
Envoy, retry/fallback happens after the router filter at the upstream level…
retry/fallback will make the requests to totally different AI providers. For
example, on the first try, it goes to OpenAI, and on the second try, it goes to
AWS Bedrock. In this case, we need to do different request transformations and
upstream authorizations."

```yaml
apiVersion: aigateway.envoyproxy.io/v1beta1
kind: AIGatewayRoute
metadata:
  name: envoy-ai-gateway-basic-anthropic
  namespace: default
spec:
  parentRefs:
    - name: envoy-ai-gateway-basic
      kind: Gateway
      group: gateway.networking.k8s.io
  rules:
    - matches:
        - headers:
            - type: Exact
              name: x-ai-eg-model
              value: claude-sonnet-4-5
      backendRefs:
        - name: envoy-ai-gateway-basic-anthropic
```

Model virtualization fans one client-facing name out to provider-specific IDs:

```yaml
      backendRefs:
        - name: aws-backend
          modelNameOverride: anthropic.claude-sonnet-4-20250514-v1:0
          weight: 50
        - name: gcp-backend
          modelNameOverride: claude-sonnet-4@20250514
          weight: 50
```

*(Note: `AIGatewayRouteSpec` has **no `targetRefs` field** — its complete set is
`parentRefs`, `hostnames`, `rules`, `llmRequestCosts`. A `targetRefs:` block
appears in `model-virtualization.md`, but that manifest would be rejected by CRD
validation. Use `parentRefs`.)*

### 13.3 Translation targets

Read from `internal/translator/*.go`, these are the exact upstream paths:

| Client → backend | Upstream path |
|---|---|
| OpenAI → AWS Bedrock | `/model/{model}/converse` or `/converse-stream` (`url.PathEscape`d, so ARNs survive) |
| OpenAI → GCP Vertex | `publishers/google/models/{model}:generateContent` / `:streamGenerateContent?alt=sse`, with `/v1/projects/{p}/locations/{r}` prepended by the auth handler |
| OpenAI → GCP Anthropic | `publishers/anthropic/models/{model}:rawPredict` / `:streamRawPredict`, injecting `anthropic_version` into the body |
| Anthropic → AWS Bedrock | `/model/{model}/invoke` / `/invoke-with-response-stream`, `anthropic_version: bedrock-2023-05-31` in the body |
| Anthropic → Anthropic | near-passthrough; `path.Join("/", prefix, "messages")`, prefix defaults to `v1` |
| OpenAI → Azure OpenAI | `/openai/deployments/{deployment}/chat/completions?api-version={schema.version}` |

Response `model` handling differs per backend: AWS Bedrock Converse, GCP Anthropic
and GCP Vertex AI responses carry **no model field at all**; OpenAI returns a
resolved snapshot (`gpt-5-nano` → `gpt-5-nano-2025-08-07`); Azure OpenAI **ignores
the request `model` entirely** and resolves by deployment name in the URI.

### 13.4 Vendor extension fields on `/v1/chat/completions`

Documented in `vendor-specific-fields.md`, with the rule "vendor fields override
translated fields when conflicts occur" and "fields and backends other than
specified in Supported Backends will be ignored":

| Field | Backends | Translates to |
|---|---|---|
| `thinking` | GCPVertexAI, GCPAnthropic, AWSBedrock | `generationConfig.thinkingConfig` / `thinking` / Converse `additionalModelRequestFields` |
| `safetySettings` | GCPVertexAI | Gemini `SafetySetting` |
| `tools[].type: "google_search"` | GCPVertexAI | Google Search grounding |
| `tools[].function.eager_input_streaming` | GCPAnthropic, AWSAnthropic | fine-grained tool streaming |

```json
{
  "model": "gemini-2.0-flash",
  "messages": [{"role": "user", "content": "What are the latest developments in quantum computing?"}],
  "tools": [{"type": "google_search",
             "google_search": {"exclude_domains": ["example.com"], "blocking_confidence": "BLOCK_LOW_AND_ABOVE"}}]
}
```

The eager-streaming contract has a real trap, stated plainly in the docs: "with
eager streaming the server no longer validates the fragments, so the accumulated
string is not guaranteed to be valid JSON — a response ending with
`finish_reason: \"length\"` can cut a parameter off midway. Guard the parse and
handle failure rather than assuming it succeeds." The field is **tri-state**:
unset = buffered, `true` = eager, explicit `false` = buffered even when the
deprecated `fine-grained-tool-streaming-2025-05-14` beta header is present.

Three code-verified (but undocumented) mappings: `service_tier` →
Bedrock `ServiceTier{Type}`; `reasoning_effort` →
`additionalModelRequestFields.reasoning_config`; `max_completion_tokens` falling
back to `max_tokens` via `cmp.Or`.

### 13.5 Token usage → rate limiting

`llmRequestCosts[].metadataKey` is **user-chosen** — `llm_input_token` and
friends are conventions, not reserved names. The `type` enum is
`OutputToken | InputToken | CachedInputToken | CacheCreationInputToken | TotalToken | ReasoningToken | CEL`.

```yaml
  llmRequestCosts:
    - metadataKey: llm_input_token
      type: InputToken
    - metadataKey: llm_cached_input_token
      type: CachedInputToken
    - metadataKey: llm_output_token
      type: OutputToken
    - metadataKey: llm_total_token
      type: TotalToken
    - metadataKey: llm_cel_calculated_token
      type: CEL
      cel: "input_tokens == uint(3) ? 100000000 : 0"
```

These land in Envoy dynamic metadata under namespace **`io.envoy.ai_gateway`**
(not to be confused with the internal-only `aigateway.envoy.io`), and are consumed
by an Envoy Gateway `BackendTrafficPolicy`:

```yaml
          cost:
            request:
              from: Number
              number: 0
            response:
              from: Metadata
              metadata:
                namespace: io.envoy.ai_gateway
                key: llm_total_token
```

Timing semantics matter: "token usage is charged **after** the response
completes," so an admitted request can overshoot — a 1,000-token hourly limit
admits a request that streams 1,200 tokens, and the bucket is then 200 over.

When cost tracking is configured, the gateway rewrites the body to force
`stream_options.include_usage: true` "to avoid the bypassing of the token usage
calculation". With no `llmRequestCosts` and no
`GatewayConfig.spec.globalLLMRequestCosts` in play, the client's `stream_options`
passes through untouched.

### 13.6 Auth, MCP, and the gotcha everyone hits

`BackendSecurityPolicy.spec.type` selects the injection:

| Type | Injects |
|---|---|
| `APIKey` | `Authorization: Bearer <key>` |
| `AnthropicAPIKey` | `x-api-key` |
| `AzureAPIKey` | `api-key` |
| `AzureCredentials` | Entra ID token exchange |
| `AWSCredentials` | SigV4; region self-corrects from the resolved upstream host |
| `GCPCredentials` | `Authorization: Bearer <token>` **and** prepends `/v1/projects/{p}/locations/{r}` to `:path` |

Per-request credential override is available via `fromRequestHeaders`
(`x-aigw-api-key`, `x-aigw-anthropic-api-key`, `x-aigw-azure-api-key`,
`x-aigw-gcp-access-token`; for AWS it's a **prefix**, default `x-aigw-aws-`), all
stripped before the backend sees them — or via `fromDynamicMetadata`, which is
preferred because metadata cannot be forged by the client.

MCPRoute is GA at `v1beta1`. Tool names are namespaced by backend on the way out
(`github__issue_read`, `context7__query-docs`), long-lived SSE streams from
multiple servers are merged into one client stream with event-ID reconstruction,
and the gateway issues a unified session ID encoding the per-backend ones.
`MCPToolFilter` has **four** fields — `include`, `includeRegex`, `exclude`,
`excludeRegex` — with include/exclude legal *together* under deny-wins precedence.
Downstream MCP auth is not only OAuth: `MCPRouteSecurityPolicy` also carries
`apiKeyAuth` and `extAuth`.

> **The gotcha that bites every first deployment.** Envoy Gateway's default
> buffer limit is 32 KiB, which is far too small for AI payloads. The official
> example ships a `ClientTrafficPolicy` setting `connection.bufferLimit: 50Mi`.

---

## 14. Other gateways

Almost all of these speak OpenAI Chat Completions on the wire. They differ in
**where provider selection lives** and **what control surface they bolt on**.
Three shapes exist.

### 14.1 Pure base-URL swap, provider encoded in `model`

| Gateway | Base URL | `model` format | Auth |
|---|---|---|---|
| Vercel AI Gateway | `https://ai-gateway.vercel.sh/v1` | `anthropic/claude-opus-5` | `Authorization: Bearer $AI_GATEWAY_API_KEY` |
| Martian | `https://api.withmartian.com/v1` | `openai/gpt-5.4-nano` (prefix **required**) | `Authorization: Bearer` |
| Requesty | `https://router.requesty.ai/v1` | `provider/model` | `Authorization: Bearer` |
| Bifrost | `http://localhost:8080/v1` | `openai/gpt-4o-mini`, or bare name via Model Catalog | none by default |
| Helicone AI Gateway | `https://ai-gateway.helicone.ai/ai` | `openai/gpt-4o-mini` | Helicone key as the sole bearer |
| TrueFoundry | `https://gateway.truefoundry.ai` | `openai-main/gpt-4o-mini` (`{account}/{model}`) | `Authorization: Bearer $TFY_API_TOKEN` |
| Portkey | `https://api.portkey.ai/v1` | `@openai-provider/gpt-4o` | `x-portkey-api-key` |
| Databricks | `https://<ws>/serving-endpoints` or `/ai-gateway/mlflow/v1` | endpoint name or `system.ai.claude-sonnet-4-5` | `-u token:$DATABRICKS_TOKEN` |

Bodies are byte-for-byte OpenAI. Only a few add top-level keys:

```json
// Martian — martian_metadata is NOT forwarded upstream
{"model": "openai/gpt-5.4-nano", "messages": [...], "martian_metadata": {"organization_id": "org-123"}}
```

Bifrost additionally mirrors each SDK's own format at `/{provider}/{path}` —
`/openai/v1/chat/completions`, `/anthropic/v1/messages` — and cross-provider
routing survives the mirror: on `/anthropic` you can pass `"openai/gpt-4o-mini"`
and get an Anthropic-shaped response from OpenAI.

TrueFoundry likewise serves an Anthropic surface at `{GATEWAY_BASE_URL}/messages`,
with `cache_control` passthrough and the account prefix still applied to `model`.

### 14.2 Portkey — everything rides in headers

The body is stock OpenAI; the gateway's whole behaviour is header-driven.

```bash
curl https://api.portkey.ai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-portkey-api-key: $PORTKEY_API_KEY" \
  -H "x-portkey-virtual-key: $PORTKEY_PROVIDER_VIRTUAL_KEY" \
  -d '{"model": "gpt-5", "messages": [{"role": "system", "content": "You are a helpful assistant."},
                                      {"role": "user", "content": "Hello!"}],
       "max_completion_tokens": 250}'
```

`x-portkey-virtual-key` is legacy; the current form puts routing in `model`
(`@anthropic-provider/claude-sonnet-4-5-20250514`). `x-portkey-config` takes a
saved config ID or inline JSON:

```json
{"strategy": {"mode": "fallback"},
 "targets": [{"override_params": {"model": "@openai-prod/gpt-4o"}},
             {"override_params": {"model": "@anthropic-prod/claude-3-5-sonnet-20241022"}}]}
```

```json
{"strategy": {"mode": "loadbalance"},
 "targets": [{"passthrough": true, "weight": 0.7}, {"provider": "@openai-backup", "weight": 0.3}]}
```

Target fields apply in order `default_params` → `override_params` →
`drop_params`, and `drop_params` paths support wildcards:
`"response_format.json_schema"`, `"tools[*].function.name"`. A `conditional`
strategy adds `conditions[].query`/`.then` with a required `default`, querying
`metadata.<key>`, `params.<key>` or `url.pathname`.

Its prompt endpoint has its own envelope — `variables` replaces `messages`, and
there is **no `model` field** (the saved template pins it):

```bash
curl -X POST "https://api.portkey.ai/v1/prompts/YOUR_PROMPT_ID/completions" \
  -H "x-portkey-api-key: $PORTKEY_API_KEY" \
  -d '{"variables": {"user_input": "Hello world"}, "max_completion_tokens": 250, "presence_penalty": 0.2}'
```

### 14.3 Cloudflare — provider in the path

```bash
curl -X POST https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}/openai/chat/completions \
  --header 'cf-aig-authorization: Bearer {CF_AIG_TOKEN}' \
  --header 'Content-Type: application/json' \
  --data '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "What is Cloudflare?"}]}'
```

Byte-for-byte the provider's own body — Cloudflare owns only the URL prefix and
the `cf-aig-*` headers: `cf-aig-metadata`, `cf-aig-cache-ttl` (60s–1 month),
`cf-aig-cache-key`, `cf-aig-skip-cache`, `cf-aig-custom-cost`,
`cf-aig-request-timeout`, `cf-aig-max-attempts` (≤5), `cf-aig-retry-delay`
(100 ms–5 s), `cf-aig-backoff` (`Constant|Linear|Exponential`), plus response-only
`cf-aig-cache-status`, `cf-aig-step`, `cf-aig-dlp`.

The OpenAI-compatible variant moves the provider into `model`, and is where
Dynamic Routing lives:

```json
{"model": "workers-ai/@cf/meta/llama-3.3-70b-instruct-fp8-fast", "messages": [...]}
{"model": "dynamic/<your-dynamic-route-name>", "messages": [...]}
```

The **Universal Endpoint** is the most unusual body in this whole document — the
top level is a bare **JSON array of attempt objects**, tried in order:

```json
[
  {"provider": "workers-ai", "endpoint": "@cf/meta/llama-3.1-8b-instruct",
   "headers": {"Authorization": "Bearer {cloudflare_token}", "Content-Type": "application/json"},
   "query": {"messages": [{"role": "system", "content": "You are a friendly assistant"},
                          {"role": "user", "content": "What is Cloudflare?"}]}},
  {"provider": "openai", "endpoint": "chat/completions",
   "headers": {"Authorization": "Bearer {open_ai_token}", "Content-Type": "application/json"},
   "query": {"model": "gpt-4o-mini", "stream": true,
             "messages": [{"role": "user", "content": "What is Cloudflare?"}]}}
]
```

It is **deprecated**: "use the OpenAI-compatible endpoint for new integrations,
and Dynamic Routing for fallbacks, retries, and conditional routing."

Workers AI's own REST run endpoint is a third shape again — model in the path,
`{"prompt": …}` in the body, wrapped in Cloudflare's `result/success/errors`
envelope:

```bash
curl https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/@cf/meta/llama-3.1-8b-instruct \
  -H 'Authorization: Bearer {API_TOKEN}' \
  -d '{ "prompt": "Where did the phrase Hello World come from" }'
```

### 14.4 Helicone — three modes, three auth shapes

```bash
# 1. Proxy — provider key in Authorization, Helicone key alongside
curl --url https://oai.helicone.ai/v1/chat/completions \
    --header "Authorization: Bearer $OPENAI_API_KEY" \
    --header "Helicone-Auth: Bearer $HELICONE_API_KEY" \
    --data '{"model": "gpt-4o-mini", "messages": [...], "temperature": 1, "max_tokens": 30}'
```

```bash
# 2. Generic gateway — the destination is a HEADER, not the URL
curl --url https://gateway.helicone.ai/v1/chat/completions \
  --header "Helicone-Auth: Bearer $HELICONE_API_KEY" \
  --header "Helicone-Target-Url: https://api.lemonfox.ai" \
  --header "Helicone-Target-Provider: LemonFox" \
  --data '{"model": "gpt-4o-mini", "messages": [...]}'
```

```
# 3. AI Gateway router — Helicone key is the ONLY key
base_url = "https://ai-gateway.helicone.ai/ai",  model = "openai/gpt-4o-mini"
```

Everything else is headers: `Helicone-Prompt-Id`, `Helicone-User-Id`,
`Helicone-Session-Id/-Name/-Path`, `Helicone-Property-[Name]`,
`Helicone-Cache-Enabled`, `Helicone-Retry-Enabled`, `Helicone-Fallbacks`,
`Helicone-Model-Override`, `Helicone-Omit-Request/-Response`, and
`Helicone-RateLimit-Policy` with its own mini-grammar
`[quota];w=[time];u=[unit];s=[segment]`.

### 14.5 Kong — the body has no `model`

Kong is the outlier: **there is no built-in client-facing path**. You create a
Route, and the `ai-proxy` plugin rewrites the OpenAI-shaped body into the
configured provider's format. Provider, model and credentials live in *plugin
config*, so the client body can legally omit `model` entirely:

```bash
curl -X POST "http://localhost:8000/anything" \
     -H "Content-Type: application/json" \
     --json '{"messages": [{"role": "system", "content": "You are a mathematician"},
                           {"role": "user", "content": "What is 1+1?"}]}'
```

```bash
curl -i -X POST http://localhost:8001/plugins/ \
    --header "Content-Type: application/json" \
    --data '{
      "name": "ai-proxy",
      "config": {
        "route_type": "llm/v1/chat",
        "auth": {"header_name": "Authorization", "header_value": "Bearer '$OPENAI_API_KEY'"},
        "model": {"provider": "openai", "name": "gpt-4", "options": {"max_tokens": 512, "temperature": 1.0}}
      }
    }'
```

`route_type` selects the upstream shape: `llm/v1/chat`, `llm/v1/embeddings`,
`llm/v1/responses`, `llm/v1/files`, `llm/v1/batches`, `llm/v1/assistants`,
`audio/v1/audio/{speech,transcriptions,translations}`,
`image/v1/images/{generations,edits}`, `video/v1/videos/generations`,
`realtime/v1/realtime` (and a deprecated `llm/v1/completions`).
`config.llm_format` ∈ `openai | anthropic | bedrock | cohere | gemini | huggingface`
controls the *client-facing* format. `config.model.name` supports templating from
headers, URI params and query params, and `config.auth.allow_override` lets a
caller supply its own credential.

`ai-proxy-advanced` swaps `model` for a `targets[]` array plus a `balancer`, with
strategies `round-robin` (weighted), `lowest-latency` (EWMA), `lowest-usage`
(token or cost), `consistent-hashing` (sticky by header), **`semantic`**
(prompt-to-model similarity, backed by `vectordb.strategy: redis|pgvector`), and
`priority` (tiered failover).

`ai-request-transformer` is unusual enough to note: it uses an LLM to **rewrite
the client's request body** before proxying, pulling the JSON back out of the
model's raw output with a Lua pattern. Kong's own docs flag that chaining it with
`ai-proxy` may fail for some providers, because raw model output is forwarded and
strict JSON is not guaranteed.

### 14.6 Databricks — an MLflow envelope for custom models

```bash
curl -X POST -u token:$DATABRICKS_API_TOKEN $ENDPOINT_INVOCATION_URL \
  -H 'Content-Type: application/json' \
  -d '{"dataframe_split": {
    "columns": ["sepal length (cm)", "sepal width (cm)", "petal length (cm)", "petal width (cm)"],
    "data": [[5.1, 3.5, 1.4, 0.2], [4.9, 3.0, 1.4, 0.2]]}}'
```

`/serving-endpoints/{name}/invocations` accepts four envelopes —
`dataframe_split`, `dataframe_records`, `inputs`, `instances` — none of them
OpenAI. Auth is HTTP basic with the **literal username `token`**. Foundation-model
endpoints accept the chat schema on the same path, where **`stream` defaults to
`true`** — the opposite of OpenAI.

The OpenAI-compatible surfaces are `/ai-gateway/mlflow/v1/chat/completions`
(`model` = a Unity Catalog name like `system.ai.claude-sonnet-4-5`) and
`/serving-endpoints` as an OpenAI client `base_url` (`model` = the endpoint name).

> **Unify's LLM router is gone** — `docs.unify.ai` no longer resolves and unify.ai
> is now a research landing page.

---

## 15. Streaming, side by side

Six framings, not one. This is where naive proxying breaks first.

| API | Transport | Event names | Terminal sentinel | Usage arrives |
|---|---|---|---|---|
| OpenAI Chat Completions | SSE, data-only | none | **`data: [DONE]`** | only with `stream_options.include_usage`, in an extra chunk with empty `choices` |
| OpenAI Responses | SSE, named | 58 typed events | **none** — ends at `response.completed` | in `response.completed` |
| Anthropic Messages | SSE, named | `message_start` … `message_stop` | **none** | split: `input_tokens` in `message_start`, **cumulative** `output_tokens` in `message_delta` |
| Gemini `:streamGenerateContent` | SSE, data-only, **different endpoint** + `?alt=sse` | none | **none** | `usageMetadata` repeated on **every** chunk |
| Gemini Interactions / Live | `stream: true` in body / WebSocket | `interaction.created`, `step.delta`, … | none | on completion |
| Cohere v2 | SSE, named | `message-start`, `content-delta`, `message-end` | **none** | on `message-end` |
| **Bedrock ConverseStream** | **AWS binary event-stream** | typed union members | **none** | once, in the terminal `metadata` event |
| Bedrock `InvokeModelWithResponseStream` | AWS binary event-stream | `{"chunk":{"bytes": blob}}` | none | inside the decoded blob |
| Ollama native | **NDJSON** | none | none (`done: true`) | in the final object |
| SGLang native | SSE | none | `data: [DONE]` | in `meta_info` |

**`data: [DONE]` exists only on OpenAI Chat Completions** (and Assistants Runs).
Assuming it on Responses, Anthropic, Gemini, Bedrock or Cohere hangs a client
waiting for a frame that never arrives.

Bedrock's framing deserves its own note, because a gateway **cannot proxy it
byte-for-byte to an SSE client** — it must decode and re-frame. Each message is:

```
┌────────────────┬────────────────┬──────────┬─────────┬─────────┬────────────┐
│ total length   │ headers length │ prelude  │ headers │ payload │ message    │
│ 4B big-endian  │ 4B big-endian  │ CRC 4B   │         │         │ CRC 4B     │
└────────────────┴────────────────┴──────────┴─────────┴─────────┴────────────┘
   fixed overhead: 16 bytes; headers carry :message-type / :event-type / :content-type
```

Content-Type is always `application/vnd.amazon.eventstream`; the real payload
media type is in the `x-amzn-bedrock-content-type` **response header**.

Two more traps:

- **Errors after HTTP 200.** Anthropic emits `event: error`; Bedrock emits
  exception *members of the same event union* — so a 429 can arrive mid-stream
  while the HTTP status still reads 200. Triton's `/generate_stream` is worse:
  the status is set by the *first* SSE event, so you must check every event.
- **SGLang's chunks are cumulative, not deltas.** Its own client tracks a `prev`
  offset and prints `output[prev:]`.

---

## 16. Tool calling, side by side

Five declaration shapes, three result-turn conventions, two argument encodings.

**Declaration:**

```json
// OpenAI Chat Completions — nested under `function`
{"type": "function", "function": {"name": "get_weather", "description": "…", "parameters": {…}}}

// OpenAI Responses — flat
{"type": "function", "name": "get_weather", "description": "…", "parameters": {…}, "strict": true}

// Anthropic — `input_schema`, no wrapper
{"name": "get_weather", "description": "…", "input_schema": {…}}

// Gemini generateContent — an ARRAY inside one Tool object
{"tools": [{"functionDeclarations": [{"name": "get_weather", "description": "…", "parameters": {…}}]}]}

// Gemini Interactions — flat, like Responses
{"type": "function", "name": "get_weather", "parameters": {…}}

// Bedrock Converse — double-nested: inputSchema.json
{"toolConfig": {"tools": [{"toolSpec": {"name": "get_weather", "inputSchema": {"json": {…}}}}]}}

// Cohere v2 — copies OpenAI Chat Completions exactly
{"type": "function", "function": {"name": "get_weather", "parameters": {…}}}
```

**The result turn:**

| API | Where the result goes |
|---|---|
| OpenAI Chat Completions | `{"role": "tool", "tool_call_id": …, "content": …}` |
| OpenAI Responses | `{"type": "function_call_output", "call_id": …, "output": …}` input item |
| Cohere v2 | `{"role": "tool", "tool_call_id": …, "content": …}` |
| **Anthropic** | `tool_result` block inside a **user** message |
| **Bedrock** | `toolResult` block inside a **user** message |
| **Gemini** | `functionResponse` part inside a **user** turn |

**Arguments are a JSON string on OpenAI and Cohere; a parsed object on Anthropic
(`input`), Gemini (`args`) and Bedrock (`input`).** Streaming deltas are partial
JSON strings everywhere.

**Tool choice:**

| API | Vocabulary |
|---|---|
| OpenAI | `"auto"` / `"none"` / `"required"` / `{"type":"function","function":{"name":…}}` |
| Anthropic | `{"type": "auto"\|"any"\|"tool"\|"none", "name": …}` |
| Gemini generateContent | `mode: AUTO\|ANY\|VALIDATED\|NONE` (**SCREAMING_CASE**) |
| Gemini Interactions | `auto\|any\|none\|validated` (**lowercase**) |
| Bedrock | union `{"auto":{}}` / `{"any":{}}` / `{"tool":{"name":…}}` |
| Cohere v2 | `"REQUIRED"` / `"NONE"` (**uppercase**) |

Note `any` (Anthropic, Bedrock) fills the role of OpenAI's `required`. Gemini's
`VALIDATED` — model may answer naturally *or* call a function, but calls are
constrained-decoded — has no analogue anywhere.

**Parallel-call suppression** is spelled differently everywhere:
`parallel_tool_calls: false` (OpenAI), `tool_choice.disable_parallel_tool_use: true`
(Anthropic), and Gemini / Bedrock / Cohere expose **no documented off-switch**.
Gemini alone matches results to calls by `id` rather than array position.

---

## 17. Multimodal content parts

```json
// OpenAI Chat Completions — image_url is an OBJECT
{"type": "image_url", "image_url": {"url": "https://…", "detail": "auto"}}
{"type": "input_audio", "input_audio": {"data": "<base64>", "format": "wav"}}

// OpenAI Responses — image_url is a BARE STRING
{"type": "input_image", "image_url": "https://…"}
{"type": "input_file", "file_url": "https://…/2024ltr.pdf", "detail": "auto"}

// Anthropic — a `source` union
{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "…"}}
{"type": "image", "source": {"type": "url", "url": "https://…"}}
{"type": "image", "source": {"type": "file", "file_id": "file_011CPMxVD3fHLUhvTqtsQA5w"}}
{"type": "document", "source": {"type": "file", "file_id": "…"}, "citations": {"enabled": true}}

// Gemini — inline base64 or a URI reference
{"inline_data": {"mime_type": "image/jpeg", "data": "<base64>"}}
{"fileData": {"mimeType": "video/mp4", "fileUri": "gs://bucket/clip.mp4"}}
{"videoMetadata": {"startOffset": {"seconds": 60}, "endOffset": {"seconds": 70}, "fps": 10.0}}

// Bedrock Converse — untagged union, bytes or S3
{"image": {"format": "png", "source": {"bytes": "…"}}}
{"image": {"format": "png", "source": {"s3Location": {"uri": "s3://…", "bucketOwner": "111122223333"}}}}
{"document": {"format": "pdf", "name": "MyDocument", "source": {"bytes": "…"}}}
{"video": {"format": "mp4", "source": {"s3Location": {"uri": "s3://…"}}}}

// Ollama native — images hang off the MESSAGE, not content parts
{"role": "user", "content": "what is in this image?", "images": ["<base64>"]}

// LM Studio /api/v1/chat — `content` and `data_url`, not `text` and `image_url`
{"type": "image", "data_url": "data:image/png;base64,…"}
```

Practical constraints that decide whether a translation is even possible:

- **Anthropic on Bedrock or Google Cloud accepts base64 sources only** — no `url`,
  no `file_id`.
- **Bedrock's `DocumentBlock` requires an accompanying text block** in the same
  message, and AWS warns the document `name` is prompt-injectable.
- **Ollama's OpenAI shim rejects image URLs** — base64 only.
- **Together AI accepts `detail` and ignores it**; vLLM doesn't support it at all.
- Only Bedrock and Gemini can reference object storage (`s3://`, `gs://`)
  directly; everyone else needs bytes or an upload step.

---

## 18. Structured output, reasoning, caching

### 18.1 Structured output — five encodings

```json
// OpenAI Chat Completions — nested
{"response_format": {"type": "json_schema", "json_schema": {"name": "…", "schema": {…}, "strict": true}}}

// OpenAI Responses — one level shallower
{"text": {"format": {"type": "json_schema", "name": "…", "schema": {…}, "strict": true}}}

// Anthropic — and the JSON comes back inside a plain TEXT block
{"output_config": {"format": {"type": "json_schema", "schema": {…, "additionalProperties": false}}}}

// Gemini — three documented spellings coexist today
{"generationConfig": {"responseMimeType": "application/json", "responseSchema": {…}}}
{"generationConfig": {"responseJsonSchema": {…}}}
{"generationConfig": {"responseFormat": {"text": {"mimeType": "application/json", "schema": {…}}}}}

// Bedrock — schema is a JSON-ENCODED STRING
{"outputConfig": {"textFormat": {"type": "json_schema",
  "structure": {"jsonSchema": {"name": "…", "schema": "{\"type\": \"object\", …}"}}}}}

// Cohere v2
{"response_format": {"type": "json_object", "schema": {…}}}
```

Two outliers worth memorising: **only Bedrock string-encodes the schema**, and
**only Anthropic returns the result as message text** rather than a distinct
typed field. Bedrock also documents the tightest subset — JSON Schema Draft
2020-12 minus recursion, external `$ref`, and numeric/length constraints, with
`additionalProperties: false` mandatory — and compiles grammars on first use
(up to minutes) then caches them 24h.

Self-hosted servers add three *more* mechanisms: GBNF grammars (llama.cpp
`grammar`, LocalAI top-level `grammar`), EBNF (vLLM
`structured_outputs.grammar`, SGLang `sampling_params.ebnf`), and regex (vLLM
`structured_outputs.regex`, SGLang `sampling_params.regex`).

### 18.2 Reasoning — an enum and a token budget are not convertible

| API | Control | Return path |
|---|---|---|
| OpenAI Chat | `reasoning_effort: none…max` | billed via `completion_tokens_details.reasoning_tokens`; no raw CoT |
| OpenAI Responses | `reasoning: {effort, summary, context}` | reasoning items + summaries |
| Anthropic | `thinking: {type: adaptive\|enabled\|disabled, budget_tokens, display}` + `output_config.effort` | `thinking` / `redacted_thinking` blocks with a **`signature`** |
| Gemini | `generationConfig.thinkingConfig{thinkingBudget, includeThoughts}`, → `thinking_level` on 3.x | parts with `thought: true` + `thoughtSignature` |
| Bedrock Converse | **none at this level** — pass through `additionalModelRequestFields` | `reasoningContent.reasoningText{text, signature}` or `redactedContent` |
| Cohere v2 | `thinking: {type, token_budget}` | content blocks of type `thinking` |

**Four of the six return an opaque signature that a proxy must preserve
byte-identically.** Anthropic is explicit: "`thinking` or `redacted_thinking`
blocks in the latest assistant message cannot be modified" — modify one and the
next request 400s. Gemini 3+ requires signatures on *all* tool call/result steps
in the Interactions API.

### 18.3 Caching — four distinct designs

```json
// (1) INLINE MARKERS — Anthropic and Bedrock. The caller controls the breakpoint.
{"cache_control": {"type": "ephemeral", "ttl": "1h"}}     // on system[], message blocks, or tools[]
{"cachePoint": {"type": "default", "ttl": "5m"}}          // same three places

// (2) IMPLICIT + an opaque routing hint — OpenAI
{"prompt_cache_key": "tenant-42", "prompt_cache_options": {"mode": "implicit", "ttl": "30m"}}

// (3) A SEPARATE RESOURCE — Gemini
POST /v1beta/cachedContents  {"model": "models/gemini-3.7-flash", "contents": […], "ttl": "300s"}
→ then  {"contents": […], "cachedContent": "cachedContents/abc123"}

// (4) NO REQUEST-SIDE CONTROL — Cohere reports cached_tokens and that's it
```

Ordering rules bite: Bedrock processes `tools` → `system` → `messages`, evaluates
the minimum token count against the **cumulative** total across all three, and
invalidates later caches when an earlier section changes. Anthropic requires
longer TTLs to appear before shorter ones, and a change to the thinking budget
invalidates cache breakpoints.

---

## 19. Usage, errors, rate limits

### 19.1 "Input tokens" means different things

| API | Field | Semantics |
|---|---|---|
| OpenAI Chat | `prompt_tokens` (+ `prompt_tokens_details.cached_tokens`) | full prompt; cached is a **subset** |
| OpenAI Responses | `input_tokens` (+ `input_tokens_details.cached_tokens`, `cache_write_tokens`) | full prompt |
| **Anthropic** | `input_tokens` | **only tokens after the last cache breakpoint** |
| **Bedrock** | `inputTokens` | **excludes cached** |
| Gemini | `promptTokenCount` | **includes** `cachedContentTokenCount` |
| Cohere v2 | `usage.tokens.input_tokens` + separate `usage.billed_units` | billed ≠ actual |

```
Anthropic total input = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
Bedrock   total input = inputTokens  + cacheReadInputTokens    + cacheWriteInputTokens
```

**A gateway that normalises to one "input tokens" number will misreport three of
the six.** (agentgateway is explicit that it adds cached tokens back in, and
exposes the provider's own numbers separately as `llm.providerInputTokens`.)

One more accounting subtlety that affects rate limits, not just cost: on
Anthropic, `cache_read_input_tokens` does **not** count toward ITPM on most models
(Haiku 3.5 excepted).

### 19.2 Error envelopes

```json
// OpenAI
{"error": {"message": "…", "type": "…", "param": null, "code": null}}

// Anthropic — the only one carrying request_id in the BODY
{"type": "error", "error": {"type": "not_found_error", "message": "…"}, "request_id": "req_011CSHoEeqs5C35K2UUqR7Fy"}

// Gemini — snake_case string codes now, not the classic gRPC {code:int, status:"INVALID_ARGUMENT"}
{"error": {"code": "rate_limit_exceeded", "message": "…"}}

// Azure Foundry /models — FLAT, not nested
{"status": 422, "code": "parameter_not_supported", "detail": {"loc": ["body", "response_format"], "input": "json_object"}, "message": "…"}

// Bedrock — NO envelope at all: typed AWS exceptions
// AccessDeniedException(403) InternalServerException(500) ModelErrorException(424)
// ModelNotReadyException(429) ModelTimeoutException(408) ThrottlingException(429) ValidationException(400)
```

Anthropic's status taxonomy is the most granular: 400 `invalid_request_error`,
401 `authentication_error`, 402 `billing_error`, 403 `permission_error`,
404 `not_found_error`, 409 `conflict_error`, 413 `request_too_large`,
429 `rate_limit_error`, 500 `api_error`, 504 `timeout_error`, 529
`overloaded_error`.

### 19.3 Rate-limit headers are not interchangeable

```
# OpenAI — reset values are DURATION STRINGS
retry-after: 56
x-ratelimit-limit-requests: 60          x-ratelimit-remaining-requests: 59
x-ratelimit-limit-tokens: 150000        x-ratelimit-remaining-tokens: 149984
x-ratelimit-reset-requests: 1s          x-ratelimit-reset-tokens: 6m0s
x-ratelimit-limit-project-tokens / -remaining-project-tokens / -reset-project-tokens

# Anthropic — reset values are RFC 3339 TIMESTAMPS
retry-after
anthropic-ratelimit-{requests,tokens,input-tokens,output-tokens}-{limit,remaining,reset}
anthropic-priority-{input,output}-tokens-*
request-id, anthropic-organization-id, anthropic-workspace-id
```

Gemini, Bedrock and Cohere document **no** rate-limit response headers at all;
Bedrock signals throttling only through `ThrottlingException` / `ModelNotReadyException`.

> **429 is not always retryable.** Anthropic's spend-cap 429 carries **no
> `retry-after`** and keeps failing until the next month — it is distinguished
> only by `error.details.error_code == "enforced_spend_limit_reached"` (a
> self-imposed limit returns 400 `invalid_request_error` instead). OpenAI's
> `insufficient_quota` is likewise a 429-class *billing* error. Blind exponential
> backoff is wrong for both.

---

## 20. Batch and async

No two batch APIs share a shape.

| Provider | Submission | Result retrieval |
|---|---|---|
| **OpenAI** | upload JSONL (`purpose: batch`), then `{input_file_id, endpoint, completion_window: "24h"}` | poll `GET /v1/batches/{id}`, download `output_file_id` |
| **Anthropic** | **inline** `{"requests": [{"custom_id", "params"}]}` in the POST body — no upload step | poll `processing_status` → `"ended"`, stream JSONL from `results_url` |
| **Gemini** | `:batchGenerateContent` → a Google **long-running Operation** | poll `GET /v1beta/{batchName}`; `JOB_STATE_PENDING\|RUNNING\|SUCCEEDED\|FAILED\|CANCELLED\|EXPIRED` |
| **Bedrock** | `CreateModelInvocationJob` with **S3 URIs** in and out | poll the returned `jobArn`; output lands in S3 |
| **Cohere** | `POST /v2/batches` over a **Dataset id** | results in an output Dataset |
| **Mistral** | `POST /v1/batch` | — |

Only OpenAI has a `completion_window` (and only `"24h"` is accepted). Only
Bedrock uses object storage — and only Bedrock has a batch-level *wire-format*
switch, `modelInvocationType: Converse | InvokeModel`. Only Gemini uses an LRO.

**Both OpenAI and Anthropic return results out of order** — always key on
`custom_id`, never on position. Prompt caching does **not** work in Bedrock batch
inference; structured outputs do.

Async-but-not-batch variants exist too: OpenAI Responses `background: true` (poll
`GET /v1/responses/{id}`, resume the stream with
`?stream=true&starting_after=<sequence_number>`), xAI's `deferred: true` →
`GET /v1/chat/deferred-completion/{request_id}`, Perplexity's `/async/sonar`
family, and Vertex's `:predictLongRunning` → `:fetchPredictOperation` for video.

---

## 21. Non-chat protocols

A modern gateway terminates more than chat. Three protocols matter.

### 21.1 MCP over streamable HTTP

One endpoint (`/mcp` by convention) serving both `POST` and `GET`, carrying
JSON-RPC 2.0. The wire details that trip up proxies:

- The client's `Accept` header must list **both** `application/json` **and**
  `text/event-stream`.
- `Mcp-Session-Id` is assigned on `InitializeResult` and echoed on every later
  request — **400** if missing, **404** → re-initialize.
- `MCP-Protocol-Version: 2025-06-18` is mandatory on all HTTP requests; absent
  means assume `2025-03-26`, invalid means 400.
- Notifications and responses get **`202 Accepted` with no body**.
- SSE resumption is per-stream, via `id:` and `Last-Event-ID`.
- `DELETE` terminates a session. `Origin` validation is a MUST.

Method names in play: `initialize`, `tools/list`, `tools/call`, `prompts/*`,
`resources/*`, `subscriptions/listen`, and — as of MCP revision 2026-07-28 —
`server/discover` in place of `initialize` for stateless clients.

Both gateways in this document multiplex several MCP servers behind one endpoint,
and **both rename tools to do it**: Envoy AI Gateway namespaces as
`github__issue_read`; agentgateway as `time_get_current_time` (delimiter `_`).
Client-side allowlists written against the raw server break under either.

### 21.2 A2A

Three bindings, not one: **JSON-RPC 2.0 over HTTP**, **gRPC/protobuf**, and
**HTTP+JSON/REST**. Discovery is `GET /.well-known/agent-card.json` (v0.3+) or
`/.well-known/agent.json` (older). Methods seen in practice: `message/send`,
`message/stream`, `tasks/*`.

agentgateway treats A2A as a marker policy: it does not gate methods, it observes
them — and it **rewrites the agent card in flight** so the `url` (v0.3) or every
`supportedInterfaces[].url` (v1.0) points back at the gateway.

### 21.3 KServe v2 and the Gateway API Inference Extension

KServe's Open Inference Protocol (`POST /v2/models/{m}/infer`, covered in §10.6)
is the tensor-level protocol underneath Triton. It also has a **gRPC** service
that HTTP-only integrations miss.

Sitting *below* an LLM gateway is the **Gateway API Inference Extension** —
`InferencePool` in `inference.networking.k8s.io/v1`, GA in v1.0.0. This is the
KV-cache- and queue-depth-aware routing layer: an **Endpoint Picker** returns the
chosen backend both in the `x-gateway-destination-endpoint` header **and** in
ext_proc `dynamic_metadata` (the two values must match), as `<ip:port>` or a
comma-separated list; the proxy constrains the choice with
`x-gateway-destination-endpoint-subset` in ext_proc filter metadata.

Envoy AI Gateway integrates with it directly:
`AIGatewayRoute.rules[].backendRefs[]` accepts an
`inference.networking.k8s.io/InferencePool` — at most one per rule, not mixable
with `AIServiceBackend` refs in the same rule, and `modelNameOverride`, mutations
and `priority` are ignored for it.

### 21.4 A fourth framing: bidirectional streaming

Bedrock's `POST /model/{modelId}/invoke-with-bidirectional-stream` (only
`amazon.nova-sonic-v1:0`) sends `{"chunk": {"bytes": blob}}` upward and
multiplexes `chunk` with inline `modelStreamErrorException` (424) and
`modelTimeoutException` (408) downward — a third streaming framing beyond SSE and
unary event-stream, and the only HTTP/2 one here.

---

## 22. What bites a gateway

Collected failure modes, in rough order of how often they cause an incident.

**Request shape**

1. **`data: [DONE]` is not universal.** §15.
2. **Tool results are re-parented.** OpenAI's `tool` role has no counterpart on
   Anthropic, Bedrock or Gemini — the result must move into a `user` turn. §16.
3. **Token-cap field names.** `max_tokens` (deprecated on Chat Completions,
   *required* on Anthropic), `max_completion_tokens`, `max_output_tokens`,
   `maxOutputTokens`, `maxTokens`, `maxTokenCount`, `max_gen_len`, `n_predict`,
   `num_predict`, `max_new_tokens`.
4. **Structured-output nesting depth** differs between OpenAI's own two APIs, and
   Bedrock string-encodes the schema. §18.1.
5. **Reasoning signatures must round-trip byte-identically** on Anthropic,
   Gemini, and Bedrock. §18.2.
6. **Multipart bodies are first-class.** `/v1/audio/transcriptions`,
   `/v1/images/edits`, OpenAI `/v1/videos*` and `/v1/content_provenance_checks`
   accept multipart *or* JSON, so body-based model routing must parse both.

**Auth and credentials**

7. **SigV4 cannot be forwarded as a static credential.** Any proxy that rewrites
   a Bedrock body must **re-sign** it.
8. **Auth header varies by route even within one gateway** — LiteLLM uses
   `Authorization`, `x-api-key`, `x-litellm-api-key` and a `?key=` query param on
   different paths. §11.
9. **Header stripping breaks opt-ins.** `anthropic-beta`,
   `OpenAI-Beta: assistants=v2` (required on every `/threads*` call), and
   `MCP-Protocol-Version` are load-bearing.

**Accounting**

10. **"Input tokens" is not one concept.** §19.1.
11. **Usage may not arrive at all.** OpenAI Chat Completions omits it unless
    `stream_options.include_usage` is set — and even then, "if the stream is
    interrupted, you may not receive the final usage chunk." Both gateways here
    force the flag on when cost tracking is configured.
12. **Token limits are charged after the fact.** Envoy AI Gateway documents the
    overshoot plainly: a 1,000-token hourly limit admits a request that streams
    1,200 tokens.
13. **There is no idempotency primitive.** OpenAI's OpenAPI spec contains no
    `Idempotency-Key` anywhere. A gateway that retries must define its own
    dedup — nothing upstream will do it.

**Errors and retries**

14. **429 is not always retryable.** §19.3.
15. **Rate-limit `reset` values are duration strings on OpenAI and RFC 3339
    timestamps on Anthropic.** Parsing one as the other silently produces a
    nonsense backoff.
16. **Errors arrive after HTTP 200** on every streaming API, and Triton sets the
    status from the *first* event only.

**Operational**

17. **Buffer limits.** Envoy Gateway's 32 KiB default is far too small; the
    official Envoy AI Gateway example raises it to 50 MiB.
18. **Request size caps differ by an order of magnitude**: Anthropic Messages
    32 MB, Batches 256 MB, Files 500 MB; Bedrock 20 MB; Google Cloud 30 MB;
    SageMaker 6 MB.
19. **Timeouts.** Claude 3.7/4 on Bedrock have a **60-minute** inference timeout —
    SDK read timeouts must be raised to match. Envoy AI Gateway defaults its route
    timeout to 60s rather than Envoy Gateway's 15s for the same reason.
20. **Silent parameter drops**, which fail open rather than loud: Together's
    `service_tier`/`store`/`metadata`/`prediction`/`detail`; Groq rewriting
    `temperature: 0` to `1e-8`; Mistral's `random_seed` vs `seed`;
    agentgateway dropping `stop_sequences` and `top_k` on Messages→Responses;
    Anthropic's OpenAI shim ignoring `reasoning_effort` and `response_format`.

**Things this document does not cover, but a complete gateway must**

Fine-tuning (`/v1/fine_tuning/jobs`), resumable uploads (OpenAI `/v1/uploads`
vs Gemini's `X-Goog-Upload-Protocol: resumable` — two incompatible protocols),
vector stores, containers, evals, video (including Vertex's long-running
`:predictLongRunning` → `:fetchPredictOperation`), stored-completion readback
(`GET /v1/chat/completions/{id}`), realtime call control and ephemeral-token
minting, admin/usage/cost planes (Anthropic's uses a *different* credential),
guardrails as a standalone call (Bedrock `ApplyGuardrail`), webhook/callback
delivery, and routing idioms this document only touches on — Hugging Face's
model-string **suffix** policies (`openai/gpt-oss-120b:fastest`), Replicate's
`Prefer: wait` sync/async switch, and Alibaba Model Studio's **tenant-in-hostname**
scheme (`{WorkspaceId}.{region}.maas.aliyuncs.com`) with region-pinned keys,
which breaks a single-base-URL gateway model outright.

---

## How this maps to envoyai

The eight `_schema` strings on envoyai's [provider classes](src/envoyai/providers/)
are exactly the dialects Envoy AI Gateway can translate between:

| Python | `_schema` | Client path in | Upstream shape out |
|---|---|---|---|
| [`ea.OpenAI`](src/envoyai/providers/openai.py) | `OpenAI` | `/v1/chat/completions` | §2 |
| [`ea.AzureOpenAI`](src/envoyai/providers/openai.py) | `AzureOpenAI` | `/v1/chat/completions` | §6 — `/openai/deployments/{d}/chat/completions?api-version=…` |
| [`ea.Anthropic`](src/envoyai/providers/anthropic.py) | `Anthropic` | `/anthropic/v1/messages` | §3 |
| [`ea.Bedrock`](src/envoyai/providers/bedrock.py) | `AWSBedrock` | `/v1/chat/completions` | §5.1 — Converse |
| [`ea.AWSAnthropic`](src/envoyai/providers/bedrock.py) | `AWSAnthropic` | `/anthropic/v1/messages` | §5.3 — InvokeModel, native Anthropic body |
| [`ea.GCPVertex`](src/envoyai/providers/gcp.py) | `GCPVertexAI` | `/v1/chat/completions` | §4 — `:generateContent` |
| [`ea.GCPAnthropic`](src/envoyai/providers/gcp.py) | `GCPAnthropic` | `/anthropic/v1/messages` | §3.6 — `:rawPredict` |
| [`ea.Cohere`](src/envoyai/providers/cohere.py) | `Cohere` | `/cohere/v2/rerank` | §7.2 |

`base_url` on `ea.OpenAI` is how every vendor in §8 and every server in §10 gets
reached — they are all `{"name": "OpenAI", "prefix": …}` backends with a different
hostname, which is why the prefix table in §8.1 matters: Groq needs
`prefix: "/openai/v1"`, DeepInfra `"/v1/openai"`, Fireworks `"/inference/v1"`.

---

*Compiled 2026-08-31 from official provider documentation, OpenAPI specs, and —
where the docs are silent — upstream source. Claims marked as verbatim are quoted
from official docs; where a shape could not be confirmed it is called out rather
than guessed. APIs in this space move fast: re-verify anything load-bearing
against the linked source before depending on it.*
