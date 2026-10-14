"""Deterministic evaluation of compiled tariff rules.

Formulas are plain Python expressions restricted to arithmetic, comparisons, conditionals
and a few helpers. They are parsed with `ast` and walked by hand, never `eval`ed.

Helpers available inside a formula:
    units(x, size)        number of `size` blocks "or part thereof"  -> ceil(x / size)
    above(x, threshold)   max(0, x - threshold)
    band(table, x, name)  value `name` from the band of `table` containing x
                          ('lower' and 'upper' are also readable)
    ceil, floor, round, min, max, abs
"""

import ast
import math
import operator
import re
from typing import Any, Callable

from portdues.rules.schema import (
    ChargeLine,
    ChargeRule,
    Condition,
    Quote,
    RateTable,
    Rulebook,
    Scalar,
    VariableSpec,
)

_EPS = 1e-9


# formula for a case the document prices by agreement or "on application": the engine asks instead of pricing
ON_APPLICATION = "on_application"


class RuleError(Exception):
    pass


class MissingInput(RuleError):
    def __init__(self, name: str):
        super().__init__(f"missing input '{name}'")
        self.name = name


def same(a: Any, b: Any) -> bool:
    """String equality that tolerates a trailing qualifier: "saldanha" matches "saldanha bay"."""
    a, b = norm(a), norm(b)
    if not isinstance(a, str) or not isinstance(b, str):
        return a == b
    return a == b or (bool(a) and bool(b) and (a.startswith(b + " ") or b.startswith(a + " ")))


def norm(value: Any) -> Any:
    """Case/punctuation-insensitive form used when comparing strings."""
    if isinstance(value, str):
        s = value.strip().lower()
        s = re.sub(r"^(the\s+)?port\s+of\s+", "", s)
        return re.sub(r"[\s_\-/]+", " ", s).strip()
    if isinstance(value, list):
        return [norm(v) for v in value]
    return value


# --------------------------------------------------------------------------- formulas


class Missing:
    """A variable the call does not state. Comparisons with it are false (an unknown cargo type is not
    'container'); any arithmetic or table lookup with it means the charge needs that input."""

    def __init__(self, name: str):
        self.name = name


def _known(*values: Any) -> None:
    for v in values:
        if isinstance(v, Missing):
            raise MissingInput(v.name)
        if isinstance(v, list):
            _known(*v)

_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_CMP_OPS: dict[type, Callable[[Any, Any], bool]] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: lambda a, b: same(a, b),
    ast.NotEq: lambda a, b: not same(a, b),
    ast.In: lambda a, b: any(same(a, x) for x in b),
    ast.NotIn: lambda a, b: not any(same(a, x) for x in b),
}


_STOPWORDS = {"and", "of", "the", "product", "thereof", "excluding", "other", "than", "a", "an", "all"}


def _key_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(norm(text)).replace("&", " and "))
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words} - _STOPWORDS


def _lookup(tables: dict[str, RateTable], name: str, key: str, field: str) -> float:
    """Value `field` from the keyed row best matching `key`, else from the row keyed "other"."""
    table = tables.get(name)
    if table is None:
        raise RuleError(f"unknown table '{name}'")
    wanted = _key_tokens(key)
    best, best_score = None, 0.0
    fallback = None
    for row in table.bands:
        if row.key is None:
            continue
        if norm(row.key) in ("other", "others", "default", "*", "unlisted"):
            fallback = row
            continue
        have = _key_tokens(row.key)
        if not have or not wanted:
            continue
        if have == wanted:
            score = 3.0
        elif wanted <= have or have <= wanted:
            score = 2.0 + len(have & wanted) / len(have | wanted)
        else:
            continue
        if score > best_score:
            best, best_score = row, score
    row = best or fallback
    if row is None:
        raise RuleError(f"'{key}' is not in table '{name}' and the table has no 'other' row")
    for v in row.values:
        if v.name == field:
            return v.value
    raise RuleError(f"table '{name}' row '{row.key}' has no value '{field}'")


def _units(x: float, size: float) -> float:
    if x <= 0:
        return 0
    return math.ceil(x / size - _EPS)


def _band(tables: dict[str, RateTable], name: str, x: float, field: str) -> float:
    table = tables.get(name)
    if table is None:
        raise RuleError(f"unknown table '{name}'")
    for band in table.bands:
        if band.lower is None:
            continue
        upper = math.inf if band.upper is None else band.upper
        if band.lower - _EPS <= x <= upper + _EPS:
            if field == "lower":
                return band.lower
            if field == "upper":
                return upper
            for v in band.values:
                if v.name == field:
                    return v.value
            raise RuleError(f"table '{name}' band {band.lower}-{band.upper} has no value '{field}'")
    raise RuleError(f"{x:g} falls outside every band of table '{name}'")


BASE_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "units": _units,
    "above": lambda x, t: max(0.0, x - t),
    "ceil": math.ceil,
    "floor": math.floor,
    "round": round,
    "min": min,
    "max": max,
    "abs": abs,
}


class Formula:
    def __init__(self, source: str):
        self.source = source.strip()
        try:
            self.tree = ast.parse(self.source, mode="eval")
        except SyntaxError as exc:
            raise RuleError(f"syntax error in formula {self.source!r}: {exc.msg}") from exc
        self._check(self.tree)

    def _check(self, node: ast.AST) -> None:
        allowed = (
            ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
            ast.Call, ast.Name, ast.Load, ast.Constant, ast.List, ast.Tuple, ast.operator, ast.unaryop,
            ast.boolop, ast.cmpop,
        )
        for child in ast.walk(node):
            if not isinstance(child, allowed):
                raise RuleError(f"'{type(child).__name__}' is not allowed in formulas")
            if isinstance(child, ast.Call):
                if not isinstance(child.func, ast.Name) or child.keywords:
                    raise RuleError("only positional calls to helper functions are allowed")
                if child.func.id not in BASE_FUNCTIONS and child.func.id not in ("band", "lookup"):
                    raise RuleError(f"unknown function '{child.func.id}'")

    def names(self) -> set[str]:
        called = {n.func.id for n in ast.walk(self.tree) if isinstance(n, ast.Call)}
        return {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name)} - called

    def table_refs(self, helper: str | None = None) -> list[tuple[str, str]]:
        """(table, field) pairs read through band() and/or lookup()."""
        refs = []
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Call) and n.func.id in ("band", "lookup") and len(n.args) == 3:
                if helper and n.func.id != helper:
                    continue
                table, _, field = n.args
                if isinstance(table, ast.Constant) and isinstance(field, ast.Constant):
                    refs.append((str(table.value), str(field.value)))
        return refs

    def evaluate(self, env: dict[str, Any], tables: dict[str, RateTable]) -> float:
        functions = {**BASE_FUNCTIONS, "band": lambda t, x, f: _band(tables, t, x, f),
                     "lookup": lambda t, k, f: _lookup(tables, t, k, f)}

        def walk(node: ast.AST) -> Any:
            match node:
                case ast.Expression(body=body):
                    return walk(body)
                case ast.Constant(value=value):
                    return value
                case ast.List(elts=elts) | ast.Tuple(elts=elts):
                    return [walk(e) for e in elts]
                case ast.Name(id=name):
                    if name not in env or env[name] is None:
                        return Missing(name)
                    return env[name]
                case ast.BinOp(left=left, op=op, right=right):
                    a, b = walk(left), walk(right)
                    _known(a, b)
                    return _BIN_OPS[type(op)](a, b)
                case ast.UnaryOp(op=ast.USub(), operand=operand):
                    value = walk(operand)
                    _known(value)
                    return -value
                case ast.UnaryOp(op=ast.UAdd(), operand=operand):
                    value = walk(operand)
                    _known(value)
                    return value
                case ast.UnaryOp(op=ast.Not(), operand=operand):
                    return not walk(operand)
                case ast.BoolOp(op=ast.And(), values=values):
                    return all(walk(v) for v in values)
                case ast.BoolOp(op=ast.Or(), values=values):
                    return any(walk(v) for v in values)
                case ast.Compare(left=left, ops=ops, comparators=comparators):
                    current = walk(left)
                    for op, comp in zip(ops, comparators):
                        nxt = walk(comp)
                        if isinstance(current, Missing) or isinstance(nxt, Missing):
                            return False
                        if not _CMP_OPS[type(op)](current, nxt):
                            return False
                        current = nxt
                    return True
                case ast.IfExp(test=test, body=body, orelse=orelse):
                    return walk(body) if walk(test) else walk(orelse)
                case ast.Call(func=ast.Name(id=fname), args=args):
                    values = [walk(a) for a in args]
                    _known(*values)
                    return functions[fname](*values)
            raise RuleError(f"cannot evaluate {ast.dump(node)}")

        result = walk(self.tree)
        _known(result)
        if isinstance(result, bool) or not isinstance(result, (int, float)):
            raise RuleError(f"formula {self.source!r} did not produce a number")
        return float(result)


# --------------------------------------------------------------------------- conditions


class Env:
    """Variable lookup with defaults, recording which defaults were relied on."""

    def __init__(self, values: dict[str, Scalar | None], specs: list[VariableSpec]):
        self.values = {k: v for k, v in values.items() if v is not None and v != ""}
        self.specs = {s.name: s for s in specs}
        self.assumed: dict[str, Scalar] = {}
        self.unknown: set[str] = set()

    def get(self, name: str) -> Scalar | None:
        if name in self.values:
            return self.values[name]
        spec = self.specs.get(name)
        if spec is not None and spec.default is not None:
            self.assumed[name] = spec.default
            return spec.default
        if spec is not None and spec.type == "boolean":
            self.assumed[name] = False
            return False
        self.unknown.add(name)
        if spec is not None and spec.type == "string":
            # an unknown category simply matches nothing; scope checks still see it in `unknown`
            return ""
        return None

    def as_dict(self, names: set[str]) -> dict[str, Scalar | None]:
        """Values for a formula; unknown variables stay None so the formula can tell them apart."""
        out = {}
        for n in names:
            value = self.get(n)
            out[n] = None if n in self.unknown else value
        return out

    def assumption_notes(self) -> list[str]:
        fmt = lambda v: f"{v:g}" if isinstance(v, float) else str(v)
        return [f"{k} not given, assumed {fmt(v)}" for k, v in sorted(self.assumed.items())]


def check(cond: Condition, env: Env) -> bool:
    actual = env.get(cond.field)
    if actual is None or actual == "":
        return False
    expected = cond.value
    match cond.op:
        case "is_true":
            return bool(actual) is True
        case "is_false":
            return bool(actual) is False
        case "eq":
            return same(actual, expected) if isinstance(actual, str) else actual == expected
        case "ne":
            return not same(actual, expected) if isinstance(actual, str) else actual != expected
        case "in" | "not_in":
            options = expected if isinstance(expected, list) else [expected]
            hit = any(same(actual, o) for o in options) if isinstance(actual, str) else actual in options
            return hit if cond.op == "in" else not hit
        case "contains":
            return str(norm(expected)) in str(norm(actual))
        case "gt" | "gte" | "lt" | "lte":
            try:
                a, b = float(actual), float(expected)
            except (TypeError, ValueError):
                return False
            return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[cond.op]
    return False


def all_hold(conds: list[Condition], env: Env) -> bool:
    return all(check(c, env) for c in conds)


def describe(conds: list[Condition]) -> str:
    return " and ".join(f"{c.field} {c.op} {c.value}" if c.value is not None else f"{c.field} {c.op}" for c in conds)


# --------------------------------------------------------------------------- rules


def with_rule_variables(rule: ChargeRule, specs: list[VariableSpec]) -> list[VariableSpec]:
    known = {s.name for s in specs}
    return specs + [v for v in rule.variables if v.name not in known]


def evaluate_rule(rule: ChargeRule, values: dict[str, Scalar | None], specs: list[VariableSpec]) -> ChargeLine:
    specs = with_rule_variables(rule, specs)
    env = Env(values, specs)
    line = ChargeLine(charge_id=rule.id, name=rule.name, section=rule.section, payer=rule.payer, status="charged")

    def finish(**updates: Any) -> ChargeLine:
        line.assumptions = env.assumption_notes()
        return line.model_copy(update=updates)

    if not all_hold(rule.applies_when, env):
        missing = sorted({c.field for c in rule.applies_when} & env.unknown)
        if missing:
            return finish(status="needs_input", reason=f"needs {', '.join(missing)} to decide applicability")
        return finish(status="not_applicable", reason=f"scope not met: {describe(rule.applies_when)}")

    for ex in rule.exemptions:
        if ex.when and all_hold(ex.when, env):
            return finish(status="exempt", amount=0.0, reason=ex.description)

    variant = None
    for candidate in rule.variants:
        if all_hold(candidate.when, env):
            variant = candidate
            break
        # Skipped only because a fact is missing (typically the port): any later fallback would be a
        # guess, e.g. pricing an unnamed port at the "other ports" rate. Ask instead.
        undecided = sorted({c.field for c in candidate.when} & env.unknown)
        if undecided:
            return finish(status="needs_input",
                          reason=f"needs {', '.join(undecided)} to choose between rate variants")
    if variant is None:
        return finish(status="not_applicable", reason="no rate variant matches this call")

    if variant.formula.strip() == ON_APPLICATION:
        return finish(status="needs_input", variant=variant.label,
                      reason=f"{variant.label}: priced by agreement or on application, not from published rates")
    try:
        formula = Formula(variant.formula)
    except RuleError as exc:
        return finish(status="error", variant=variant.label, formula=variant.formula, reason=str(exc))
    constants = {c.name: c.value for c in rule.constants}
    tables = {t.name: t for t in rule.tables}
    variable_names = formula.names() - constants.keys()
    try:
        amount = formula.evaluate({**env.as_dict(variable_names), **constants}, tables)
    except MissingInput as exc:
        return finish(status="needs_input", variant=variant.label, formula=variant.formula,
                      reason=f"needs {exc.name}")
    except (RuleError, ZeroDivisionError, TypeError, ValueError) as exc:
        return finish(status="error", variant=variant.label, formula=variant.formula, reason=str(exc))

    if amount == 0:
        # a variant that prices the call at zero means the charge does not arise for it
        return finish(status="not_applicable", variant=variant.label, formula=variant.formula,
                      reason=f"evaluates to zero ({variant.label})")

    steps = [f"{variant.label}: {variant.formula} = {amount:,.2f}"]
    if rule.minimum is not None and amount < rule.minimum:
        amount = rule.minimum
        steps.append(f"raised to minimum {rule.minimum:,.2f}")
    if rule.maximum is not None and amount > rule.maximum:
        amount = rule.maximum
        steps.append(f"capped at maximum {rule.maximum:,.2f}")
    for adj in rule.adjustments:
        if adj.when and all_hold(adj.when, env):
            factor = 1 + adj.percent / 100 if adj.kind == "surcharge" else 1 - adj.percent / 100
            amount *= factor
            sign = "+" if adj.kind == "surcharge" else "-"
            steps.append(f"{adj.name}: {sign}{adj.percent:g}% -> {amount:,.2f}")

    inputs = {k: v for k, v in env.as_dict(variable_names).items() if v is not None}
    return finish(amount=round(amount, 2), variant=variant.label, formula=variant.formula,
                  inputs=inputs, steps=steps)


def quote(rulebook: Rulebook, values: dict[str, Scalar | None], charge_ids: list[str] | None = None) -> Quote:
    specs = rulebook.all_variables()
    rules = [r for r in rulebook.charges if not charge_ids or r.id in charge_ids]
    lines = [evaluate_rule(r, values, specs) for r in rules]
    total = lambda payer: round(sum(l.amount or 0 for l in lines if l.payer == payer and l.status == "charged"), 2)
    return Quote(currency=rulebook.currency, lines=lines, vessel_total=total("vessel"),
                 cargo_total=total("cargo"), tax_note=rulebook.tax_note)


# --------------------------------------------------------------------------- validation

def _port_problems(rule: ChargeRule, specs: list[VariableSpec], ports: list[str]) -> list[str]:
    """Port names must be ports the document covers, and a rule must apply to at least one of them.
    Catches valid-looking rules that can never fire, e.g. port in ['south african port']."""
    problems = []
    groups = [rule.applies_when] + [v.when for v in rule.variants] + [a.when for a in rule.adjustments] \
        + [e.when for e in rule.exemptions]
    for conds in groups:
        for c in conds:
            if c.field != "port" or c.value is None:
                continue
            for name in c.value if isinstance(c.value, list) else [c.value]:
                if not any(same(name, p) for p in ports):
                    problems.append(f"port condition names '{name}', which is not a port in this tariff; "
                                    f"use names from: {', '.join(ports)}")
    # only the port conditions are probed: other scope conditions (cargo type, vessel type ...) legitimately
    # depend on the call
    port_scope = [c for c in rule.applies_when if c.field == "port"]
    if port_scope and not problems and not any(all_hold(port_scope, Env({"port": p}, specs)) for p in ports):
        problems.append("applies_when excludes every port in this tariff, so the charge could never apply")
    return problems


def _parses(source: str) -> bool:
    try:
        Formula(source)
        return True
    except RuleError:
        return False


_PROBE_TONNAGES = [450, 1_800, 6_500, 25_000, 51_300, 88_000, 140_000]


def validate_rule(rule: ChargeRule, specs: list[VariableSpec], ports: list[str] | None = None) -> list[str]:
    """Static checks plus a dry run over a spread of vessel sizes. Returns problems found."""
    problems: list[str] = []
    if len(rule.variables) > 3:
        problems.append("declare at most 3 rule variables; reuse the vocabulary where it fits")
    core = {s.name for s in specs}
    for v in rule.variables:
        if v.name in core:
            problems.append(f"rule variable '{v.name}' duplicates a vocabulary variable; use the existing one")
        if v.default is None:
            problems.append(f"rule variable '{v.name}' needs a default for the common case")
        if v.type == "string":
            problems.append(f"rule variable '{v.name}' must be boolean or number; express categories with the "
                            "vocabulary (vessel_type, call_purpose, is_government_vessel ...) or one boolean each")
    specs = with_rule_variables(rule, specs)
    if ports:
        problems += _port_problems(rule, specs, ports)
    known = {s.name for s in specs}
    constants = {c.name for c in rule.constants}
    tables = {t.name: t for t in rule.tables}

    if not rule.variants:
        problems.append("rule has no variants")
    for group, conds in [("applies_when", rule.applies_when)] + [
        (f"variant '{v.label}'", v.when) for v in rule.variants
    ] + [(f"adjustment '{a.name}'", a.when) for a in rule.adjustments] + [
        (f"exemption '{e.description[:40]}'", e.when) for e in rule.exemptions
    ]:
        for c in conds:
            if c.field not in known:
                problems.append(f"{group}: unknown variable '{c.field}'")
    for a in rule.adjustments:
        if not a.when:
            problems.append(f"adjustment '{a.name}' has no conditions; unconditional changes belong in the formula")
    for t in rule.tables:
        if not t.bands:
            problems.append(f"table '{t.name}' has no rows")
        for b in t.bands:
            if b.key is None and b.lower is None:
                problems.append(f"table '{t.name}' has a row with neither a key nor a lower bound")
                break

    for v in rule.variants:
        if v.formula.strip() == ON_APPLICATION:
            continue
        try:
            f = Formula(v.formula)
        except RuleError as exc:
            problems.append(f"variant '{v.label}': {exc}")
            continue
        for name in f.names() - constants - known:
            problems.append(f"variant '{v.label}': '{name}' is neither a variable nor a constant")
        for node in ast.walk(f.tree):
            if isinstance(node, ast.IfExp) and ast.dump(node.body) == ast.dump(node.orelse):
                problems.append(f"variant '{v.label}': both branches of '... if {ast.unparse(node.test)} else ...' "
                                "give the same value, so the condition has no effect; use the rates that differ")
        for helper in ("band", "lookup"):
            for table, field in f.table_refs(helper):
                if table not in tables:
                    problems.append(f"variant '{v.label}': unknown table '{table}'")
                    continue
                rows = [b for b in tables[table].bands if (b.key is None) == (helper == "band")]
                if not rows:
                    kind = "numeric bands (lower/upper)" if helper == "band" else "keyed rows (key)"
                    problems.append(f"variant '{v.label}': {helper}() needs a table of {kind}; '{table}' has none")
                elif field not in ("lower", "upper"):
                    for b in rows:
                        if field not in {x.name for x in b.values}:
                            label = b.key if b.key is not None else f"{b.lower}-{b.upper}"
                            problems.append(f"table '{table}' row '{label}' lacks '{field}'")
                            break
    priced = [v for v in rule.variants if v.formula.strip() != ON_APPLICATION]
    used_tables = {t for v in priced if _parses(v.formula) for t, _ in Formula(v.formula).table_refs()}
    for name in tables.keys() - used_tables:
        problems.append(f"table '{name}' is defined but no formula reads it; look rates up with band('{name}', ...)"
                        " instead of hard-coding one value")
    if problems:
        return problems

    probe = {"stay_days": 2.5, "stay_hours": 60, "loa": 180, "nt": 15000, "dwt": 40000,
             "cargo_tonnes": 30000, "teu": 500, "beam": 30, "draft": 11}
    errors = set()
    for gt in _PROBE_TONNAGES:
        for v in priced:
            f = Formula(v.formula)
            env = {**{s.name: s.default for s in specs if s.default is not None}, **probe, "gt": gt}
            env.update({c.name: c.value for c in rule.constants})
            try:
                f.evaluate(env, tables)
            except MissingInput:
                pass
            except (RuleError, ZeroDivisionError, TypeError, ValueError) as exc:
                if "outside every band" not in str(exc):
                    errors.add(f"variant '{v.label}' failed at gt={gt}: {exc}")
    return sorted(errors)
