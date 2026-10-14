import pytest

from portdues.rules.engine import Formula, RuleError, evaluate_rule, quote, validate_rule
from portdues.rules.schema import (
    CORE_VARIABLES,
    VariableSpec,
    Adjustment,
    Band,
    ChargeRule,
    Condition,
    Exemption,
    NamedValue,
    RateTable,
    Rulebook,
    Variant,
)

REFERENCE = {"port": "Durban", "gt": 51300, "stay_days": 3.39, "num_services": 2}


def rule(**kw) -> ChargeRule:
    base = dict(id="x", name="X", section="1", description="", applies_when=[], constants=[], tables=[],
                variants=[], adjustments=[], exemptions=[])
    return ChargeRule(**{**base, **kw})


def towage() -> ChargeRule:
    bands = [
        Band(lower=0, upper=2000, values=[NamedValue(name="base", value=8140.00), NamedValue(name="inc", value=0),
                                          NamedValue(name="floor", value=0)]),
        Band(lower=2001, upper=10000, values=[NamedValue(name="base", value=12633.99),
                                              NamedValue(name="inc", value=268.99), NamedValue(name="floor", value=2000)]),
        Band(lower=10001, upper=50000, values=[NamedValue(name="base", value=38494.51),
                                               NamedValue(name="inc", value=84.95), NamedValue(name="floor", value=10000)]),
        Band(lower=50001, upper=100000, values=[NamedValue(name="base", value=73118.07),
                                                NamedValue(name="inc", value=32.24), NamedValue(name="floor", value=50000)]),
    ]
    return rule(
        id="towage", name="Towage", tables=[RateTable(name="dbn", description="", bands=bands)],
        variants=[Variant(label="Durban", when=[Condition(field="port", op="in", value=["durban"])],
                          formula="(band('dbn', gt, 'base') + band('dbn', gt, 'inc')"
                                  " * units(above(gt, band('dbn', gt, 'floor')), 100)) * num_services")],
        adjustments=[Adjustment(name="after hours", kind="surcharge", percent=25,
                                when=[Condition(field="outside_working_hours", op="is_true")])],
    )


def test_units_rounds_part_blocks_up():
    assert Formula("units(51300, 100)").evaluate({}, {}) == 513
    assert Formula("units(51301, 100)").evaluate({}, {}) == 514
    assert Formula("units(0, 100)").evaluate({}, {}) == 0


def test_reference_vessel_light_and_port_dues():
    light = rule(constants=[NamedValue(name="rate", value=117.08)],
                 variants=[Variant(label="all", when=[], formula="units(gt, 100) * rate")])
    port = rule(constants=[NamedValue(name="basic", value=192.73), NamedValue(name="daily", value=57.79)],
                variants=[Variant(label="all", when=[], formula="units(gt, 100) * (basic + daily * stay_days)")])
    assert evaluate_rule(light, REFERENCE, CORE_VARIABLES).amount == 60062.04
    assert evaluate_rule(port, REFERENCE, CORE_VARIABLES).amount == pytest.approx(199371.35, abs=0.01)


def test_banded_table_and_port_matching():
    line = evaluate_rule(towage(), {**REFERENCE, "port": "Port of Durban"}, CORE_VARIABLES)
    assert line.status == "charged"
    assert line.amount == 147074.38


def test_adjustment_applies_only_when_condition_holds():
    line = evaluate_rule(towage(), {**REFERENCE, "outside_working_hours": True}, CORE_VARIABLES)
    assert line.amount == pytest.approx(147074.38 * 1.25, abs=0.01)
    assert any("after hours" in s for s in line.steps)


def test_unmatched_port_is_not_applicable():
    line = evaluate_rule(towage(), {**REFERENCE, "port": "Cape Town"}, CORE_VARIABLES)
    assert line.status == "not_applicable"


def test_missing_port_asks_instead_of_falling_back_to_other_ports():
    r = rule(variants=[Variant(label="Durban", when=[Condition(field="port", op="in", value=["durban"])], formula="gt * 0.65"),
                       Variant(label="Other ports", when=[], formula="gt * 0.54")])
    line = evaluate_rule(r, {"gt": 51300}, CORE_VARIABLES)
    assert line.status == "needs_input" and "port" in line.reason
    assert evaluate_rule(r, {"gt": 51300, "port": "Cape Town"}, CORE_VARIABLES).amount == 27702.0


def test_port_names_must_exist_and_qualifiers_still_match():
    bad = rule(applies_when=[Condition(field="port", op="in", value=["south african port"])],
               variants=[Variant(label="all", when=[], formula="gt")])
    problems = validate_rule(bad, CORE_VARIABLES, ports=["durban", "saldanha bay"])
    assert any("south african port" in p for p in problems)
    good = rule(variants=[Variant(label="Saldanha", when=[Condition(field="port", op="in", value=["saldanha bay"])],
                                  formula="gt * 0.65"), Variant(label="other", when=[], formula="gt * 0.54")])
    assert validate_rule(good, CORE_VARIABLES, ports=["durban", "saldanha bay"]) == []
    assert evaluate_rule(good, {"gt": 1000, "port": "Saldanha"}, CORE_VARIABLES).amount == 650
    cargo = rule(applies_when=[Condition(field="cargo_category", op="eq", value="container")],
                 variants=[Variant(label="all", when=[], formula="teu * 10")])
    assert validate_rule(cargo, CORE_VARIABLES, ports=["durban"]) == []


def test_flattened_rates_are_rejected():
    flat = rule(tables=[RateTable(name="dry_bulk", description="", bands=[
                    Band(lower=1, upper=1, values=[NamedValue(name="exports", value=10.65)])])],
                variants=[Variant(label="dry bulk", when=[], formula="cargo_tonnes * (18.49 if cargo_direction == 'import' else 18.49)")])
    problems = validate_rule(flat, CORE_VARIABLES)
    assert any("same value" in p for p in problems)
    assert any("no formula reads it" in p for p in problems)


def test_defaults_are_reported_as_assumptions():
    line = evaluate_rule(towage(), {"port": "durban", "gt": 51300}, CORE_VARIABLES)
    assert line.amount == 147074.38
    assert "num_services not given, assumed 2" in line.assumptions


def test_missing_input_is_reported_not_guessed():
    r = rule(variants=[Variant(label="all", when=[], formula="loa * 24.64")])
    line = evaluate_rule(r, {"gt": 1000}, CORE_VARIABLES)
    assert line.status == "needs_input" and "loa" in line.reason


def test_unknown_category_matches_nothing_but_missing_numbers_still_block():
    r = rule(variants=[Variant(label="all", when=[],
                               formula="gt * (0.9 if cargo_category == 'container' else 1)")])
    assert evaluate_rule(r, {"gt": 1000}, CORE_VARIABLES).amount == 1000
    scoped = rule(applies_when=[Condition(field="port", op="eq", value="durban")],
                  variants=[Variant(label="all", when=[], formula="gt")])
    assert evaluate_rule(scoped, {"gt": 1000}, CORE_VARIABLES).status == "needs_input"


def test_exemption_and_minimum():
    r = rule(constants=[NamedValue(name="rate", value=0.65)],
             variants=[Variant(label="all", when=[], formula="gt * rate")], minimum=235.52,
             exemptions=[Exemption(description="naval", when=[Condition(field="is_government_vessel", op="is_true")])])
    assert evaluate_rule(r, {"gt": 100}, CORE_VARIABLES).amount == 235.52
    assert evaluate_rule(r, {"gt": 100, "is_government_vessel": True}, CORE_VARIABLES).status == "exempt"


def test_membership_against_list_literals():
    f = Formula("1.5 if port in ['durban', 'saldanha'] else 1")
    assert f.evaluate({"port": "Port of Durban"}, {}) == 1.5
    assert f.evaluate({"port": "Cape Town"}, {}) == 1


@pytest.mark.parametrize("src", ["__import__('os')", "gt.real", "{1: 2}", "open('x')", "lambda: 1", "[x for x in y]"])
def test_formulas_reject_anything_but_arithmetic(src):
    with pytest.raises(RuleError):
        Formula(src)


def test_validation_catches_unknown_names_and_tables():
    bad = rule(variants=[Variant(label="v", when=[Condition(field="colour", op="eq", value="red")],
                                 formula="band('nope', gt, 'base') * mystery")])
    problems = validate_rule(bad, CORE_VARIABLES)
    assert any("colour" in p for p in problems)
    assert any("mystery" in p for p in problems)
    assert any("nope" in p for p in problems)
    assert validate_rule(towage(), CORE_VARIABLES) == []


def test_quote_totals_by_payer():
    cargo = rule(id="cargo", payer="cargo", constants=[NamedValue(name="r", value=10.65)],
                 variants=[Variant(label="all", when=[], formula="cargo_tonnes * r")])
    book = Rulebook(charges=[towage(), cargo])
    q = quote(book, {**REFERENCE, "cargo_tonnes": 40000})
    assert q.vessel_total == 147074.38
    assert q.cargo_total == 426000.0


def cargo_rule() -> ChargeRule:
    rows = [Band(key=k, values=[NamedValue(name="imports", value=i), NamedValue(name="exports", value=e)])
            for k, i, e in [("Iron Ore", 18.49, 10.65), ("Coal", 11.70, 5.96),
                            ("Fertilizer & products thereof", 18.49, 18.49), ("other", 6.84, 6.84)]]
    return rule(id="dry_bulk", payer="cargo", tables=[RateTable(name="dry_bulk", description="", bands=rows)],
                variants=[Variant(label="exports", when=[Condition(field="cargo_direction", op="eq", value="export")],
                                  formula="cargo_tonnes * lookup('dry_bulk', cargo_commodity, 'exports')"),
                          Variant(label="imports", when=[Condition(field="cargo_direction", op="eq", value="import")],
                                  formula="cargo_tonnes * lookup('dry_bulk', cargo_commodity, 'imports')")])


@pytest.mark.parametrize("commodity,direction,amount", [
    ("iron ore", "export", 426000.0),
    ("Iron ore", "import", 739600.0),
    ("fertilizers", "import", 739600.0),
    ("coal", "export", 238400.0),
    ("vermiculite", "export", 273600.0),  # unlisted -> 'other'
])
def test_keyed_tables_match_commodities(commodity, direction, amount):
    vals = {"cargo_tonnes": 40000, "cargo_commodity": commodity, "cargo_direction": direction}
    assert evaluate_rule(cargo_rule(), vals, CORE_VARIABLES).amount == pytest.approx(amount)
    assert validate_rule(cargo_rule(), CORE_VARIABLES) == []


def test_missing_commodity_needs_input_instead_of_other_rate():
    line = evaluate_rule(cargo_rule(), {"cargo_tonnes": 40000, "cargo_direction": "export"}, CORE_VARIABLES)
    assert line.status == "needs_input" and "cargo_commodity" in line.reason


def test_lookup_needs_keyed_rows():
    bad = cargo_rule()
    bad.tables[0].bands = [Band(lower=0, upper=None, values=[NamedValue(name="exports", value=1.0),
                                                             NamedValue(name="imports", value=1.0)])]
    assert any("keyed rows" in p for p in validate_rule(bad, CORE_VARIABLES))


def test_compiler_test_tool_reports_amount_and_problems():
    import json as _json

    from portdues.rules.compiler import make_test_tool

    tool = make_test_tool(CORE_VARIABLES, ["durban"])
    out = _json.loads(tool.invoke({"rule_json": cargo_rule().model_dump_json(),
                                   "call_json": '{"cargo_tonnes": 40000, "cargo_commodity": "iron ore", '
                                                '"cargo_direction": "export"}'}))
    assert out["amount"] == 426000.0 and out["validation_problems"] == []
    assert "not a valid ChargeRule" in tool.invoke({"rule_json": "{}", "call_json": "{}"})


def test_on_application_variant_asks_instead_of_pricing():
    r = rule(variants=[Variant(label="Bona fide coasters", when=[Condition(field="is_coaster", op="is_true")],
                               formula="on_application"),
                       Variant(label="Other vessels", when=[], formula="units(gt, 100) * 117.08")])
    assert validate_rule(r, CORE_VARIABLES) == []
    assert evaluate_rule(r, {"gt": 51300, "is_coaster": True}, CORE_VARIABLES).status == "needs_input"
    assert evaluate_rule(r, {"gt": 51300}, CORE_VARIABLES).amount == 60062.04


def test_broken_formula_is_an_error_line_not_an_exception():
    r = rule(variants=[Variant(label="v", when=[], formula="(gt * 2")])
    assert evaluate_rule(r, {"gt": 1}, CORE_VARIABLES).status == "error"


def test_acceptance_cases_catch_a_rule_that_applies_the_wrong_rate():
    from portdues.rules.compiler import AcceptanceCase, CallFact, _failed_cases

    per_metre_for_everyone = rule(constants=[NamedValue(name="per_metre", value=24.64)],
                                  variants=[Variant(label="all", when=[], formula="units(loa, 1) * per_metre")])
    typical = AcceptanceCase(description="typical bulk carrier", applies=True, expected="units(51300, 100) * 117.08",
                             basis="all other vessels, per 100 tons",
                             call=[CallFact(name="gt", value=51300), CallFact(name="loa", value=229.2)])
    failures = _failed_cases(per_metre_for_everyone, [typical], CORE_VARIABLES)
    assert len(failures) == 1 and "60,062.04" in failures[0]
    right = rule(variants=[Variant(label="all", when=[], formula="units(gt, 100) * 117.08")])
    assert _failed_cases(right, [typical], CORE_VARIABLES) == []
    # expected amounts written over the call's own facts are evaluated, not skipped
    symbolic = typical.model_copy(update={"expected": "units(gt, 100) * 117.08"})
    assert len(_failed_cases(per_metre_for_everyone, [symbolic], CORE_VARIABLES)) == 1


def test_port_holes_find_a_dropped_table_cell():
    from portdues.rules.compiler import _port_holes

    ports = ["durban", "cape town", "saldanha", "mossel bay"]
    bands = lambda: [Band(lower=0, upper=50000, values=[NamedValue(name="rate", value=1.0)])]
    full = [Band(lower=0, upper=None, values=[NamedValue(name="rate", value=1.0)])]
    r = rule(tables=[RateTable(name="t_full", description="", bands=full),
                     RateTable(name="t_short", description="", bands=bands())],
             variants=[Variant(label="Saldanha", when=[Condition(field="port", op="in", value=["saldanha"])],
                               formula="gt * band('t_short', gt, 'rate')"),
                       Variant(label="Others", when=[], formula="gt * band('t_full', gt, 'rate')")])
    holes = _port_holes(r, CORE_VARIABLES, ports)
    assert holes and all(h.startswith("saldanha") for h in holes)


async def test_consensus_keeps_the_rule_the_samples_agree_on(monkeypatch):
    from portdues.config import get_settings
    from portdues.rules import compiler

    good = rule(id="light", variants=[Variant(label="all", when=[], formula="units(gt, 100) * 117.08")])
    bad = rule(id="light", variants=[Variant(label="all", when=[], formula="units(loa, 1) * 24.64")])
    samples = iter([(good, 0, 0), (bad, 0, 2), (good, 0, 0)])

    async def fake_compile(*args, **kwargs):
        return next(samples)

    monkeypatch.setattr(compiler, "compile_charge", fake_compile)
    monkeypatch.setattr(get_settings(), "compile_samples", 2)
    entry = compiler.CatalogueEntry(name="Light dues", sections=["1"], payer="vessel", routine=True,
                                    computable=True, reason="")
    chosen, _ = await compiler.compile_with_consensus("doc", entry, CORE_VARIABLES, ["durban", "cape town"])
    assert chosen is good


def test_shadowed_variant_is_flagged():
    from portdues.rules.compiler import _shadowed_variants

    r = rule(variants=[
        Variant(label="Non-working vessel", when=[Condition(field="cargo_working", op="is_false")], formula="gt * 0.65"),
        Variant(label="Bunkers only", when=[Condition(field="call_purpose", op="eq", value="bunkers")], formula="gt * 0.4"),
        Variant(label="Standard", when=[], formula="gt")])
    problems = _shadowed_variants(r, CORE_VARIABLES, ["durban"])
    assert len(problems) == 1 and "Bunkers only" in problems[0] and "Non-working vessel" in problems[0]
    r.variants = [r.variants[1], r.variants[0], r.variants[2]]
    assert _shadowed_variants(r, CORE_VARIABLES, ["durban"]) == []


def test_rules_can_declare_qualifier_variables():
    r = rule(variables=[VariableSpec(name="tanker_certified", type="boolean", default=False,
                                     description="double hull, segregated ballast or Green Award")],
             variants=[Variant(label="all", when=[], formula="gt")],
             adjustments=[Adjustment(name="certified tanker", kind="reduction", percent=10,
                                     when=[Condition(field="is_tanker", op="is_true"),
                                           Condition(field="tanker_certified", op="is_true")])])
    assert validate_rule(r, CORE_VARIABLES) == []
    assert evaluate_rule(r, {"gt": 1000, "is_tanker": True}, CORE_VARIABLES).amount == 1000
    assert evaluate_rule(r, {"gt": 1000, "is_tanker": True, "tanker_certified": True}, CORE_VARIABLES).amount == 900
    assert "tanker_certified" in {v.name for v in Rulebook(charges=[r]).all_variables()}


def test_undeclared_rule_variables_are_rejected():
    r = rule(variants=[Variant(label="all", when=[Condition(field="tanker_certified", op="is_true")], formula="gt")])
    assert any("tanker_certified" in p for p in validate_rule(r, CORE_VARIABLES))
