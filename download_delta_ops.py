"""Collect Delta's adjusted revenue and operating stats from its earnings press releases.

Source: the press release (Exhibit 99.1) attached to each quarterly earnings 8-K
        that Delta files with the SEC.
Output: data/delta_ops.csv, one row per quarter from Q1 2022 on, with
        - adjusted_revenue_usd: operating revenue excluding third-party refinery sales
        - rpm_millions: revenue passenger miles (how much people flew)
        - asm_millions: available seat miles (how many seats Delta offered)
        - passenger_mile_yield_cents: passenger revenue per passenger mile (average fare per mile)
        - fuel_price_per_gallon_usd: average price Delta paid per gallon of fuel
        - filed_date: the day the release was filed, i.e. when the numbers became public

Each quarter's figures are taken from the release that first announced them.
Later releases repeat past quarters for comparison; those repeats are used only
to double-check the figures.

Before running, copy .env.example to .env and fill in your name and email.

Run with:  python3 download_delta_ops.py
"""

import io
import os
import re
import time
from pathlib import Path

import lxml.html
import pandas as pd
import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent
FILINGS_INDEX_URL = "https://data.sec.gov/submissions/CIK0000027904.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/27904"
OUTPUT_PATH = PROJECT_DIR / "data" / "delta_ops.csv"
GAAP_REVENUE_PATH = PROJECT_DIR / "data" / "delta_quarterly_revenue.csv"
FIRST_QUARTER = pd.Period("2022Q1")

QUARTER_END = r"(March|June|September|December) (\d{1,2})"
# The label varies between releases: "Operating revenue, adjusted" or
# "Total operating revenue, adjusted".
REVENUE_ROWS = {
    "gaap": re.compile(r"(total )?operating revenue", re.I),
    "refinery": re.compile(r"third-party refinery sales", re.I),
    "adjusted": re.compile(r"(total )?operating revenue, adjusted", re.I),
}
# Rows to read from the statistical summary table, keyed by output column.
STAT_ROWS = {
    "rpm_millions": re.compile(r"Revenue passenger miles \(millions\)"),
    "asm_millions": re.compile(r"Available seat miles \(millions\)"),
    "passenger_mile_yield_cents": re.compile(r"Passenger mile yield \(cents\)"),
    # Not the "adjusted" row beneath it, which strips out fuel hedging effects.
    "fuel_price_per_gallon_usd": re.compile(r"Average price per fuel gallon"),
}
# How far a figure may differ between two releases. Delta rounds each dollar figure
# to the nearest $1M separately, so those can be off by one. Everything else must
# match exactly as printed.
TOLERANCE = {"gaap": 1, "refinery": 1, "adjusted": 1}
# Cells that hold only table decoration, not data.
DECORATION = {"", "$", ")", "%", ")%", ") %"}


def sec_get(url: str, headers: dict) -> requests.Response:
    """Fetch a page from the SEC, pausing first to stay well under its 10 requests/second limit."""
    time.sleep(0.2)
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return response


def find_earnings_filings(headers: dict) -> pd.DataFrame:
    """Return Delta's earnings 8-K filings that cover Q1 2022 onward, oldest first."""
    filings = pd.DataFrame(sec_get(FILINGS_INDEX_URL, headers).json()["filings"]["recent"])
    is_earnings = (filings["form"] == "8-K") & filings["items"].str.contains("2.02", regex=False)
    # Item 2.02 marks an earnings announcement. Q1 2022 results came out in April 2022.
    after_start = filings["filingDate"] >= f"{FIRST_QUARTER.year}-04-01"
    return filings[is_earnings & after_start].sort_values("filingDate")


def fetch_press_release(accession: str, headers: dict) -> str:
    """Return the HTML of the press release attached to one filing."""
    folder = f"{ARCHIVE_URL}/{accession.replace('-', '')}"
    index_page = sec_get(f"{folder}/{accession}-index.html", headers).text
    documents = pd.read_html(io.StringIO(index_page))[0]
    # The file name changes between filings, but the press release is always typed EX-99.1.
    exhibit = documents.loc[documents["Type"] == "EX-99.1", "Document"]
    if len(exhibit) != 1:
        raise ValueError(f"Expected one EX-99.1 exhibit in filing {accession}, found {len(exhibit)}.")
    return sec_get(f"{folder}/{exhibit.iloc[0].split()[0]}", headers).text


def read_tables(html: str) -> list[list[list[str]]]:
    """Return every table as a list of rows, each row being its non-empty cell texts."""
    tables = []
    for table in lxml.html.fromstring(html).iter("table"):
        rows = []
        for tr in table.xpath("./tr | ./thead/tr | ./tbody/tr"):
            cells = [" ".join(cell.text_content().split()) for cell in tr.xpath("./td | ./th")]
            cells = [cell for cell in cells if cell not in DECORATION]
            if cells:
                rows.append(cells)
        tables.append(rows)
    return tables


def numbers_in(row: list[str]) -> list[float]:
    """Return the numeric cells of a row in order. Accountants write negatives as (1,187)."""
    values = []
    for cell in row[1:]:
        text = cell.replace("$", "").replace(",", "").strip()
        match = re.fullmatch(r"(\()?(\d+(?:\.\d+)?)\)?", text)
        if match:
            values.append(-float(match.group(2)) if match.group(1) else float(match.group(2)))
    return values


def quarter_ending(month: str, day: str, year: str) -> pd.Period:
    return pd.Timestamp(f"{month} {day}, {year}").to_period("Q")


def parse_operating_stats(tables: list) -> dict:
    """Return {quarter: {one value per STAT_ROWS column}} from the statistical summary table."""
    matches = [t for t in tables if any(r[0].startswith("Revenue passenger miles") for r in t)]
    if len(matches) != 1:
        raise ValueError(f"Expected one operating statistics table, found {len(matches)}.")
    table = matches[0]

    header_text = " ".join(" ".join(row) for row in table[:4])
    if not table[0][0].startswith("Three Months Ended"):
        raise ValueError(f"Operating statistics table does not start with a quarter: {table[0]}")
    month, day = re.search(QUARTER_END, header_text).groups()
    # The quarter's two columns come first; year-to-date columns, when present, follow.
    years = re.findall(r"\b(20\d\d)\b", header_text)[:2]

    found = {}
    for name, pattern in STAT_ROWS.items():
        rows = [row for row in table if pattern.fullmatch(row[0])]
        if len(rows) != 1:
            raise ValueError(f"Expected one '{pattern.pattern}' row, found {len(rows)}.")
        found[name] = numbers_in(rows[0])
    return {
        quarter_ending(month, day, year): {name: values[i] for name, values in found.items()}
        for i, year in enumerate(years)
    }


def parse_adjusted_revenue(tables: list) -> dict:
    """Return {quarter: {gaap, refinery, adjusted}} in $ millions from the reconciliation tables."""
    readings = {}
    for table in tables:
        if not re.match(r"Three\s+Months\s+Ended", table[0][0] if table else ""):
            continue
        found = {}
        for row in table:
            for name, pattern in REVENUE_ROWS.items():
                # Only the first match counts; some tables stack other reconciliations below.
                if name not in found and pattern.fullmatch(row[0]):
                    found[name] = numbers_in(row)
        if len(found) != len(REVENUE_ROWS):
            continue

        header_text = " ".join(" ".join(row) for row in table[:3])
        columns = [quarter_ending(*parts) for parts in re.findall(QUARTER_END + r", (20\d\d)", header_text)]
        for i, quarter in enumerate(columns):
            readings[quarter] = {name: values[i] for name, values in found.items()}
    if not readings:
        raise ValueError("No adjusted revenue reconciliation table found.")
    return readings


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    name = os.getenv("SEC_CONTACT_NAME")
    email = os.getenv("SEC_CONTACT_EMAIL")
    if not name or not email:
        raise SystemExit("Set SEC_CONTACT_NAME and SEC_CONTACT_EMAIL in .env (see .env.example).")
    headers = {"User-Agent": f"{name} {email}"}

    rows = []
    # Every appearance of every figure, including repeats in later releases, for cross-checking.
    readings = []
    for filing in find_earnings_filings(headers).itertuples():
        tables = read_tables(fetch_press_release(filing.accessionNumber, headers))
        stats = parse_operating_stats(tables)
        revenue = parse_adjusted_revenue(tables)

        announced = max(stats)
        rows.append(
            {
                "quarter": announced,
                "adjusted_revenue_usd": int(revenue[announced]["adjusted"] * 1_000_000),
                "rpm_millions": int(stats[announced]["rpm_millions"]),
                "asm_millions": int(stats[announced]["asm_millions"]),
                "passenger_mile_yield_cents": stats[announced]["passenger_mile_yield_cents"],
                "fuel_price_per_gallon_usd": stats[announced]["fuel_price_per_gallon_usd"],
                "filed_date": filing.filingDate,
            }
        )
        for quarter, values in list(stats.items()) + list(revenue.items()):
            for metric, value in values.items():
                readings.append({"quarter": quarter, "metric": metric, "value": value, "filed": filing.filingDate})
        print(f"Read release filed {filing.filingDate}: {announced}")

    ops = pd.DataFrame(rows).sort_values("quarter", ignore_index=True)
    readings = pd.DataFrame(readings)
    readings = readings[readings["quarter"] >= FIRST_QUARTER]

    # Fail loudly rather than save a file with a problem in it.
    problems = []

    expected = pd.period_range(FIRST_QUARTER, ops["quarter"].max(), freq="Q")
    if list(ops["quarter"]) != list(expected):
        problems.append(f"Quarters are not consecutive: {list(ops['quarter'].astype(str))}")

    spread = readings.groupby(["quarter", "metric"])["value"].agg(["min", "max", "count"])
    allowed = spread.index.get_level_values("metric").map(lambda metric: TOLERANCE.get(metric, 0))
    # The tiny extra margin absorbs floating-point noise in the subtraction, nothing more.
    differs = (spread["max"] - spread["min"]).to_numpy() > allowed.to_numpy() + 1e-9
    for (quarter, metric), r in spread[differs].iterrows():
        problems.append(f"{quarter} {metric} differs between releases: {r['min']:,.2f} vs {r['max']:,.2f}")

    first = readings.sort_values("filed").drop_duplicates(["quarter", "metric"]).pivot(
        index="quarter", columns="metric", values="value"
    )
    gap = (first["gaap"] + first["refinery"] - first["adjusted"]).abs()
    for quarter in gap[gap > 1].index:
        problems.append(f"{quarter}: GAAP revenue minus refinery sales does not equal adjusted revenue.")

    if GAAP_REVENUE_PATH.exists():
        sec = pd.read_csv(GAAP_REVENUE_PATH)
        sec = sec.set_index(pd.PeriodIndex(sec["quarter"], freq="Q"))["revenue_usd"] / 1_000_000
        # Q4 in that file is a full year minus three rounded quarters, so it can be off by $2M.
        gap = (first["gaap"] - sec.reindex(first.index)).abs()
        for quarter in gap[gap > 2].index:
            problems.append(f"{quarter}: GAAP revenue in the release differs from {GAAP_REVENUE_PATH.name}.")

    if problems:
        raise ValueError("Checks failed:\n  " + "\n  ".join(problems))

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    ops.to_csv(OUTPUT_PATH, index=False)
    print(
        f"Saved {len(ops)} quarters ({ops['quarter'].iloc[0]} to {ops['quarter'].iloc[-1]}) to {OUTPUT_PATH}\n"
        f"Cross-checked {int((spread['count'] > 1).sum())} of {len(spread)} figures against a later release."
    )


if __name__ == "__main__":
    main()
