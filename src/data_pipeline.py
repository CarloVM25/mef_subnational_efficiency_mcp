#!/usr/bin/env python3
"""
Peruvian public budget analysis pipeline — fiscal year 2025.

Downloads the pre-filtered OPDS CSV (~52 MB, regional + municipal OPDs) from
fs.datosabiertos.mef.gob.pe and reconstructs entity-level Avance % by merging
two row types that exist in the file:

  MES_EJE == 0   → annual budget rows  (MONTO_PIM populated, MONTO_DEVENGADO = 0)
  MES_EJE == N   → monthly exec rows   (MONTO_PIM = 0, MONTO_DEVENGADO populated)

The pipeline streams the file once, routes each row into the right bucket,
then aggregates both buckets by entity + department + OPD type and outer-joins
them before computing Avance_Pct and Saldo_No_Devengado.

Why not CKAN datastore_search?
  www.datosabiertos.gob.pe returns HTTP 500 for all datastore_search calls —
  the CKAN datastore is inactive on this portal.  Direct CSV streaming is the
  only available access method.

Usage:
    python -m src.data_pipeline --period 2025-Q4
    python -m src.data_pipeline --period 2025-12 --resource-id <uuid>
    python -m src.data_pipeline --snapshot-only --resource-id <uuid>
"""

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ── output paths ──────────────────────────────────────────────────────────────
PROCESSED_DIR = Path("data/processed")
SNAPSHOTS_DIR = Path("data/snapshots")

# ── CKAN / file server ────────────────────────────────────────────────────────
BASE_URL   = "https://www.datosabiertos.gob.pe"

# Package containing the OPDS (regional + municipal OPD) gasto CSVs.
PACKAGE_ID = "gasto-presupuestal-de-los-organismos-públicos-descentralizados-regionales-y-municipales"

# Resource UUID for "2025-Gasto-OPDS.csv" (~52 MB, pre-filtered to OPDs).
# Confirm/update at:
#   https://www.datosabiertos.gob.pe/api/3/action/package_show?id=<PACKAGE_ID>
# Override at runtime with --resource-id.
DEFAULT_RESOURCE_ID = "98b4f75d-2515-46e9-91a5-1e51b9ae4ab3"

# ── shared HTTP options ───────────────────────────────────────────────────────
_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0"}
_HTTP_TIMEOUT = 120
_HTTP_VERIFY  = False   # bypass self-signed / corporate certificate issues

# ── column names (as they appear in the OPDS CSV) ─────────────────────────────
COL_YEAR   = "ANO_EJE"
COL_MONTH  = "MES_EJE"
COL_NIVEL  = "GRUPO_ENTIDAD_NOMBRE"          # "...REGIONALES" / "...MUNICIPALES"
COL_ENTITY = "EJECUTORA_NOMBRE"
COL_DEPT   = "DEPARTAMENTO_EJECUTORA_NOMBRE"
COL_PIM    = "MONTO_PIM"
COL_DEV    = "MONTO_DEVENGADO"

# Derived columns written to the output files
COL_AVANCE = "Avance_Pct"
COL_SALDO  = "Saldo_No_Devengado"

# Dimensions used for groupby aggregation when merging PIM and DEV rows
GROUP_COLS   = [COL_ENTITY, COL_DEPT, COL_NIVEL]
SUMMARY_COLS = [COL_ENTITY, COL_DEPT, COL_NIVEL, COL_PIM, COL_DEV, COL_AVANCE, COL_SALDO]

# ── filter threshold ──────────────────────────────────────────────────────────
# Applied after merging: only keep entities whose annual PIM exceeds this value.
PIM_MIN = 10_000_000

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
    """Coerce a string/mixed series to float; NaN → 0.0."""
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    ).fillna(0.0)


def _get_resource_url(resource_id: str) -> str:
    """
    Look up the direct CSV download URL for a resource via CKAN.

    Tries resource_show first; falls back to scanning PACKAGE_ID's resource list
    because www.datosabiertos.gob.pe disables some CKAN actions (e.g. package_search).
    """
    with httpx.Client(headers=_HTTP_HEADERS, timeout=30, verify=_HTTP_VERIFY) as client:
        resp = client.get(
            f"{BASE_URL}/api/3/action/resource_show",
            params={"id": resource_id},
        )
        if resp.is_success:
            payload = resp.json()
            if payload.get("success"):
                url = payload["result"]["url"]
                logger.debug("resource_show → %s", url)
                return url

        logger.debug("resource_show unavailable; falling back to package_show.")
        resp = client.get(
            f"{BASE_URL}/api/3/action/package_show",
            params={"id": PACKAGE_ID},
        )
        resp.raise_for_status()
        payload = resp.json()

    if not payload.get("success"):
        raise RuntimeError(f"CKAN package_show failed: {payload.get('error')}")

    for res in payload["result"]["resources"]:
        if res["id"] == resource_id:
            url = res["url"]
            logger.debug("package_show → %s", url)
            return url

    raise RuntimeError(
        f"Resource {resource_id!r} not found in package '{PACKAGE_ID}'. "
        "Pass the UUID of a resource that belongs to that package, "
        "or update PACKAGE_ID in the source to match a different package."
    )


def _iter_decoded_lines(response: httpx.Response):
    """Yield UTF-8 decoded lines from an httpx streaming response, stripping any BOM."""
    first = True
    for line in response.iter_lines():
        yield line.lstrip("﻿") if first else line
        first = False


# ── two-pass CSV collection ───────────────────────────────────────────────────

def _stream_raw_rows(
    resource_id: str,
    year: Optional[int],
    months: Optional[list[int]],
) -> tuple[list[dict], list[dict]]:
    """
    Stream the OPDS CSV once, partitioning rows into two buckets:

      pim_rows  —  MES_EJE == 0  (annual budget; MONTO_PIM populated)
      dev_rows  —  MES_EJE in months  (monthly execution; MONTO_DEVENGADO populated)

    year=None   → no year filter (accept all years present in the file)
    months=None → collect all months 1–12 for dev_rows
    """
    months_set = set(months) if months is not None else set(range(1, 13))
    csv_url = _get_resource_url(resource_id)
    logger.info("Streaming CSV → %s", csv_url)

    pim_rows: list[dict] = []
    dev_rows: list[dict] = []
    total_read = 0

    with httpx.Client(
        headers=_HTTP_HEADERS,
        timeout=_HTTP_TIMEOUT,
        verify=_HTTP_VERIFY,
        follow_redirects=True,
    ) as client:
        with client.stream("GET", csv_url) as response:
            response.raise_for_status()
            reader = csv.DictReader(_iter_decoded_lines(response))

            for row in reader:
                total_read += 1
                try:
                    row_year  = int(float(row.get(COL_YEAR,  0) or 0))
                    row_month = int(float(row.get(COL_MONTH, -1) or -1))
                except (ValueError, TypeError):
                    continue

                if year is not None and row_year != year:
                    continue

                if row_month == 0:
                    pim_rows.append(row)
                elif row_month in months_set:
                    dev_rows.append(row)

                if total_read % 50_000 == 0:
                    logger.info(
                        "  … %d rows read | pim_rows=%d  dev_rows=%d",
                        total_read, len(pim_rows), len(dev_rows),
                    )

    logger.info(
        "Stream complete — %d rows read → %d PIM rows (MES_EJE=0), "
        "%d DEV rows (MES_EJE∈%s)",
        total_read, len(pim_rows), len(dev_rows), sorted(months_set),
    )
    return pim_rows, dev_rows


# ── aggregation and merge ─────────────────────────────────────────────────────

def _merge_and_filter(
    pim_rows: list[dict],
    dev_rows: list[dict],
) -> pd.DataFrame:
    """
    Aggregate PIM rows and DEV rows independently by GROUP_COLS, then outer-join
    them and apply the PIM_MIN threshold.

    Returns a DataFrame ready for _add_metrics(), or an empty DataFrame if no
    entity clears the threshold.
    """
    def _agg(rows: list[dict], value_col: str) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=GROUP_COLS + [value_col])
        df = pd.DataFrame(rows)
        df[value_col] = _to_numeric_col(df[value_col])
        keep_cols = [c for c in GROUP_COLS if c in df.columns] + [value_col]
        return df[keep_cols].groupby(GROUP_COLS, as_index=False)[value_col].sum()

    pim_agg = _agg(pim_rows, COL_PIM)
    dev_agg = _agg(dev_rows, COL_DEV)

    merged = pim_agg.merge(dev_agg, on=GROUP_COLS, how="outer").fillna(0.0)

    for col in (COL_PIM, COL_DEV):
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0.0)

    result = merged[merged[COL_PIM] > PIM_MIN].reset_index(drop=True)
    logger.info(
        "Merge complete — %d unique entities | %d pass PIM > %s",
        len(merged), len(result), f"{PIM_MIN:,}",
    )
    return result


# ── metric calculation ────────────────────────────────────────────────────────

def _add_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Append Avance_Pct and Saldo_No_Devengado to a copy of df."""
    out = df.copy()
    out[COL_AVANCE] = (out[COL_DEV] / out[COL_PIM].replace(0.0, float("nan"))) * 100
    out[COL_SALDO]  = out[COL_PIM] - out[COL_DEV]
    return out


# ── snapshot ──────────────────────────────────────────────────────────────────

def save_snapshot(resource_id: str) -> None:
    """
    Fetch only the first 10 rows from the CSV and write
    column names + sample data to data/snapshots/budget_2025_schema.json.
    Does not read the full file.
    """
    logger.info("Saving schema snapshot for resource %s", resource_id)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    csv_url = _get_resource_url(resource_id)

    with httpx.Client(
        headers=_HTTP_HEADERS,
        timeout=_HTTP_TIMEOUT,
        verify=_HTTP_VERIFY,
        follow_redirects=True,
    ) as client:
        with client.stream("GET", csv_url) as response:
            response.raise_for_status()
            reader = csv.DictReader(_iter_decoded_lines(response))
            columns = list(reader.fieldnames or [])
            records = [row for _, row in zip(range(10), reader)]

    out_path = SNAPSHOTS_DIR / "budget_2025_schema.json"
    out_path.write_text(
        json.dumps(
            {
                "resource_id": resource_id,
                "source_url": csv_url,
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
      1. Parse period → (year, months).  No period = all months in the file.
      2. Stream CSV once, collecting:
           - MES_EJE=0 rows  → annual PIM per entity
           - MES_EJE∈months  → execution DEVENGADO per entity
      3. Aggregate each bucket by (EJECUTORA_NOMBRE, DEPARTAMENTO, GRUPO_ENTIDAD).
      4. Outer-join the two aggregates; filter merged MONTO_PIM > PIM_MIN.
      5. Compute Avance_Pct and Saldo_No_Devengado.
      6. Write to data/processed/budget_2025.parquet and budget_2025_summary.csv.
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = PROCESSED_DIR / "budget_2025.parquet"
    summary_path = PROCESSED_DIR / "budget_2025_summary.csv"

    year: Optional[int] = None
    months: Optional[list[int]] = None
    if period:
        year, months = _parse_period(period)
        logger.info("Period → year=%d  months=%s", year, months)

    logger.info(
        "Pipeline start — resource_id=%s  period=%s  PIM_MIN=%s",
        resource_id, period, f"{PIM_MIN:,}",
    )

    pim_rows, dev_rows = _stream_raw_rows(resource_id, year, months)

    if not pim_rows and not dev_rows:
        logger.warning("No rows collected — check year/period filter.")
        return

    result = _add_metrics(_merge_and_filter(pim_rows, dev_rows))

    if result.empty:
        logger.warning("No entities with PIM > %s after merge.", f"{PIM_MIN:,}")
        return

    # Parquet — aggregated result is small; single write is sufficient
    table = pa.Table.from_pandas(result, preserve_index=False)
    pq.write_table(table, str(parquet_path))
    logger.info("Parquet → %s  (%d entities)", parquet_path, len(result))

    # Summary CSV: top-50 entities with the lowest Avance %
    available_cols = [c for c in SUMMARY_COLS if c in result.columns]
    summary = result[available_cols].sort_values(COL_AVANCE, ascending=True).head(50)
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
        help="CKAN resource UUID for the budget execution dataset",
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
