"""Natural-language query -> structured vessel call.

The pydantic model is generated from the rulebook's vocabulary, so variables a document
introduces (e.g. "green_award") become extractable fields without code changes.
"""

from datetime import datetime
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, create_model

from portdues.llm import chat_model
from portdues.rules.schema import Rulebook, Scalar

PROFILE_PROMPT = """You read a question about port charges for a vessel call and fill in the call profile.

- Fill a field only when the message states it or it follows directly (a bulk carrier is not a
  tanker; "loading iron ore" means cargo_working true, cargo_direction export, cargo_category
  dry_bulk). Leave everything else null; defaults are applied later and reported as assumptions.
- Flags that grant reductions or exemptions (certifications, government ownership, coaster status, small
  or locally registered craft) are true only when the message says so; "small" in a description does
  not make a vessel a small vessel in the tariff's sense. When the message says a vessel lacks something,
  set that flag to false.
- Numbers as plain numbers in the units the field describes. Port without "Port of".
- "number of operations/movements/services" maps to num_services.
- requested_charges: the charges the user explicitly asks about. Empty when they want all charges or
  a full cost estimate. People often say "port dues" loosely for every charge of a call ("calculate all
  the port dues payable"); only fill this when they single out particular charges.
- When a previous profile is given, the message is a follow-up: start from the previous profile and
  apply only what the message changes. Keep the previous requested_charges unless the message itself
  asks for particular charges ("what would the pilotage be then?"). Remarks about which charges will
  change or stay the same ("only the service charges should change") describe the effect of the change,
  not a request to show fewer charges."""

_SKIP = {"stay_days", "stay_hours"}
_PY_TYPES = {"number": float, "string": str, "boolean": bool}


def profile_model(rulebook: Rulebook | None) -> type[BaseModel]:
    specs = rulebook.all_variables() if rulebook else []
    fields: dict[str, Any] = {
        "vessel_name": (str | None, Field(None, description="Vessel name if given")),
        "arrival": (str | None, Field(None, description="Arrival time, ISO 8601")),
        "departure": (str | None, Field(None, description="Departure time, ISO 8601")),
        "days_alongside": (float | None, Field(None, description="Days alongside/in port if stated in days")),
        "hours_alongside": (float | None, Field(None, description="Time in port if stated in hours")),
    }
    for spec in specs:
        if spec.name not in _SKIP:
            fields[spec.name] = (_PY_TYPES[spec.type] | None, Field(None, description=spec.description))
    charge_ids = [c.id for c in rulebook.charges] if rulebook else []
    charge_type = list[Literal[tuple(charge_ids)]] if charge_ids else list[str]
    fields["requested_charges"] = (charge_type, Field(default_factory=list,
                                                      description="Charge ids the user asks about"))
    return create_model("VesselCall", **fields)


def to_values(profile: dict[str, Any]) -> dict[str, Scalar | None]:
    """Profile -> engine variables, deriving the stay length."""
    values = {k: v for k, v in profile.items() if k not in ("requested_charges", "vessel_name")}
    days = profile.get("days_alongside")
    if days is None and profile.get("hours_alongside") is not None:
        days = round(profile["hours_alongside"] / 24, 4)
    if days is None and profile.get("arrival") and profile.get("departure"):
        try:
            delta = datetime.fromisoformat(profile["departure"]) - datetime.fromisoformat(profile["arrival"])
            days = round(delta.total_seconds() / 86400, 4)
        except ValueError:
            days = None
    values["stay_days"] = days
    values["stay_hours"] = round(days * 24, 4) if days is not None else None
    for key in ("arrival", "departure", "days_alongside", "hours_alongside"):
        values.pop(key, None)
    return values


def charge_catalogue(rulebook: Rulebook | None) -> str:
    if not rulebook:
        return ""
    lines = [f"- {c.id}: {c.name} (§{c.section}) {c.description}" for c in rulebook.charges]
    lines += [f"- (not compiled) {u.name} (§{u.section}): {u.reason}" for u in rulebook.uncovered]
    return "\n".join(lines)


async def extract_profile(
    question: str, rulebook: Rulebook | None, previous: dict[str, Any] | None, config: dict[str, Any]
) -> dict[str, Any]:
    model = profile_model(rulebook)
    content = f"Charges defined in the tariff:\n{charge_catalogue(rulebook)}\n\n"
    if previous:
        content += f"Previous profile: {previous}\n\n"
    content += f"Message:\n{question}"
    result = await chat_model().with_structured_output(model).ainvoke(
        [SystemMessage(PROFILE_PROMPT), HumanMessage(content)],
        config=config,
    )
    return result.model_dump()
