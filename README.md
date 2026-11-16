# International Cost of Capital Calculator

A Streamlit app for an international finance class. It estimates a project's cost of equity, cost of debt and WACC in **USD**, then converts them into a **selected foreign currency** with the yield-ratio conversion factor from the class deck. The math is kept transparent: every formula is shown with the actual numbers plugged in, and built-in checks run live on your inputs.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit user interface (inputs, tables, math, checks, downloads) |
| `calculations.py` | Pure calculation functions. No Streamlit and no network. |
| `data_sources.py` | Fetching and parsing of the four data sources (one try/except per source) |
| `test_calculations.py` | Unit tests for the math (the eight required checks) |
| `test_data_sources.py` | Offline parser tests on sample pages, plus an app smoke test with mocked data |
| `requirements.txt` | Packages with minimum versions |

## Formulas (class deck)

An asterisk (*) means the foreign currency. Domestic currency = USD. **All rates are stored as decimals** (0.065, not 6.5). They are converted to percent only for display.

1. D/E = d / (1 − d)
2. βE = βU × [1 + (1 − τ) × D/E]  (debt and tax shield have zero beta)
3. Re = Rf + βE × ERP  (U.S. 10y yield as risk-free rate; no country risk premium)
4. Rd = Rf + s
5. After-tax Rd = Rd × (1 − τ)
6. WACC = (1 − d) × Re + d × Rd × (1 − τ)
7. F = (1 + Rf*) / (1 + Rf)
8. 1 + Re* = (1 + Re) × F
9. s* = s × F (default) or a manual value (override); Rd* = Rf* + s*  (with the default, this equals (1 + Rd) × F − 1)
10. WACC* = (1 − d) × Re* + d × Rd* × (1 − τ)

The same τ, d and ERP are used in both currencies. τ is the U.S. or the selected country's OECD rate (sidebar toggle), and that single τ is used for the beta relevering and for both debt tax shields. The unlevering formula βU = βE / [1 + (1 − τ) D/E] is shown for explanation only. Damodaran's unlevered beta is used directly.

## Data sources

| Item | Source | How it is read |
|---|---|---|
| Unlevered industry betas | Damodaran `betas.xls` (pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) | pandas + xlrd. The header row is found by searching for "Industry Name". The column named exactly "Unlevered beta" is used, not "Unlevered beta corrected for cash". "Total Market" rows are dropped. The "Date updated" text is shown. |
| USD BBB spread | Damodaran ratings page (`ratings.html`) | The section is found by its heading text "For large non-financial service firms", not by position. The `Baa2/BBB` row's spread is read. The table and section used are listed in Data status. "Data used is as of …" is shown. |
| Corporate tax rates | OECD Corporate Tax Statistics, combined (central + sub-central) statutory CIT rate | 1) the OECD report page; 2) the OECD Data Explorer SDMX API (dataflow `OECD.CTP.TPS:DSD_TAX_CIT@DF_CIT`), latest year per country, targeted small-business rates excluded; 3) **fallback**: a hard-coded 2024 table with a visible ⚠️ warning. |
| 10y government yields | Trading Economics `/bonds` | `requests` with a browser User-Agent, then `pandas.read_html`. Names are mapped to OECD names (`COUNTRY_ALIASES`, e.g. "South Korea" → "Korea"). A 403 or layout change shows an error, and you type yields manually. |

**About the tax fallback table:** the OECD figure on the report page is an image, so the app first tries the SDMX API. If both fail, it uses a fallback table of **2024** combined rates. Those values are OECD figures **as republished by the Tax Foundation** (European table rounded to 0.1 pp; non-European OECD members from its worldwide 2024 table). The app labels them ⚠️ fallback. Verify them against the OECD database before relying on them.

**Rules the app follows:** values are cached for one day (`st.cache_data`, ttl = 1 day). **Refresh data** clears the cache. A failed source never breaks the others. No value is ever invented: a missing value forces its manual override on, with an empty box you must fill. Fetched values are checked against plausible ranges (beta 0.2–3.0, tax 0–50%, BBB spread 0.3–5%, 10y yield −1% to 40%), and a warning appears if one falls outside.

## Using the app

- First load: Software (System & Application), United States, U.S. tax rate, ERP 6.5%, d = 30%, all overrides off.
- Each override has its own checkbox. When ON, a box appears, prefilled with the fetched value; it keeps your value until you change it (also if you switch country).
- The tax override replaces whichever rate the toggle selects. While it is ON, the toggle has no effect.
- **Load deck example (Colombia)** fills the overrides with the deck's reference inputs (βU 0.80, τ 35%, Rf 4%, Rf* 12%, s 1.5%, ERP 6.5%, d 30%; foreign-spread override off) and selects Colombia if it is in the country list.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q                     # all tests should pass
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Push these files to a **public** GitHub repo, with `app.py` at the root.
2. Go to share.streamlit.io → **Create app** → choose the repo, branch and `app.py`.
3. Under **Advanced settings**, choose Python 3.11 or newer.
4. Deploy. Trading Economics often blocks cloud servers (HTTP 403). The app then shows ❌ for yields, and you type them in.

## Verification

### Automated
`pytest -q` runs:
- the deck's Colombia case to 4 decimals (D/E 0.4286; βE 1.0229; Re 10.6486%; Rd 5.5000%; 3.5750%; WACC 8.5265%; F 1.0769231; Re* 19.1600%; s* 1.6154%; Rd* 13.6154%; 8.8500%; WACC* 16.0670%)
- equal yields → equal results
- zero debt → WACC = cost of equity
- foreign-spread override leaves USD unchanged
- s × F spread equivalence
- tax toggle consistency
- monotonicity
- percent-instead-of-decimal raises `InputUnitError`
- parser tests on sample pages
- app smoke tests (defaults, deck example button, failed source)

### Rebuild the Colombia example in Excel
| Cell | Label | Value / formula | Result |
|---|---|---|---|
| B1 | βU | 0.80 | |
| B2 | d | 0.30 | |
| B3 | τ | 0.35 | |
| B4 | ERP | 0.065 | |
| B5 | Rf | 0.04 | |
| B6 | Rf* | 0.12 | |
| B7 | s | 0.015 | |
| B9 | D/E | `=B2/(1-B2)` | 0.428571 |
| B10 | βE | `=B1*(1+(1-B3)*B9)` | 1.022857 |
| B11 | Re | `=B5+B10*B4` | 10.6486% |
| B12 | Rd | `=B5+B7` | 5.5000% |
| B13 | Rd(1−τ) | `=B12*(1-B3)` | 3.5750% |
| B14 | WACC | `=(1-B2)*B11+B2*B13` | 8.5265% |
| B15 | F | `=(1+B6)/(1+B5)` | 1.0769231 |
| B16 | Re* | `=(1+B11)*B15-1` | 19.1600% |
| B17 | s* | `=B7*B15` | 1.6154% |
| B18 | Rd* | `=B6+B17` | 13.6154% |
| B19 | Rd*(1−τ) | `=B18*(1-B3)` | 8.8500% |
| B20 | WACC* | `=(1-B2)*B16+B2*B19` | 16.0670% |
| B21 | check | `=(1+B12)*B15-1-B18` | 0 (to rounding) |

### The three assignment checks in the app
1. **Equal yields → equal results:** click *Load deck example*, then set the *Selected-country 10y yield* override to **4.0000** (same as the U.S. yield), with the foreign-spread override **off**. F shows 1.0000000, and every row matches across the two columns (WACC 8.5265% in both). Another way: pick **United States** as the country with no overrides.
2. **Zero debt → WACC = cost of equity:** click *Load deck example*, set **d = 0**. βE = 0.8000; USD WACC = Re = **9.2000%**; foreign WACC = Re* = **17.6000%**.
3. **Foreign-spread override leaves USD unchanged:** click *Load deck example* and note the USD column (WACC 8.5265%). Tick *Override foreign borrowing spread s** and enter **3.0000**. The USD column doesn't change. Foreign: Rd* = 15.0000%, after-tax 9.7500%, WACC* = **16.3370%**.

The **Built-in checks** panel runs checks 1–3 on whatever inputs are on screen, plus the deck reference case.
