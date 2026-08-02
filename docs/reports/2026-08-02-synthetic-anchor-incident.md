# Incident: Synthetic Anchor Injection

**Date:** 2026-08-02  
**Severity:** Security and protocol-integrity breach  
**Status:** Contained; permanent redesign planned

## What happened

The sender appended a deterministic, non-model candidate when the selected
angle was not visibly represented strongly enough in the candidates returned
by the LLM. The template inserted an extracted angle phrase into a stock
sentence:

```text
I can see why people keep coming back to <source quote>.
```

For post `1lpu1xg`, this produced: `I can see why people keep coming back to
visa after he released a song.` The candidate decoded successfully and was
accepted even though it was not a coherent reply.

## Why this is a security breach

The selection-channel protocol requires the carrier to be an ordinary,
LLM-generated visible reply whose selected angle can be recovered by the
receiver. The fallback silently changed the carrier after model generation in
order to force recoverability. That means the system could claim decode
success while bypassing the intended generation and quality contract.

This is not an acceptable reliability mechanism. It contaminates generated
artifacts, biases quality evaluation, creates trivially recognizable text, and
makes successful decoding an invalid proxy for a valid carrier.

## Immediate containment

- Removed the synthetic-anchor module and all call sites.
- Removed tests that endorsed or ranked synthetic anchors.
- Added a regression test that candidate generation returns only model output.
- Deleted the contaminated source artifacts and paired-sample datasets found
  in the active comparison artifacts.
- Confirmed the focused pipeline tests and Ruff checks pass.

## Permanent rules

1. No post-generation code may create, append, splice, or template a visible
   candidate reply from an angle, tangent, source quote, payload, or decoder
   result.
2. A candidate is eligible only if it originated from the configured LLM
   generation or an explicitly logged LLM revision call.
3. Decoder failure is a legitimate encode failure. It must be recorded and
   reported; it must never be repaired by manufacturing text.
4. Any future candidate transformation must preserve provenance, include the
   full prompt/response trace, and be rejected unless it is an LLM call made
   before receiver validation.
5. Benchmark and dashboard artifacts must retain only verified, provenance
   complete outputs.

The remediation roadmap is [Project LUCID](../plans/project-lucid-tangentdb-and-feedback-loop.md).
