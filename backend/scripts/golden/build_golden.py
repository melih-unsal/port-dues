"""Build the synthetic golden set: hand-specified scenarios, labels from za_reference,
questions phrased by the LLM (and checked to contain every number they must carry).

    python scripts/golden/build_golden.py   # writes portdues/evals/golden.jsonl
"""

import asyncio
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2]))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from za_reference import Call, expected  # noqa: E402

from portdues.llm import chat_model  # noqa: E402

OUT = Path(__file__).parents[2] / "portdues" / "evals" / "golden.jsonl"
FULL = None  # every routine charge

# (id, difficulty, call, charges, facts the question must state, style, scenario notes)
SPECS = [
    ("e01", "easy", Call("cape town", 28400), ["light_dues"], {"gt": "28,400"}, "terse", ""),
    ("e02", "easy", Call("durban", 12500), ["vts_dues"], {"gt": "12,500"}, "conversational", ""),
    ("e03", "easy", Call("richards bay", 41250), ["pilotage_dues"], {"gt": "41,250", "services": "2"},
     "terse", "entering and leaving"),
    ("e04", "easy", Call("east london", 9870, stay_days=2), ["port_dues"], {"gt": "9,870", "days": "2"},
     "conversational", ""),
    ("e05", "easy", Call("saldanha", 66000), ["berthing_services"], {"gt": "66,000", "services": "2"},
     "terse", "berthing on arrival and unberthing on departure"),
    ("e06", "easy", Call("cape town", 18000), ["running_lines"], {"gt": "18,000", "services": "2"},
     "conversational", "lines run on arrival and departure"),
    ("e07", "easy", Call("ngqura", 74350), ["light_dues"], {"gt": "74,350"}, "terse", ""),
    ("e08", "easy", Call("mossel bay", 3200), ["pilotage_dues"], {"gt": "3,200", "services": "2"},
     "conversational", "in and out"),
    ("e09", "easy", Call("saldanha", 160000), ["vts_dues"], {"gt": "160,000"}, "terse", ""),
    ("e10", "easy", Call("durban", 51300, stay_days=1), ["light_dues", "vts_dues"], {"gt": "51,300"},
     "conversational", ""),

    ("m01", "medium", Call("durban", 8750), ["towage_dues"], {"gt": "8,750", "services": "2"}, "terse",
     "tug assistance in and out"),
    ("m02", "medium", Call("richards bay", 101800), ["towage_dues"], {"gt": "101,800", "services": "2"},
     "conversational", "Capesize bulk carrier, tugs on arrival and departure"),
    ("m03", "medium", Call("port elizabeth", 380), ["vts_dues"], {"gt": "380"}, "conversational",
     "a small supply boat calling once"),
    ("m04", "medium", Call("cape town", 41000, stay_days=1.5), FULL, {"gt": "41,000", "days": "1.5"},
     "agent_email", "container ship, discharging and loading, normal working hours, in and out once"),
    ("m05", "medium", Call("richards bay", 33100, stay_days=2.75), FULL,
     {"gt": "33,100"},
     "json", "Handysize bulk carrier loading coal; stay derived from arrival 2024-09-03 06:00 to departure "
             "2024-09-06 00:00 (2.75 days)"),
    ("m06", "medium", Call("ngqura", 54500, services=3), ["pilotage_dues", "towage_dues"],
     {"gt": "54,500", "services": "3"}, "agent_email", "arrival, one shift between berths and departure"),
    ("m07", "medium", Call("saldanha", 92000, stay_days=4.2), ["port_dues", "light_dues"],
     {"gt": "92,000", "days": "4.2"}, "terse", "ore carrier loading iron ore"),
    ("m08", "medium", Call("east london", 6500, stay_days=1), FULL, {"gt": "6,500", "days": "1"},
     "conversational", "general cargo ship discharging, daytime moves only"),
    ("m09", "medium", Call("mossel bay", 1850), ["towage_dues"], {"gt": "1,850", "services": "2"}, "terse",
     "coastal product carrier"),
    ("m10", "medium", Call("port elizabeth", 22750, stay_days=2.5), FULL, {"gt": "22,750", "days": "2.5"},
     "agent_email", "car carrier discharging vehicles, normal hours"),

    ("h01", "hard", Call("durban", 51300, stay_days=3.39, cargo_tonnes=40000, cargo_commodity="iron ore",
                         cargo_direction="export", cargo_category="dry_bulk"), FULL, {}, "reference",
     "brief reference vessel"),
    ("h02", "hard", Call("durban", 45200, stay_days=2, outside_hours=True), FULL,
     {"gt": "45,200", "days": "2"}, "agent_email",
     "arrives 23:30 on a Sunday and sails at 02:00, so every marine service is outside ordinary working hours"),
    ("h03", "hard", Call("durban", 3700, stay_days=3, government=True), FULL, {"gt": "3,700", "days": "3"},
     "conversational", "a South African Navy (SANDF) patrol vessel on a routine visit"),
    ("h04", "hard", Call("cape town", 62000, stay_days=1.25, cargo_working=False, bunkers_only=True), FULL,
     {"gt": "62,000", "hours": "30"}, "agent_email",
     "calls only to take bunkers and stores, no cargo, total stay 30 hours"),
    ("h05", "hard", Call("richards bay", 14300, stay_days=3, coaster=True), ["port_dues"],
     {"gt": "14,300", "days": "3"}, "conversational",
     "approved bona fide coaster running a regular schedule between SA ports, discharging cargo"),
    ("h06", "hard", Call("saldanha", 120000, stay_days=10 / 24, tanker=True), FULL,
     {"gt": "120,000", "hours": "10"}, "agent_email",
     "crude tanker discharging at the jetty; total time in port 10 hours; no Green Award or other certification"),
    ("h07", "hard", Call("cape town", 91000, stay_days=14 / 24, passenger=True), ["port_dues", "light_dues"],
     {"gt": "91,000", "hours": "14"}, "conversational", "cruise ship with 2,400 passengers on a 14 hour call"),
    ("h08", "hard", Call("ngqura", 27450, stay_days=3.0), FULL,
     {"gt": "27 450"}, "agent_email",
     "messy email from an agent with particulars inline (GT 27 450, NRT 14 200, LOA 199.9 m); "
     "arrival 10 Feb 2025 08:00, departure 13 Feb 2025 08:00; loading containers"),
    ("h09", "hard", Call("durban", 30000, stay_days=5, cargo_working=False), ["port_dues", "pilotage_dues"],
     {"gt": "30,000", "days": "5"}, "conversational",
     "vessel calling for crew change and minor repairs at a lay-by berth, no cargo work"),
    ("h10", "hard", Call("port elizabeth", 58600, stay_days=2, services=4), FULL,
     {"gt": "58,600", "days": "2"}, "json",
     "bulk carrier exporting manganese ore, shifts berth twice (4 marine services in total), normal hours"),
]

REFERENCE_QUESTION = """Calculate all the port dues payable for this vessel calling at Durban.
{
  "vessel_metadata": {"name": "SUDESTADA", "built_year": 2010, "flag": "MLT - Malta",
    "classification_society": "Registro Italiano Navale", "call_sign": null},
  "technical_specs": {"imo_number": null, "type": "Bulk Carrier", "dwt": 93274, "gross_tonnage": 51300,
    "net_tonnage": 31192, "loa_meters": 229.2, "beam_meters": 38.0, "moulded_depth_meters": 20.7,
    "lbp_meters": 222.0, "draft_sw_s_w_t": [14.9, 0.0, 0.0], "suez_gt": null, "suez_nt": 49069},
  "operational_data": {"cargo_quantity_mt": 40000, "days_alongside": 3.39,
    "arrival_time": "2024-11-15T10:12:00", "departure_time": "2024-11-22T13:00:00",
    "activity": "Exporting Iron Ore", "num_operations": 2, "num_holds": 7}
}"""

# values given in the brief; its "Running Lines" figure equals clause 3.8 berthing services.
# Cargo dues are not in the brief: §7.2 dry bulk, iron ore exports 10.65/t x 40,000 t.
REFERENCE_EXPECTED = {
    "light_dues": 60062.04, "port_dues": 199549.22, "towage_dues": 147074.38, "vts_dues": 33315.75,
    "pilotage_dues": 47189.94, "berthing_services": 19639.50, "cargo_dues": 426000.00,
}

# charges whose treatment the tariff leaves genuinely ambiguous for the scenario
UNSCORED = {"h03": ["pilotage_dues"]}  # SANDF pilotage exempt "except if performed on request"

STYLES = {
    "terse": "a short one-line question, like a busy operator typing into a chat box",
    "conversational": "a natural, friendly question of two or three sentences",
    "agent_email": "a short email from a ship agent asking for a proforma disbursement estimate",
    "json": "a sentence asking for the estimate followed by the vessel particulars as a small JSON object",
}

PHRASE_PROMPT = """Write one question a user would send to a port-tariff assistant. Style: {style}.
It must state the port, the vessel facts and the scenario below, every number exactly as written,
and ask for {ask}. Do not mention clause numbers, rates or the answer. Output only the question."""

NAMES = {
    "light_dues": "light dues", "port_dues": "port dues", "vts_dues": "VTS dues", "pilotage_dues": "pilotage",
    "towage_dues": "towage (tug) dues", "berthing_services": "berthing services", "running_lines": "running of lines",
}


def _digits(s: str) -> str:
    return re.sub(r"[^\d]", "", s)


async def phrase(spec) -> str:
    case_id, difficulty, call, charges, facts, style, notes = spec
    if style == "reference":
        return REFERENCE_QUESTION
    ask = "all the port charges payable for the call" if charges is None else \
        " and ".join(NAMES[c] for c in charges)
    details = {"port": call.port.title(), **facts, "scenario": notes}
    for attempt in range(4):
        msg = await chat_model().ainvoke([
            SystemMessage(PHRASE_PROMPT.format(style=STYLES[style], ask=ask)),
            HumanMessage(json.dumps(details)),
        ])
        text = msg.text.strip()
        flat = _digits(text)
        if all(_digits(v) in flat for v in facts.values() if _digits(v)):
            return text
    raise RuntimeError(f"{case_id}: could not phrase a question carrying every fact")


def label(case_id: str, style: str, call: Call, charges: list[str] | None) -> dict[str, float]:
    if style == "reference":
        return REFERENCE_EXPECTED
    exp = expected(call, charges)
    for key in UNSCORED.get(case_id, []):
        exp.pop(key, None)
    return exp


def relabel() -> None:
    """Recompute expected values for the existing questions (after fixing the reference)."""
    specs = {s[0]: s for s in SPECS}
    cases = [json.loads(line) for line in OUT.read_text().splitlines()]
    for case in cases:
        case_id, _, call, charges, _, style, _ = specs[case["id"]]
        case["expected"] = label(case_id, style, call, charges)
        case["call"] = asdict(call)
    OUT.write_text("".join(json.dumps(c) + "\n" for c in cases))
    print(f"relabelled {len(cases)} cases")


async def main() -> None:
    if "--relabel" in sys.argv:
        relabel()
        return
    questions = await asyncio.gather(*(phrase(s) for s in SPECS))
    with OUT.open("w") as fh:
        for spec, question in zip(SPECS, questions):
            case_id, difficulty, call, charges, facts, style, notes = spec
            exp = label(case_id, style, call, charges)
            fh.write(json.dumps({
                "id": case_id, "difficulty": difficulty, "question": question, "expected": exp,
                "currency": "ZAR", "port": call.port, "scenario": notes, "style": style,
                "call": asdict(call), "full_estimate": charges is None,
            }) + "\n")
    print(f"wrote {len(SPECS)} cases to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
