# Context-weighted angle sampler

The parent-conditioned sampler is migration-gated and disabled by default.

```powershell
$env:WORKFLOW_CONTEXT_SAMPLER = 'context_weighted_v2'
```

It requires a non-empty post body and uses only comments and already-persisted
`search_results`. It never performs research fetches. For each frame, the sender consumes
the recoverable parent bits first, builds the selected parent's dictionary and verified
tangent set, and only then consumes the tangent bits. The receiver observes the sender
comment's parent and rebuilds the same dictionary.

Effective settings:

| Variable | Default |
|---|---:|
| `WORKFLOW_CONTEXT_COMMENT_WEIGHT` | `3` |
| `WORKFLOW_CONTEXT_RESEARCH_WEIGHT` | `1` |
| `WORKFLOW_CONTEXT_MAX_ANCESTORS` | `8` |
| `WORKFLOW_CONTEXT_INCLUDE_CHILDREN` | `1` |
| `WORKFLOW_CONTEXT_GLOBAL_FALLBACK` | `1` |

Artifacts use the separate `selection_channel_angles/context_weighted_v2` namespace and
record the parent ID, frozen-research hash, allocation and relationship counts, dictionary
ID, tangent hash, and recoverable widths. Explicitly mixed sampler versions are rejected
within a frame-planning lane. Roll back with:

```powershell
$env:WORKFLOW_CONTEXT_SAMPLER = 'post_level_v1'
```
