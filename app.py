"""
Real Exchange Rate Explorer
===========================

A Streamlit app that downloads monthly exchange rates and consumer price
indices from the IMF SDMX 3.0 API and computes the bilateral real exchange
rate between any two economies.

Notation
--------
E_i(t)  units of country i's currency per 1 US dollar (IMF "ER" dataflow,
        period average). For the United States E = 1.0 by definition.
S(t)    units of Country 2 currency per 1 unit of Country 1 currency
        = E_2(t) / E_1(t)
P_i(t)  monthly CPI of country i (IMF "CPI" dataflow, index)
RER(t)  = S(t) * P1(t) / P2(t)
RER*    = mean of RER(t) over the valid months of the selected sample
Index   = 100 * RER(t) / RER*   (averages exactly 100 over the sample)

A rise in RER means Country 1's goods become more expensive relative to
Country 2's goods, i.e. a real appreciation of Country 1's currency.

No API keys are needed. Months with missing data are dropped (inner join);
nothing is ever forward-filled or interpolated.
"""

from __future__ import annotations

import inspect
import io
import math
import re
import traceback
from dataclasses import dataclass, field

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

# =============================================================================
# Configuration
# =============================================================================

APP_TITLE = "Real Exchange Rate Explorer"
BASE_URL = "https://api.imf.org/external/sdmx/3.0"
AGENCY = "IMF.STA"

START_PERIOD = "1976-M01"                    # value used in the c[TIME_PERIOD] filter
START_DATE = pd.Timestamp("1976-01-01")      # same date, used to filter after download

CACHE_TTL_SECONDS = 24 * 60 * 60             # cache downloads for ~24 hours
REQUEST_TIMEOUT = (10, 60)                   # (connect, read) seconds

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 RealExchangeRateExplorer/1.0"
)
ACCEPT_HEADERS = {
    "json": "application/vnd.sdmx.data+json;version=2.0.0",
    "csv": "application/vnd.sdmx.data+csv;version=2.0.0",
}
STRUCTURE_ACCEPT = "application/vnd.sdmx.structure+json;version=2.0.0, application/json;q=0.9"

# Order in which download strategies are tried for each series:
# JSON first (with and without the server-side date filter), then CSV.
FETCH_ATTEMPTS = [("json", True), ("json", False), ("csv", True), ("csv", False)]

EURO_AREA = "G163"
USA = "USA"

# Chart colours: mid-tone hues that read well on both light and dark backgrounds.
COLOR_REAL = "#2563EB"      # blue  (Real Index)
COLOR_REAL_MA = "#60A5FA"   # light blue (Real Index, 12-month MA)
COLOR_NOM = "#E8833A"       # orange (nominal S)
COLOR_NOM_MA = "#F4B183"    # light orange (nominal S, 12-month MA)
COLOR_REF = "rgba(128,128,128,0.85)"

# Hard-coded fallback list: ISO3 -> (display name, ISO 4217 currency code).
# Also used to attach currency codes to names coming from the IMF codelist.
# Euro-area members are labelled EUR, the unit in which the IMF reports
# their current exchange rates.
FALLBACK_COUNTRIES: dict[str, tuple[str, str]] = {
    EURO_AREA: ("Euro area", "EUR"),
    "USA": ("United States", "USD"),
    "GBR": ("United Kingdom", "GBP"),
    "JPN": ("Japan", "JPY"),
    "CHN": ("China", "CNY"),
    "CAN": ("Canada", "CAD"),
    "AUS": ("Australia", "AUD"),
    "NZL": ("New Zealand", "NZD"),
    "CHE": ("Switzerland", "CHF"),
    "SWE": ("Sweden", "SEK"),
    "NOR": ("Norway", "NOK"),
    "DNK": ("Denmark", "DKK"),
    "ISL": ("Iceland", "ISK"),
    "DEU": ("Germany", "EUR"),
    "FRA": ("France", "EUR"),
    "ITA": ("Italy", "EUR"),
    "ESP": ("Spain", "EUR"),
    "NLD": ("Netherlands", "EUR"),
    "BEL": ("Belgium", "EUR"),
    "AUT": ("Austria", "EUR"),
    "FIN": ("Finland", "EUR"),
    "IRL": ("Ireland", "EUR"),
    "PRT": ("Portugal", "EUR"),
    "GRC": ("Greece", "EUR"),
    "LUX": ("Luxembourg", "EUR"),
    "SVN": ("Slovenia", "EUR"),
    "SVK": ("Slovak Republic", "EUR"),
    "EST": ("Estonia", "EUR"),
    "LVA": ("Latvia", "EUR"),
    "LTU": ("Lithuania", "EUR"),
    "CYP": ("Cyprus", "EUR"),
    "MLT": ("Malta", "EUR"),
    "HRV": ("Croatia", "EUR"),
    "BGR": ("Bulgaria", "EUR"),
    "POL": ("Poland", "PLN"),
    "CZE": ("Czech Republic", "CZK"),
    "HUN": ("Hungary", "HUF"),
    "ROU": ("Romania", "RON"),
    "SRB": ("Serbia", "RSD"),
    "ALB": ("Albania", "ALL"),
    "MKD": ("North Macedonia", "MKD"),
    "BIH": ("Bosnia and Herzegovina", "BAM"),
    "MDA": ("Moldova", "MDL"),
    "UKR": ("Ukraine", "UAH"),
    "BLR": ("Belarus", "BYN"),
    "RUS": ("Russia", "RUB"),
    "TUR": ("Türkiye", "TRY"),
    "GEO": ("Georgia", "GEL"),
    "ARM": ("Armenia", "AMD"),
    "AZE": ("Azerbaijan", "AZN"),
    "KAZ": ("Kazakhstan", "KZT"),
    "UZB": ("Uzbekistan", "UZS"),
    "KGZ": ("Kyrgyz Republic", "KGS"),
    "ISR": ("Israel", "ILS"),
    "SAU": ("Saudi Arabia", "SAR"),
    "ARE": ("United Arab Emirates", "AED"),
    "QAT": ("Qatar", "QAR"),
    "KWT": ("Kuwait", "KWD"),
    "BHR": ("Bahrain", "BHD"),
    "OMN": ("Oman", "OMR"),
    "JOR": ("Jordan", "JOD"),
    "IRN": ("Iran", "IRR"),
    "EGY": ("Egypt", "EGP"),
    "MAR": ("Morocco", "MAD"),
    "DZA": ("Algeria", "DZD"),
    "TUN": ("Tunisia", "TND"),
    "ZAF": ("South Africa", "ZAR"),
    "NGA": ("Nigeria", "NGN"),
    "GHA": ("Ghana", "GHS"),
    "KEN": ("Kenya", "KES"),
    "ETH": ("Ethiopia", "ETB"),
    "TZA": ("Tanzania", "TZS"),
    "UGA": ("Uganda", "UGX"),
    "ZMB": ("Zambia", "ZMW"),
    "BWA": ("Botswana", "BWP"),
    "MUS": ("Mauritius", "MUR"),
    "CIV": ("Côte d'Ivoire", "XOF"),
    "SEN": ("Senegal", "XOF"),
    "CMR": ("Cameroon", "XAF"),
    "IND": ("India", "INR"),
    "PAK": ("Pakistan", "PKR"),
    "BGD": ("Bangladesh", "BDT"),
    "LKA": ("Sri Lanka", "LKR"),
    "NPL": ("Nepal", "NPR"),
    "IDN": ("Indonesia", "IDR"),
    "MYS": ("Malaysia", "MYR"),
    "THA": ("Thailand", "THB"),
    "PHL": ("Philippines", "PHP"),
    "VNM": ("Vietnam", "VND"),
    "SGP": ("Singapore", "SGD"),
    "KOR": ("Korea", "KRW"),
    "HKG": ("Hong Kong SAR", "HKD"),
    "MNG": ("Mongolia", "MNT"),
    "KHM": ("Cambodia", "KHR"),
    "MMR": ("Myanmar", "MMK"),
    "FJI": ("Fiji", "FJD"),
    "PNG": ("Papua New Guinea", "PGK"),
    "MEX": ("Mexico", "MXN"),
    "BRA": ("Brazil", "BRL"),
    "ARG": ("Argentina", "ARS"),
    "CHL": ("Chile", "CLP"),
    "COL": ("Colombia", "COP"),
    "PER": ("Peru", "PEN"),
    "URY": ("Uruguay", "UYU"),
    "PRY": ("Paraguay", "PYG"),
    "BOL": ("Bolivia", "BOB"),
    "ECU": ("Ecuador", "USD"),
    "CRI": ("Costa Rica", "CRC"),
    "GTM": ("Guatemala", "GTQ"),
    "HND": ("Honduras", "HNL"),
    "NIC": ("Nicaragua", "NIO"),
    "SLV": ("El Salvador", "USD"),
    "PAN": ("Panama", "PAB"),
    "DOM": ("Dominican Republic", "DOP"),
    "JAM": ("Jamaica", "JMD"),
    "TTO": ("Trinidad and Tobago", "TTD"),
    "BRB": ("Barbados", "BBD"),
}

# =============================================================================
# Small utilities
# =============================================================================


def stretch_kwargs(fn) -> dict:
    """Return the keyword that makes a Streamlit element full-width.

    Newer Streamlit versions use width="stretch"; older ones use
    use_container_width=True. Checking the signature keeps the app working
    on either, without deprecation warnings.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    width = params.get("width")
    if width is not None and isinstance(width.default, str):
        return {"width": "stretch"}
    if "use_container_width" in params:
        return {"use_container_width": True}
    return {}


def is_finite(x) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def fmt_num(x) -> str:
    """Human-friendly number for cards and labels (never used for the CSV)."""
    if not is_finite(x):
        return "—"
    x = float(x)
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.2f}"
    if ax >= 1:
        return f"{x:,.4f}"
    if ax >= 1e-3:
        return f"{x:.6f}"
    return f"{x:.4e}"


def plotly_num_format(series: pd.Series) -> str:
    """d3 format string for hover labels, chosen from the data's magnitude."""
    s = series.dropna().abs()
    med = float(s.median()) if not s.empty else 1.0
    if med >= 1000:
        return ",.2f"
    if med >= 1:
        return ",.4f"
    if med >= 1e-3:
        return ".6f"
    return ".4e"


def month_label(ts: pd.Timestamp) -> str:
    """YYYY-MM label used by the slider, CSV and filenames."""
    return pd.Timestamp(ts).strftime("%Y-%m")


def pretty_month(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).strftime("%b %Y")


# =============================================================================
# Parsing
# =============================================================================

_TIME_RE = re.compile(r"^\s*(\d{4})-?M?(\d{1,2})(?!\d)")


def parse_time_period(value) -> pd.Timestamp | None:
    """Turn '1976-M01', '1976M01', '1976-01' or '1976-01-01' into 1976-01-01.

    Anything that is not a monthly period (e.g. '1976' or '1976-Q1') returns None.
    """
    if value is None:
        return None
    m = _TIME_RE.match(str(value))
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return pd.Timestamp(year=year, month=month, day=1)


def to_monthly_series(times, values, name: str = "value") -> pd.Series:
    """Build a clean month-start indexed float series.

    - unparseable dates and non-numeric / non-positive values are dropped
    - duplicate months keep the last value
    - only months from START_DATE onward are kept
    - NO filling or interpolation of any kind
    """
    dates = [parse_time_period(t) for t in times]
    df = pd.DataFrame({"date": dates, "value": pd.to_numeric(pd.Series(list(values), dtype="object"), errors="coerce")})
    df = df.dropna(subset=["date", "value"])
    df = df[df["value"] > 0]
    if df.empty:
        return pd.Series(dtype="float64", name=name, index=pd.DatetimeIndex([], name="date").as_unit("ns"))
    df = df.drop_duplicates(subset="date", keep="last").sort_values("date")
    idx = pd.DatetimeIndex(df["date"], name="date").as_unit("ns")
    s = pd.Series(df["value"].astype("float64").to_numpy(), index=idx, name=name)
    return s[s.index >= START_DATE]


def _dim_value(v: dict):
    """A dimension value in SDMX-JSON can carry its code in 'id' or 'value'."""
    if isinstance(v, dict):
        return v.get("id", v.get("value"))
    return v


def _obs_value(raw):
    """SDMX-JSON observations are arrays whose first element is the value."""
    if isinstance(raw, list):
        return raw[0] if raw else None
    return raw


def parse_sdmx_json(payload: dict, name: str = "value") -> pd.Series:
    """Parse an SDMX-JSON (2.0 or 1.0) data message into a monthly series.

    Handles both 'series' layout (dimension at observation = TIME_PERIOD) and
    flat 'AllDimensions' layout. If several series are returned, the one with
    the most observations is used.
    """
    if not isinstance(payload, dict):
        raise ValueError("JSON payload is not an object")
    data = payload.get("data", payload)
    datasets = data.get("dataSets") or data.get("datasets") or []
    structures = data.get("structures")
    if not structures:
        single = data.get("structure") or payload.get("structure")
        structures = [single] if single else []
    if not datasets or not structures:
        raise ValueError("SDMX-JSON message has no dataSets/structures")

    best: pd.Series | None = None
    for ds in datasets:
        st_idx = ds.get("structure", 0) if isinstance(ds.get("structure", 0), int) else 0
        structure = structures[st_idx] if st_idx < len(structures) else structures[0]
        dims = structure.get("dimensions", {})
        obs_dims = dims.get("observation", []) or []
        if not obs_dims:
            raise ValueError("No observation dimensions in structure")
        time_pos = next((i for i, d in enumerate(obs_dims) if d.get("id") == "TIME_PERIOD"), 0)
        time_values = [_dim_value(v) for v in obs_dims[time_pos].get("values", [])]

        def collect(observations: dict) -> pd.Series:
            times, vals = [], []
            for okey, oval in (observations or {}).items():
                parts = str(okey).split(":")
                try:
                    t_index = int(parts[time_pos])
                except (IndexError, ValueError):
                    continue
                if 0 <= t_index < len(time_values):
                    times.append(time_values[t_index])
                    vals.append(_obs_value(oval))
            return to_monthly_series(times, vals, name)

        candidates = []
        if isinstance(ds.get("series"), dict):
            for _skey, sobj in ds["series"].items():
                candidates.append(collect(sobj.get("observations", {})))
        elif isinstance(ds.get("observations"), dict):
            candidates.append(collect(ds["observations"]))

        for c in candidates:
            if best is None or len(c) > len(best):
                best = c

    if best is None:
        return to_monthly_series([], [], name)
    return best


def parse_sdmx_csv(text: str, name: str = "value") -> pd.Series:
    """Parse an SDMX-CSV data message (needs TIME_PERIOD and OBS_VALUE columns)."""
    df = pd.read_csv(io.StringIO(text), dtype=str)
    cols = {c.strip().upper(): c for c in df.columns}
    if "TIME_PERIOD" not in cols or "OBS_VALUE" not in cols:
        raise ValueError("CSV has no TIME_PERIOD/OBS_VALUE columns")
    return to_monthly_series(df[cols["TIME_PERIOD"]].tolist(), df[cols["OBS_VALUE"]].tolist(), name)


def parse_codelist_json(payload: dict) -> dict[str, str]:
    """Return {code: English name} from an SDMX structure message codelist."""
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    codelists = data.get("codelists") or []
    out: dict[str, str] = {}
    for cl in codelists:
        for code in cl.get("codes", []) or []:
            cid = code.get("id")
            name = code.get("name")
            if not name:
                names = code.get("names") or {}
                name = names.get("en") or next(iter(names.values()), None)
            if cid and name:
                out[str(cid)] = str(name)
    return out


# =============================================================================
# Fetching (cached)
# =============================================================================


class FetchError(Exception):
    """Raised when a series cannot be downloaded or parsed.

    Exceptions are not cached by st.cache_data, so a failed request is retried
    on the next run instead of being remembered for 24 hours.
    """

    def __init__(self, message: str, log: list[dict]):
        super().__init__(message)
        self.log = log


def data_url(dataflow: str, key: str) -> str:
    return f"{BASE_URL}/data/dataflow/{AGENCY}/{dataflow}/+/{key}"


def prepared_url(url: str, params: dict | None) -> str:
    """Full URL including the query string (for the debug panel)."""
    return requests.Request("GET", url, params=params).prepare().url


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False, max_entries=500)
def download_series(dataflow: str, key: str) -> tuple[pd.Series, list[dict]]:
    """Download one IMF series, trying JSON then CSV, with and without the date filter.

    Returns (series, request_log). Raises FetchError if nothing usable came back.
    """
    url = data_url(dataflow, key)
    log: list[dict] = []
    for fmt, use_filter in FETCH_ATTEMPTS:
        params = {"c[TIME_PERIOD]": f"ge:{START_PERIOD}"} if use_filter else None
        full_url = prepared_url(url, params)
        headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT_HEADERS[fmt]}
        entry = {"format": fmt.upper(), "url": full_url, "status": None, "result": ""}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.ConnectionError as exc:
            entry["result"] = f"Connection error: {exc.__class__.__name__}"
            log.append(entry)
            raise FetchError("Could not connect to the IMF API (it may be down or unreachable).", log)
        except requests.exceptions.Timeout:
            entry["result"] = "Timed out"
            log.append(entry)
            continue
        except requests.exceptions.RequestException as exc:
            entry["result"] = f"Request failed: {exc.__class__.__name__}"
            log.append(entry)
            continue

        entry["status"] = resp.status_code
        if resp.status_code == 404:
            entry["result"] = "No data for this key (HTTP 404)"
            log.append(entry)
            continue
        if resp.status_code != 200:
            entry["result"] = f"HTTP {resp.status_code}: {resp.text[:200].strip()}"
            log.append(entry)
            continue

        try:
            if fmt == "json":
                series = parse_sdmx_json(resp.json(), name=key)
            else:
                series = parse_sdmx_csv(resp.text, name=key)
        except Exception as exc:  # malformed / unexpected payload -> try next strategy
            entry["result"] = f"Could not parse {fmt.upper()}: {exc.__class__.__name__}: {str(exc)[:150]}"
            log.append(entry)
            continue

        if series.empty:
            entry["result"] = "Response contained no monthly observations from 1976 onward"
            log.append(entry)
            continue

        entry["result"] = f"OK: {len(series)} monthly observations"
        log.append(entry)
        return series, log

    raise FetchError("The IMF API returned no usable monthly data for this series.", log)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def download_country_codelist() -> tuple[dict[str, str], str]:
    """Download the IMF COUNTRY codelist. Returns ({ISO3: name}, url)."""
    candidates = [
        f"{BASE_URL}/structure/codelist/IMF/CL_COUNTRY/+",
        f"{BASE_URL}/structure/codelist/IMF.STA/CL_COUNTRY/+",
        f"{BASE_URL}/structure/codelist/*/CL_COUNTRY/+",
    ]
    last_error = "no attempt made"
    for url in candidates:
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": STRUCTURE_ACCEPT},
                timeout=(10, 30),
            )
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code} at {url}"
                continue
            codes = parse_codelist_json(resp.json())
            # Keep ISO3-looking codes (countries); regional aggregates use other patterns.
            codes = {k: v for k, v in codes.items() if re.fullmatch(r"[A-Z]{3}", k)}
            if len(codes) >= 50:
                return codes, url
            last_error = f"Codelist at {url} had only {len(codes)} country codes"
        except requests.exceptions.ConnectionError:
            raise RuntimeError("Could not connect to the IMF API")
        except Exception as exc:
            last_error = f"{exc.__class__.__name__} at {url}"
    raise RuntimeError(last_error)


@dataclass
class LoadResult:
    """Outcome of loading one input series."""

    label: str                     # e.g. "Exchange rate (ER) for United Kingdom"
    series: pd.Series | None
    log: list[dict] = field(default_factory=list)
    error: str | None = None
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.series is not None and not self.series.empty


def usd_per_usd_series() -> pd.Series:
    """E_USA(t) = 1.0 for every month from January 1976 to the current month."""
    end = pd.Timestamp.today().normalize().replace(day=1)
    idx = pd.date_range(START_DATE, end, freq="MS", name="date").as_unit("ns")
    return pd.Series(1.0, index=idx, name="USA.XDC_USD")


def load_exchange_rate(iso3: str, country_name: str) -> LoadResult:
    label = f"Exchange rate (ER, national currency per USD) for {country_name}"
    if iso3 == USA:
        return LoadResult(
            label=label,
            series=usd_per_usd_series(),
            log=[{"format": "—", "url": "(not requested — USD per USD is 1.0 by definition)",
                  "status": None, "result": "Set to 1.0 for every month"}],
            note="Set to 1.0 by definition (not downloaded)",
        )
    try:
        s, log = download_series("ER", f"{iso3}.XDC_USD.PA_RT.M")
        return LoadResult(label=label, series=s, log=log)
    except FetchError as exc:
        return LoadResult(label=label, series=None, log=exc.log, error=str(exc))
    except Exception as exc:  # defensive: never let a download crash the app
        return LoadResult(label=label, series=None, error=f"Unexpected error: {exc}")


def load_cpi(iso3: str, country_name: str) -> LoadResult:
    label = f"Consumer price index (CPI) for {country_name}"
    try:
        s, log = download_series("CPI", f"{iso3}.CPI._T.IX.M")
        return LoadResult(label=label, series=s, log=log)
    except FetchError as exc:
        return LoadResult(label=label, series=None, log=exc.log, error=str(exc))
    except Exception as exc:
        return LoadResult(label=label, series=None, error=f"Unexpected error: {exc}")


def get_countries() -> tuple[dict[str, tuple[str, str | None]], str]:
    """Country table {ISO3: (name, currency or None)} and a note on its source.

    The IMF codelist is merged with the fallback list so that the Euro area and
    all major economies are always available, with currency codes attached.
    """
    countries: dict[str, tuple[str, str | None]] = {k: (v[0], v[1]) for k, v in FALLBACK_COUNTRIES.items()}
    try:
        codes, url = download_country_codelist()
        for iso3, name in codes.items():
            if iso3 not in countries:
                countries[iso3] = (name, None)
        return countries, f"IMF COUNTRY codelist ({len(codes)} codes) + built-in list"
    except Exception:
        return countries, "Built-in list (the IMF codelist could not be downloaded)"


def country_label(iso3: str, countries: dict) -> str:
    name, cur = countries.get(iso3, (iso3, None))
    return f"{name} ({iso3}, {cur})" if cur else f"{name} ({iso3})"


def currency_unit(iso3: str, countries: dict) -> str:
    _name, cur = countries.get(iso3, (iso3, None))
    return cur if cur else f"{iso3} currency"


# =============================================================================
# Computation
# =============================================================================


def build_panel(e1: pd.Series, e2: pd.Series, p1: pd.Series, p2: pd.Series) -> pd.DataFrame:
    """Inner-join the four inputs and compute S and RER for every valid month.

    A month is valid only if E1, E2, P1 and P2 are all observed. There is no
    forward-filling or interpolation: missing months simply drop out.
    """
    panel = pd.concat(
        {"E1": e1.astype("float64"), "E2": e2.astype("float64"),
         "P1": p1.astype("float64"), "P2": p2.astype("float64")},
        axis=1,
        join="inner",
    ).dropna()
    panel.index.name = "date"
    panel = panel.sort_index()
    # S = Country-2 currency per 1 unit of Country-1 currency = E2 / E1
    panel["S"] = panel["E2"] / panel["E1"]
    # RER = S * P1 / P2
    panel["RER"] = panel["S"] * panel["P1"] / panel["P2"]
    return panel


def compute_sample(panel: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, float]:
    """Restrict to [start, end], then compute RER* and the Real Index on THAT sample only."""
    sample = panel.loc[(panel.index >= start) & (panel.index <= end)].copy()
    if sample.empty:
        return sample, float("nan")
    rer_star = float(sample["RER"].mean())
    sample["RER_star"] = rer_star
    sample["real_index"] = 100.0 * sample["RER"] / rer_star
    return sample, rer_star


def series_coverage(name: str, s: pd.Series | None, note: str = "") -> dict:
    """First/last month, observation count and internal gaps of one series."""
    if s is None or s.empty:
        return {"Series": name, "First month": "—", "Last month": "—",
                "Observations": 0, "Missing months inside span": None, "Note": note or "No data"}
    first, last = s.index.min(), s.index.max()
    expected = (last.year - first.year) * 12 + (last.month - first.month) + 1
    return {"Series": name, "First month": month_label(first), "Last month": month_label(last),
            "Observations": int(len(s)), "Missing months inside span": int(expected - len(s)), "Note": note}


def build_export(sample: pd.DataFrame, iso1: str, iso2: str) -> pd.DataFrame:
    """CSV export frame with the exact column names requested (full precision)."""
    return pd.DataFrame({
        "date": [month_label(d) for d in sample.index],
        "country1_iso3": iso1,
        "country2_iso3": iso2,
        "E1_lcu_per_usd": sample["E1"].to_numpy(),
        "E2_lcu_per_usd": sample["E2"].to_numpy(),
        "S_country2_per_country1": sample["S"].to_numpy(),
        "P1_cpi": sample["P1"].to_numpy(),
        "P2_cpi": sample["P2"].to_numpy(),
        "RER": sample["RER"].to_numpy(),
        "RER_star": sample["RER_star"].to_numpy(),
        "real_index": sample["real_index"].to_numpy(),
    })


# =============================================================================
# Plotting
# =============================================================================


def _for_plot(sample: pd.DataFrame) -> pd.DataFrame:
    """Place the sample on a complete monthly grid so missing months show as
    visible breaks in the line (NaN), not as straight-line interpolation.
    The 12-month moving averages require 12 consecutive valid months."""
    full = pd.date_range(sample.index.min(), sample.index.max(), freq="MS", name="date").as_unit("ns")
    df = sample[["S", "real_index"]].reindex(full)
    df["real_index_ma"] = df["real_index"].rolling(12, min_periods=12).mean()
    df["S_ma"] = df["S"].rolling(12, min_periods=12).mean()
    return df


def _style_layout(fig: go.Figure, height: int = 540) -> None:
    fig.update_layout(
        height=height,
        hovermode="x unified",
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(
        hoverformat="%b %Y",
        showgrid=False,
        rangeslider=dict(visible=True, thickness=0.07),
        rangeselector=dict(
            buttons=[
                dict(count=5, label="5Y", step="year", stepmode="backward"),
                dict(count=10, label="10Y", step="year", stepmode="backward"),
                dict(count=20, label="20Y", step="year", stepmode="backward"),
                dict(step="all", label="All"),
            ],
            bgcolor="rgba(128,128,128,0.15)",
            activecolor="rgba(37,99,235,0.35)",
        ),
    )


def make_chart(sample: pd.DataFrame, mode: str, show_ma: bool, units: str) -> go.Figure:
    """Build the Real / Nominal / Both chart. 'Both' uses two separate y-axes."""
    df = _for_plot(sample)
    s_fmt = plotly_num_format(df["S"])
    x = df.index

    real_trace = go.Scatter(
        x=x, y=df["real_index"], name="Real Index (sample mean = 100)", mode="lines",
        line=dict(color=COLOR_REAL, width=2.4), connectgaps=False,
        hovertemplate="Real Index: %{y:.2f}<extra></extra>",
    )
    real_ma_trace = go.Scatter(
        x=x, y=df["real_index_ma"], name="Real Index, 12-month MA", mode="lines",
        line=dict(color=COLOR_REAL_MA, width=1.8, dash="dot"), connectgaps=False,
        hovertemplate="Real Index 12m MA: %{y:.2f}<extra></extra>",
    )
    nom_trace = go.Scatter(
        x=x, y=df["S"], name=f"Nominal S(t), {units}", mode="lines",
        line=dict(color=COLOR_NOM, width=2.4), connectgaps=False,
        hovertemplate=f"S(t): %{{y:{s_fmt}}} {units}<extra></extra>",
    )
    nom_ma_trace = go.Scatter(
        x=x, y=df["S_ma"], name="Nominal S(t), 12-month MA", mode="lines",
        line=dict(color=COLOR_NOM_MA, width=1.8, dash="dot"), connectgaps=False,
        hovertemplate=f"S(t) 12m MA: %{{y:{s_fmt}}} {units}<extra></extra>",
    )
    ref_kwargs = dict(
        y=100, line_dash="dash", line_color=COLOR_REF, line_width=1.2,
        annotation_text="100 = sample average", annotation_position="top left",
        annotation_font_size=11,
    )

    if mode == "Nominal":
        fig = go.Figure([nom_trace] + ([nom_ma_trace] if show_ma else []))
        fig.update_yaxes(title_text=units, tickformat=s_fmt if s_fmt.endswith("e") else None)
    elif mode == "Both":
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(real_trace, secondary_y=False)
        if show_ma:
            fig.add_trace(real_ma_trace, secondary_y=False)
        fig.add_trace(nom_trace, secondary_y=True)
        if show_ma:
            fig.add_trace(nom_ma_trace, secondary_y=True)
        fig.add_hline(secondary_y=False, **ref_kwargs)
        fig.update_yaxes(title_text="Real Index (sample mean = 100)", secondary_y=False,
                         title_font=dict(color=COLOR_REAL), tickfont=dict(color=COLOR_REAL))
        fig.update_yaxes(title_text=f"S(t), {units}", secondary_y=True, showgrid=False,
                         title_font=dict(color=COLOR_NOM), tickfont=dict(color=COLOR_NOM))
    else:  # "Real"
        fig = go.Figure([real_trace] + ([real_ma_trace] if show_ma else []))
        fig.add_hline(**ref_kwargs)
        fig.update_yaxes(title_text="Real Index (sample mean = 100)")

    _style_layout(fig)
    return fig


# =============================================================================
# UI helpers
# =============================================================================

CUSTOM_CSS = """
<style>
.block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px;}
.rer-hero {padding: 1.1rem 1.4rem; border-radius: 16px; margin-bottom: 0.4rem;
  background: linear-gradient(135deg, rgba(37,99,235,0.10), rgba(232,131,58,0.08));
  border: 1px solid rgba(128,128,128,0.18);}
.rer-hero h1 {font-size: 2.0rem; font-weight: 750; margin: 0 0 0.2rem 0; padding: 0; letter-spacing: -0.01em;}
.rer-hero p {margin: 0; opacity: 0.85; font-size: 1.02rem;}
[data-testid="stMetric"] {background: rgba(128,128,128,0.06); border: 1px solid rgba(128,128,128,0.18);
  border-radius: 12px; padding: 0.8rem 1rem;}
[data-testid="stMetricLabel"] p {font-size: 0.85rem; opacity: 0.85;}
.rer-badge {display: inline-block; padding: 0.28rem 0.75rem; border-radius: 999px; font-size: 0.86rem;
  font-weight: 600; margin: 0.15rem 0.4rem 0.35rem 0;}
.rer-ok {background: rgba(16,185,129,0.14); color: #10b981; border: 1px solid rgba(16,185,129,0.4);}
.rer-bad {background: rgba(239,68,68,0.14); color: #ef4444; border: 1px solid rgba(239,68,68,0.4);}
.rer-info {background: rgba(37,99,235,0.12); color: #3b82f6; border: 1px solid rgba(37,99,235,0.35);}
.rer-muted {background: rgba(128,128,128,0.10); border: 1px solid rgba(128,128,128,0.3);}
</style>
"""


def badge(text: str, kind: str = "info") -> str:
    return f'<span class="rer-badge rer-{kind}">{text}</span>'


def render_header() -> None:
    st.markdown(
        f"""<div class="rer-hero"><h1>💱 {APP_TITLE}</h1>
        <p>Bilateral real exchange rates from IMF monthly exchange-rate and CPI data, 1976 onward.</p></div>""",
        unsafe_allow_html=True,
    )
    st.latex(
        r"S_t=\frac{E_{2,t}}{E_{1,t}}\qquad RER_t=S_t\,\frac{P_{1,t}}{P_{2,t}}"
        r"\qquad \text{Real Index}_t = 100\,\frac{RER_t}{RER^{*}},\;\; RER^{*}=\overline{RER}_{\text{sample}}"
    )
    st.caption(
        "E = national currency per USD · S = Country 2 currency per 1 unit of Country 1 currency · "
        "a rise in the index is a real appreciation of Country 1's currency."
    )


def show_load_failure(res: LoadResult) -> None:
    """Friendly error naming the country and series, plus the request log."""
    st.error(
        f"**Could not load the {res.label}.**  \n{res.error or 'No data returned.'}  \n"
        "This country may not report this series to the IMF, or the IMF API may be temporarily "
        "unavailable. Try another country, or reload the page in a few minutes."
    )
    with st.expander("Debug details"):
        render_request_log(res.log)


def render_request_log(log: list[dict]) -> None:
    if not log:
        st.write("No request was made.")
        return
    for entry in log:
        status = entry.get("status")
        status_txt = f"HTTP {status}" if status is not None else "no HTTP status"
        st.markdown(f"**{entry.get('format', '')}** · {status_txt} · {entry.get('result', '')}")
        st.code(entry.get("url", ""), language=None)


# =============================================================================
# Session state: country pickers, swap, and "no duplicates"
# =============================================================================


def init_state(country_codes: list[str]) -> None:
    ss = st.session_state
    if ss.get("c1") not in country_codes:
        ss["c1"] = "GBR" if "GBR" in country_codes else country_codes[0]
    if ss.get("c2") not in country_codes:
        ss["c2"] = "USA" if "USA" in country_codes else country_codes[1]
    if ss["c1"] == ss["c2"]:
        ss["c2"] = next(c for c in country_codes if c != ss["c1"])
    ss.setdefault("prev_c1", ss["c1"])
    ss.setdefault("prev_c2", ss["c2"])


def on_c1_change() -> None:
    """If Country 1 is set equal to Country 2, move Country 2 to the old Country 1."""
    ss = st.session_state
    if ss["c1"] == ss["c2"]:
        ss["c2"] = ss["prev_c1"]
    ss["prev_c1"], ss["prev_c2"] = ss["c1"], ss["c2"]


def on_c2_change() -> None:
    ss = st.session_state
    if ss["c2"] == ss["c1"]:
        ss["c1"] = ss["prev_c2"]
    ss["prev_c1"], ss["prev_c2"] = ss["c1"], ss["c2"]


def swap_countries() -> None:
    ss = st.session_state
    ss["c1"], ss["c2"] = ss["c2"], ss["c1"]
    ss["prev_c1"], ss["prev_c2"] = ss["c1"], ss["c2"]


# =============================================================================
# Page sections
# =============================================================================


def render_kpis(sample: pd.DataFrame, rer_star: float, units: str, n_span_months: int) -> None:
    first, last = sample.iloc[0], sample.iloc[-1]
    last_date = sample.index[-1]
    s_change = (last["S"] / first["S"] - 1.0) * 100.0
    idx_change = (last["real_index"] / first["real_index"] - 1.0) * 100.0

    cols = st.columns(5)
    cols[0].metric(
        f"Latest S(t) · {units}", fmt_num(last["S"]),
        delta=f"{s_change:+.2f}% vs. {pretty_month(sample.index[0])}",
        help=f"Nominal rate in {pretty_month(last_date)}: units of Country 2 currency per 1 unit of Country 1 currency.",
    )
    cols[1].metric(
        "Latest Real Index", f"{last['real_index']:.2f}",
        delta=f"{last['real_index'] - 100:+.2f} vs. sample mean (100)",
        help=f"Real Index in {pretty_month(last_date)}. Above 100: Country 1's currency is "
             "stronger in real terms than its sample average.",
    )
    cols[2].metric(
        "RER* (sample mean of RER)", fmt_num(rer_star),
        help=f"Mean of RER(t) over the {len(sample)} valid months in the selected sample. "
             f"Units: {units} × (P1/P2); the CPI base years make the level arbitrary.",
    )
    cols[3].metric(
        "Valid months", f"{len(sample):,}",
        help=f"Months in the selected range where E1, E2, P1 and P2 are all available "
             f"({n_span_months - len(sample)} of {n_span_months} calendar months missing). "
             "Missing months are excluded, never filled.",
    )
    cols[4].metric(
        "Real Index change over sample", f"{idx_change:+.2f}%",
        help=f"From {first['real_index']:.2f} in {pretty_month(sample.index[0])} to "
             f"{last['real_index']:.2f} in {pretty_month(last_date)}. Positive = real appreciation of Country 1.",
    )


def render_sanity_badges(sample: pd.DataFrame, units: str, iso1: str, iso2: str) -> None:
    mean_idx = float(sample["real_index"].mean())
    ok = abs(mean_idx - 100.0) < 5e-4
    parts = [
        badge(f"✓ Mean Real Index over sample = {mean_idx:.3f}" if ok
              else f"✗ Mean Real Index over sample = {mean_idx:.3f} (expected 100.000)",
              "ok" if ok else "bad"),
        badge(f"S(t) units: {units}", "info"),
    ]
    if USA in (iso1, iso2):
        parts.append(badge("USD per USD fixed at 1.0", "muted"))
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_verify(sample: pd.DataFrame, rer_star: float, units: str, iso1: str, iso2: str,
                  cur1: str, cur2: str, key: str) -> None:
    """Step-by-step recomputation for one month, with full-precision numbers."""
    with st.expander("🔎 Verify a month (step-by-step calculation)"):
        months = [month_label(d) for d in sample.index]
        chosen = st.selectbox("Month to verify", months, index=len(months) - 1, key=key)
        row = sample.loc[pd.Timestamp(chosen + "-01")]
        e1, e2, p1, p2 = (float(row[c]) for c in ("E1", "E2", "P1", "P2"))
        s = e2 / e1
        rer = s * p1 / p2
        idx = 100.0 * rer / rer_star
        e1_note = "   (USD per USD, 1.0 by definition)" if iso1 == USA else ""
        e2_note = "   (USD per USD, 1.0 by definition)" if iso2 == USA else ""
        text = (
            f"Month: {chosen}    Country 1 = {iso1} ({cur1})    Country 2 = {iso2} ({cur2})\n\n"
            f"E1  = {e1!r}   {cur1} per USD{e1_note}\n"
            f"E2  = {e2!r}   {cur2} per USD{e2_note}\n"
            f"S   = E2 / E1 = {e2!r} / {e1!r}\n"
            f"    = {s!r}   {units}\n\n"
            f"P1  = {p1!r}   CPI {iso1}\n"
            f"P2  = {p2!r}   CPI {iso2}\n\n"
            f"RER = S × P1 / P2 = {s!r} × {p1!r} / {p2!r}\n"
            f"    = {rer!r}\n\n"
            f"RER* = mean of RER over {len(sample)} valid months "
            f"({month_label(sample.index[0])} to {month_label(sample.index[-1])})\n"
            f"     = {rer_star!r}\n\n"
            f"Real Index = 100 × RER / RER* = 100 × {rer!r} / {rer_star!r}\n"
            f"           = {idx!r}"
        )
        st.code(text, language=None)
        diff = max(abs(rer - float(row["RER"])), abs(idx - float(row["real_index"])))
        if diff <= 1e-9 * max(1.0, abs(idx)):
            st.success("✓ Matches the value in the data table and the CSV export.")
        else:
            st.warning(f"Recomputed value differs from the table by {diff:g}.")


def render_methodology(units: str, label1: str, label2: str) -> None:
    st.markdown("#### Definitions")
    st.markdown(
        "- $E_i(t)$: units of country *i*'s currency per 1 US dollar, monthly period average "
        "(IMF `ER` dataflow, indicator `XDC_USD`, `PA_RT`). For the United States $E(t)=1$ by definition.\n"
        "- $P_i(t)$: monthly consumer price index of country *i* (IMF `CPI` dataflow, all items, index)."
    )
    st.latex(r"S(t) = \frac{E_2(t)}{E_1(t)} \quad\text{(Country 2 currency per 1 unit of Country 1 currency)}")
    st.latex(r"RER(t) = S(t)\,\frac{P_1(t)}{P_2(t)}")
    st.latex(r"RER^{*} = \frac{1}{N}\sum_{t\in\text{sample}} RER(t), \qquad "
             r"\text{Real Index}(t) = 100\,\frac{RER(t)}{RER^{*}}")
    st.markdown("#### Direction of S(t)")
    st.markdown(
        f"With **Country 1 = {label1}** and **Country 2 = {label2}**, S(t) is measured in **{units}**. "
        "For example, Country 1 = United Kingdom and Country 2 = United States gives USD per GBP "
        "(roughly 1.2–1.4 in recent years). A rise in S means Country 1's currency buys more of Country 2's "
        "currency (nominal appreciation of Country 1)."
    )
    st.markdown("#### Interpretation")
    st.markdown(
        "RER(t) compares the price of Country 1's consumption basket, converted into Country 2's currency, "
        "with the price of Country 2's basket. **A rise in RER means Country 1's goods become more expensive "
        "relative to Country 2's goods — a real appreciation of Country 1's currency.** "
        "Because CPIs are indices with arbitrary base years, the level of RER has no meaning on its own; "
        "the app therefore normalises by RER\\*, the average over the selected sample, so the Real Index "
        "averages exactly 100. Values above 100 mean Country 1's currency is stronger in real terms than "
        "its average over the sample (relative-PPP view)."
    )
    st.markdown("#### Sample and missing data")
    st.markdown(
        "- A month is used only when E1, E2, P1 and P2 are all available (inner join).\n"
        "- Missing months are **never** forward-filled or interpolated; they show as breaks in the chart.\n"
        "- RER\\* and the Real Index are recomputed every time the countries or the date range change.\n"
        "- The 12-month moving average needs 12 consecutive valid months, so it starts 11 months into the "
        "sample and breaks around gaps.\n"
        "- Nominal S(t) is always shown in actual currency units and is never normalised."
    )
    st.markdown("#### Data source")
    st.markdown(
        f"International Monetary Fund, SDMX 3.0 API (`{BASE_URL}`), agency `{AGENCY}`:\n"
        "- Exchange rates: `data/dataflow/IMF.STA/ER/+/{ISO3}.XDC_USD.PA_RT.M`\n"
        "- CPI: `data/dataflow/IMF.STA/CPI/+/{ISO3}.CPI._T.IX.M`\n"
        "- The Euro area uses the code `G163`.\n\n"
        "Downloads are cached for about 24 hours."
    )


def render_coverage(results: dict[str, LoadResult], panel: pd.DataFrame | None,
                    iso1: str, iso2: str) -> None:
    rows = [
        series_coverage(f"E1 · ER {iso1}", results["E1"].series, results["E1"].note or ""),
        series_coverage(f"E2 · ER {iso2}", results["E2"].series, results["E2"].note or ""),
        series_coverage(f"P1 · CPI {iso1}", results["P1"].series),
        series_coverage(f"P2 · CPI {iso2}", results["P2"].series),
    ]
    if panel is not None and not panel.empty:
        rows.append(series_coverage("All four available (valid months)", panel["RER"],
                                    "Months used for S, RER and the index"))
    st.dataframe(pd.DataFrame(rows), hide_index=True, **stretch_kwargs(st.dataframe))

    notes = [
        "**Gaps are not filled.** If any of the four inputs is missing in a month, that month is dropped.",
        "**Euro:** euro exchange rates begin in January 1999. Euro-area aggregate (G163) series may start "
        "late, and euro-member exchange rates before 1999 may be missing or expressed in converted terms.",
        "**United States:** the USD-per-USD rate is set to 1.0 for every month (not downloaded), so the "
        "US row above simply covers January 1976 to the current month.",
        "**Publication lags:** CPI usually appears a month or two after exchange rates, so the latest "
        "common month may trail today's date.",
        "**Revisions and redenominations:** the IMF revises data; currencies that were redenominated "
        "(e.g. after hyperinflations) are reported in current units, which can produce very large early values.",
    ]
    st.markdown("\n".join(f"- {n}" for n in notes))


# =============================================================================
# Main app
# =============================================================================


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="💱", layout="wide",
                       initial_sidebar_state="expanded")
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    try:
        run_app()
    except Exception:  # last-resort guard: friendly message instead of a raw traceback
        st.error("Something unexpected went wrong. Please change the selection or reload the page.")
        with st.expander("Debug details"):
            st.code(traceback.format_exc(), language=None)


def run_app() -> None:
    render_header()

    # ---------------- Sidebar: countries ----------------
    with st.spinner("Loading the country list…"):
        countries, country_source = get_countries()
    codes = sorted(countries, key=lambda c: (c != EURO_AREA, countries[c][0].lower()))
    init_state(codes)

    sb = st.sidebar
    sb.markdown("### Countries")
    sb.selectbox("Country 1 (base)", codes, key="c1", on_change=on_c1_change,
                 format_func=lambda c: country_label(c, countries),
                 help="Type to search. S(t) is expressed per 1 unit of Country 1's currency.")
    sb.selectbox("Country 2 (comparison)", codes, key="c2", on_change=on_c2_change,
                 format_func=lambda c: country_label(c, countries),
                 help="Type to search. Choosing the same country as Country 1 swaps the two.")
    sb.button("⇅  Swap countries", on_click=swap_countries, **stretch_kwargs(sb.button))
    sb.caption(f"Country list: {country_source}")

    range_box = sb.container()   # date range is filled in after the data loads

    sb.markdown("### Chart")
    mode = sb.segmented_control("Chart mode", ["Real", "Nominal", "Both"], default="Real",
                                key="chart_mode") or "Real"
    show_ma = sb.toggle("Show 12-month moving average", value=False)
    export_box = sb.container()

    iso1, iso2 = st.session_state["c1"], st.session_state["c2"]
    if iso1 == iso2:
        st.error("Please choose two different countries.")
        return

    name1, name2 = countries[iso1][0], countries[iso2][0]
    label1, label2 = country_label(iso1, countries), country_label(iso2, countries)
    cur1, cur2 = currency_unit(iso1, countries), currency_unit(iso2, countries)
    units = f"{cur2} per {cur1}"

    # ---------------- Data loading ----------------
    with st.spinner(f"Downloading IMF data for {name1} and {name2}…"):
        results = {
            "E1": load_exchange_rate(iso1, name1),
            "E2": load_exchange_rate(iso2, name2),
            "P1": load_cpi(iso1, name1),
            "P2": load_cpi(iso2, name2),
        }

    failures = [r for r in results.values() if not r.ok]
    if failures:
        for r in failures:
            show_load_failure(r)
        st.subheader("Data coverage")
        render_coverage(results, None, iso1, iso2)
        return

    panel = build_panel(results["E1"].series, results["E2"].series,
                        results["P1"].series, results["P2"].series)
    if panel.empty:
        st.error(
            f"**No overlapping months.** {name1} and {name2} have no month in which the exchange "
            "rates and both CPIs are all available, so the real exchange rate cannot be computed. "
            "See the coverage table below."
        )
        render_coverage(results, panel, iso1, iso2)
        return

    # ---------------- Sidebar: date range ----------------
    month_opts = [month_label(d) for d in panel.index]
    range_key = f"range_{iso1}_{iso2}"
    saved = st.session_state.get(range_key)
    if saved is not None and (saved[0] not in month_opts or saved[1] not in month_opts):
        del st.session_state[range_key]
    with range_box:
        st.markdown("### Sample period")
        if len(month_opts) >= 2:
            start_lbl, end_lbl = st.select_slider(
                "Start and end month", options=month_opts, value=(month_opts[0], month_opts[-1]),
                key=range_key, help="Only months where all four series are available are listed. "
                                    "RER* and the index are recomputed for this range.",
            )
        else:
            start_lbl = end_lbl = month_opts[0]
            st.caption(f"Only one common month is available: {start_lbl}")

    if start_lbl > end_lbl:   # defensive: a range slider normally prevents this
        st.warning(f"The start month ({start_lbl}) is after the end month ({end_lbl}); "
                   "the two have been swapped.")
        start_lbl, end_lbl = end_lbl, start_lbl

    start_ts, end_ts = pd.Timestamp(start_lbl + "-01"), pd.Timestamp(end_lbl + "-01")
    sample, rer_star = compute_sample(panel, start_ts, end_ts)
    if sample.empty or not is_finite(rer_star):
        st.error("The selected date range contains no valid months. Please widen the range.")
        return

    n_span = (end_ts.year - start_ts.year) * 12 + (end_ts.month - start_ts.month) + 1

    # ---------------- Friendly notices ----------------
    if len(panel) < 36:
        st.info(f"The overlapping sample for {name1} and {name2} is short: only {len(panel)} months "
                f"({month_opts[0]} to {month_opts[-1]}). Results should be read with care.")
    if len(sample) < 12:
        st.warning(f"The selected sample has only {len(sample)} valid month(s) (fewer than 12). "
                   "RER* is an average of very few observations and the 12-month moving average "
                   "is not available.")
    if len(sample) < n_span:
        st.caption(f"ℹ️ {n_span - len(sample)} month(s) between {start_lbl} and {end_lbl} are missing at "
                   "least one input and are excluded (not filled).")

    # ---------------- KPIs, badges, verification ----------------
    st.markdown(f"#### {label1}  vs.  {label2}  ·  {pretty_month(start_ts)} – {pretty_month(end_ts)}")
    render_kpis(sample, rer_star, units, n_span)
    render_sanity_badges(sample, units, iso1, iso2)
    render_verify(sample, rer_star, units, iso1, iso2, cur1, cur2,
                  key=f"verify_{iso1}_{iso2}_{start_lbl}_{end_lbl}")

    # ---------------- Export ----------------
    export_df = build_export(sample, iso1, iso2)
    csv_bytes = export_df.to_csv(index=False).encode("utf-8")   # default float repr = full precision
    file_name = f"rer_{iso1}_{iso2}_{start_lbl}_{end_lbl}.csv"
    with export_box:
        st.markdown("### Export")
        st.download_button("⬇️  Download sample (CSV)", data=csv_bytes, file_name=file_name,
                           mime="text/csv", key="dl_sidebar", **stretch_kwargs(st.download_button))

    # ---------------- Tabs ----------------
    tab_chart, tab_table, tab_method, tab_cov = st.tabs(["📈 Chart", "🧾 Data table", "📘 Methodology",
                                                          "🗂️ Data coverage"])
    with tab_chart:
        fig = make_chart(sample, mode, show_ma, units)
        st.plotly_chart(fig, theme="streamlit", config={"displaylogo": False},
                        **stretch_kwargs(st.plotly_chart))
        st.caption(
            f"Rising Real Index = {name1}'s goods become more expensive relative to {name2}'s goods "
            f"(real appreciation of the {cur1}). Nominal S(t) is in {units}, not normalised. "
            "Breaks in a line mark months with missing data."
        )

    with tab_table:
        table = pd.DataFrame({
            "Month": [month_label(d) for d in sample.index],
            f"E1 ({cur1} per USD)": sample["E1"].to_numpy(),
            f"E2 ({cur2} per USD)": sample["E2"].to_numpy(),
            f"S ({units})": sample["S"].to_numpy(),
            f"P1 (CPI {iso1})": sample["P1"].to_numpy(),
            f"P2 (CPI {iso2})": sample["P2"].to_numpy(),
            "RER": sample["RER"].to_numpy(),
            "Real Index": sample["real_index"].to_numpy(),
        })
        st.dataframe(
            table, hide_index=True, height=480,
            column_config={
                f"P1 (CPI {iso1})": st.column_config.NumberColumn(format="%.3f"),
                f"P2 (CPI {iso2})": st.column_config.NumberColumn(format="%.3f"),
                "Real Index": st.column_config.NumberColumn(format="%.3f"),
            },
            **stretch_kwargs(st.dataframe),
        )
        st.caption(f"{len(sample)} valid months · RER* = {rer_star!r}. The CSV export contains every "
                   "value at full precision.")
        st.download_button("⬇️  Download this sample as CSV", data=csv_bytes, file_name=file_name,
                           mime="text/csv", key="dl_table")

    with tab_method:
        render_methodology(units, label1, label2)

    with tab_cov:
        render_coverage(results, panel, iso1, iso2)

    # ---------------- Debug ----------------
    with st.expander("Debug details (IMF request URLs)"):
        for key_name, res in results.items():
            st.markdown(f"##### {key_name}: {res.label}")
            render_request_log(res.log)


if __name__ == "__main__":
    main()
