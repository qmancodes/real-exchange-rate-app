# 💱 Real Exchange Rate Explorer

A Streamlit app that computes and charts the **bilateral real exchange rate** between any two IMF economies (including the Euro area). It uses monthly exchange rates and consumer price indices from the IMF SDMX 3.0 API, from January 1976 onward.

## What the app does

- Lets you pick **Country 1** and **Country 2** from a searchable list, e.g. "United Kingdom (GBR, GBP)". The defaults are UK vs. US. There is a swap button, and you can't select the same country twice.
- Downloads each country's monthly exchange rate against the US dollar and its CPI, then caches them for about 24 hours.
- Computes the nominal rate **S(t)**, the real exchange rate **RER(t)**, and a **Real Index** normalised to average 100 over the period you select.
- Charts the series in Real, Nominal or Both mode. Both mode puts them on one chart with two separate y-axes. An optional 12-month moving average is available.
- Shows KPI cards, a formatted data table, a methodology page and a data-coverage page.
- Includes a **"Verify a month"** panel that shows every step of the calculation at full precision.
- Shows sanity badges: the mean of the Real Index over the sample (should read 100.000) and the units of S(t).
- Exports the selected sample to CSV at full numeric precision.

## The economics

| Symbol | Meaning |
|---|---|
| E_i(t) | Units of country *i*'s currency per 1 US dollar (monthly period average). For the US, E = 1.0 by definition. |
| S(t) = E₂(t) / E₁(t) | Units of **Country 2** currency per 1 unit of **Country 1** currency |
| P₁(t), P₂(t) | Monthly CPI of Country 1 and Country 2 |
| RER(t) = S(t) × P₁(t) / P₂(t) | Real exchange rate |
| RER* = mean of RER(t) over the valid months in the selected sample | Normalising constant |
| Real Index(t) = 100 × RER(t) / RER* | Averages exactly 100 over the sample |

**Direction check:** with Country 1 = United Kingdom and Country 2 = United States, S is **USD per GBP**, roughly 1.2–1.4 in recent years.

**Interpretation:** a rise in RER (or in the Real Index) means Country 1's goods become more expensive relative to Country 2's goods. That is a **real appreciation** of Country 1's currency. Index values above 100 mean Country 1's currency is stronger in real terms than its average over the chosen sample.

**Rules the app follows:**

- A month is **valid** only if E₁, E₂, P₁ and P₂ are all available. The series are inner-joined, and gaps are never forward-filled or interpolated. Gaps appear as breaks in the chart.
- RER* and the Real Index are recomputed whenever the countries or the date range change.
- Nominal S(t) is always shown in actual currency units and is never normalised.

## Data source

The data comes from the International Monetary Fund's SDMX 3.0 API (`https://api.imf.org/external/sdmx/3.0`). No API key is needed.

| Series | Endpoint |
|---|---|
| Exchange rate (national currency per USD, period average, monthly) | `/data/dataflow/IMF.STA/ER/+/{ISO3}.XDC_USD.PA_RT.M` |
| CPI (all items, index, monthly) | `/data/dataflow/IMF.STA/CPI/+/{ISO3}.CPI._T.IX.M` |
| Country list | IMF `CL_COUNTRY` codelist, merged with a built-in list of about 120 economies |

- The Euro area is `G163`. The US exchange rate is never downloaded; it is set to 1.0 for every month.
- Data is requested from `1976-M01` using the `c[TIME_PERIOD]=ge:1976-M01` filter, and the app also filters by date after download.
- The app asks for SDMX-JSON first and falls back to SDMX-CSV.
- Every request URL is listed under **"Debug details"** at the bottom of the page.

## How to use it

1. Choose Country 1 and Country 2 in the sidebar. You can type to search.
2. Drag the **Sample period** slider. Only months where all four series exist are offered.
3. Choose **Real**, **Nominal** or **Both**, and optionally turn on the 12-month moving average.
4. Read the KPI cards and badges. Open **Verify a month** to see a hand calculation.
5. Download the CSV from the sidebar or from the **Data table** tab.

The CSV columns are `date, country1_iso3, country2_iso3, E1_lcu_per_usd, E2_lcu_per_usd, S_country2_per_country1, P1_cpi, P2_cpi, RER, RER_star, real_index`. Files are named `rer_{ISO3_1}_{ISO3_2}_{start}_{end}.csv`.

## Deploying on Streamlit Community Cloud

1. Put `app.py`, `requirements.txt` and `README.md` at the root of the public GitHub repo `real-exchange-rate-app`.
2. On share.streamlit.io choose **New app**, select the repo and branch, and set the main file to `app.py`.
3. Under **Advanced settings**, choose Python 3.12, then deploy.

## Known data limitations

- **Coverage varies.** Some countries don't report monthly CPI or exchange rates to the IMF, or have long gaps. The app tells you which country and series failed, and the **Data coverage** tab shows the first and last month of each input.
- **Euro.** Euro exchange rates begin in January 1999, so pairs involving the Euro area usually start then or later. Pre-1999 rates for euro members may be missing or expressed in converted euro terms.
- **CPI base years differ** across countries. This doesn't affect the Real Index, because it is normalised, but the *level* of RER and RER* has no standalone meaning.
- **Publication lags.** CPI is often published a month or two after exchange rates, so the latest common month can trail today's date.
- **Redenominations and hyperinflations** (e.g. Argentina, Türkiye, Zimbabwe) can produce extreme values early in a sample. Choose the sample period carefully.
- **Revisions.** The IMF revises data, and results are cached for about 24 hours.
- **Bilateral and CPI-based.** This is a CPI-deflated bilateral rate, not a trade-weighted effective rate. It also does not use producer prices or unit labour costs.
- **Currency labels** come from a built-in table. Countries that exist only in the IMF codelist are labelled "{ISO3} currency".
