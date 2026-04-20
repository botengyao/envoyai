# Price sheets

Versioned, immutable JSON sheets with per-provider, per-model pricing. The
ledger reads the sheet that was in effect at the request's timestamp, so
historical cost queries stay correct even after prices change.

## File naming

```
<provider>-<YYYY-MM-DD>.json
```

Where the date is the sheet's `effective_from` — the first day the prices
apply. Examples:

- `openai-2026-01-01.json`
- `anthropic-2026-01-15.json`
- `bedrock-2025-11-01.json`

## Schema

```json
{
  "$schema": "envoyai-price-sheet/v1",
  "provider": "openai",
  "effective_from": "2026-01-01",
  "currency": "USD",
  "models": {
    "gpt-4o": {
      "input_per_1m_tokens": 2.50,
      "output_per_1m_tokens": 10.00
    },
    "gpt-4o-mini": {
      "input_per_1m_tokens": 0.15,
      "output_per_1m_tokens": 0.60
    }
  }
}
```

## Rules

1. **Sheets are immutable after the first release that includes them.**
   Never edit a published sheet. To change a price, add a new sheet with a
   later `effective_from`.
2. **The ledger picks the sheet active at request time.** A cost query for
   last month uses last month's prices, even if newer prices have landed.
3. **No network I/O at import.** Sheets are bundled into the wheel. A
   separate `envoyai prices update` CLI refreshes them in place, with
   explicit user intent.
4. **User overrides are per-call.** `ledger.total(..., prices=my_sheet)`
   lets a caller answer "what would this cost with those prices?" without
   touching the bundled data.
