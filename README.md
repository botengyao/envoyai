# envoyai

Python SDK for [Envoy AI Gateway](https://github.com/envoyproxy/ai-gateway). Define, run, and deploy a production-grade LLM gateway without touching YAML or Kubernetes.

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

gw.local()                               # dev: gateway on :1975, admin on :1976
gw.render_k8s().write("manifests/")      # GitOps
gw.apply(kubeconfig="~/.kube/config")    # direct apply
```

Status: alpha, under active development at [github.com/botengyao/envoyai](https://github.com/botengyao/envoyai).
