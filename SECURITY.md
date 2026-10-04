# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities **privately** via
[GitHub Security Advisories](https://github.com/dfdezdom/investdaytip/security/advisories/new)
(*Report a vulnerability* tab). Do not open a public issue.

You will get an acknowledgement as soon as possible (target: 72 hours), and a
fix or triage decision before any public disclosure.

## Scope

InvestDayTip is a local CLI tool. In scope:

- **API key handling** — `FMP_API_KEY` and `STOCKFIT_API_KEY` are read from
  environment variables only: never logged, never written to the cache, never
  included in exported HTML reports.
- **The SQLite cache** (`~/.investdaytip/`) — it must never contain secrets.
- **HTML report export** — reports are self-contained and must not embed
  credentials or local filesystem paths beyond ticker metadata.
- Vulnerabilities in the published `investdaytip` PyPI package (dependency
  supply chain included).

Out of scope:

- Issues in third-party data sources or services (Yahoo Finance, Financial
  Modeling Prep, StockFit, DataRoma, CNN) — report those to the respective
  providers.
- Findings that require a compromised local environment or malicious
  `~/.investdaytip` cache tampering.
- Denial of service against external APIs through misuse of the CLI.

## Supported Versions

Only the latest release published on PyPI is supported with security fixes.
