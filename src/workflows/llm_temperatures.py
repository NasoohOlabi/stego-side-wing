"""Shared LLM sampling defaults for workflow pipelines."""

# Encode (stego) and decode share the same temperature so sender/receiver behavior stays aligned.
STEGO_CYCLE_LLM_TEMPERATURE: float = 0.7
