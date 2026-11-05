"""SLO + eval-scale probe (S9).

Measures what can be measured without a live DB/API and reports the rest as
explicit gaps vs docs/PRD.md §2/§12 targets:
- hit-test latency <150ms (in-process grading loop)
- eval scale: 30-40 masks + 30 Qs (data/labels.jsonl)
- harness presence: contract/e2e tests, agent prompts, alembic versions

Usage: python -m eval.measure_slo  (run from backend/)
"""

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent


def _fake_comp(cid, polygons):
    return SimpleNamespace(
        id=cid, polygons=polygons, area_fraction=0.04, confidence=0.9, mask_asset_id=None
    )


async def measure_hit_test_latency(n: int = 200) -> dict:
    from app.services.grading import grade_spatial_attempt

    sq = [[[[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4], [0.2, 0.2]]]]
    target = _fake_comp(uuid.uuid4(), sq)
    others = [_fake_comp(uuid.uuid4(), sq) for _ in range(5)]
    image = SimpleNamespace(width_px=1000, height_px=1000)
    latencies = []
    for i in range(n):
        x = 0.2 + (i % 20) * 0.01
        start = time.perf_counter()
        await grade_spatial_attempt(x, 0.3, target, image, [target, *others], db=None)
        latencies.append((time.perf_counter() - start) * 1000)
    latencies.sort()
    return {
        "n": n,
        "p50_ms": round(latencies[n // 2], 3),
        "max_ms": round(latencies[-1], 3),
        "target_ms": 150,
        "pass": latencies[-1] < 150,
    }


def eval_scale() -> dict:
    labels_file = REPO / "data" / "labels.jsonl"
    masks, qas, raw_lines = 0, 0, 0
    if labels_file.exists():
        for line in labels_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw_lines += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and ("question" in row or "prompt" in row):
                qas += 1
            else:
                masks += 1
    return {
        "masks": masks,
        "qas": qas,
        "raw_lines": raw_lines,
        "prd_masks_target": "30-40",
        "prd_qs_target": 30,
        "scale_met": masks >= 30 and qas >= 30,
    }


def harness_presence() -> dict:
    def _nonempty(path: Path) -> bool:
        if not path.exists():
            return False
        return any(
            p
            for p in path.rglob("*")
            if p.is_file() and "__pycache__" not in str(p) and p.name != "__init__.py"
        )

    return {
        "contract_tests": _nonempty(BACKEND / "tests" / "contract"),
        "e2e_tests": _nonempty(BACKEND / "tests" / "e2e"),
        "agent_prompts": _nonempty(BACKEND / "app" / "agent" / "prompts"),
        "alembic_versions": _nonempty(BACKEND / "alembic" / "versions"),
        "boards_data": _nonempty(REPO / "data" / "boards"),
        "datasheets_data": _nonempty(REPO / "data" / "datasheets"),
    }


def main() -> int:
    latency = asyncio.run(measure_hit_test_latency())
    scale = eval_scale()
    harness = harness_presence()
    report = {
        "hit_test_latency": latency,
        "eval_scale": scale,
        "harness": harness,
        "unmeasured_slos": [
            "cached Q&A <1.0s (needs live API + cache)",
            "cold p50<8s/p95<12s (needs live SAM/Spark + DB)",
            "recall>=70%@IoU0.5, naming>=80%, grounding 100%/85% (need 30-40 masks + 30 Qs gold set)",
            "spend <$120 (needs live model_calls ledger)",
        ],
    }
    print(json.dumps(report, indent=2))
    ok = latency["pass"] and scale["scale_met"] and all(harness.values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
