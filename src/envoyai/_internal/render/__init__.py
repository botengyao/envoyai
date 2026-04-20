"""Renderers — Gateway → target-specific configuration.

A renderer is a function that takes a :class:`envoyai.Gateway` and emits
either a list of manifest dicts or a string. Each target (aigw standalone,
Kubernetes CRDs, Envoy static YAML, xDS snapshot, Traffic Director, …)
lives in its own module.

Today only ``aigw_standalone`` is implemented.
"""
