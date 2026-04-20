"""Versioned price sheets for cost attribution.

Each sheet is a JSON file named with the provider and its ``effective_from``
date, e.g. ``openai-2026-01-01.json``. Sheets are immutable after publish —
a change to published prices means a new sheet with a later
``effective_from``. Historical queries read the sheet that was in effect at
the request's timestamp.

This package performs zero I/O at import time. The JSON files are loaded on
demand by the ledger query path.
"""
