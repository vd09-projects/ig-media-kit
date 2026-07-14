# Multi-Perspective Review Config — IG Media Kit
<!-- rune-generated: 2026-07-14 | git: acdd3c5 | rune: 1.0 -->

## Reviewer Overrides

always_include:
  - Reliability / Rate-Limit Reviewer (the politeness invariant is load-bearing; abuse degrades the shared IP)

always_exclude:
  - Accessibility Reviewer (no UI in this project)

## Project Context

domain: Instagram reel fetcher (anonymous, self-hosted MCP)
primary_languages: Python
architecture: Single-process MCP server (FastMCP) over a shared fetch engine + flat-file store
urgency_default: normal
debt_tolerance: normal

## Custom Triage Rules

- Any change touching an IG-hitting code path (Fetcher, feed pagination, rate-limit handling) → always include the Reliability / Rate-Limit Reviewer, and treat scope as at-least-medium.
- Any change introducing auth, cookies, login, or an account → hard stop: violates the anonymous-only invariant. Flag as blocking.

## Reviewer Voice Tuning

- Reliability reviewer: enforce pace / page-cap / partial-on-401 / no-cooldown-polling on every IG call.
- Security reviewer: confirm no credentials, tokens, or personal-identity linkage anywhere.
