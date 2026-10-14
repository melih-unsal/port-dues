import pytest

from portdues.evals.runner import load_cases, select_cases
from portdues.evals.scorers import canonical, numeric_score


@pytest.mark.parametrize("name,key", [
    ("Light Dues", "light_dues"),
    ("Port dues (§4.1.1)", "port_dues"),
    ("VTS charges", "vts_dues"),
    ("Vessel Traffic Services", "vts_dues"),
    ("Pilotage services", "pilotage_dues"),
    ("Tugs / vessel assistance", "towage_dues"),
    ("Berthing services", "berthing_services"),
    ("Running of vessel lines", "running_lines"),
    ("Berth dues", "berth_dues"),
    ("Cargo dues - iron ore export", "cargo_dues"),
])
def test_canonical_names(name, key):
    assert canonical(name) == key


def test_numeric_score_tolerance_and_exemptions():
    case = {"expected": {"light_dues": 60062.04, "port_dues": 199549.22, "vts_dues": 0.0, "towage_dues": 1000.0}}
    lines = [
        {"name": "Light dues", "status": "charged", "amount": 60062.04},
        {"name": "Port dues", "status": "charged", "amount": 199371.35},  # 0.09% off
        {"name": "VTS", "status": "exempt", "amount": 0.0},
        {"name": "Towage", "status": "charged", "amount": 1500.0},
        {"name": "Cargo dues", "status": "charged", "amount": 426000.0},
    ]
    result = numeric_score(case, lines)
    assert result["score"] == 0.75
    assert result["per_charge"]["port_dues"]["hit"]
    assert not result["per_charge"]["towage_dues"]["hit"]
    assert result["extra"] == ["cargo_dues"]


def test_golden_set_is_balanced_and_sampling_keeps_the_mix():
    cases = load_cases()
    assert {c["difficulty"] for c in cases} == {"easy", "medium", "hard"}
    assert len({c["id"] for c in cases}) == len(cases)
    picked = select_cases({"limit": 6})
    assert [c["difficulty"] for c in picked].count("hard") == 2


def test_reference_rules_score_perfectly_offline():
    """The hand-written rules from test_engine reproduce the brief's case through the offline checker."""
    from portdues.evals.rulebook_check import call_values

    case = next(c for c in load_cases() if c["id"] == "h01")
    values = call_values(case["call"])
    assert values["port"] == "durban" and values["num_services"] == 2 and values["stay_hours"] == 81.36
