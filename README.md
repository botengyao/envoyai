# envoyai

Python SDK for [Envoy AI Gateway](https://github.com/envoyproxy/ai-gateway). Define an LLM gateway in Python — run it locally, or ship it to Kubernetes in one call. No YAML, no CRDs to memorize.

## On your laptop (no cluster required)

```python
import envoyai as ea

openai  = ea.OpenAI(api_key=ea.env("OPENAI_KEY"))
bedrock = ea.Bedrock(region="us-east-1", credentials=ea.aws.irsa())

gw = ea.Gateway("team-a")
gw.model("chat").route(
    primary=openai("gpt-4o"),
    fallbacks=[bedrock("anthropic.claude-sonnet-4-20250514-v1:0")],
    retry=ea.RetryPolicy.rate_limit_tolerant(),
)

gw.local()                        # gateway on :1975, admin UI on :1976
client = gw.client()
client.chat.completions.create(model="chat", messages=[...])
```

## Ship to production

```python
gw.deploy(kubeconfig="~/.kube/config")       # one call: render → apply → wait for ready
# or, for GitOps:
gw.render_k8s().write("manifests/")
```

Status: alpha, under active development at [github.com/botengyao/envoyai](https://github.com/botengyao/envoyai).
