"""Download the Consumer Price Index for airline fares, from January 2022 through today.

Source: Bureau of Labor Statistics public data API, series CUUR0000SETG01
        (airline fares, U.S. city average, all urban consumers, not seasonally
        adjusted). It is an index where the 1982-84 average equals 100, so only
        changes in it are meaningful, not the level.
Output: data/airfare_cpi_monthly.csv    one row per month, as published
        data/airfare_cpi_quarterly.csv  the monthly values averaged into quarters

A month can be missing: the BLS published no October 2025 figure because of that
year's government shutdown, and the latest month comes out about two weeks after
it ends. A quarter is averaged over the months that exist, and the
months_in_average column records how many that was.

Run with:  python3 download_airfare_cpi.py
"""

from datetime import date
from pathlib import Path

import pandas as pd
import requests

API_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
SERIES_ID = "CUUR0000SETG01"
START_YEAR = 2022
DATA_DIR = Path(__file__).parent / "data"
MONTHLY_PATH = DATA_DIR / "airfare_cpi_monthly.csv"
QUARTERLY_PATH = DATA_DIR / "airfare_cpi_quarterly.csv"


def fetch_monthly() -> pd.DataFrame:
    """Return one row per month with the index value, blank where the BLS published none."""
    response = requests.post(
        API_URL,
        json={"seriesid": [SERIES_ID], "startyear": str(START_YEAR), "endyear": str(date.today().year)},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if payload["status"] != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS request failed: {payload.get('message')}")

    rows = []
    for entry in payload["Results"]["series"][0]["data"]:
        # Periods M01-M12 are months; anything else is an annual or half-year average.
        if not entry["period"].startswith("M") or entry["period"] == "M13":
            continue
        rows.append(
            {
                "month": pd.Period(f"{entry['year']}-{entry['period'][1:]}", freq="M"),
                # A missing month comes back as "-", which becomes a blank here.
                "airfare_cpi": pd.to_numeric(entry["value"], errors="coerce"),
                "note": "; ".join(f["text"] for f in entry["footnotes"] if f.get("text")),
            }
        )
    return pd.DataFrame(rows).sort_values("month", ignore_index=True)


def main() -> None:
    monthly = fetch_monthly()

    # Fail loudly rather than save a file with a repeated month or one the BLS skipped entirely.
    if monthly["month"].duplicated().any():
        raise ValueError("Duplicate months found in the BLS data.")
    expected = pd.period_range(f"{START_YEAR}-01", monthly["month"].max(), freq="M")
    if list(monthly["month"]) != list(expected):
        raise ValueError(f"Months are not consecutive: {sorted(set(expected) - set(monthly['month']))}")

    quarterly = (
        monthly.groupby(monthly["month"].dt.asfreq("Q").rename("quarter"))["airfare_cpi"]
        .agg(airfare_cpi="mean", months_in_average="count")
        .reset_index()
    )
    quarterly["airfare_cpi"] = quarterly["airfare_cpi"].round(3)

    DATA_DIR.mkdir(exist_ok=True)
    monthly.to_csv(MONTHLY_PATH, index=False)
    quarterly.to_csv(QUARTERLY_PATH, index=False)

    published = monthly.dropna(subset=["airfare_cpi"])
    print(
        f"Saved {len(published)} months ({published['month'].min()} to {published['month'].max()}) "
        f"to {MONTHLY_PATH}\n"
        f"Saved {len(quarterly)} quarters ({quarterly['quarter'].iloc[0]} to {quarterly['quarter'].iloc[-1]}) "
        f"to {QUARTERLY_PATH}"
    )
    for row in quarterly[quarterly["months_in_average"] < 3].itertuples():
        print(f"Note: {row.quarter} is averaged over {row.months_in_average} of 3 months.")


if __name__ == "__main__":
    main()
