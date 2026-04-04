---
name: Anthropic Auth — Subscription Bearer Token
description: Subscription users must use ANTHROPIC_AUTH_TOKEN (Bearer), not ANTHROPIC_API_KEY
type: feedback
---

Use `ANTHROPIC_AUTH_TOKEN` (sk-ant-oat01-* prefix) via `anthropic.Anthropic(auth_token=...)` for subscription users. API keys (sk-ant-api03-*) require billing credits. Anthropic does NOT support OIDC workload identity for direct API access — that's cloud provider only (Bedrock/Vertex).

**Why:** User is on a Claude subscription with no billing credits loaded. API key auth fails with "credit balance too low". Bearer token from subscription works without credits.

**How to apply:** In `make_client()`, always check `ANTHROPIC_AUTH_TOKEN` first, then fall back to `ANTHROPIC_API_KEY`. Token lives at `~/.claude/.credentials.json` → `claudeAiOauthAccount.oauthToken.accessToken`.
