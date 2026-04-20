# b — Proxy mode

Run envoyai as a foreground, long-running proxy. Start it in one terminal;
hit it from any OpenAI-compatible client in another — Python, Node, Go, Ruby,
or `curl`. The gateway speaks OpenAI-format natively, so existing client
code works against it without changes.

Good for: multi-language stacks, shared dev servers, production deployments.

If you only need to call the gateway from the same Python process that
configured it, [`../a_sdk_mode.py`](../a_sdk_mode.py) is shorter.

## Run

```bash
# Terminal 1 — start the proxy
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
python server.py
```

```bash
# Terminal 2 — call it from any client
python client_python.py
# or
./client_curl.sh
```

Ctrl-C in terminal 1 shuts the proxy down cleanly.
