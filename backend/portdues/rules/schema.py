"""A small typed language for tariff rules.

The compiler agent writes `ChargeRule`s from the document; `engine.py` evaluates them.
Nothing in here knows about a particular port or tariff book: the rules carry their own
rates, bands and conditions, and the vocabulary below only names the facts a vessel call
can have.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

Scalar = str | bool | int | float


class VariableSpec(BaseModel):
    name: str
    type: Literal["number", "string", "boolean"]
    description: str
    default: Scalar | None = None


# Facts about a vessel call that tariff books condition on. Documents can extend this with
# their own variables (Rulebook.variables), e.g. "green_award" or "double_hulled".
CORE_VARIABLES: list[VariableSpec] = [
    VariableSpec(name="port", type="string", description="Port of call, e.g. 'Durban'"),
    VariableSpec(name="vessel_type", type="string",
                 description="Vessel type in lower case, e.g. 'bulk carrier', 'container', 'tanker', 'passenger', 'ro-ro'"),
    VariableSpec(name="gt", type="number", description="Gross tonnage per the 1969 tonnage certificate"),
    VariableSpec(name="nt", type="number", description="Net tonnage"),
    VariableSpec(name="dwt", type="number", description="Deadweight in metric tons"),
    VariableSpec(name="loa", type="number", description="Length overall in metres"),
    VariableSpec(name="beam", type="number", description="Beam in metres"),
    VariableSpec(name="draft", type="number", description="Maximum draft in metres"),
    VariableSpec(name="stay_days", type="number",
                 description="Billable length of the stay in port, in days (fractional). The declared days "
                             "alongside when given, otherwise departure minus arrival"),
    VariableSpec(name="stay_hours", type="number", description="stay_days * 24"),
    VariableSpec(name="num_services", type="number", default=2,
                 description="Number of marine service movements for the call (entering + leaving = 2, "
                             "each shift adds one). Use for anything charged 'per service'"),
    VariableSpec(name="cargo_tonnes", type="number",
                 description="Cargo quantity in metric tons (kilolitres for liquid bulk)"),
    VariableSpec(name="cargo_commodity", type="string", description="Commodity in lower case, e.g. 'iron ore'"),
    VariableSpec(name="cargo_direction", type="string",
                 description="One of import, export, transhipment, coastwise"),
    VariableSpec(name="cargo_category", type="string",
                 description="One of dry_bulk, liquid_bulk, breakbulk, container"),
    VariableSpec(name="teu", type="number", description="Number of containers expressed in TEU"),
    VariableSpec(name="cargo_working_hours", type="number",
                 description="Hours of actual cargo operations during the stay (terminal records)"),
    VariableSpec(name="cargo_working", type="boolean", default=True,
                 description="Vessel loads or discharges cargo during the call"),
    VariableSpec(name="self_propelled", type="boolean", default=True, description="Vessel has its own propulsion"),
    VariableSpec(name="outside_working_hours", type="boolean", default=False,
                 description="Marine services start or finish outside ordinary working hours"),
    VariableSpec(name="is_coaster", type="boolean", default=False,
                 description="Vessel holds bona fide coaster status"),
    VariableSpec(name="is_tanker", type="boolean", default=False, description="Vessel is a tanker"),
    VariableSpec(name="is_passenger_vessel", type="boolean", default=False,
                 description="Vessel carries more than 12 passengers"),
    VariableSpec(name="registered_at_port", type="boolean", default=False,
                 description="The vessel is registered or licensed at the port of call (local craft). Rates "
                             "described as applying 'at their registered port' depend on this"),
    VariableSpec(name="is_small_vessel", type="boolean", default=False,
                 description="Locally registered small commercial craft (tug, launch, fishing boat ...)"),
    VariableSpec(name="is_pleasure_vessel", type="boolean", default=False,
                 description="Used solely for sport/recreation"),
    VariableSpec(name="is_government_vessel", type="boolean", default=False,
                 description="Naval, police, defence force or maritime-authority vessel"),
    VariableSpec(name="call_purpose", type="string", default="cargo",
                 description="Primary purpose of the call: cargo, bunkers, stores, repairs, passengers, "
                             "crew_change, medical, shelter"),
]

CORE_NAMES = {v.name for v in CORE_VARIABLES}


class Condition(BaseModel):
    field: str = Field(description="A vocabulary variable name")
    op: Literal["eq", "ne", "in", "not_in", "contains", "gt", "gte", "lt", "lte", "is_true", "is_false"]
    value: str | float | bool | list[str] | None = Field(
        default=None, description="Comparison value; a list for in/not_in, null for is_true/is_false"
    )


class NamedValue(BaseModel):
    name: str = Field(description="snake_case identifier usable in formulas")
    value: float
    source: str = Field(default="", description="Where the number comes from, e.g. '§3.3, p.13'")


class Band(BaseModel):
    """A table row: either a numeric band (lower/upper, read with band()) or a named row (key, read with
    lookup()), e.g. one commodity of a cargo-dues table."""

    key: str | None = Field(default=None, description="Row name for tables keyed by item (e.g. a commodity, "
                                                     "'other' for unlisted items); null for numeric bands")
    lower: float | None = Field(default=None, description="Inclusive lower bound of a numeric band; null for keyed rows")
    upper: float | None = Field(default=None, description="Inclusive upper bound; null for open-ended or keyed rows")
    values: list[NamedValue]


class RateTable(BaseModel):
    name: str = Field(description="snake_case identifier referenced by band()")
    description: str
    bands: list[Band]


class Variant(BaseModel):
    label: str = Field(description="Short label, e.g. 'Durban' or 'self-propelled at registered port'")
    when: list[Condition] = Field(description="All must hold; empty means default")
    formula: str = Field(description="Arithmetic expression over variables, constants and helper functions")


class Adjustment(BaseModel):
    name: str
    kind: Literal["surcharge", "reduction"]
    percent: float = Field(description="Percentage, e.g. 50 for +50% or 35 for -35%")
    when: list[Condition] = Field(description="All must hold; never empty")
    source: str = ""


class Exemption(BaseModel):
    description: str
    when: list[Condition] = Field(description="All must hold; never empty")


class ChargeRule(BaseModel):
    id: str = Field(description="snake_case id, e.g. 'pilotage_dues'")
    name: str
    section: str = Field(description="Clause number(s) in the document, e.g. '3.3'")
    payer: Literal["vessel", "cargo"] = "vessel"
    description: str = Field(description="One or two sentences: what is charged and on what basis")
    applies_when: list[Condition] = Field(description="Scope conditions; empty if it applies to every call")
    variables: list[VariableSpec] = Field(default_factory=list, description=(
        "Facts this rule's conditions need that the vocabulary lacks (e.g. a certification), at most 3, each with "
        "a default for the common case"))
    constants: list[NamedValue]
    tables: list[RateTable]
    variants: list[Variant] = Field(description="Evaluated in order; the first whose conditions hold is used")
    minimum: float | None = None
    maximum: float | None = None
    adjustments: list[Adjustment]
    exemptions: list[Exemption]
    notes: str = ""


class UncoveredCharge(BaseModel):
    name: str
    section: str
    reason: str


class Rulebook(BaseModel):
    title: str = ""
    currency: str = "ZAR"
    tax_note: str = ""
    variables: list[VariableSpec] = Field(default_factory=list, description="Document-specific extra variables")
    ports: list[str] = Field(default_factory=list, description="Ports the tariff covers, lower case")
    charges: list[ChargeRule] = Field(default_factory=list)
    uncovered: list[UncoveredCharge] = Field(default_factory=list)
    # how the rulebook was produced: agent design, model, repairs, tokens and cost
    compiled_with: dict[str, Any] = Field(default_factory=dict)

    def all_variables(self) -> list[VariableSpec]:
        """Core vocabulary, document-level extras and variables declared by individual rules."""
        out, seen = list(CORE_VARIABLES), set(CORE_NAMES)
        for v in [*self.variables, *(v for rule in self.charges for v in rule.variables)]:
            if v.name not in seen:
                out.append(v)
                seen.add(v.name)
        return out

    def charge(self, charge_id: str) -> ChargeRule | None:
        return next((c for c in self.charges if c.id == charge_id), None)


class ChargeLine(BaseModel):
    charge_id: str
    name: str
    section: str = ""
    payer: Literal["vessel", "cargo"] = "vessel"
    status: Literal["charged", "exempt", "not_applicable", "needs_input", "error"]
    amount: float | None = None
    method: Literal["rule", "research"] = "rule"
    variant: str | None = None
    formula: str | None = None
    inputs: dict[str, Scalar] = Field(default_factory=dict)
    steps: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    reason: str = ""


class Quote(BaseModel):
    currency: str
    lines: list[ChargeLine]
    vessel_total: float
    cargo_total: float
    tax_note: str = ""
