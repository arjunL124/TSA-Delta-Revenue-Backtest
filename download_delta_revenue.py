"""Download Delta Air Lines' quarterly revenue from the SEC, from Q1 2022 on.

Source: SEC EDGAR company facts API (Delta's company ID, or CIK, is 27904).
Output: data/delta_quarterly_revenue.csv, one row per quarter.

Before running, copy .env.example to .env and fill in your name and email.
The SEC rejects requests that don't identify the sender.

Run with:  python3 download_delta_revenue.py
"""

import os
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000027904.json"
OUTPUT_PATH = PROJECT_DIR / "data" / "delta_quarterly_revenue.csv"
START_DATE = "2022-01-01"

# Delta's total operating revenue. The simpler "Revenues" label stops at the end
# of 2018, when a new accounting standard changed which label companies file under.
REVENUE_TAG = "RevenueFromContractWithCustomerExcludingAssessedTax"


def fetch_revenue_facts() -> pd.DataFrame:
    """Return every revenue value Delta has filed, with the length of each period in days."""
    load_dotenv(PROJECT_DIR / ".env")
    name = os.getenv("SEC_CONTACT_NAME")
    email = os.getenv("SEC_CONTACT_EMAIL")
    if not name or not email:
        raise SystemExit("Set SEC_CONTACT_NAME and SEC_CONTACT_EMAIL in .env (see .env.example).")

    response = requests.get(
        COMPANY_FACTS_URL, headers={"User-Agent": f"{name} {email}"}, timeout=60
    )
    response.raise_for_status()

    facts = pd.DataFrame(response.json()["facts"]["us-gaap"][REVENUE_TAG]["units"]["USD"])
    facts["start"] = pd.to_datetime(facts["start"])
    facts["end"] = pd.to_datetime(facts["end"])
    facts["days"] = (facts["end"] - facts["start"]).dt.days + 1
    return facts[facts["start"] >= START_DATE]


def latest_per_period(facts: pd.DataFrame) -> pd.DataFrame:
    """Keep one value per period.

    Each report repeats the prior year's figures for comparison, so the same
    period shows up in several filings. The most recently filed copy wins, so
    any restated number replaces the original.
    """
    return facts.sort_values("filed").drop_duplicates(["start", "end"], keep="last")


def main() -> None:
    facts = fetch_revenue_facts()

    # The same label also holds 6-month and 9-month running totals, so period
    # length is what separates a single quarter (~90 days) from a full year.
    quarters = latest_per_period(facts[facts["days"].between(80, 100)])
    years = latest_per_period(facts[facts["days"].between(350, 380)])

    rows = [
        {"period_start": q.start, "period_end": q.end, "revenue_usd": q.val, "source": "reported"}
        for q in quarters.itertuples()
    ]

    # Delta files Q1-Q3 individually but Q4 only as part of the full year,
    # so Q4 = full year minus the three reported quarters.
    for year in years.itertuples():
        in_year = quarters[(quarters["start"] >= year.start) & (quarters["end"] <= year.end)]
        if len(in_year) != 3:
            raise ValueError(f"Expected 3 reported quarters in {year.start.year}, found {len(in_year)}.")
        rows.append(
            {
                "period_start": in_year["end"].max() + pd.Timedelta(days=1),
                "period_end": year.end,
                "revenue_usd": year.val - in_year["val"].sum(),
                "source": "calculated: full year minus Q1-Q3",
            }
        )

    revenue = pd.DataFrame(rows).sort_values("period_end", ignore_index=True)
    revenue.insert(0, "quarter", revenue["period_end"].dt.to_period("Q").astype(str))

    # Fail loudly rather than save a file with a skipped or repeated quarter.
    expected = pd.period_range(revenue["quarter"].iloc[0], revenue["quarter"].iloc[-1], freq="Q")
    if list(revenue["quarter"]) != list(expected.astype(str)):
        raise ValueError(f"Quarters are not consecutive: {list(revenue['quarter'])}")

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    revenue.to_csv(OUTPUT_PATH, index=False)
    print(
        f"Saved {len(revenue)} quarters "
        f"({revenue['quarter'].iloc[0]} to {revenue['quarter'].iloc[-1]}) to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
