"""Hand-transcribed reference for the Transnet 2024/25 tariff book.

Used ONLY to label the synthetic golden dataset. The application never imports this:
the whole point of the system is to derive these rules from the document by itself.
Rates are copied from the PDF; clause numbers are given next to each block.
"""

import math
from dataclasses import dataclass

PORTS = ["richards bay", "durban", "east london", "port elizabeth", "ngqura", "mossel bay", "cape town", "saldanha"]


def units(x: float, size: float) -> int:
    return math.ceil(x / size - 1e-9) if x > 0 else 0


@dataclass
class Call:
    port: str
    gt: float
    loa: float = 200.0
    stay_days: float = 2.0
    services: int = 2
    outside_hours: bool = False
    government: bool = False
    coaster: bool = False
    passenger: bool = False
    cargo_working: bool = True
    bunkers_only: bool = False
    tanker: bool = False
    cargo_tonnes: float = 0
    cargo_commodity: str | None = None
    cargo_direction: str | None = None
    cargo_category: str | None = None


# §1.1.1 light dues, "all other vessels"
def light_dues(c: Call) -> float:
    if c.government:
        return 0.0
    return units(c.gt, 100) * 117.08


# §2.1.1 VTS
def vts_dues(c: Call) -> float:
    if c.government:
        return 0.0
    rate = 0.65 if c.port in ("durban", "saldanha") else 0.54
    return max(c.gt * rate, 235.52)


# §3.3 pilotage
_PILOTAGE = {
    "richards bay": (30960.46, 10.93), "durban": (18608.61, 9.72), "port elizabeth": (8970.00, 14.33),
    "ngqura": (8970.00, 14.33), "cape town": (6342.39, 10.20), "saldanha": (9673.57, 13.66),
}


def pilotage_dues(c: Call) -> float:
    if c.government:
        return 0.0
    base, per100 = _PILOTAGE.get(c.port, (6547.45, 10.49))
    fee = (base + units(c.gt, 100) * per100) * c.services
    fee = fee * 1.5 if c.outside_hours else fee
    if c.port == "saldanha" and c.tanker:  # PLO duties for pilots on board tankers during the stay, per hour
        fee += 886.20 * c.stay_days * 24
    return fee


# §3.6 towage: per service, columns RB, DBN, EL, PE/NGQ, MB, CT, SAL
_TOW_COL = {"richards bay": 0, "durban": 1, "east london": 2, "port elizabeth": 3, "ngqura": 3,
            "mossel bay": 4, "cape town": 5, "saldanha": 6}
_TOW_BANDS = [  # (lower, upper, floor, bases, increments per 100 t above floor)
    (0, 2000, 0, [7001.67, 8140.00, 5622.16, 7206.98, 6316.53, 5411.47, 9038.42], [0] * 7),
    (2001, 10000, 2000, [13020.67, 12633.99, 8152.14, 11168.45, 8152.14, 7898.57, 15378.78],
     [275.32, 268.99, 200.97, 237.53, 173.37, 194.63, 327.43]),
    (10001, 50000, 10000, [39999.88, 38494.51, 27956.91, 32257.98, 25806.37, 27741.85, 47311.70],
     [101.08, 84.95, 66.67, 73.10, 60.21, 64.52, 103.23]),
    (50001, 100000, 50000, [79999.76, 73118.07, 55913.82, 64515.95, None, 53978.33, 90322.33],
     [30.11, 32.24, 25.80, 21.50, None, 47.32, 27.97]),
    (100001, math.inf, 100000, [103999.70, 93548.13, None, 82542.46, None, 79569.67, 111827.63],
     [21.50, 23.65, None, 21.50, None, 38.71, 47.32]),
]


def towage_dues(c: Call) -> float | None:  # §3.6 lists no exemptions
    col = _TOW_COL[c.port]
    for lower, upper, floor, bases, incs in _TOW_BANDS:
        if lower <= math.ceil(c.gt) <= upper:
            if bases[col] is None:
                return None  # n/a for this port
            fee = (bases[col] + incs[col] * units(c.gt - floor, 100)) * c.services
            return fee * 1.25 if c.outside_hours else fee
    raise ValueError(c.gt)


# §3.8 berthing services
_BERTHING = {"richards bay": (3175.89, 13.46), "port elizabeth": (3838.62, 18.72), "ngqura": (3838.62, 18.72),
             "cape town": (3052.33, 14.92), "saldanha": (4006.34, 16.97)}


def berthing_services(c: Call) -> float:
    base, per100 = _BERTHING.get(c.port, (2801.91, 13.68))
    fee = (base + units(c.gt, 100) * per100) * c.services
    return fee * 1.5 if c.outside_hours else fee


# §3.9 running of vessel lines (outside hours: published minimum per service)
# Cape Town's outside-hours minimum is printed as 3 309.05 in one table and 4 741.69 in the other;
# the golden set avoids out-of-hours calls at Cape Town for that reason.
_LINES = {"port elizabeth": (2266.73, 4533.42), "ngqura": (2266.73, 4533.42), "cape town": (2370.84, 4741.69),
          "saldanha": (2085.59, 4171.18)}


def running_lines(c: Call) -> float:
    normal, outside = _LINES.get(c.port, (1654.56, 3309.05))
    return (outside if c.outside_hours else normal) * c.services


# §4.1.1 port dues
def port_dues(c: Call) -> float:
    if c.government:
        return 0.0
    blocks = units(c.gt, 100)
    fee = blocks * 192.73 + blocks * 57.79 * c.stay_days
    if c.bunkers_only and c.stay_days <= 2:
        fee *= 0.40
    elif c.coaster or c.passenger or not c.cargo_working:
        fee *= 0.65
    if c.stay_days * 24 < 12:
        fee *= 0.85
    return fee


CHARGES = {
    "light_dues": light_dues,
    "port_dues": port_dues,
    "vts_dues": vts_dues,
    "pilotage_dues": pilotage_dues,
    "towage_dues": towage_dues,
    "berthing_services": berthing_services,
    "running_lines": running_lines,
}


def expected(call: Call, keys: list[str] | None = None) -> dict[str, float]:
    out = {}
    for key in keys or CHARGES:
        value = CHARGES[key](call)
        if value is not None:
            out[key] = round(value, 2)
    return out
