#!/usr/bin/env python3
"""
Peruvian public budget analysis pipeline — fiscal year 2025.

Downloads filtered execution data (PIM > 10 M soles) from datosabiertos.gob.pe
via the CKAN datastore API, one page at a time.  Never loads the full dataset
into memory.

Usage:
    python -m src.data_pipeline --period 2025-Q4
    python -m src.data_pipeline --period 2025-12 --resource-id <uuid>
    python -m src.data_pipeline --snapshot-only --resource-id <uuid>
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Generator, Optional

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ── output paths ──────────────────────────────────────────────────────────────
PROCESSED_DIR = Path("data/processed")
SNAPSHOTS_DIR = Path("data/snapshots")

# ── CKAN ──────────────────────────────────────────────────────────────────────
BASE_URL = "https://datosabiertos.gob.pe"

# Resource ID for "Ejecución del Gasto Presupuestario" on datosabiertos.gob.pe.
# Find/confirm the current value at:
#   https://datosabiertos.gob.pe/dataset/ejecucion-presupuestal
# Override at runtime with --resource-id.
DEFAULT_RESOURCE_ID = "ejecucion-presupuestal-2025"

# ── column names (as returned by the CKAN datastore) ─────────────────────────
COL_YEAR   = "Año Ejecucion"
COL_MONTH  = "Mes Ejecucion"
COL_NIVEL  = "Nivel de Gobierno"
COL_ENTITY = "Nombre Pliego"
COL_PIM    = "PIM"
COL_DEV    = "Devengado"

# Derived columns written to the output files
COL_AVANCE = "Avance_Pct"
COL_SALDO  = "Saldo_No_Devengado"

SUMMARY_COLS = [COL_ENTITY, COL_NIVEL, COL_PIM, COL_DEV, COL_AVANCE, COL_SALDO]

# ── filter thresholds ─────────────────────────────────────────────────────────
PIM_MIN   = 10_000_000
GOV_TYPES = frozenset({"Gobierno Regional", "Gobierno Local"})

# ── streaming ─────────────────────────────────────────────────────────────────
CHUNK_SIZE = 1_000   # rows per CKAN page request

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_period(period: str) -> tuple[int, list[int]]:
    """
    Parse a period string into (year, [months]).

    '2025-12'  → (2025, [12])
    '2025-Q4'  → (2025, [10, 11, 12])
    """
    parts = period.split("-", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid period {period!r}. Expected YYYY-MM or YYYY-QN.")
    year = int(parts[0])
    suffix = parts[1].upper()
    quarters: dict[str, list[int]] = {
        "Q1": [1, 2, 3],
        "Q2": [4, 5, 6],
        "Q3": [7, 8, 9],
        "Q4": [10, 11, 12],
    }
    if suffix in quarters:
        return year, quarters[suffix]
    return year, [int(suffix)]


def _to_numeric_col(series: pd.Series) -> pd.Series:
    """Strip thousands separators and coerce to float; NaN on failure."""
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    ).fillna(0.0)


def _fetch_page(client: httpx.Client, resource_id: str, offset: int) -> list[dict]:
    """Request one page from CKAN datastore_search and return its records."""
    resp = client.get(
        f"{BASE_URL}/api/3/action/datastore_search",
        params={"resource_id": resource_id, "limit": CHUNK_SIZE, "offset": offset},
    )
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("success"):
        raise RuntimeError(
            f"CKAN error at offset {offset}: {payload.get('error', 'unknown')}"
        )
    return payload["result"]["records"]


# ── streaming generator ───────────────────────────────────────────────────────

def stream_filtered_chunks(
    resource_id: str,
    period: Optional[str],
) -> Generator[pd.DataFrame, None, None]:
    """
    Yield filtered DataFrames one CKAN page at a time.

    Filters applied per chunk (never in bulk):
      • PIM > PIM_MIN
      • Nivel de Gobierno in GOV_TYPES
      • Optional year / month filter from `period`

    At most CHUNK_SIZE rows are held in memory at any point.
    """
    year: Optional[int] = None
    months: Optional[list[int]] = None
    if period:
        year, months = _parse_period(period)
        logger.info("Period filter → year=%d  months=%s", year, months)

    total_fetched = total_kept = offset = 0

    with httpx.Client(timeout=60) as client:
        while True:
            logger.debug("→ CKAN offset=%d", offset)
            records = _fetch_page(client, resource_id, offset)
            if not records:
                logger.info("Empty page at offset=%d — stream complete.", offset)
                break

            df = pd.DataFrame(records)

            # Coerce numeric columns (API may return them as strings)
            for col in (COL_PIM, COL_DEV):
                if col in df.columns:
                    df[col] = _to_numeric_col(df[col])

            # Build filter mask
            mask = (df[COL_PIM] > PIM_MIN) & df[COL_NIVEL].isin(GOV_TYPES)

            if year is not None and COL_YEAR in df.columns:
                mask &= _to_numeric_col(df[COL_YEAR]) == year
            if months is not None and COL_MONTH in df.columns:
                mask &= _to_numeric_col(df[COL_MONTH]).isin(months)

            filtered = df[mask].copy()
            total_fetched += len(df)
            total_kept += len(filtered)

            logger.info(
                "offset=%-7d  page_rows=%d  kept=%d  cumulative=%d/%d",
                offset, len(df), len(filtered), total_kept, total_fetched,
            )

            if not filtered.empty:
                yield filtered

            if len(records) < CHUNK_SIZE:
                break  # last (partial) page
            offset += CHUNK_SIZE

    logger.info(
        "Stream complete — fetched %d rows, kept %d after filters.",
        total_fetched,
        total_kept,
    )


# ── metric calculation ────────────────────────────────────────────────────────

def _add_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Append Avance_Pct and Saldo_No_Devengado to a copy of df."""
    out = df.copy()
    out[COL_AVANCE] = (out[COL_DEV] / out[COL_PIM].replace(0.0, float("nan"))) * 100
    out[COL_SALDO] = out[COL_PIM] - out[COL_DEV]
    return out


# ── snapshot ──────────────────────────────────────────────────────────────────

def save_snapshot(resource_id: str) -> None:
    """
    Fetch only the first 10 rows from the CKAN datastore and write
    column names + sample data to data/snapshots/budget_2025_schema.json.
    Does not touch the full dataset.
    """
    logger.info("Saving schema snapshot for resource %s", resource_id)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{BASE_URL}/api/3/action/datastore_search",
            params={"resource_id": resource_id, "limit": 10},
        )
        resp.raise_for_status()
        payload = resp.json()

    if not payload.get("success"):
        raise RuntimeError(f"Snapshot CKAN error: {payload.get('error')}")

    result = payload["result"]
    columns = [f["id"] for f in result.get("fields", [])]
    records = result.get("records", [])[:10]

    out_path = SNAPSHOTS_DIR / "budget_2025_schema.json"
    out_path.write_text(
        json.dumps(
            {
                "resource_id": resource_id,
                "columns": columns,
                "column_count": len(columns),
                "sample_rows": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("Snapshot → %s  (%d columns)", out_path, len(columns))


# ── pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(resource_id: str, period: Optional[str]) -> None:
    """
    Full pipeline:
      1. Stream and filter budget records from CKAN (one page at a time)
      2. Calculate Avance_Pct and Saldo_No_Devengado per chunk
      3. Write all matching rows to data/processed/budget_2025.parquet
         using an incremental PyArrow writer (never the full frame at once)
      4. Write top-50 worst-performing entities to budget_2025_summary.csv
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = PROCESSED_DIR / "budget_2025.parquet"
    summary_path = PROCESSED_DIR / "budget_2025_summary.csv"

    logger.info(
        "Pipeline start — resource_id=%s  period=%s  PIM_MIN=%s",
        resource_id, period, f"{PIM_MIN:,}",
    )

    parquet_writer: Optional[pq.ParquetWriter] = None
    # Lightweight list: only SUMMARY_COLS columns kept in memory for the CSV
    summary_chunks: list[pd.DataFrame] = []
    rows_written = 0

    try:
        for raw_chunk in stream_filtered_chunks(resource_id, period):
            chunk = _add_metrics(raw_chunk)
            rows_written += len(chunk)

            # Incremental parquet write — schema inferred from first chunk
            table = pa.Table.from_pandas(chunk, preserve_index=False)
            if parquet_writer is None:
                parquet_writer = pq.ParquetWriter(str(parquet_path), table.schema)
            parquet_writer.write_table(table)

            # Accumulate only summary columns (much smaller footprint)
            summary_chunks.append(chunk[SUMMARY_COLS].copy())

    finally:
        if parquet_writer is not None:
            parquet_writer.close()

    if rows_written == 0:
        logger.warning("No records matched the active filters — output files not written.")
        return

    logger.info("Parquet → %s  (%d rows, incremental write)", parquet_path, rows_written)

    # Summary CSV: top-50 entities with the lowest Avance %
    summary = (
        pd.concat(summary_chunks, ignore_index=True)
        .sort_values(COL_AVANCE, ascending=True)
        .head(50)
    )
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    logger.info(
        "Summary CSV → %s  (50 worst, Avance range %.1f%% – %.1f%%)",
        summary_path,
        summary[COL_AVANCE].min(),
        summary[COL_AVANCE].max(),
    )


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Peruvian public budget analysis pipeline — fiscal year 2025",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--period",
        metavar="PERIOD",
        default=None,
        help="Reporting period to filter, e.g. 2025-12 (December) or 2025-Q4",
    )
    parser.add_argument(
        "--resource-id",
        metavar="ID",
        default=DEFAULT_RESOURCE_ID,
        dest="resource_id",
        help="CKAN resource ID for the budget execution dataset",
    )
    parser.add_argument(
        "--snapshot-only",
        action="store_true",
        help="Only save the schema snapshot without running the full pipeline",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)

    if args.snapshot_only:
        save_snapshot(args.resource_id)
        return

    save_snapshot(args.resource_id)
    run_pipeline(resource_id=args.resource_id, period=args.period)


if __name__ == "__main__":
    main()
