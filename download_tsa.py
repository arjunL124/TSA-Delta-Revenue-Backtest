"""Download daily TSA checkpoint passenger counts from January 2022 through today.

Source: https://www.tsa.gov/travel/passenger-volumes
Output: data/tsa_daily.csv with two columns, date and travelers.

Run with:  python3 download_tsa.py
"""

import io
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://www.tsa.gov/travel/passenger-volumes"
START_YEAR = 2022
OUTPUT_PATH = Path(__file__).parent / "data" / "tsa_daily.csv"

# TSA's site rejects requests that don't look like they come from a browser.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


def fetch_table(url: str) -> pd.DataFrame:
    """Return the single Date / Numbers table found on a TSA passenger volumes page."""
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return pd.read_html(io.StringIO(response.text))[0]


def main() -> None:
    # Past years each live on their own page; the current year is on the base page.
    urls = [f"{BASE_URL}/{year}" for year in range(START_YEAR, date.today().year)]
    urls.append(BASE_URL)

    raw = pd.concat([fetch_table(url) for url in urls], ignore_index=True)

    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["Date"], format="%m/%d/%Y"),
            "travelers": raw["Numbers"].astype(int),
        }
    ).sort_values("date", ignore_index=True)

    # Fail loudly rather than save a file with gaps or repeated days.
    if daily["date"].duplicated().any():
        raise ValueError("Duplicate dates found in the TSA tables.")
    expected_days = pd.date_range(daily["date"].min(), daily["date"].max())
    missing_days = expected_days.difference(daily["date"])
    if len(missing_days) > 0:
        raise ValueError(f"Missing dates in the TSA tables: {list(missing_days.date)}")

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    daily.to_csv(OUTPUT_PATH, index=False)
    print(
        f"Saved {len(daily):,} days "
        f"({daily['date'].min().date()} to {daily['date'].max().date()}) to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
