"""Score a compiled rulebook against the golden labels without any LLM call.

Each golden case stores the facts of its call, so the rules can be evaluated directly. This isolates rule
quality from query understanding (the profile extractor) and answer writing, which makes it the right
yardstick for comparing compilers, and it costs nothing to run after every recompile.
"""

from typing import Any

from portdues.evals.runner import load_cases
from portdues.evals.scorers import canonical, numeric_score
from portdues.rules.engine import quote
from portdues.rules.schema import Rulebook


def call_values(call: dict[str, Any]) -> dict[str, Any]:
    stay = call["stay_days"]
    values = {
        "port": call["port"], "gt": call["gt"], "loa": call["loa"],
        "stay_days": stay, "stay_hours": round(stay * 24, 4), "num_services": call["services"],
        "outside_working_hours": call["outside_hours"], "is_government_vessel": call["government"],
        "is_coaster": call["coaster"], "is_passenger_vessel": call["passenger"], "is_tanker": call["tanker"],
        "cargo_working": call["cargo_working"], "call_purpose": "bunkers" if call["bunkers_only"] else "cargo",
    }
    for key in ("cargo_tonnes", "cargo_commodity", "cargo_direction", "cargo_category"):
        if call.get(key):
            values[key] = call[key]
    if call["tanker"]:
        values["vessel_type"] = "tanker"
        values.setdefault("cargo_category", "liquid_bulk")
    elif call["passenger"]:
        values["vessel_type"] = "passenger"
    if call["government"]:
        values["vessel_type"] = "naval patrol vessel"
    return values


def check_rulebook(rulebook: Rulebook) -> dict[str, Any]:
    rows = []
    for case in load_cases():
        lines = [l.model_dump() for l in quote(rulebook, call_values(case["call"])).lines]
        score = numeric_score(case, lines)
        rows.append({"case_id": case["id"], "difficulty": case["difficulty"], **score})
    by_difficulty = {
        d: round(sum(r["score"] for r in rows if r["difficulty"] == d) / max(1, sum(r["difficulty"] == d for r in rows)), 4)
        for d in ("easy", "medium", "hard")
    }
    return {
        "numeric": round(sum(r["score"] for r in rows) / len(rows), 4),
        "exact": sum(r["exact"] for r in rows),
        "cases": len(rows),
        "by_difficulty": by_difficulty,
        "misses": [
            {"case_id": r["case_id"], "charge": k, "expected": c["expected"], "predicted": c["predicted"]}
            for r in rows for k, c in r["per_charge"].items() if not c["hit"]
        ],
    }


PORTS = ["durban", "richards bay", "cape town", "saldanha", "port elizabeth", "ngqura", "east london", "mossel bay"]
TONNAGES = [1500, 8750, 33100, 51300, 101800]
SITUATIONS = {
    "normal": {},
    "outside hours": {"outside_working_hours": True},
    "navy": {"is_government_vessel": True, "vessel_type": "naval patrol vessel"},
    "coaster": {"is_coaster": True},
    "tanker, 10 h": {"is_tanker": True, "vessel_type": "tanker", "cargo_category": "liquid_bulk",
                     "stay_days": 10 / 24, "stay_hours": 10},
    "bunkers only": {"call_purpose": "bunkers", "cargo_working": False, "stay_days": 1.25, "stay_hours": 30},
}
CARGO = [
    ("iron ore", "dry_bulk"), ("coal", "dry_bulk"), ("manganese ore", "dry_bulk"), ("wheat", "dry_bulk"),
    ("citrus fruit", "breakbulk"), ("steel pellets", "breakbulk"), ("crude & petroleum products", "liquid_bulk"),
    ("sunflower seed oil", "liquid_bulk"),
]


def scenario_grid() -> list[tuple[str, dict[str, Any]]]:
    grid = []
    for port in PORTS:
        for gt in TONNAGES:
            for name, extra in SITUATIONS.items():
                values = {"port": port, "gt": gt, "loa": 200, "stay_days": 2.5, "stay_hours": 60,
                          "num_services": 2, "cargo_working": True, "call_purpose": "cargo", **extra}
                grid.append((f"{port}, {gt:,} GT, {name}", values))
    for commodity, category in CARGO:
        for direction in ("import", "export"):
            grid.append((f"cargo: {commodity} {direction}", {
                "port": "durban", "gt": 30000, "stay_days": 2, "stay_hours": 48, "num_services": 2,
                "cargo_tonnes": 10000, "cargo_commodity": commodity, "cargo_category": category,
                "cargo_direction": direction}))
    return grid


def _amounts(rulebook: Rulebook, values: dict[str, Any]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for line in quote(rulebook, values).lines:
        key = canonical(line.name)
        if line.status in ("charged", "exempt"):
            out[key] = round((out.get(key) or 0) + (line.amount or 0), 2)
        elif line.status in ("needs_input", "error"):
            out.setdefault(key, None)
    return out


def compare_rulebooks(candidate: Rulebook, reference: Rulebook, tolerance: float = 0.01) -> dict[str, Any]:
    """Price the same grid of calls with two rulebooks and report where they disagree, charge by charge."""
    per_charge: dict[str, list[bool]] = {}
    disagreements = []
    for label, values in scenario_grid():
        got, want = _amounts(candidate, values), _amounts(reference, values)
        for key in sorted(set(got) | set(want)):
            a, b = got.get(key), want.get(key)
            same = (a is None and b is None) or (a is not None and b is not None and
                                                 abs(a - b) <= tolerance * max(abs(b), 1.0))
            per_charge.setdefault(key, []).append(same)
            if not same:
                disagreements.append({"call": label, "charge": key, "candidate": a, "reference": b})
    agreement = {k: round(sum(v) / len(v), 4) for k, v in per_charge.items()}
    total = [x for v in per_charge.values() for x in v]
    return {"agreement": round(sum(total) / len(total), 4), "by_charge": agreement, "comparisons": len(total),
            "disagreements": disagreements}
