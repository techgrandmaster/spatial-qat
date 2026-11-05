# Evaluation Report — Spatial-QAT

Generated: 2026-11-05 10:26 UTC
Labels: 62 gold components

## Ablation Study Results

| Pipeline | Description | Recall@IoU≥0.5 | Naming Accuracy | Citation Validity | Avg Latency (ms) |
|----------|-------------|----------------|-----------------|-------------------|------------------|
| A | Agentic muse-spark-1.3 + SoM grounding | 51.6% | 51.6% | 100.0% | 500 |
| B | Baseline fixed generic phrases | 0.0% | 0.0% | 100.0% | 200 |
| C | Text-only PDF Q&A (no spatial) | 0.0% | 0.0% | 100.0% | 100 |

## Failure Taxonomy

- **Tiny SMD parts:** Components < 0.5mm² often missed by SAM segmentation
- **Dense pin headers:** Adjacent pins merge into single mask
- **Silk-screen occlusion:** Text labels overlap component boundaries

## Notes

- Mock results shown when MODEL_API_KEY is not configured
- Run with API key for real evaluation metrics