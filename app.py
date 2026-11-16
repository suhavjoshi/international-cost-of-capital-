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
yield_dates: dict[str, str] = yields_res.data.get("dates", {}) if yields_res.status !=
