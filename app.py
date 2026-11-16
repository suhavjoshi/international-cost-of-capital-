"""International Cost of Capital Calculator (Streamlit).

All math lives in calculations.py (pure, tested). All data fetching lives in
data_sources.py. This file only handles inputs, caching and display.

Unit rule: widgets take PERCENT (6.5); they are converted to DECIMALS exactly
once with calc.pct_to_dec(). Everything is stored and computed as a decimal and
converted back to percent exactly once for display with calc.pct().
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import streamlit as st

import calculations as calc
import data_sources as ds

st.set_page_config(page_title="International Cost of Capital Calculator", layout="wide")

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
TAX_US = "U.S. rate"
TAX_SEL = "Selected-country rate"
ss = st.session_state

# ---------------------------------------------------------------------------
# Cached data loads (one try/except per source lives inside each fetch_*)
# ---------------------------------------------------------------------------
TTL = 60 * 60 * 24


@st.cache_data(ttl=TTL, show_spinner="Fetching Damodaran betas…")
def load_betas() -> ds.SourceResult:
    return ds.fetch_betas()


@st.cache_data(ttl=TTL, show_spinner="Fetching Damodaran ratings spread…")
def load_spread() -> ds.SourceResult:
    return ds.fetch_bbb_spread()


@st.cache_data(ttl=TTL, show_spinner="Fetching OECD tax rates…")
def load_tax() -> ds.SourceResult:
    return ds.fetch_tax_rates()


@st.cache_data(ttl=TTL, show_spinner="Fetching 10y yields…")
def load_yields() -> ds.SourceResult:
    return ds.fetch_yields()


def safe_load(fn, name: str, url: str) -> ds.SourceResult:
    """Never let a loader crash the app."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return ds.SourceResult(name, "failed", url, ds._now(), error=f"{type(exc).__name__}: {exc}")


betas_res = safe_load(load_betas, "Unlevered industry betas (Damodaran)", ds.BETAS_URL)
spread_res = safe_load(load_spread, "USD BBB spread (Damodaran ratings)", ds.RATINGS_URL)
tax_res = safe_load(load_tax, "Corporate tax rates (OECD)", ds.OECD_PAGE_URL)
yields_res = safe_load(load_yields, "10y government bond yields (Trading Economics)", ds.BONDS_URL)

betas: dict[str, float] = betas_res.data.get("betas", {}) if betas_res.status != "failed" else {}
bbb_spread_dec: float | None = spread_res.data.get("spread_dec") if spread_res.status != "failed" else None
tax_rates: dict[str, float] = tax_res.data.get("rates", {}) if tax_res.status != "failed" else {}
tax_years: dict[str, int] = tax_res.data.get("years", {}) if tax_res.status != "failed" else {}
yields: dict[str, float] = yields_res.data.get("yields", {}) if yields_res.status != "failed" else {}
yield_dates: dict[str, str] = yields_res.data.get("dates", {}) if yields_res.status != "failed" else {}

industries = sorted(betas)
if yields and tax_rates:
    countries = sorted(set(yields) & set(tax_rates))
elif tax_rates:
    countries = sorted(tax_rates)  # yields must then be typed in
elif yields:
    countries = sorted(yields)  # tax must then be typed in
else:
    countries = []
if DEFAULT_COUNTRY not in countries:
    countries = [DEFAULT_COUNTRY] + countries
ss["_country_options"] = countries

# ---------------------------------------------------------------------------
# First-load defaults
# ---------------------------------------------------------------------------
if "_initialized" not in ss:
    ss["industry"] = DEFAULT_INDUSTRY if DEFAULT_INDUSTRY in industries else (industries[0] if industries else None)
    ss["country"] = DEFAULT_COUNTRY
    ss["tax_mode"] = TAX_US
    ss["erp_pct"] = 6.5
    ss["d_pct"] = 30.0
    for k in ("beta", "tax", "spread", "rfus", "rffor", "sfor"):
        ss[f"ovr_{k}_on"] = False
    ss["_initialized"] = True
if ss.get("country") not in countries:
    ss["country"] = DEFAULT_COUNTRY
if industries and ss.get("industry") not in industries:
    ss["industry"] = DEFAULT_INDUSTRY if DEFAULT_INDUSTRY in industries else industries[0]


def load_deck_example() -> None:
    """Fill the manual overrides with the deck's Colombia reference inputs."""
    ss.update(
        {
            "ovr_beta_on": True, "ovr_beta_val": 0.80,
            "ovr_tax_on": True, "ovr_tax_val": 35.0,
            "ovr_spread_on": True, "ovr_spread_val": 1.5,
            "ovr_rfus_on": True, "ovr_rfus_val": 4.0,
            "ovr_rffor_on": True, "ovr_rffor_val": 12.0,
            "ovr_sfor_on": False,
            "erp_pct": 6.5, "d_pct": 30.0,
        }
    )
    if "Colombia" in ss.get("_country_options", []):
        ss["country"] = "Colombia"


def refresh_data() -> None:
    st.cache_data.clear()


def override(label: str, key: str, fetched_dec: float | None, *, is_rate: bool = True, help_text: str = "") -> tuple[float | None, bool]:
    """Checkbox + number input. Returns (value_as_decimal_or_beta, override_is_on).

    When the fetched value is missing, the override is forced ON and starts empty
    (the app never fills in a made-up number).
    """
    on_key, val_key = f"ovr_{key}_on", f"ovr_{key}_val"
    fetched_disp = None if fetched_dec is None else (fetched_dec * 100.0 if is_rate else fetched_dec)
    forced = fetched_dec is None
    if forced:
        ss[on_key] = True

    def _prefill() -> None:
        if ss.get(on_key):
            ss[val_key] = fetched_disp

    shown = "—" if fetched_dec is None else (calc.pct(fetched_dec) if is_rate else f"{fetched_dec:.4f}")
    st.checkbox(
        f"Override {label}" + (" (required: no data)" if forced else f" (fetched: {shown})"),
        key=on_key, on_change=_prefill, disabled=forced, help=help_text or None,
    )
    if not ss.get(on_key):
        return fetched_dec, False
    if val_key not in ss:
        ss[val_key] = fetched_disp
    st.number_input(
        f"{label} {'(%)' if is_rate else ''}", key=val_key, step=0.01 if is_rate else 0.01,
        format="%.4f", label_visibility="visible",
    )
    raw = ss.get(val_key)
    if raw is None:
        return None, True
    return (calc.pct_to_dec(raw) if is_rate else float(raw)), True


# ---------------------------------------------------------------------------
# Sidebar inputs
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Inputs")
    c1, c2 = st.columns(2)
    c1.button("🔄 Refresh data", on_click=refresh_data, width="stretch")
    c2.button("📘 Load deck example (Colombia)", on_click=load_deck_example, width="stretch")

    if industries:
        st.selectbox("Industry (Damodaran)", industries, key="industry")
    else:
        st.selectbox("Industry (Damodaran)", ["(betas unavailable: enter β manually)"], disabled=True)
    st.selectbox("Target country", countries, key="country")
    country = ss["country"]
    st.radio("Corporate tax rate", [TAX_US, TAX_SEL], key="tax_mode", horizontal=True)
    st.number_input("Equity risk premium (%)", min_value=0.0, max_value=20.0, step=0.1, format="%.4f", key="erp_pct")
    st.number_input("Target debt share d = D/(D+E) (%)", min_value=0.0, max_value=90.0, step=1.0, format="%.2f", key="d_pct")

    st.subheader("Manual overrides")
    fetched_beta = betas.get(ss.get("industry")) if industries else None
    beta_u, beta_ovr = override("unlevered beta", "beta", fetched_beta, is_rate=False)

    tax_country = DEFAULT_COUNTRY if ss["tax_mode"] == TAX_US else country
    fetched_tax = tax_rates.get(tax_country)
    tau_dec, tax_ovr = override(
        f"corporate tax rate in use ({tax_country})", "tax", fetched_tax,
        help_text="Applies to whichever rate the toggle selects. While ON, the toggle has no effect.",
    )
    if tax_ovr and fetched_tax is not None:
        st.caption("Tax override is ON: the U.S./selected-country toggle does not change τ until you turn it off.")

    s_dec, spread_ovr = override("USD BBB spread", "spread", bbb_spread_dec)
    rf_us_dec, rfus_ovr = override("U.S. 10y yield", "rfus", yields.get(DEFAULT_COUNTRY))
    rf_for_dec, rffor_ovr = override(f"{country} 10y yield", "rffor", yields.get(country))

    # Foreign spread: default s* = s × F (needs Rf, Rf*, s)
    auto_s_star = None
    if None not in (rf_us_dec, rf_for_dec, s_dec):
        auto_s_star = calc.foreign_spread(s_dec, calc.conversion_factor(rf_for_dec, rf_us_dec))
    s_star_override_dec, sfor_ovr = (None, False)
    if auto_s_star is not None:
        st.checkbox(f"Override foreign borrowing spread s* (auto s × F = {calc.pct(auto_s_star)})", key="ovr_sfor_on",
                    on_change=lambda: ss.__setitem__("ovr_sfor_val", auto_s_star * 100.0) if ss.get("ovr_sfor_on") else None)
        if ss.get("ovr_sfor_on"):
            if "ovr_sfor_val" not in ss:
                ss["ovr_sfor_val"] = auto_s_star * 100.0
            st.number_input("Foreign borrowing spread s* (%)", key="ovr_sfor_val", step=0.01, format="%.4f")
            s_star_override_dec = calc.pct_to_dec(ss.get("ovr_sfor_val"))
            sfor_ovr = True
        else:
            st.caption(f"s* is auto-calculated as s × F = {calc.pct(auto_s_star)}")

erp_dec = calc.pct_to_dec(ss["erp_pct"])
d_dec = calc.pct_to_dec(ss["d_pct"])

# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------
st.title("International Cost of Capital Calculator")
st.caption(
    "USD cost of capital from CAPM with U.S. industry betas, converted to the selected country's currency "
    "with the yield-ratio factor F = (1 + Rf*) / (1 + Rf). All rates shown to 4 decimals; computed at full precision."
)

# Data status ---------------------------------------------------------------
ICON = {"live": "✅ live", "fallback": "⚠️ fallback", "failed": "❌ failed"}
with st.expander("Data status", expanded=True):
    rows = []
    for r in (betas_res, spread_res, tax_res, yields_res):
        rows.append(
            {
                "Source": r.name,
                "Status": ICON.get(r.status, r.status),
                "As of (on source)": r.as_of or "not published",
                "Fetched by app": r.fetched_at,
                "URL": r.source_url,
                "Notes": " ".join(r.notes),
                "Error": r.error or "",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if tax_res.status == "fallback":
        st.warning("⚠️ OECD tax rates are from the hard-coded FALLBACK table (2024), not a live fetch. " + ds.FALLBACK_SOURCE)
    for r in (betas_res, spread_res, tax_res, yields_res):
        if r.status == "failed":
            st.error(f"❌ {r.name} could not be fetched. Enter the value(s) manually in the sidebar. Details: {r.error}")

# Plausibility warnings on the values actually used (fetched or typed)
warns = [
    calc.plausibility_warning("beta", beta_u, "Unlevered beta"),
    calc.plausibility_warning("tax", tau_dec, "Tax rate"),
    calc.plausibility_warning("bbb_spread", s_dec, "USD BBB spread"),
    calc.plausibility_warning("yield_10y", rf_us_dec, "U.S. 10y yield"),
    calc.plausibility_warning("yield_10y", rf_for_dec, f"{country} 10y yield"),
]
for w in filter(None, warns):
    st.warning("⚠️ " + w)

missing = [n for n, v in [("unlevered beta", beta_u), ("tax rate", tau_dec), ("USD BBB spread", s_dec),
                          ("U.S. 10y yield", rf_us_dec), (f"{country} 10y yield", rf_for_dec)] if v is None]
if missing:
    st.warning("Enter these values in the sidebar to compute results: " + ", ".join(missing) + ".")
    st.stop()

inp = calc.Inputs(
    unlevered_beta=beta_u,
    debt_share_dec=d_dec,
    tax_rate_dec=tau_dec,
    erp_dec=erp_dec,
    rf_usd_dec=rf_us_dec,
    rf_foreign_dec=rf_for_dec,
    spread_usd_dec=s_dec,
    foreign_spread_override_dec=s_star_override_dec,
)
try:
    res = calc.compute(inp)
except calc.InputUnitError as exc:
    st.error(f"Input problem: {exc}")
    st.stop()
except Exception as exc:  # noqa: BLE001
    st.error(f"Unexpected calculation problem: {type(exc).__name__}: {exc}")
    st.stop()


def src_label(is_override: bool, live_note: str) -> str:
    return "manual override" if is_override else live_note


tax_note = (
    f"OECD {tax_years.get(tax_country, '?')} combined rate, {tax_country}"
    + (" (FALLBACK table)" if tax_res.status == "fallback" else "")
)
inputs_used = [
    ("Industry", ss.get("industry") or "(manual β)", ""),
    ("Target country", country, ""),
    ("Unlevered beta βU", f"{beta_u:.4f}", src_label(beta_ovr, f"Damodaran, column '{betas_res.data.get('meta', {}).get('column', 'Unlevered beta')}'")),
    ("Tax rate τ (both currencies)", calc.pct(tau_dec), src_label(tax_ovr, tax_note)),
    ("Equity risk premium", calc.pct(erp_dec), "user input"),
    ("Debt share d", calc.pct(d_dec), "user input"),
    ("U.S. 10y yield Rf", calc.pct(rf_us_dec), src_label(rfus_ovr, f"Trading Economics ({yield_dates.get(DEFAULT_COUNTRY, 'date n/a')})")),
    (f"{country} 10y yield Rf*", calc.pct(rf_for_dec), src_label(rffor_ovr, f"Trading Economics ({yield_dates.get(country, 'date n/a')})")),
    ("USD BBB spread s", calc.pct(s_dec), src_label(spread_ovr, "Damodaran ratings, large non-financial firms, Baa2/BBB")),
    ("Foreign spread s*", calc.pct(res.spread_foreign_dec), "manual override" if sfor_ovr else "auto: s × F"),
]

# Results table ------------------------------------------------------------
st.subheader(f"Results: USD vs {country} currency")
m1, m2, m3, m4 = st.columns(4)
m1.metric("WACC (USD)", calc.pct(res.wacc_usd_dec))
m2.metric(f"WACC ({country})", calc.pct(res.wacc_foreign_dec))
m3.metric("Cost of equity (USD)", calc.pct(res.cost_of_equity_usd_dec))
m4.metric(f"Cost of equity ({country})", calc.pct(res.cost_of_equity_foreign_dec))

fcol = f"{country} currency"
table = pd.DataFrame(
    [
        ("Risk-free rate (10y govt yield)", calc.pct(res.rf_usd_dec), calc.pct(res.rf_foreign_dec)),
        ("Conversion factor F = (1+Rf*)/(1+Rf)", "1.0000000", f"{res.conversion_factor:.7f}"),
        ("Unlevered beta βU", f"{res.unlevered_beta:.4f}", f"{res.unlevered_beta:.4f}"),
        ("Relevered beta βE", f"{res.relevered_beta:.4f}", f"{res.relevered_beta:.4f}"),
        ("Equity risk premium", calc.pct(res.erp_dec), calc.pct(res.erp_dec)),
        ("Cost of equity", calc.pct(res.cost_of_equity_usd_dec), calc.pct(res.cost_of_equity_foreign_dec)),
        ("Borrowing spread", calc.pct(res.spread_usd_dec),
         calc.pct(res.spread_foreign_dec) + (" (override)" if res.spread_foreign_is_override else " (s × F)")),
        ("Pretax borrowing cost", calc.pct(res.pretax_cost_of_debt_usd_dec), calc.pct(res.pretax_cost_of_debt_foreign_dec)),
        ("After-tax borrowing cost", calc.pct(res.aftertax_cost_of_debt_usd_dec), calc.pct(res.aftertax_cost_of_debt_foreign_dec)),
        ("Equity weight (1 − d)", calc.pct(res.equity_weight_dec), calc.pct(res.equity_weight_dec)),
        ("Debt weight d", calc.pct(res.debt_weight_dec), calc.pct(res.debt_weight_dec)),
        ("Tax rate used τ", calc.pct(res.tax_rate_usd_dec), calc.pct(res.tax_rate_foreign_dec)),
        ("WACC", calc.pct(res.wacc_usd_dec), calc.pct(res.wacc_foreign_dec)),
    ],
    columns=["Item", "USD", fcol],
)
left, right = st.columns([3, 2])
left.dataframe(table, hide_index=True, width="stretch", height=490)
right.markdown("**Inputs used and where they came from**")
right.dataframe(pd.DataFrame(inputs_used, columns=["Input", "Value", "Source"]), hide_index=True, width="stretch", height=390)

# Show the math -------------------------------------------------------------
P = calc.pct
with st.expander("Show the math", expanded=False):
    d, tau, de, be = d_dec, tau_dec, res.debt_to_equity, res.relevered_beta
    f = res.conversion_factor
    eq_usd, debt_usd = (1 - d) * res.cost_of_equity_usd_dec, d * res.aftertax_cost_of_debt_usd_dec
    eq_for, debt_for = (1 - d) * res.cost_of_equity_foreign_dec, d * res.aftertax_cost_of_debt_foreign_dec
    s_star_line = (
        f"s* = manual override = **{P(res.spread_foreign_dec)}**"
        if res.spread_foreign_is_override
        else f"s* = s × F = {P(s_dec)} × {f:.7f} = **{P(res.spread_foreign_dec)}**"
    )
    st.markdown(
        f"""
**Explanation only — unlevering** (not computed; Damodaran already provides βU): βU = βE / [1 + (1 − τ) × D/E]

**USD**
1. D/E = d / (1 − d) = {d:.4f} / (1 − {d:.4f}) = **{de:.6f}**
2. βE = βU × [1 + (1 − τ) × D/E] = {beta_u:.4f} × [1 + (1 − {tau:.4f}) × {de:.6f}] = **{be:.6f}**
3. Re = Rf + βE × ERP = {P(rf_us_dec)} + {be:.6f} × {P(erp_dec)} = **{P(res.cost_of_equity_usd_dec)}**
4. Rd = Rf + s = {P(rf_us_dec)} + {P(s_dec)} = **{P(res.pretax_cost_of_debt_usd_dec)}**
5. Rd × (1 − τ) = {P(res.pretax_cost_of_debt_usd_dec)} × (1 − {tau:.4f}) = **{P(res.aftertax_cost_of_debt_usd_dec)}**
6. WACC = (1 − d) × Re + d × Rd × (1 − τ) = {1 - d:.4f} × {P(res.cost_of_equity_usd_dec)} + {d:.4f} × {P(res.aftertax_cost_of_debt_usd_dec)}
   = {eq_usd * 100:.4f} pp (equity) + {debt_usd * 100:.4f} pp (debt) = **{P(res.wacc_usd_dec)}**

**{country} currency**

7. F = (1 + Rf*) / (1 + Rf) = (1 + {P(rf_for_dec)}) / (1 + {P(rf_us_dec)}) = **{f:.7f}**
8. 1 + Re* = (1 + Re) × F = (1 + {P(res.cost_of_equity_usd_dec)}) × {f:.7f} → Re* = **{P(res.cost_of_equity_foreign_dec)}**
9. {s_star_line}; Rd* = Rf* + s* = {P(rf_for_dec)} + {P(res.spread_foreign_dec)} = **{P(res.pretax_cost_of_debt_foreign_dec)}**
   (identity check: (1 + Rd) × F − 1 = {P((1 + res.pretax_cost_of_debt_usd_dec) * f - 1)}{' — differs because the override is ON' if res.spread_foreign_is_override else ''})
   Rd* × (1 − τ) = {P(res.pretax_cost_of_debt_foreign_dec)} × (1 − {tau:.4f}) = **{P(res.aftertax_cost_of_debt_foreign_dec)}**
10. WACC* = (1 − d) × Re* + d × Rd* × (1 − τ) = {1 - d:.4f} × {P(res.cost_of_equity_foreign_dec)} + {d:.4f} × {P(res.aftertax_cost_of_debt_foreign_dec)}
   = {eq_for * 100:.4f} pp (equity) + {debt_for * 100:.4f} pp (debt) = **{P(res.wacc_foreign_dec)}**
"""
    )

# Built-in checks -----------------------------------------------------------
st.subheader("Built-in checks (run on your current inputs)")
for chk in calc.iter_checks(inp):
    st.markdown(f"{'✅' if chk.passed else '❌'} **{chk.name}** — {chk.detail}")

# Key assumptions -----------------------------------------------------------
st.subheader("Key assumptions")
st.markdown(
    f"""
- **Risk is measured relative to the U.S. market**, not the investor's own portfolio or a world portfolio.
- **U.S. industry betas proxy for project risk**; CAPM omits any other sources of priced risk.
- **Government yields are risk-free proxies**, but they include liquidity and term premia and may include sovereign default risk.
- **Currency conversion assumes equal real rates and relative PPP**, so the yield difference reflects expected depreciation: F = (1 + Rf*)/(1 + Rf).
- **No separate country equity risk premium**; currency risk is treated as diversifiable (FX betas near zero).
- **Target leverage stays constant**, interest deductions are fully usable, and unlevering assumes a zero debt beta.
- **Same τ, d and ERP in both currencies**; interest is deducted in each financing currency.
- **Data dates:** betas: {betas_res.as_of or 'n/a'} ({ICON[betas_res.status]}); BBB spread: {spread_res.as_of or 'n/a'} ({ICON[spread_res.status]});
  tax: {tax_res.as_of or 'n/a'} ({ICON[tax_res.status]}); yields: {yields_res.as_of or 'n/a'} ({ICON[yields_res.status]}). App fetch time: {yields_res.fetched_at}.
"""
)

# All-industry table ---------------------------------------------------------
st.subheader("All industries (current country, tax, ERP and debt settings)")
ind_df = pd.DataFrame()
if betas:
    raw_rows = calc.all_industry_rows(betas, inp)
    ind_df = pd.DataFrame(raw_rows)
    disp = ind_df.copy()
    pct_cols = ["Cost of equity USD", "WACC USD", "Cost of equity foreign", "WACC foreign"]
    for col in pct_cols:
        disp[col] = disp[col] * 100.0  # the single decimal -> percent conversion for this table
    disp = disp.rename(columns={c: c.replace("foreign", f"({country})") + " (%)" for c in pct_cols})
    sel = ss.get("industry")

    def _hl(row):
        return ["background-color: #fff3b0; font-weight: 600" if row["Industry"] == sel else "" for _ in row]

    styler = disp.style.apply(_hl, axis=1).format(
        {c: "{:.4f}" for c in disp.columns if c != "Industry"}
    )
    st.caption(f"Selected industry highlighted: **{sel}**. Click a column header to sort.")
    st.dataframe(styler, hide_index=True, width="stretch", height=420)
    if beta_ovr:
        st.info("The beta override applies to the selected-industry results above, not to this table (which uses Damodaran's betas).")
else:
    st.info("Industry betas are unavailable, so the all-industry table cannot be built.")

# Downloads ------------------------------------------------------------------
st.subheader("Downloads")
scenario_rows = [("Input", n, v, s) for n, v, s in inputs_used]
for _, row in table.iterrows():
    scenario_rows.append(("Output", row["Item"], f"USD: {row['USD']} | {fcol}: {row[fcol]}", "computed"))
for k, v in res.as_dict().items():
    scenario_rows.append(("Output (full precision, decimal)", k, repr(v), ""))
for r in (betas_res, spread_res, tax_res, yields_res):
    scenario_rows.append(("Source", r.name, f"{r.status}; as of {r.as_of or 'n/a'}; fetched {r.fetched_at}", r.source_url))
for chk in calc.iter_checks(inp):
    scenario_rows.append(("Check", chk.name, "PASS" if chk.passed else "FAIL", chk.detail))
scenario_csv = pd.DataFrame(scenario_rows, columns=["Section", "Item", "Value", "Source / detail"]).to_csv(index=False)
d1, d2 = st.columns(2)
d1.download_button("⬇️ Current scenario (CSV)", scenario_csv, file_name="scenario.csv", mime="text/csv")
if not ind_df.empty:
    d2.download_button(
        "⬇️ All-industry table (CSV, decimals)",
        ind_df.assign(Country=country, Tax_rate_dec=tau_dec, ERP_dec=erp_dec, Debt_share_dec=d_dec).to_csv(index=False),
        file_name="all_industries.csv", mime="text/csv",
    )
