"""11 — Per-team spend caps.

Attribute cost to a team via a request header, set a monthly budget per
model, alert at 80%, enforce at 100%. Enforcement translates to a
gateway-level rate limit when the cap is reached; alerts fire a webhook.
"""
from __future__ import annotations

import envoyai as ea

openai = ea.OpenAI(api_key=ea.env("OPENAI_KEY"))

gw = ea.Gateway()
gw.track_cost(
    team_from=ea.header("x-team"),
    user_from=ea.header("x-user"),
)

gw.model("chat") \
  .route(primary=openai("gpt-4o")) \
  .budget(
      ea.Budget(team="chat", monthly_usd=500, alert_at=0.8, enforce_at=1.0)
  )

gw.model("fast") \
  .route(primary=openai("gpt-4o-mini")) \
  .budget(
      ea.Budget(team="chat", monthly_usd=50, alert_at=0.9)
  )

gw.local()

# Clients set `x-team: chat` (and optionally `x-user: <id>`) on requests.
# Spend is attributed accordingly; the admin UI shows per-team totals.
