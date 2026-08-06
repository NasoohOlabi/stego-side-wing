# Paired quality metrics

Offline Codex judge calls use `codex exec`, rather than `LLMAdapter`, because Codex CLI is not an OpenAI-compatible workflow endpoint. Prompts and JSON schemas are frozen and SHA-256 hashed per judgment; task construction is deterministic, while reasoning-model outputs are not guaranteed deterministic.
