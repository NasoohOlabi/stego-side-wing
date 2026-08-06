# Documentation Index

Agent-friendly map for project documentation. Root `README.md` remains the project entry point, and root `AGENTS.md` remains in place for agent/tooling discovery.

## Reference

- [API spec](reference/api-spec.md) - `/api/v1/*` HTTP contract, metrics endpoints, workflow state, and prompts routes.
- [Frontend integration guide](reference/frontend-integration-guide.md) - frontend/backend integration notes.
- [Paired quality metrics](reference/paired-quality-metrics.md) - deterministic and judge-based quality metrics for accepted paired rows.

## Development

- [Architecture layers](development/architecture-layers.md) - allowed import direction and layering rules.
- [Validation per phase](development/validation-per-phase.md) - checks to run after refactors and workflow changes.
- [Contributing](development/contributing.md) - contributor workflow notes.
- [Sender/receiver pipeline](development/stego-sender-receiver-pipeline.md) - sender/receiver behavior and shared-contract notes.
- [Naturalness gate](development/naturalness-gate-v1.md) - generation-quality gate design.
- [Refactor summary](development/refactor-summary.md) - historical refactor summary.

## Results And Benchmarks

- [Current research state (2026-08-03)](reports/2026-08-03-current-research-state.md) - authoritative status for the active LUCID bulk lane and latest unpaired ZLG lane.
- [Method and ZLG benchmark guide](../.agents/method-and-zlg-benchmark.md) - method structure, metric interpretation, and ASUS GPU operating workflow.
- [Results findings](results/results-findings.md) - interpretation of saved metric artifacts and current highlights.
- [Sample size plan](results/sample-size-plan.md) - plan for clean, current-code benchmark sample collection.
- [Workload runs and artifacts](results/workload-runs-and-artifacts.md) - where run outputs and logs are stored.
- [Publication benchmark implementation](reports/2026-07-18-publication-benchmark-implementation.md) - implemented paired-runner protocol and accounting rules.
- [ZLG benchmark audit (2026-07-26)](reports/zlg-benchmark-audit-2026-07-26.md) - current historical-results interpretation and limitations.
- [ZLG endpoint capacity specification](zlg_endpoint_capacity_spec.md) - baseline service capacity-accounting contract.
- [Publication benchmark plan](plans/publication-benchmark/README.md) - current execution gates and live-run authorization.
- [Publication benchmark results guide](results/publication-benchmark.md) - result-artifact interpretation.
- [Unified experiment plan](results/unified-experiment-plan.md) - cross-experiment evaluation plan.

## Operations

- [Log navigation and frontend tips](operations/log-navigation-and-frontend-tips.md) - practical log navigation notes.
- [Double process debug summary](operations/double-process-debug-summary.md) - debug summary for double-process behavior.
- [Double process live findings](operations/double-process-live-findings.md) - live findings for double-process behavior.

## Plans

- [Angle compatibility plan](plans/angle-compatibility-plan.md) - plan for angle compatibility work.

## Archive

- `imported/` contains imported historical artifacts. It is reference material,
  not the canonical location for current benchmark claims.

## HTML Reports

- [Data load pipeline report](reports/data-load-pipeline-report.html)
- [Generate angles pipeline report](reports/gen-angles-pipeline-report.html)
- [Research pipeline report](reports/research-pipeline-report.html)
- [Stego pipeline report](reports/stego-pipeline-report.html)
