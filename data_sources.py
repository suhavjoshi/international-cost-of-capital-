"""Data fetching and parsing for the International Cost of Capital Calculator.

Each ``fetch_*`` function downloads one source and returns a ``SourceResult``.
Each ``parse_*`` function is pure (bytes/str in, data out) so it can be tested
offline. No function here ever invents a value: on failure the result has
``status="failed"`` and an empty ``data`` dict, and the app asks for manual input.

All rates returned are DECIMALS (0.0111, not 1.11).
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Source URLs
# ---------------------------------------------------------------------------
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
OECD_PAGE_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/"
    "statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
)
# OECD Data Explorer (SDMX) dataflow: "Corporate income tax (CIT) - statutory and targeted small business rates"
OECD_SDMX_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod=2018&dimensionAtObservation=AllDimensions&format=csvfilewithlabels"
)
OECD_DATA_EXPLORER_URL = "https://data-explorer.oecd.org/ (dataflow OECD.CTP.TPS:DSD_TAX_CIT@DF_CIT)"
BONDS_URL = "https://tradingeconomics.com/bonds"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT_S = 25


@dataclass
class SourceResult:
    """Outcome of one data fetch."""

    name: str
    status: str  # "live" | "fallback" | "failed"
    source_url: str
    fetched_at: str  # UTC time the app fetched it
    as_of: str | None = None  # date published on the source, if any
    data: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_S)
    resp.raise_for_status()
    return resp


def _norm(text: object) -> str:
    """Lower-case, collapse whitespace, strip. Used for header matching."""
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


# ---------------------------------------------------------------------------
# Country names: everything is normalised to OECD-style names
# ---------------------------------------------------------------------------
COUNTRY_ALIASES: dict[str, str] = {
    # Trading Economics / common names  ->  OECD names
    "us": "United States",
    "u.s.": "United States",
    "usa": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "great britain": "United Kingdom",
    "south korea": "Korea",
    "republic of korea": "Korea",
    "korea, republic of": "Korea",
    "czech republic": "Czechia",
    "slovakia": "Slovak Republic",
    "turkey": "Türkiye",
    "turkiye": "Türkiye",
    "china": "China (People's Republic of)",
    "china (people's republic of)": "China (People's Republic of)",
    "hong kong": "Hong Kong (China)",
    "hong kong, china": "Hong Kong (China)",
    "hong kong (china)": "Hong Kong (China)",
    "vietnam": "Viet Nam",
    "russian federation": "Russia",
    "macau": "Macao (China)",
    "macao, china": "Macao (China)",
    "taiwan": "Chinese Taipei",
    "ivory coast": "Côte d'Ivoire",
    "cote d'ivoire": "Côte d'Ivoire",
    "egypt, arab rep.": "Egypt",
}


def _strip_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


def canonical_country(name: str) -> str:
    """Map a source-specific country name to the OECD-style name used in the app."""
    clean = re.sub(r"\s+", " ", str(name)).strip()
    key = clean.casefold()
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key]
    key2 = _strip_accents(key)
    if key2 in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key2]
    return clean


# ---------------------------------------------------------------------------
# 1. Damodaran unlevered betas
# ---------------------------------------------------------------------------
TARGET_BETA_COLUMN = "unlevered beta"


def parse_betas_xls(content: bytes, engine: str = "xlrd") -> tuple[dict[str, float], dict]:
    """Parse Damodaran's betas.xls (engine xlrd; ``engine`` is a parameter only so tests can use .xlsx).

    Returns (betas, meta) where betas maps industry -> unlevered beta and meta
    has 'sheet', 'header_row', 'column', 'as_of', 'dropped_rows'.
    """
    sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, engine=engine)
    for sheet_name, raw in sheets.items():
        header_row = None
        for i in range(min(len(raw), 60)):
            if any(_norm(v) == "industry name" for v in raw.iloc[i].tolist()):
                header_row = i
                break
        if header_row is None:
            continue

        headers = [_norm(v) for v in raw.iloc[header_row].tolist()]
        name_col = headers.index("industry name")
        matches = [j for j, h in enumerate(headers) if h == TARGET_BETA_COLUMN]
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one column named 'Unlevered beta' in sheet '{sheet_name}', "
                f"found {len(matches)}. Headers: {headers}"
            )
        beta_col = matches[0]
        matched_header = str(raw.iat[header_row, beta_col]).strip()
        if "corrected" in _norm(matched_header):  # belt and braces
            raise ValueError("Matched the cash-corrected beta column by mistake.")

        # 'Date updated' text above the header
        as_of = None
        for i in range(header_row):
            row = raw.iloc[i].tolist()
            for j, v in enumerate(row):
                if isinstance(v, str) and "date updated" in v.casefold():
                    after = v.split(":", 1)[1].strip() if ":" in v else ""
                    if after:
                        as_of = after
                    else:
                        for w in row[j + 1 :]:
                            if pd.notna(w) and str(w).strip():
                                as_of = w.strftime("%B %d, %Y") if hasattr(w, "strftime") else str(w).strip()
                                break
                    break
            if as_of:
                break

        betas: dict[str, float] = {}
        dropped: list[str] = []
        for i in range(header_row + 1, len(raw)):
            name = raw.iat[i, name_col]
            if pd.isna(name) or not str(name).strip():
                continue
            name = str(name).strip()
            if name.casefold().startswith("total market"):
                dropped.append(name)
                continue
            val = pd.to_numeric(raw.iat[i, beta_col], errors="coerce")
            if pd.isna(val):
                dropped.append(name)
                continue
            betas[name] = float(val)
        if not betas:
            raise ValueError("Header found but no industry rows parsed.")
        meta = {
            "sheet": str(sheet_name),
            "header_row": header_row + 1,  # 1-based for humans
            "column": matched_header,
            "as_of": as_of,
            "dropped_rows": dropped,
        }
        return betas, meta
    raise ValueError("Could not find an 'Industry Name' header cell in any sheet.")


def fetch_betas() -> SourceResult:
    """Download and parse the Damodaran betas file."""
    res = SourceResult("Unlevered industry betas (Damodaran)", "failed", BETAS_URL, _now())
    try:
        content = _get(BETAS_URL).content
        betas, meta = parse_betas_xls(content)
        res.status = "live"
        res.as_of = meta["as_of"]
        res.data = {"betas": betas, "meta": meta}
        res.notes.append(
            f"Sheet '{meta['sheet']}', header row {meta['header_row']}, column matched by name: "
            f"'{meta['column']}' (not the cash-corrected column). "
            f"{len(betas)} industries; dropped summary rows: {', '.join(meta['dropped_rows']) or 'none'}."
        )
    except Exception as exc:  # noqa: BLE001
        res.error = f"{type(exc).__name__}: {exc}"
    return res


# ---------------------------------------------------------------------------
# 2. Damodaran ratings / BBB spread
# ---------------------------------------------------------------------------
_LARGE_RE = re.compile(r"large\s+non-?\s*financial", re.I)
_SECTION_RE = re.compile(r"^\s*for\s+.*(firm|compan)", re.I)


def _pct_text_to_dec(text: str) -> float:
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", str(text))
    if not m:
        raise ValueError(f"No percent value in {text!r}")
    return float(m.group(1)) / 100.0


def parse_ratings_html(html: str, rating: str = "Baa2/BBB") -> tuple[float, dict]:
    """Find the large non-financial firm table on Damodaran's ratings page and read the spread for ``rating``.

    The table is located by its heading text, never by position. Returns (spread_dec, meta).
    """
    tables = pd.read_html(io.StringIO(html))
    for t_idx, tbl in enumerate(tables):
        grid = tbl.astype(str).where(tbl.notna(), "").values.tolist()
        # include header labels as a row in case read_html promoted the heading row to the header
        header_row = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in tbl.columns]
        grid = [header_row] + grid
        for r_idx, row in enumerate(grid):
            cols = [j for j, v in enumerate(row) if _LARGE_RE.search(str(v))]
            if not cols:
                continue
            c0 = cols[0]
            heading = str(row[c0]).strip()
            window = range(c0, min(c0 + 4, len(row)))
            for r2 in range(r_idx + 1, len(grid)):
                cells = [str(grid[r2][j]).strip() for j in window]
                if any(_SECTION_RE.search(x) for x in cells) and not any(_LARGE_RE.search(x) for x in cells):
                    break  # next section (e.g. smaller and riskier firms)
                for k, j in enumerate(window):
                    if cells[k].replace(" ", "").casefold() == rating.replace(" ", "").casefold():
                        for j2 in range(j + 1, min(c0 + 4, len(grid[r2]))):
                            txt = str(grid[r2][j2])
                            if "%" in txt:
                                spread = _pct_text_to_dec(txt)
                                meta = {
                                    "table_index": t_idx,
                                    "heading": heading,
                                    "row": cells[k],
                                    "spread_text": txt.strip(),
                                }
                                return spread, meta
            # heading found but no rating row: keep scanning other matches
    raise ValueError(f"Could not find a '{rating}' row under a 'large non-financial service firms' heading.")


def parse_ratings_as_of(html: str) -> str | None:
    """Pull 'Data used is as of <Month Year>' from the ratings page text."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"Data used is as of\s+([A-Za-z]+\s+\d{4})", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"Date of Analysis:?\s*([^.<]{3,60})", text, re.I)
    return m.group(1).strip() if m else None


def fetch_bbb_spread() -> SourceResult:
    """Download the ratings page and read the Baa2/BBB spread for large non-financial firms."""
    res = SourceResult("USD BBB spread (Damodaran ratings)", "failed", RATINGS_URL, _now())
    try:
        html = _get(RATINGS_URL).text
        spread, meta = parse_ratings_html(html)
        res.status = "live"
        res.as_of = parse_ratings_as_of(html)
        res.data = {"spread_dec": spread, "meta": meta}
        res.notes.append(
            f"Used HTML table #{meta['table_index'] + 1}, section '{meta['heading']}', "
            f"row '{meta['row']}', spread cell '{meta['spread_text']}'."
        )
    except Exception as exc:  # noqa: BLE001
        res.error = f"{type(exc).__name__}: {exc}"
    return res


# ---------------------------------------------------------------------------
# 3. OECD statutory corporate income tax rates (combined)
# ---------------------------------------------------------------------------
# FALLBACK ONLY. Used when neither the OECD page nor the OECD SDMX API can be read.
# Year: 2024 (OECD Corporate Tax Statistics 2024 / OECD Table II.1), combined central + sub-central.
# These numbers were NOT read from OECD directly; they are OECD figures as republished by the Tax Foundation:
#   A = Tax Foundation, "Corporate Income Tax Rates in Europe, 2024" (source: OECD Table II.1; Bloomberg Tax),
#       rates rounded to 0.1 pp.
#   B = Tax Foundation, "Corporate Tax Rates around the World, 2024", Table 6 (OECD members; source note
#       cites OECD "Corporate income tax statutory and targeted small business rates").
# Only OECD members / jurisdictions in table A are included. Values are percent here and converted once below.
FALLBACK_YEAR = 2024
FALLBACK_SOURCE = (
    "Hard-coded fallback: OECD 2024 combined statutory CIT rates as republished by the Tax Foundation "
    "(Europe 2024 table, rounded to 0.1 pp; Worldwide 2024 Table 6 for non-European OECD members). "
    "Verify against the OECD Corporate Tax Statistics database before relying on them."
)
_FALLBACK_TAX_PCT: dict[str, tuple[float, str]] = {
    "Austria": (23.0, "A"), "Belgium": (25.0, "A"), "Bulgaria": (10.0, "A"), "Croatia": (18.0, "A"),
    "Cyprus": (12.5, "A"), "Czechia": (19.0, "A"), "Denmark": (22.0, "A"), "Estonia": (20.0, "A"),
    "Finland": (20.0, "A"), "France": (25.8, "A"), "Germany": (29.9, "A"), "Greece": (22.0, "A"),
    "Hungary": (9.0, "A"), "Iceland": (20.0, "A"), "Ireland": (12.5, "A"), "Italy": (27.8, "A"),
    "Latvia": (20.0, "A"), "Lithuania": (15.0, "A"), "Luxembourg": (24.9, "A"), "Malta": (35.0, "A"),
    "Netherlands": (25.8, "A"), "Norway": (22.0, "A"), "Poland": (19.0, "A"), "Portugal": (31.5, "A"),
    "Romania": (16.0, "A"), "Slovak Republic": (21.0, "A"), "Slovenia": (19.0, "A"), "Spain": (25.0, "A"),
    "Sweden": (20.6, "A"), "Switzerland": (19.7, "A"), "Türkiye": (25.0, "A"), "United Kingdom": (25.0, "A"),
    "United States": (25.63, "B"), "Canada": (26.14, "B"), "Mexico": (30.0, "B"), "Colombia": (35.0, "B"),
    "Chile": (27.0, "B"), "Japan": (29.74, "B"), "Korea": (26.4, "B"), "Australia": (30.0, "B"),
    "New Zealand": (28.0, "B"), "Israel": (23.0, "B"), "Costa Rica": (30.0, "B"),
}
FALLBACK_TAX_DEC: dict[str, float] = {k: v[0] / 100.0 for k, v in _FALLBACK_TAX_PCT.items()}


def parse_oecd_page_html(html: str) -> tuple[dict[str, float], dict[str, int]]:
    """Try to read a combined-rate table straight from the OECD report page (usually a chart image, so often fails)."""
    tables = pd.read_html(io.StringIO(html))
    for tbl in tables:
        cols = [_norm(" ".join(map(str, c)) if isinstance(c, tuple) else c) for c in tbl.columns]
        comb = [i for i, c in enumerate(cols) if "combined" in c]
        if not comb:
            continue
        rates: dict[str, float] = {}
        years: dict[str, int] = {}
        year_m = re.search(r"(20\d\d)", cols[comb[0]])
        for _, row in tbl.iterrows():
            name = canonical_country(row.iloc[0])
            val = pd.to_numeric(str(row.iloc[comb[0]]).replace("%", ""), errors="coerce")
            if pd.notna(val):
                rates[name] = float(val) / 100.0
                years[name] = int(year_m.group(1)) if year_m else 0
        if rates:
            return rates, years
    raise ValueError("No table with a 'combined' rate column on the OECD page (it is likely a chart image).")


def parse_oecd_sdmx_csv(text: str) -> tuple[dict[str, float], dict[str, int], dict]:
    """Parse an OECD SDMX 'csvfilewithlabels' export of DSD_TAX_CIT@DF_CIT.

    Keeps rows whose Measure label contains 'combined' and no label mentions 'small' (targeted
    small-business rates). For each jurisdiction, takes the latest year with a value.
    Returns (rates_dec, years, meta).
    """
    df = pd.read_csv(io.StringIO(text), dtype=str)
    cols = {c.casefold(): c for c in df.columns}
    area_col = cols.get("reference area") or cols.get("ref_area")
    measure_col = cols.get("measure")
    time_col = cols.get("time_period")
    value_col = cols.get("obs_value")
    if not all([area_col, measure_col, time_col, value_col]):
        raise ValueError(f"Unexpected SDMX CSV columns: {list(df.columns)}")

    text_cols = [c for c in df.columns if c not in (value_col, time_col)]
    row_text = df[text_cols].fillna("").agg(" | ".join, axis=1).str.casefold()
    keep = df[measure_col].fillna("").str.casefold().str.contains("combined") & ~row_text.str.contains("small")
    sub = df.loc[keep, [area_col, time_col, value_col] + [c for c in df.columns if "unit" in c.casefold()]].copy()
    sub["value"] = pd.to_numeric(sub[value_col], errors="coerce")
    sub["year"] = pd.to_numeric(sub[time_col].str[:4], errors="coerce")
    sub = sub.dropna(subset=["value", "year"])
    if sub.empty:
        raise ValueError("No combined statutory CIT rows in the SDMX response.")

    unit_text = " ".join(sub[[c for c in sub.columns if "unit" in c.casefold()]].astype(str).stack().unique()).casefold()
    is_percent = ("percent" in unit_text) or ("pc" in unit_text.split()) or sub["value"].max() > 1.5
    scale = 100.0 if is_percent else 1.0

    rates: dict[str, float] = {}
    years: dict[str, int] = {}
    for area, grp in sub.groupby(area_col):
        latest = grp["year"].max()
        vals = grp.loc[grp["year"] == latest, "value"].unique()
        if len(vals) != 1:
            raise ValueError(f"Ambiguous combined rate for {area} in {int(latest)}: {list(vals)}")
        name = canonical_country(area)
        rates[name] = float(vals[0]) / scale
        years[name] = int(latest)
    meta = {"latest_year": max(years.values()), "unit": "percent" if is_percent else "decimal"}
    return rates, years, meta


def fetch_tax_rates() -> SourceResult:
    """OECD combined statutory CIT rates: report page -> SDMX API -> labelled fallback."""
    res = SourceResult("Corporate tax rates (OECD)", "failed", OECD_PAGE_URL, _now())
    errors: list[str] = []
    try:
        rates, years = parse_oecd_page_html(_get(OECD_PAGE_URL).text)
        res.status, res.data = "live", {"rates": rates, "years": years}
        res.as_of = f"{max(years.values())}" if years else None
        res.notes.append("Read from the OECD report page table.")
        return res
    except Exception as exc:  # noqa: BLE001
        errors.append(f"OECD report page: {type(exc).__name__}: {exc}")
    try:
        rates, years, meta = parse_oecd_sdmx_csv(_get(OECD_SDMX_URL).text)
        res.status, res.source_url = "live", OECD_SDMX_URL
        res.data = {"rates": rates, "years": years}
        res.as_of = f"latest year {meta['latest_year']} (per-country year shown in the app)"
        res.notes.append(
            f"OECD Data Explorer / SDMX dataflow {OECD_DATA_EXPLORER_URL}; measure = combined "
            f"(central + sub-central) statutory rate; {len(rates)} jurisdictions; unit {meta['unit']}."
        )
        res.notes.extend(f"(Tried first) {e}" for e in errors)
        return res
    except Exception as exc:  # noqa: BLE001
        errors.append(f"OECD SDMX API: {type(exc).__name__}: {exc}")
    res.status = "fallback"
    res.source_url = OECD_PAGE_URL
    res.data = {"rates": dict(FALLBACK_TAX_DEC), "years": {k: FALLBACK_YEAR for k in FALLBACK_TAX_DEC}}
    res.as_of = f"{FALLBACK_YEAR} (fallback table)"
    res.notes.append(FALLBACK_SOURCE)
    res.error = " | ".join(errors)
    return res


# ---------------------------------------------------------------------------
# 4. Trading Economics 10-year government bond yields
# ---------------------------------------------------------------------------
def _clean_bond_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", str(raw)).strip()
    name = re.sub(r"\b10\s*Y(ear)?\b", "", name, flags=re.I).strip()
    return canonical_country(name)


def parse_bonds_html(html: str) -> tuple[dict[str, float], dict[str, str]]:
    """Parse the Trading Economics bonds page. Returns (yields_dec, dates).

    Every table with a 'Yield' column is read; the first column holds the country.
    Trading Economics quotes yields in percent, converted to decimals here (once).
    """
    tables = pd.read_html(io.StringIO(html))
    yields: dict[str, float] = {}
    dates: dict[str, str] = {}
    for tbl in tables:
        cols = [str(c[-1] if isinstance(c, tuple) else c).strip() for c in tbl.columns]
        if "yield" not in [c.casefold() for c in cols]:
            continue
        y_idx = [c.casefold() for c in cols].index("yield")
        d_idx = [c.casefold() for c in cols].index("date") if "date" in [c.casefold() for c in cols] else None
        for _, row in tbl.iterrows():
            name = _clean_bond_name(row.iloc[0])
            if not name or name.casefold() in {"nan", ""}:
                continue
            val = pd.to_numeric(str(row.iloc[y_idx]).replace("%", "").replace(",", ""), errors="coerce")
            if pd.isna(val) or name in yields:
                continue
            yields[name] = float(val) / 100.0
            if d_idx is not None:
                dates[name] = str(row.iloc[d_idx])
    if not yields:
        raise ValueError("No table with a 'Yield' column found (layout may have changed).")
    return yields, dates


def fetch_yields() -> SourceResult:
    """Scrape 10y government bond yields from Trading Economics (no API key)."""
    res = SourceResult("10y government bond yields (Trading Economics)", "failed", BONDS_URL, _now())
    try:
        resp = requests.get(BONDS_URL, headers=HEADERS, timeout=TIMEOUT_S)
        if resp.status_code == 403:
            raise PermissionError("HTTP 403: Trading Economics blocked the request.")
        resp.raise_for_status()
        yields, dates = parse_bonds_html(resp.text)
        res.status = "live"
        res.data = {"yields": yields, "dates": dates}
        us_date = dates.get("United States")
        res.as_of = f"U.S. quote date on page: {us_date}" if us_date else "quote dates per country on page"
        res.notes.append(f"{len(yields)} countries parsed; names normalised to OECD names with COUNTRY_ALIASES.")
        if "United States" not in yields:
            res.notes.append("United States not found on the page: enter the U.S. yield manually.")
    except Exception as exc:  # noqa: BLE001
        res.error = f"{type(exc).__name__}: {exc}"
    return res
