# Changelog

All notable changes to envoyai are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial scaffold: `Gateway` builder, eight providers (OpenAI, AzureOpenAI,
  Bedrock, AWSAnthropic, Anthropic, Cohere, GCPVertex, GCPAnthropic), auth
  helpers (`env`, `secret`, `header`, `aws`, `azure`, `gcp`), policy types
  (`RetryPolicy`, `Budget`, `Timeouts`), typed error hierarchy.
- Target outputs on `Gateway`: `.local()`, `.deploy()`, `.render_k8s()`,
  `.apply()`, `.diff()` — currently stubs raising `NotImplementedError`.
- README with laptop and ship-to-Kubernetes flows.
- Smoke tests covering the public surface and the README example.

### Changed
- `Gateway` no longer takes a required `namespace`; the `namespace` kwarg
  moved to `render_k8s()` / `apply()` / `deploy()` / `diff()` where the
  Kubernetes output actually uses it.
- `Gateway.listener_port` → `Gateway.port`.
- `RetryPolicy.attempts_per_priority` → `attempts_per_step`.
- `Timeouts.backend` → `Timeouts.provider`.

### Removed
- Envoy- and CRD-flavored vocabulary from user-visible docstrings and error
  messages (internal implementation names stay under `envoyai._internal`).

[Unreleased]: https://github.com/botengyao/envoyai/compare/main...HEAD
