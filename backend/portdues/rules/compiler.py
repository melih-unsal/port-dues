"""Compile the document tree into an executable rulebook.

1. Catalogue: from the root and top-level digests, list every charge a vessel call can incur, the ports
   the tariff covers and the extra variables (beyond the core vocabulary) the document conditions on.
2. Compile: one ReAct agent per charge reads the relevant clauses and writes a `ChargeRule`. It has a
   `test_rule` tool that runs a draft rule through the engine, so it can check its work against rows of
   the document's own tables before answering. The final rule is validated statically and dry-run by the
   engine; problems go back to the agent until the rule runs, or the charge is recorded as uncovered.
"""

import asyncio
import json
import logging
from typing import Awaitable, Callable, Literal

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, ValidationError

from portdues.agents.tools import build_tools
from portdues.config import get_settings
from portdues.llm import chat_model, run_config
from portdues.rules.engine import (Env, Formula, RuleError, all_hold, evaluate_rule, validate_rule,
                                   with_rule_variables)
from portdues.rules.schema import CORE_VARIABLES, ChargeRule, Rulebook, UncoveredCharge, VariableSpec
from portdues.storage.db import get_db

log = logging.getLogger(__name__)

MAX_REPAIRS = 2
MAX_VERIFY_ROUNDS = 3
MAX_EXTRA_VARIABLES = 8


class CatalogueEntry(BaseModel):
    name: str
    sections: list[str] = Field(description="Clause numbers that define or modify the charge")
    payer: Literal["vessel", "cargo"]
    routine: bool = Field(description="True when it arises on an ordinary commercial call: entering, berthing, "
                                      "working cargo, leaving. False for on-request or situational services")
    computable: bool = Field(description="False only when the document says the amount is on application, "
                                         "quoted separately or set by another regulation")
    reason: str = Field(description="One line: when the charge arises")


class Catalogue(BaseModel):
    title: str
    ports: list[str] = Field(description="Every port this tariff prices, lower case, as named in the document "
                                         "(list both names when a port appears under two, e.g. a port and its bay)")
    currency: str = Field(description="ISO code, e.g. ZAR, EUR")
    tax_note: str = Field(description="How tax/VAT applies to the published fees, or empty")
    entries: list[CatalogueEntry]
    variables: list[VariableSpec] = Field(
        description="Extra facts about a call that the document conditions on and the core vocabulary lacks"
    )


CATALOGUE_PROMPT = """You are mapping a port tariff document before its charges are turned into formulas.

The input is the document's root overview and the overviews of its top-level sections. Overviews point to
clauses ("rates in [§x.y]") instead of repeating figures: a charge whose rates sit in a referenced clause IS
published. Mark computable false only when the document says the amount is on application, quoted
separately, or set by another body's regulations.

List the charges a vessel calling at a port covered by this document may have to pay, and the dues on the
cargo it loads or discharges. Mark routine the ones that arise on an ordinary commercial call (port/harbour
dues, light dues, pilotage, towage/tugs, berthing/mooring, line handling, VTS, berth dues, cargo dues ...).
Situational or on-request services (dry-docking, fire services, equipment hire, utilities, passengers'
baggage handling ...) are listed with routine false. Leave out licences, permits, training, penalties for
late paperwork and similar administrative fees.

Merge a charge that is split over several clauses into one entry listing all its clauses. But when one
charge has a separate rate table per category (for example cargo dues with one table for dry bulk, one for
liquid bulk, one for breakbulk and one for containers), make one entry per table, so that each rule stays
small enough to get exactly right.

Core variables already available to formulas:
{core}

Add `variables` only for facts that change the amount of a routine charge on a commercial call and that
the core vocabulary cannot express (e.g. a certification that grants a reduction): at most {max_vars}, fewer
is better. Every variable becomes a question the system has to answer for each query. Use snake_case
names, a type and a default that represents the common case."""

COMPILE_PROMPT = """You turn one charge from a port tariff document into an executable rule.

How to work
1. Read the clauses with read_section (verbatim text and tables) and look for conditions stated elsewhere
   with search_tariff (exemptions, surcharges, reductions, minimums, the definition of the basis).
2. Copy every rate exactly from the verbatim text. Never invent, average or round a figure.
3. Write the complete rule, then check it with test_rule before answering. Test at least three calls taken
   from the document itself: a row of each rate table (a listed port, band or commodity), a band edge, and
   a case where an exemption or surcharge applies if the rule has one. Work out the expected amount from the
   printed rates with calculator and compare. Fix the rule and test again until every test agrees.
4. Answer with the final rule only once all tests agree.

The rule language
- Variables: {variables}
- Formulas are arithmetic expressions over variables and the rule's constants/tables. Helpers:
  units(x, size)          -> ceil(x/size); use for "per <size> or part thereof"
  above(x, threshold)     -> max(0, x - threshold)
  band(table, x, name)    -> value `name` from the numeric band of `table` that contains x
  lookup(table, key, name)-> value `name` from the row of a keyed `table` whose key matches `key`
                             (e.g. a commodity), or from the row keyed "other" when none matches
  ceil, floor, round, min, max, abs; comparisons, `x in [...]` and `a if cond else b` are allowed.
- Variants are tried in order; the first whose `when` conditions all hold is used. Put the general
  case last with an empty `when`. Use one variant per port (or port group) when rates differ by port.
- Conditions compare a variable with a value. Port names are matched case-insensitively; use the port
  names listed above, every spelling a port appears under, with op "in" (e.g. ["port elizabeth", "ngqura"]).
- `adjustments` are conditional percentage surcharges/reductions applied after the formula.
  `exemptions` zero the charge. Both need at least one condition.
- `minimum`/`maximum` clamp the formula result before adjustments.

Tables
- Numeric bands (tonnage ranges): one band per printed row with lower/upper set and key null. For
  "plus X per 100 tons above N", store N in the band as a value and use above(x, band(...)).
- Rates listed per named item (commodity, cargo type, container size): a keyed table with one row per
  printed item, `key` set to the item name exactly as printed, lower/upper null, and one value per column
  (e.g. "imports" and "exports"). Add a row keyed "other" with the rate the document gives for unlisted
  items. Read it with lookup(), using one variant per column, e.g. a variant for cargo_direction "export"
  with formula cargo_tonnes * lookup('dry_bulk', cargo_commodity, 'exports').
- Never collapse a table into one number, and never write `a if cond else a`: if the document prints
  different rates, the rule must produce them.

Modelling guidance
- Price a normal commercial call. Charges quoted "on application" cannot be modelled; say so in notes.
- Only multiply by a quantity when the text prices the fee per that quantity (per service, per day,
  per tug ...). Use num_services for anything charged per service/movement.
- Time-based fees: stay_days for pro-rata day rates, units(stay_hours, 24) for "per 24 hours or part".
  A fee charged per hour "during the stay" or "while in port" is priced on stay_hours; only fees for part of
  the stay (hours worked, hours waiting, hours on standby) need their own input.
- Never drop a qualifier. If a rate, reduction or exemption applies only to some vessels (e.g. "self-propelled
  vessels ... at their registered port", "tankers certified double hulled or holding a Green Award") keep every
  part of that condition, using the variables above (registered_at_port, is_small_vessel ...). When the
  vocabulary has no variable for a qualifier, declare one in the rule's `variables` (snake_case, boolean or
  number, with the default for the common case, usually false) and use it in the condition. Check the
  vocabulary first: SAPS, SANDF, naval, police and maritime-authority vessels are is_government_vessel;
  coasters are is_coaster; bunkers/stores-only calls are call_purpose. Never declare a variable for those.
- When the document says a case is priced by special agreement or on application (e.g. for coasters), give
  that case its own variant with the formula `on_application`: the engine then asks for the amount
  instead of applying a published rate that does not belong to the case.
- Work through the "Facts found in these clauses" list in the brief: every rate, surcharge, reduction,
  exemption, minimum and extra fee there must be modelled (or explained in notes) and covered by a test_rule
  call. Extra fees for particular vessels or ports (e.g. hourly attendance for tankers) are added terms.
- When the amount depends on a quantity the call may not state (hours worked, number of tugs used ...),
  reference that variable instead of substituting a proxy: the engine reports "needs input" rather than
  guessing. Never price the whole stay when the tariff charges only part of it.
- Cite clause and page in each constant's `source`.

Example of the shape (fictitious tariff, not from your document):
{example}"""

EXAMPLE_RULE = {
    "id": "wharfage", "name": "Wharfage", "section": "5.2", "payer": "cargo",
    "description": "Per tonne of cargo by commodity and direction; Northport adds a flat security levy.",
    "applies_when": [{"field": "cargo_category", "op": "eq", "value": "dry_bulk"}],
    "constants": [{"name": "northport_levy", "value": 250.0, "source": "§5.2, p.14"}],
    "tables": [{
        "name": "wharfage_rates", "description": "Rate per tonne by commodity (§5.2 table)",
        "bands": [
            {"key": "grain", "lower": None, "upper": None, "values": [
                {"name": "imports", "value": 5.1, "source": "§5.2"}, {"name": "exports", "value": 4.2, "source": "§5.2"}]},
            {"key": "iron ore", "lower": None, "upper": None, "values": [
                {"name": "imports", "value": 7.0, "source": "§5.2"}, {"name": "exports", "value": 3.3, "source": "§5.2"}]},
            {"key": "other", "lower": None, "upper": None, "values": [
                {"name": "imports", "value": 6.0, "source": "§5.2"}, {"name": "exports", "value": 6.0, "source": "§5.2"}]},
        ],
    }],
    "variants": [
        {"label": "Exports", "when": [{"field": "cargo_direction", "op": "eq", "value": "export"}],
         "formula": "cargo_tonnes * lookup('wharfage_rates', cargo_commodity, 'exports')"
                    " + (northport_levy if port == 'northport' else 0)"},
        {"label": "Imports", "when": [{"field": "cargo_direction", "op": "eq", "value": "import"}],
         "formula": "cargo_tonnes * lookup('wharfage_rates', cargo_commodity, 'imports')"
                    " + (northport_levy if port == 'northport' else 0)"},
    ],
    "minimum": None, "maximum": None,
    "adjustments": [],
    "exemptions": [{"description": "Bunkers for the vessel's own use",
                    "when": [{"field": "call_purpose", "op": "eq", "value": "bunkers"}]}],
    "notes": "",
}

class CallFact(BaseModel):
    name: str = Field(description="A variable name from the vocabulary")
    value: str | float | bool


class AcceptanceCase(BaseModel):
    description: str = Field(description="One line, e.g. 'typical 51,300 GT bulk carrier at Durban, 2 services'")
    call: list[CallFact] = Field(description="Every fact the amount depends on")
    applies: bool = Field(description="False when the charge does not arise for this call")
    expected: str = Field(description="The amount the document prescribes, as an arithmetic expression over the "
                                      "printed rates, e.g. 'units(51300, 100) * 117.08'; '0' when exempt or not applicable")
    basis: str = Field(description="Which printed rate or rule the expected amount comes from")


class AcceptanceSuite(BaseModel):
    cases: list[AcceptanceCase]


VERIFY_PROMPT = """You write acceptance tests for one charge of a port tariff, working only from the clause text.
You do not see the rule that will be tested, so read the text carefully and independently.

Write 4 to 6 vessel calls and, for each, the amount the document prescribes, as an arithmetic expression over the
printed rates (helpers: units(x, size) = ceil(x/size) for "per size or part thereof", above(x, t), min, max,
round). Always include:
- a typical foreign-going commercial cargo ship: a 30,000-60,000 GT bulk carrier on a normal call at one of the
  tariff's ports, self_propelled true, registered_at_port false, not a coaster or small craft. Price it with the
  rate the document gives for ordinary vessels, even if the charge does not apply to it;
- calls that exercise different ports, tonnage bands or listed items (commodities, sizes) the clause prices;
- a call for each surcharge, reduction or exemption the clause states, and for each additional fee it attaches
  to particular vessels or ports (e.g. an hourly attendance fee for tankers at one port).
Give every fact the amount depends on (port, gt, loa, num_services, stay_days, stay_hours, cargo_tonnes,
cargo_commodity, cargo_direction, cargo_category, flags such as outside_working_hours or is_government_vessel).
If the charge does not arise for a call, set applies to false and expected to "0".

Variables: {variables}"""


ProgressFn = Callable[[str, float], Awaitable[None]]


def _vocabulary(specs: list[VariableSpec]) -> str:
    return "\n".join(f"  {v.name} ({v.type}): {v.description}" for v in specs)


def _variables_block(specs: list[VariableSpec], ports: list[str]) -> str:
    text = "\n" + _vocabulary(specs)
    if ports:
        text += "\n- Ports in this tariff (use exactly these names in port conditions): " + ", ".join(ports)
    return text


async def build_catalogue(doc_id: str) -> Catalogue:
    db = get_db()
    sections = await db.nodes(doc_id, kind="section")
    root = next(n for n in sections if n["section"] == "")
    top = [n for n in sections if n["meta"].get("depth") == 1]
    body = "\n\n".join([root["text"]] + [f"## §{n['section']} {n['title']}\n{n['text']}" for n in top])
    llm = chat_model("compiler").with_structured_output(Catalogue)
    return await llm.ainvoke(
        [SystemMessage(CATALOGUE_PROMPT.format(core=_vocabulary(CORE_VARIABLES), max_vars=MAX_EXTRA_VARIABLES)),
         HumanMessage(body)],
        config=run_config("build_catalogue", session_id=doc_id, tags=["compile"]),
    )


def make_test_tool(specs: list[VariableSpec], ports: list[str]) -> BaseTool:
    @tool
    def test_rule(rule_json: str, call_json: str) -> str:
        """Run a draft rule through the tariff engine for one vessel call, before you submit it.

        rule_json: the complete ChargeRule as JSON.
        call_json: variable values for the call, e.g. {"port": "durban", "gt": 51300, "num_services": 2,
                   "cargo_commodity": "iron ore", "cargo_direction": "export", "cargo_tonnes": 1000}.
        Returns validation problems, or the amount with the variant, steps and assumptions, so you can
        compare it with the amount you work out by hand from the printed rates.
        """
        try:
            rule = ChargeRule.model_validate_json(rule_json)
        except ValidationError as exc:
            return f"rule_json is not a valid ChargeRule: {exc}"[:1500]
        try:
            values = json.loads(call_json)
        except json.JSONDecodeError as exc:
            return f"call_json is not valid JSON: {exc}"
        try:
            problems = validate_rule(rule, specs, ports)
            line = evaluate_rule(rule, values, specs)
        except Exception as exc:  # never let a bad draft kill the agent; tell it what broke
            return f"the engine could not run this rule: {type(exc).__name__}: {exc}"[:1500]
        return json.dumps({
            "validation_problems": problems, "status": line.status, "amount": line.amount,
            "variant": line.variant, "steps": line.steps, "reason": line.reason, "assumptions": line.assumptions,
        })

    return test_rule


# everything that can change an amount; definitions and procedure are left out to keep the brief short
_CHECKLIST_CATEGORIES = {"rate", "basis", "surcharge", "reduction", "exemption", "minimum", "condition"}


async def _brief(doc_id: str, entry: CatalogueEntry) -> str:
    """The charge, the digests of its clauses, and a checklist of the conditions extracted from them."""
    db = get_db()
    context, checklist = [], []
    for key in entry.sections:
        node = await db.section_node(doc_id, key)
        if not node:
            continue
        context.append(f"### Digest of §{key} {node['title']}\n{node['text']}")
        for fact in await db.children(node["id"], kind="fact"):
            if fact["meta"].get("category") in _CHECKLIST_CATEGORIES:
                checklist.append(f"- [§{key}, {fact['meta']['category']}] {fact['text']}")
    brief = (f"Charge: {entry.name}\nPayer: {entry.payer}\nClauses: {', '.join(entry.sections)}\n"
             f"When it arises: {entry.reason}\n\n" + "\n\n".join(context))
    if checklist:
        brief += ("\n\n## Facts found in these clauses\nEach rate, condition and extra fee must end up in the rule "
                  "(a table row, variant, adjustment, exemption, minimum or an added term in the formula) or, if it "
                  "belongs to another charge or cannot be modelled, be mentioned in notes. Verify against the "
                  "verbatim text.\n" + "\n".join(checklist[:80]))
    return brief


def _repair_note(problems: list[str]) -> str:
    return ("The rule failed validation:\n- " + "\n- ".join(problems)
            + "\nFix these problems, check the result with test_rule, and return the complete corrected rule.")


async def _acceptance_cases(doc_id: str, entry: CatalogueEntry, specs: list[VariableSpec],
                            ports: list[str]) -> list[AcceptanceCase]:
    """Independent tests for a charge, written from the verbatim clauses without seeing the rule."""
    db = get_db()
    text = []
    for key in entry.sections:
        node = await db.section_node(doc_id, key)
        if node:
            sources = await db.children(node["id"], kind="source")
            text.append(f"## §{key} {node['title']}\n" + "\n\n".join(s["text"] for s in sources))
    if not text:
        return []
    suite: AcceptanceSuite = await chat_model("compiler").with_structured_output(AcceptanceSuite).ainvoke(
        [SystemMessage(VERIFY_PROMPT.format(variables=_variables_block(specs, ports))),
         HumanMessage(f"Charge: {entry.name}\n\n" + "\n\n".join(text)[:20000])],
        config=run_config(f"acceptance:{entry.name}", session_id=doc_id, tags=["compile", "verify"]),
    )
    return suite.cases


def _failed_cases(rule: ChargeRule, cases: list[AcceptanceCase], specs: list[VariableSpec]) -> list[str]:
    failures, usable = [], 0
    for case in cases:
        values = {f.name: f.value for f in case.call}
        try:
            # the expected amount may use the call's own facts, e.g. units(gt, 100) * 117.08
            expected = Formula(case.expected).evaluate(values, {}) if case.applies else 0.0
        except (RuleError, ZeroDivisionError, TypeError, ValueError) as exc:
            log.info("acceptance case for %s unusable (%s): %s", rule.id, exc, case.expected)
            continue
        usable += 1
        line = evaluate_rule(rule, values, specs)
        got = line.amount if line.status == "charged" else 0.0 if line.status in ("exempt", "not_applicable") else None
        if got is None or abs(got - expected) > max(0.01 * abs(expected), 0.01):
            shown = f"{got:,.2f}" if got is not None else f"{line.status} ({line.reason})"
            failures.append(f"- {case.description}: the document gives {expected:,.2f} ({case.basis}; "
                            f"{case.expected}), your rule gives {shown}. Call: {json.dumps(values)}")
    log.info("acceptance %s: %d cases, %d usable, %d disagree", rule.id, len(cases), usable, len(failures))
    return failures


_HOLE_TONNAGES = [1500, 8750, 33100, 51300, 101800]


def _port_holes(rule: ChargeRule, specs: list[VariableSpec], ports: list[str]) -> list[str]:
    """Calls where the rule prices most ports at a tonnage but returns nothing for one of them. That is
    usually a dropped table cell, occasionally a genuine "n/a" in the document; the agent decides which."""
    if len(ports) < 3:
        return []
    holes = []
    for gt in _HOLE_TONNAGES:
        priced, missing = 0, []
        for port in ports:
            values = {"port": port, "gt": gt, "loa": 200, "stay_days": 2.5, "stay_hours": 60, "num_services": 2}
            line = evaluate_rule(rule, values, specs)
            if line.status == "charged":
                priced += 1
            elif line.status in ("error", "needs_input"):
                missing.append(f"{port} at {gt:,} GT ({line.reason})")
        if missing and priced >= len(ports) / 2:
            holes += missing
    return holes


async def compile_charge(doc_id: str, entry: CatalogueEntry, specs: list[VariableSpec],
                         ports: list[str] | None = None) -> tuple[ChargeRule, int, int]:
    """Compile one charge; returns the rule, the repair rounds it needed and the acceptance cases it still fails.

    After the rule validates, an independent acceptance suite (written from the verbatim clauses by a
    separate call that never sees the rule) is run through the engine. Disagreements go back to the agent,
    which must either fix the rule or explain in notes why the test is wrong."""
    ports = ports or []
    system = COMPILE_PROMPT.format(variables=_variables_block(specs, ports),
                                   example=json.dumps(EXAMPLE_RULE, separators=(",", ":")))
    agent = create_agent(
        chat_model("compiler"), [*build_tools(doc_id), make_test_tool(specs, ports)], system_prompt=system,
        response_format=ChargeRule, name="rule_compiler",
    )
    config = run_config(f"compile:{entry.name}", session_id=doc_id, tags=["compile"])
    config["recursion_limit"] = get_settings().agent_max_steps * 2
    cases_task = asyncio.create_task(_acceptance_cases(doc_id, entry, specs, ports))
    messages = [HumanMessage(await _brief(doc_id, entry))]
    repairs, verify_rounds, checked_holes = 0, 0, False
    while True:
        state = await agent.ainvoke({"messages": messages}, config=config)
        rule: ChargeRule = state["structured_response"]
        problems = validate_rule(rule, specs, ports)
        if problems:
            log.info("rule %s repair %d: %s", rule.id, repairs, problems)
            if repairs >= MAX_REPAIRS:
                raise ValueError("; ".join(problems))
            repairs += 1
            messages = state["messages"] + [HumanMessage(_repair_note(problems))]
            continue
        try:
            failures = _failed_cases(rule, await cases_task, specs)
        except Exception:
            log.exception("acceptance tests for %s could not be built", entry.name)
            failures = []
        if not failures and not checked_holes:
            checked_holes = True
            holes = _port_holes(rule, specs, ports) + _shadowed_variants(rule, specs, ports)
            if holes:
                verify_rounds += 1
                log.info("rule %s has %d port holes", rule.id, len(holes))
                messages = state["messages"] + [HumanMessage(
                    "Probing your rule over the tariff's ports and common calls found:\n- " + "\n- ".join(holes)
                    + "\nRe-read the document. Fix what the text contradicts (missing table cells, variant order); "
                      "where the document really prints n/a or intends this behaviour, keep it and say so in notes. "
                      "Check with test_rule and return the complete rule."
                )]
                continue
        if not failures or verify_rounds >= MAX_VERIFY_ROUNDS:
            if failures:
                log.info("rule %s still disagrees with %d acceptance cases", rule.id, len(failures))
                rule.notes = (rule.notes + " Unresolved acceptance checks: " + " ".join(failures))[:2000].strip()
            return rule, repairs + verify_rounds, len(failures)
        verify_rounds += 1
        log.info("rule %s verify round %d: %d disagreements", rule.id, verify_rounds, len(failures))
        messages = state["messages"] + [HumanMessage(
            "An independent reading of the clause produced these test calls, and your rule disagrees with them:\n"
            + "\n".join(failures)
            + "\nRe-read the verbatim text. Where the test is right, fix the rule; where the test misreads the "
              "document, keep the rule and say why in notes. Check with test_rule and return the complete rule."
        )]


_PROBE_SITUATIONS = {
    "normal": {},
    "outside hours": {"outside_working_hours": True},
    "government": {"is_government_vessel": True},
    "coaster": {"is_coaster": True},
    "tanker, short stay": {"is_tanker": True, "vessel_type": "tanker", "cargo_category": "liquid_bulk",
                           "stay_days": 10 / 24, "stay_hours": 10},
    "bunkers only": {"call_purpose": "bunkers", "cargo_working": False, "stay_days": 1.25, "stay_hours": 30},
    "repairs, no cargo": {"call_purpose": "repairs", "cargo_working": False, "stay_days": 5, "stay_hours": 120},
    "passenger vessel": {"is_passenger_vessel": True, "vessel_type": "passenger"},
}
_CARGO_CATEGORIES = ["dry_bulk", "liquid_bulk", "breakbulk", "container"]


def _probes(rules: list[ChargeRule], ports: list[str]) -> list[dict]:
    """A grid of calls built from the tariff itself: its ports, tonnage bands, common situations, and the
    named rows (e.g. commodities) of the rules' keyed tables."""
    probes = []
    for port in ports or [""]:
        for gt in _HOLE_TONNAGES:
            for extra in _PROBE_SITUATIONS.values():
                probes.append({"port": port, "gt": gt, "loa": 200, "stay_days": 2.5, "stay_hours": 60,
                               "num_services": 2, **extra})
    keys = {b.key for r in rules for t in r.tables for b in t.bands if b.key}
    for key in sorted(keys)[:40]:
        for category in _CARGO_CATEGORIES:
            for direction in ("import", "export"):
                probes.append({"port": (ports or [""])[0], "gt": 30000, "stay_days": 2, "stay_hours": 48,
                               "num_services": 2, "cargo_tonnes": 1000, "teu": 100, "cargo_commodity": key,
                               "cargo_category": category, "cargo_direction": direction})
    return probes


def _outcomes(rule: ChargeRule, probes: list[dict], specs: list[VariableSpec]) -> list[float | None]:
    out = []
    for values in probes:
        line = evaluate_rule(rule, values, specs)
        out.append(line.amount if line.status == "charged" else 0.0 if line.status in ("exempt", "not_applicable")
                   else None)
    return out


def _agree(a: list[float | None], b: list[float | None]) -> float:
    same = sum(1 for x, y in zip(a, b) if (x is None and y is None) or
               (x is not None and y is not None and abs(x - y) <= max(0.01 * abs(y), 0.01)))
    return same / max(len(a), 1)


def _shadowed_variants(rule: ChargeRule, specs: list[VariableSpec], ports: list[str]) -> list[str]:
    """Variants that never get used: on every probe where their conditions hold, an earlier variant wins.
    Usually a precedence mistake, e.g. a specific reduction listed after a general one."""
    usage: dict[int, list[int]] = {}
    specs = with_rule_variables(rule, specs)
    for values in _probes([rule], ports):
        chosen = None
        for i, v in enumerate(rule.variants):
            if all_hold(v.when, Env(values, specs)):
                if chosen is None:
                    chosen = i
                usage.setdefault(i, []).append(chosen)
    problems = []
    for i, chosen_list in usage.items():
        if i > 0 and rule.variants[i].when and all(c != i for c in chosen_list):
            winner = rule.variants[max(set(chosen_list), key=chosen_list.count)].label
            problems.append(f"variant '{rule.variants[i].label}' is never used: whenever it applies, the earlier "
                            f"variant '{winner}' is chosen first. If the document means it to take precedence (e.g. "
                            "reductions that do not stack, where this one should win), move it earlier")
    return problems


async def compile_with_consensus(doc_id: str, entry: CatalogueEntry, specs: list[VariableSpec],
                                 ports: list[str]) -> tuple[ChargeRule, int]:
    """Self-consistency: compile independently, keep the rule the other samples agree with.

    Two samples are compiled in parallel and priced on a probe grid. If they agree everywhere, the first
    is kept; otherwise a third sample breaks the tie and the candidate agreeing most with the others wins.
    Independent runs of a small model rarely make the same mistake, so this removes most of the
    run-to-run variance at roughly twice the compile cost."""
    samples = max(1, get_settings().compile_samples)
    results = await asyncio.gather(*(compile_charge(doc_id, entry, specs, ports) for _ in range(min(samples, 2))),
                                   return_exceptions=True)
    candidates = [r for r in results if not isinstance(r, BaseException)]
    if not candidates:
        raise results[0]
    if samples < 2 or len(candidates) == 1:
        return candidates[0][:2]
    probes = _probes([c[0] for c in candidates], ports)
    outcomes = [_outcomes(c[0], probes, specs) for c in candidates]
    if _agree(outcomes[0], outcomes[1]) == 1.0:
        return min(candidates, key=lambda c: c[2])[:2]
    log.info("%s: samples disagree on %.0f%% of probes, compiling a tie-breaker", entry.name,
             100 * (1 - _agree(outcomes[0], outcomes[1])))
    try:
        third = await compile_charge(doc_id, entry, specs, ports)
        candidates.append(third)
        probes = _probes([c[0] for c in candidates], ports)
        outcomes = [_outcomes(c[0], probes, specs) for c in candidates]
    except Exception:
        log.exception("tie-breaker for %s failed", entry.name)
    # agreement with the other samples, minus a penalty for each independent acceptance case still failed
    scores = [sum(_agree(o, other) for j, other in enumerate(outcomes) if j != i) - 0.25 * candidates[i][2]
              for i, o in enumerate(outcomes)]
    best = max(range(len(candidates)), key=lambda i: scores[i])
    log.info("%s: kept sample %d of %d (scores %s, unresolved acceptance cases %s)", entry.name, best + 1,
             len(candidates), [round(x, 3) for x in scores], [c[2] for c in candidates])
    return candidates[best][:2]


async def compile_rulebook(doc_id: str, progress: ProgressFn) -> Rulebook:
    await progress("Cataloguing charges", 0.0)
    catalogue = await build_catalogue(doc_id)
    rulebook = Rulebook(title=catalogue.title, currency=catalogue.currency, tax_note=catalogue.tax_note,
                        variables=catalogue.variables[:MAX_EXTRA_VARIABLES],
                        ports=[p.strip().lower() for p in catalogue.ports if p.strip()])
    specs = rulebook.all_variables()
    # Only routine charges get a compiler agent; the rest are recorded as not compiled, which keeps
    # compile-time tokens proportional to what an ordinary call actually needs.
    todo = [e for e in catalogue.entries if e.computable and e.routine]
    rulebook.uncovered = [
        UncoveredCharge(name=e.name, section=", ".join(e.sections),
                        reason="amount not published" if not e.computable else f"situational: {e.reason}")
        for e in catalogue.entries if not (e.computable and e.routine)
    ]
    gate = asyncio.Semaphore(max(2, get_settings().llm_concurrency // 2))
    done = repairs = 0

    async def one(entry: CatalogueEntry) -> None:
        nonlocal done, repairs
        async with gate:
            try:
                rule, attempts = await compile_with_consensus(doc_id, entry, specs, rulebook.ports)
                repairs += attempts
                rulebook.charges.append(rule)
            except Exception as exc:
                log.exception("could not compile %s", entry.name)
                rulebook.uncovered.append(UncoveredCharge(
                    name=entry.name, section=", ".join(entry.sections), reason=f"compilation failed: {exc}"[:300]
                ))
        done += 1
        await progress(f"Compiled {done}/{len(todo)} charges ({entry.name})", done / max(len(todo), 1))

    await asyncio.gather(*(one(e) for e in todo))
    order = {e.name: i for i, e in enumerate(catalogue.entries)}
    rulebook.charges.sort(key=lambda r: order.get(r.name, len(order)))
    _dedupe_ids(rulebook)
    rulebook.compiled_with = {
        "model": get_settings().compiler_model, "samples": get_settings().compile_samples,
        "rules": len(rulebook.charges),
        "attempted": len(todo), "repairs": repairs, "failed": len(todo) - len(rulebook.charges),
    }
    return rulebook


def _dedupe_ids(rulebook: Rulebook) -> None:
    seen: set[str] = set()
    for rule in rulebook.charges:
        base, n = rule.id, 2
        while rule.id in seen:
            rule.id = f"{base}_{n}"
            n += 1
        seen.add(rule.id)


async def recompile_charges(doc_id: str, rule_ids: list[str], progress: ProgressFn) -> Rulebook:
    """Recompile selected rules in place, e.g. after a vocabulary change, without paying for the rest."""
    body = await get_db().get_rulebook(doc_id)
    if body is None:
        raise ValueError("no rulebook to update")
    rulebook = Rulebook.model_validate(body)
    for i, rule_id in enumerate(rule_ids):
        old = rulebook.charge(rule_id)
        if old is None:
            raise ValueError(f"unknown rule {rule_id}")
        await progress(f"Recompiling {old.name}", i / len(rule_ids))
        # the vocabulary must not include the variables of the rule being replaced, or the new rule could
        # rely on them without declaring them and lose them once the old rule is gone
        others = rulebook.model_copy(update={"charges": [r for r in rulebook.charges if r.id != old.id]})
        specs = others.all_variables()
        entry = CatalogueEntry(name=old.name, sections=[x.strip() for x in old.section.split(",")], payer=old.payer,
                               routine=True, computable=True, reason=old.description)
        new, _ = await compile_with_consensus(doc_id, entry, specs, rulebook.ports)
        new.id = old.id
        rulebook.charges = [new if r.id == old.id else r for r in rulebook.charges]
    return rulebook
