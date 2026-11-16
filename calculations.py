"""Pure calculation functions for the International Cost of Capital Calculator.

No Streamlit, no network calls. Every rate is a DECIMAL (0.065 means 6.5%).
Variable names ending in ``_dec`` are decimals; nothing in this module ever
works in percent. Conversion to percent happens only in the display layer.

Formulas (class deck), asterisk (*) = foreign currency, domestic = USD:
    1. D/E    = d / (1 - d)
    2. bE     = bU * [1 + (1 - tau) * D/E]
    3. Re     = Rf + bE * ERP
    4. Rd     = Rf + s
    5. Rd(1 - tau)
    6. WACC   = (1 - d) * Re + d * Rd * (1 - tau)
    7. F      = (1 + Rf*) / (1 + Rf)
    8. 1 + Re* = (1 + Re) * F
    9. s*     = s * F (or manual override), Rd* = Rf* + s*
   10. WACC*  = (1 - d) * Re* + d * Rd* * (1 - tau)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Iterable, Mapping


class InputUnitError(ValueError):
    """Raised when an input is outside its allowed decimal range.

    The most common cause is passing a percent (6.5) where a decimal (0.065)
    is expected.
    """


# ---------------------------------------------------------------------------
# Input / output containers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Inputs:
    """All model inputs. Every rate is a decimal."""

    unlevered_beta: float
    debt_share_dec: float  # d = D / (D + E)
    tax_rate_dec: float  # tau, same in both currencies
    erp_dec: float  # equity risk premium
    rf_usd_dec: float  # U.S. 10y government yield
    rf_foreign_dec: float  # selected-country 10y government yield
    spread_usd_dec: float  # USD BBB borrowing spread
    foreign_spread_override_dec: float | None = None  # None = override OFF


@dataclass(frozen=True)
class Results:
    """All model outputs. Every rate is a decimal."""

    # shared
    debt_to_equity: float
    unlevered_beta: float
    relevered_beta: float
    erp_dec: float
    equity_weight_dec: float
    debt_weight_dec: float
    conversion_factor: float  # F
    # USD column
    tax_rate_usd_dec: float
    rf_usd_dec: float
    cost_of_equity_usd_dec: float
    spread_usd_dec: float
    pretax_cost_of_debt_usd_dec: float
    aftertax_cost_of_debt_usd_dec: float
    wacc_usd_dec: float
    # foreign column
    tax_rate_foreign_dec: float
    rf_foreign_dec: float
    cost_of_equity_foreign_dec: float
    spread_foreign_dec: float
    spread_foreign_is_override: bool
    pretax_cost_of_debt_foreign_dec: float
    aftertax_cost_of_debt_foreign_dec: float
    wacc_foreign_dec: float

    def as_dict(self) -> dict:
        """Return the results as a plain dict."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
_PERCENT_HINT = (
    "Rates must be decimals (e.g. 0.065 for 6.5%). "
    "It looks like a percent was passed."
)


def _check(name: str, value: float, lo: float, hi: float, hint: str = _PERCENT_HINT) -> None:
    if value is None:
        raise InputUnitError(f"{name} is missing.")
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise InputUnitError(f"{name} must be a number, got {value!r}.") from exc
    if v != v:  # NaN
        raise InputUnitError(f"{name} is NaN.")
    if not (lo <= v <= hi):
        raise InputUnitError(f"{name} = {v} is outside the allowed range [{lo}, {hi}]. {hint}")


def validate_inputs(inp: Inputs) -> None:
    """Raise InputUnitError if any input is outside its allowed decimal range.

    These are hard limits meant to catch unit mistakes (percent vs decimal),
    not plausibility checks on data (see ``plausibility_warnings``).
    """
    _check("Unlevered beta", inp.unlevered_beta, 0.0, 10.0, "Beta should be a plain number like 0.80.")
    _check("Debt share d", inp.debt_share_dec, 0.0, 0.95)
    _check("Tax rate", inp.tax_rate_dec, 0.0, 0.99)
    _check("Equity risk premium", inp.erp_dec, 0.0, 0.50)
    _check("U.S. 10y yield", inp.rf_usd_dec, -0.05, 1.0)
    _check("Foreign 10y yield", inp.rf_foreign_dec, -0.05, 1.0)
    _check("USD borrowing spread", inp.spread_usd_dec, 0.0, 0.50)
    if inp.foreign_spread_override_dec is not None:
        _check("Foreign borrowing spread override", inp.foreign_spread_override_dec, 0.0, 0.50)


# Plausibility ranges for fetched data (decimals, except beta).
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "beta": (0.2, 3.0),
    "tax": (0.0, 0.50),
    "bbb_spread": (0.003, 0.05),
    "yield_10y": (-0.01, 0.40),
}


def plausibility_warning(kind: str, value: float | None, label: str) -> str | None:
    """Return a warning string if ``value`` is outside the plausible range for ``kind``."""
    if value is None:
        return None
    lo, hi = PLAUSIBLE_RANGES[kind]
    if not (lo <= value <= hi):
        if kind == "beta":
            rng = f"{lo}–{hi}"
            shown = f"{value:.4f}"
        else:
            rng = f"{lo * 100:g}%–{hi * 100:g}%"
            shown = f"{value * 100:.4f}%"
        return f"{label} = {shown} is outside the plausible range {rng}. Check the source or use a manual override."
    return None


# ---------------------------------------------------------------------------
# Individual formulas
# ---------------------------------------------------------------------------
def debt_to_equity(debt_share_dec: float) -> float:
    """Formula 1: D/E = d / (1 - d)."""
    if debt_share_dec >= 1.0:
        raise InputUnitError("Debt share must be below 100%.")
    return debt_share_dec / (1.0 - debt_share_dec)


def relever_beta(unlevered_beta: float, tax_rate_dec: float, de_ratio: float) -> float:
    """Formula 2: bE = bU * [1 + (1 - tau) * D/E] (zero-beta debt and tax shield)."""
    return unlevered_beta * (1.0 + (1.0 - tax_rate_dec) * de_ratio)


def unlever_beta(levered_beta: float, tax_rate_dec: float, de_ratio: float) -> float:
    """Explanation only: bU = bE / [1 + (1 - tau) * D/E]. Not used by the app's math."""
    return levered_beta / (1.0 + (1.0 - tax_rate_dec) * de_ratio)


def cost_of_equity_usd(rf_usd_dec: float, levered_beta: float, erp_dec: float) -> float:
    """Formula 3: Re = Rf + bE * ERP (no separate country risk premium)."""
    return rf_usd_dec + levered_beta * erp_dec


def pretax_cost_of_debt(rf_dec: float, spread_dec: float) -> float:
    """Formulas 4 and 9: Rd = Rf + s (or Rd* = Rf* + s*)."""
    return rf_dec + spread_dec


def aftertax_cost_of_debt(pretax_dec: float, tax_rate_dec: float) -> float:
    """Formula 5: Rd * (1 - tau)."""
    return pretax_dec * (1.0 - tax_rate_dec)


def wacc(debt_share_dec: float, cost_of_equity_dec: float, pretax_debt_dec: float, tax_rate_dec: float) -> float:
    """Formulas 6 and 10: WACC = (1 - d) * Re + d * Rd * (1 - tau)."""
    return (1.0 - debt_share_dec) * cost_of_equity_dec + debt_share_dec * pretax_debt_dec * (1.0 - tax_rate_dec)


def conversion_factor(rf_foreign_dec: float, rf_usd_dec: float) -> float:
    """Formula 7: F = (1 + Rf*) / (1 + Rf)."""
    return (1.0 + rf_foreign_dec) / (1.0 + rf_usd_dec)


def convert_rate(usd_rate_dec: float, factor: float) -> float:
    """Formula 8: (1 + R*) = (1 + R) * F, returns R*."""
    return (1.0 + usd_rate_dec) * factor - 1.0


def foreign_spread(spread_usd_dec: float, factor: float) -> float:
    """Formula 9 (default): s* = s * F."""
    return spread_usd_dec * factor


# ---------------------------------------------------------------------------
# Full model
# ---------------------------------------------------------------------------
def compute(inp: Inputs) -> Results:
    """Run the full model on ``inp`` (validated first). Full precision throughout."""
    validate_inputs(inp)
    d = inp.debt_share_dec
    tau = inp.tax_rate_dec  # the SAME tau everywhere below

    de = debt_to_equity(d)
    beta_e = relever_beta(inp.unlevered_beta, tau, de)

    # USD
    re_usd = cost_of_equity_usd(inp.rf_usd_dec, beta_e, inp.erp_dec)
    rd_usd = pretax_cost_of_debt(inp.rf_usd_dec, inp.spread_usd_dec)
    rd_usd_at = aftertax_cost_of_debt(rd_usd, tau)
    wacc_usd = wacc(d, re_usd, rd_usd, tau)

    # Foreign
    f = conversion_factor(inp.rf_foreign_dec, inp.rf_usd_dec)
    re_for = convert_rate(re_usd, f)
    if inp.foreign_spread_override_dec is None:
        s_for = foreign_spread(inp.spread_usd_dec, f)
        is_override = False
    else:
        s_for = inp.foreign_spread_override_dec
        is_override = True
    rd_for = pretax_cost_of_debt(inp.rf_foreign_dec, s_for)
    rd_for_at = aftertax_cost_of_debt(rd_for, tau)
    wacc_for = wacc(d, re_for, rd_for, tau)

    return Results(
        debt_to_equity=de,
        unlevered_beta=inp.unlevered_beta,
        relevered_beta=beta_e,
        erp_dec=inp.erp_dec,
        equity_weight_dec=1.0 - d,
        debt_weight_dec=d,
        conversion_factor=f,
        tax_rate_usd_dec=tau,
        rf_usd_dec=inp.rf_usd_dec,
        cost_of_equity_usd_dec=re_usd,
        spread_usd_dec=inp.spread_usd_dec,
        pretax_cost_of_debt_usd_dec=rd_usd,
        aftertax_cost_of_debt_usd_dec=rd_usd_at,
        wacc_usd_dec=wacc_usd,
        tax_rate_foreign_dec=tau,
        rf_foreign_dec=inp.rf_foreign_dec,
        cost_of_equity_foreign_dec=re_for,
        spread_foreign_dec=s_for,
        spread_foreign_is_override=is_override,
        pretax_cost_of_debt_foreign_dec=rd_for,
        aftertax_cost_of_debt_foreign_dec=rd_for_at,
        wacc_foreign_dec=wacc_for,
    )


USD_FIELDS = (
    "cost_of_equity_usd_dec",
    "pretax_cost_of_debt_usd_dec",
    "aftertax_cost_of_debt_usd_dec",
    "wacc_usd_dec",
    "relevered_beta",
    "debt_to_equity",
    "spread_usd_dec",
    "rf_usd_dec",
    "tax_rate_usd_dec",
)

# (usd_field, foreign_field) pairs that must match when Rf* = Rf
PAIRED_FIELDS = (
    ("rf_usd_dec", "rf_foreign_dec"),
    ("cost_of_equity_usd_dec", "cost_of_equity_foreign_dec"),
    ("spread_usd_dec", "spread_foreign_dec"),
    ("pretax_cost_of_debt_usd_dec", "pretax_cost_of_debt_foreign_dec"),
    ("aftertax_cost_of_debt_usd_dec", "aftertax_cost_of_debt_foreign_dec"),
    ("wacc_usd_dec", "wacc_foreign_dec"),
    ("tax_rate_usd_dec", "tax_rate_foreign_dec"),
)


# ---------------------------------------------------------------------------
# Built-in checks (run on any inputs)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CheckResult:
    """Result of one built-in check."""

    name: str
    passed: bool
    detail: str


def check_equal_yields(inp: Inputs, tol: float = 1e-10) -> CheckResult:
    """Check 2: set Rf* = Rf and spread override OFF -> every foreign result equals USD."""
    r = compute(replace(inp, rf_foreign_dec=inp.rf_usd_dec, foreign_spread_override_dec=None))
    diffs = {u: abs(getattr(r, u) - getattr(r, f)) for u, f in PAIRED_FIELDS}
    worst = max(diffs.values())
    ok = worst <= tol and abs(r.conversion_factor - 1.0) <= tol
    return CheckResult(
        "Equal yields → equal results",
        ok,
        f"Rf* set to Rf = {inp.rf_usd_dec * 100:.4f}%; F = {r.conversion_factor:.10f}; "
        f"largest USD-vs-foreign difference = {worst:.2e} (tolerance {tol:g}).",
    )


def check_zero_debt(inp: Inputs, tol: float = 1e-12) -> CheckResult:
    """Check 3: d = 0 -> bE = bU and WACC = cost of equity in both currencies."""
    r = compute(replace(inp, debt_share_dec=0.0))
    d1 = abs(r.relevered_beta - r.unlevered_beta)
    d2 = abs(r.wacc_usd_dec - r.cost_of_equity_usd_dec)
    d3 = abs(r.wacc_foreign_dec - r.cost_of_equity_foreign_dec)
    ok = max(d1, d2, d3) <= tol
    return CheckResult(
        "Zero debt → WACC = cost of equity",
        ok,
        f"d = 0: |βE−βU| = {d1:.1e}; |WACC−Re| USD = {d2:.1e}; foreign = {d3:.1e}.",
    )


def check_override_independence(inp: Inputs, tol: float = 0.0) -> CheckResult:
    """Check 4: toggling the foreign-spread override leaves every USD result unchanged."""
    base = compute(replace(inp, foreign_spread_override_dec=None))
    alt_value = (inp.foreign_spread_override_dec if inp.foreign_spread_override_dec is not None else 0.0) + 0.0123
    alt = compute(replace(inp, foreign_spread_override_dec=alt_value))
    diffs = {f: abs(getattr(base, f) - getattr(alt, f)) for f in USD_FIELDS}
    worst = max(diffs.values())
    foreign_changed = abs(base.wacc_foreign_dec - alt.wacc_foreign_dec) > 0 or inp.debt_share_dec == 0
    ok = worst <= tol and foreign_changed
    return CheckResult(
        "Foreign-spread override leaves USD unchanged",
        ok,
        f"Override OFF vs ON (s* = {alt_value * 100:.4f}%): largest change in any USD result = {worst:.1e}; "
        f"foreign WACC changed: {'yes' if abs(base.wacc_foreign_dec - alt.wacc_foreign_dec) > 0 else 'no (d = 0)'}.",
    )


# Deck reference case (Colombia)
DECK_INPUTS = Inputs(
    unlevered_beta=0.80,
    debt_share_dec=0.30,
    tax_rate_dec=0.35,
    erp_dec=0.065,
    rf_usd_dec=0.04,
    rf_foreign_dec=0.12,
    spread_usd_dec=0.015,
    foreign_spread_override_dec=None,
)

# Expected values from the deck, as decimals (percent values / 100).
DECK_EXPECTED: dict[str, float] = {
    "debt_to_equity": 0.4286,
    "relevered_beta": 1.0229,
    "cost_of_equity_usd_dec": 0.106486,
    "pretax_cost_of_debt_usd_dec": 0.055000,
    "aftertax_cost_of_debt_usd_dec": 0.035750,
    "wacc_usd_dec": 0.085265,
    "conversion_factor": 1.0769231,
    "cost_of_equity_foreign_dec": 0.191600,
    "spread_foreign_dec": 0.016154,
    "pretax_cost_of_debt_foreign_dec": 0.136154,
    "aftertax_cost_of_debt_foreign_dec": 0.088500,
    "wacc_foreign_dec": 0.160670,
}
# Rounding precision each expected value was quoted at (decimal places of the decimal value).
DECK_DECIMALS: dict[str, int] = {
    "debt_to_equity": 4,
    "relevered_beta": 4,
    "conversion_factor": 7,
}


def check_deck_reference() -> CheckResult:
    """Recompute the deck's Colombia example and compare to the published numbers."""
    r = compute(DECK_INPUTS)
    bad = []
    for field, expected in DECK_EXPECTED.items():
        places = DECK_DECIMALS.get(field, 6)  # 4 dp in percent = 6 dp as decimal
        if round(getattr(r, field), places) != round(expected, places):
            bad.append(f"{field}: got {getattr(r, field):.8f}, expected {expected}")
    return CheckResult(
        "Deck reference case (Colombia) reproduces",
        not bad,
        "All 12 deck values match to the quoted precision." if not bad else "; ".join(bad),
    )


# ---------------------------------------------------------------------------
# All-industry table
# ---------------------------------------------------------------------------
def all_industry_rows(betas: Mapping[str, float], base: Inputs) -> list[dict]:
    """Recompute the model for every industry beta, holding all other inputs fixed.

    Returns a list of dicts with decimal values (no rounding).
    """
    rows: list[dict] = []
    for industry, beta_u in betas.items():
        r = compute(replace(base, unlevered_beta=float(beta_u)))
        rows.append(
            {
                "Industry": industry,
                "Unlevered beta": r.unlevered_beta,
                "Relevered beta": r.relevered_beta,
                "Cost of equity USD": r.cost_of_equity_usd_dec,
                "WACC USD": r.wacc_usd_dec,
                "Cost of equity foreign": r.cost_of_equity_foreign_dec,
                "WACC foreign": r.wacc_foreign_dec,
            }
        )
    return rows


def pct(value_dec: float | None, places: int = 4) -> str:
    """Format a decimal rate as a percent string. The ONLY decimal->percent conversion for display."""
    if value_dec is None:
        return "—"
    return f"{value_dec * 100:.{places}f}%"


def pct_to_dec(value_pct: float | None) -> float | None:
    """Convert a user-entered percent (6.5) to a decimal (0.065). The ONLY percent->decimal conversion."""
    if value_pct is None:
        return None
    return float(value_pct) / 100.0


def iter_checks(inp: Inputs) -> Iterable[CheckResult]:
    """Run the live checks (2, 3, 4) plus the deck reference check."""
    yield check_equal_yields(inp)
    yield check_zero_debt(inp)
    yield check_override_independence(inp)
    yield check_deck_reference()
