# Sindri Config — IG Media Kit
<!-- rune-generated: 2026-07-14 | git: acdd3c5 | rune: 1.0 -->

## Language

primary_language: python
language_version: python3.12

## Scope

- The MCP kit only. Mirror yt-media-kit's shape where it translates; don't re-invent config/manifest ergonomics.

## Quality Overrides

- **Stricter — any IG-hitting code path:** must honor the politeness invariant (pace pages, cap ~4/call, partial-on-401, no cooldown polling). A path that can hammer the API is a bug regardless of tests.
- **Stricter — anonymous-only invariant:** no login/cookie/session/account code anywhere. Reject on sight.
- Verify IG-behavior assumptions with a real probe before building on them (endpoints/limits/fields drift). <!-- confidence: HIGH -->

## Interrogation Defaults

- default_stage: build
- test_framework: pytest
- http_client: curl_cffi (impersonate="chrome") — never plain requests/httpx for IG calls
- No live-IG calls in unit tests — fixture the feed JSON; keep one opt-in integration smoke test.

## Persona Integration

- domain_persona: none
