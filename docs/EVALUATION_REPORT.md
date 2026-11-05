# Evaluation Report

Auto-generated ablation & latency metrics table.

Run `python -m eval.run_evals` to populate this report.

## Metrics

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Component Discovery Recall (IoU ≥ 0.50) | ≥ 70% | — | ⏳ |
| Component Naming Accuracy | ≥ 80% | — | ⏳ |
| Grounding & Citation Validity | 100% | — | ⏳ |
| Spatial Click Hit-Test Latency | < 150ms | — | ⏳ |
| Cached Demo Path Q&A Latency | < 1.0s | — | ⏳ |
| Cold Interactive Q&A Latency (p50/p95) | < 8s / < 12s | — | ⏳ |
| Spatial Quiz Determinism | 100% | — | ⏳ |
| Total API Spend | < $120.00 | — | ⏳ |

## Ablation Study

| Pipeline | Description | Recall | Accuracy |
|----------|-------------|--------|----------|
| A (Agentic) | muse-spark-1.3 proposed phrases + SoM grounding | — | — |
| B (Baseline) | Fixed generic phrase list | — | — |
| C (Text-only) | PDF Q&A without spatial selection | — | — |

## Failure Taxonomy

- Tiny SMD parts
- Dense pin headers
- Silk-screen occlusion
