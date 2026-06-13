# MEF Subnational Efficiency Dashboard

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.58-FF4B4B?logo=streamlit&logoColor=white)
![PaddleOCR](https://img.shields.io/badge/PaddleOCR-3.7-0070BB?logo=paddlepaddle&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

A local multi-agent analytics pipeline for auditing public expenditure efficiency across Peru's subnational governments. The system ingests live budget data from the MEF Open Data Portal, computes execution metrics, flags underperforming entities, and surfaces findings in an interactive Streamlit dashboard. A secondary pipeline recovers historical budget data from a 1964 PDF using OCR, enabling long-run comparison.

The pipeline is orchestrated through a FastMCP server that exposes discrete skills — executor, evaluator — which Claude Code invokes as tools. All computation runs locally; no cloud inference is required for the data pipeline or dashboard.

## Key Findings — September 2025

Pipeline run against `2025-Gasto-OPDS.csv` (52 MB, 65,660 rows), period `2025-09`:

- **15 entities** identified with PIM > S/ 10,000,000 and execution below threshold
- **S/ 1,061,320,734** in frozen capital (Saldo No Devengado) across those 15 entities
- **National execution rate: 5.5%** by end of September 2025 — well below the ~75% expected at that point in the fiscal year for well-managed entities
- **Worst performer:** FONDO METROPOLITANO DE INVERSIONES DE LIMA — 2.1% execution on S/ 369,873,483 PIM (S/ 362M frozen)
- **Avance range:** 2.1% – 9.7% across the 15 flagged entities
- **Historical OCR (1964 PDF):** 15 pages processed, 50 numerical values and 199 category labels extracted

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Claude Code (Claude AI)               │
│                                                         │
│   calls tools exposed by the local MCP server below    │
└─────────────────────┬───────────────────────────────────┘
                      │ MCP (stdio)
                      ▼
┌─────────────────────────────────────────────────────────┐
│              src/mcp_server.py  (FastMCP)               │
│                                                         │
│   ┌──────────────────────┐  ┌──────────────────────┐   │
│   │   executor_skill     │  │   evaluator_skill    │   │
│   │  runs data pipeline  │  │  audits app.py +     │   │
│   │  + OCR engine        │  │  pipeline output     │   │
│   └──────────┬───────────┘  └──────────┬───────────┘   │
└──────────────┼──────────────────────────┼───────────────┘
               │                          │
       ┌───────▼────────┐        ┌────────▼────────┐
       │ src/            │        │ data/processed/ │
       │ data_pipeline.py│        │ evaluator_      │
       │ ocr_engine.py   │        │ report.md       │
       │ analytical_     │        └─────────────────┘
       │ engine.py       │
       └───────┬─────────┘
               │ writes parquet + CSV
               ▼
┌─────────────────────────────────────────────────────────┐
│                    app.py  (Streamlit)                   │
│                                                         │
│   Tab 1: National Overview  │  Tab 2: Entity Analysis  │
│   Tab 3: Shame List         │  Tab 4: Historical 1964  │
└─────────────────────────────────────────────────────────┘
               ▲
               │ reads from
       data/processed/
         budget_2025.parquet
         budget_2025_summary.csv
         historical_1964.json
```

## Repository Structure

```
mef_subnational_efficiency_mcp/
├── app.py                          # Streamlit dashboard (4 tabs)
├── requirements.txt                # Pinned dependencies
├── README.md
│
├── src/
│   ├── mcp_server.py               # FastMCP server — exposes executor & evaluator skills
│   ├── data_pipeline.py            # Streams OPDS CSV, aggregates, writes parquet + CSV
│   ├── ocr_engine.py               # PaddleOCR pipeline for 1964 PDF
│   ├── analytical_engine.py        # Shared metric helpers (Avance%, Saldo)
│   └── utils.py                    # Logging, path helpers
│
├── data/
│   ├── raw_pdfs/                   # Place presupuesto_1964.pdf here (not tracked)
│   ├── processed/
│   │   ├── budget_2025.parquet     # Full 15-entity dataset (pipeline output)
│   │   ├── budget_2025_summary.csv # Top-50 worst by Avance % (dashboard input)
│   │   ├── historical_1964.json    # OCR extraction results
│   │   ├── historical_1964_summary.csv
│   │   ├── evaluator_report.md     # Evaluator skill output
│   │   └── page_1964_*.png         # Rendered PDF page images (DPI 150)
│   └── snapshots/
│       └── budget_2025_schema.json # Column schema snapshot (58 columns)
│
└── video/
    └── link.txt                    # Demo video link
```

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/CarloVM25/mef_subnational_efficiency_mcp.git
cd mef_subnational_efficiency_mcp

# 2. Create and activate a virtual environment
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

> **Note:** PaddlePaddle and PaddleOCR pull in large model weights on first run (~500 MB). On Windows, `enable_mkldnn=False` is set automatically to avoid a PaddlePaddle 3.x CPU executor error.

## Usage

### Run the data pipeline

Fetches and processes the live OPDS dataset from `datosabiertos.mef.gob.pe`.

```bash
# Full run for September 2025
python -m src.data_pipeline --period 2025-9

# Custom period (e.g. full year through December)
python -m src.data_pipeline --period 2025-12

# Schema snapshot only (no full CSV download)
python -m src.data_pipeline --snapshot-only
```

Outputs:
- `data/processed/budget_2025.parquet` — filtered entity dataset
- `data/processed/budget_2025_summary.csv` — top-50 worst executors

### Run OCR on the 1964 PDF

Place `presupuesto_1964.pdf` in `data/raw_pdfs/`, then:

```bash
python -m src.ocr_engine
# Process a custom page range (0-indexed)
python -m src.ocr_engine --page-start 4 --page-end 19
```

Outputs:
- `data/processed/historical_1964.json`
- `data/processed/historical_1964_summary.csv`
- `data/processed/chart_numbers_per_page.png`
- `data/processed/chart_top15_keywords.png`

### Launch the dashboard

```bash
streamlit run app.py
```

Opens at `http://localhost:8501` by default.

### Start the MCP server (for Claude Code integration)

```bash
python -m src.mcp_server
```

Add to your Claude Code MCP config (`~/.claude/settings.json` or `.claude/settings.json`):

```json
{
  "mcpServers": {
    "mef-efficiency": {
      "command": "python",
      "args": ["-m", "src.mcp_server"],
      "cwd": "/path/to/mef_subnational_efficiency_mcp"
    }
  }
}
```

## Dashboard Tabs

| Tab | Name | Description |
|-----|------|-------------|
| **1** | National Overview | KPI cards for total PIM, total Devengado, and weighted national Avance %. Contextual narrative explaining subnational budget fragmentation. |
| **2** | Entity Analysis | Filterable scatter plot (PIM vs. Avance %, bubble size = Saldo) and bar chart of average execution by entity type (municipal OPD vs. regional OPD). Dropdown filters by executor name. |
| **3** | Shame List | All entities with PIM > S/ 10M and Avance < 30%, sorted by worst performance. Includes formatted table (Entidad, Departamento, Nivel, PIM, Devengado, Avance %, Saldo) and horizontal bar chart of top-20 worst executors. |
| **4** | Historical 1964 | OCR extraction results from the 1964 Peruvian national budget PDF — numerical values per page, top-15 category keywords, and raw page images for visual verification. |

## Analytical Metrics

**Avance % (Budget Execution Rate)**

```
Avance_Pct = (MONTO_DEVENGADO / MONTO_PIM) × 100
```

Computed after a two-pass merge: `MES_EJE = 0` rows supply `MONTO_PIM` (annual budget allocation); `MES_EJE ∈ {1..12}` rows supply `MONTO_DEVENGADO` (monthly cumulative execution). Both sets are grouped by `[EJECUTORA_NOMBRE, DEPARTAMENTO_EJECUTORA_NOMBRE, GRUPO_ENTIDAD_NOMBRE]` and outer-joined before the ratio is computed.

**Saldo No Devengado (Unexecuted Balance)**

```
Saldo_No_Devengado = MONTO_PIM − MONTO_DEVENGADO
```

Represents capital appropriated but not yet committed to spending. A large Saldo at mid-year is the primary indicator of execution risk.

**Entity filter:** only entities with `MONTO_PIM > S/ 10,000,000` are retained. This threshold excludes very small executing units where low Avance % may reflect deliberate phasing rather than dysfunction.

## GitHub Workflow

```
main          ← stable, always deployable
  └── feature/<short-description>   ← one branch per feature/fix
        └── PR → review → squash merge into main
```

Branches follow the convention used in this repository:

| Branch | Purpose |
|--------|---------|
| `main` | Stable dashboard + pipeline |
| `feature/historical-1964-paddle-ocr` | PaddleOCR integration for 1964 PDF |
| `feature/executor-dashboard-draft` | Initial 4-tab Streamlit dashboard |
| `feature/evaluator-qa-refinement` | Evaluator skill + QA report scaffold |

## Limitations & Next Steps

**Current limitations:**

- The OPDS dataset covers only regional and municipal OPDs (Organismos Públicos Descentralizados). Central-government ministries and regional governments proper are excluded.
- `datastore_search` (server-side CKAN filtering) is inactive on `www.datosabiertos.gob.pe`; the pipeline streams and filters the full 52 MB CSV on every run.
- OCR quality on the 1964 PDF degrades on pages with two-column tables and handwritten annotations.
- The evaluator report (`data/processed/evaluator_report.md`) contains placeholder entries pending a live automated run.

**Next steps:**

- [ ] Add the full `2025-Gasto-Mensual.csv` (9.78 GB) as an optional data source to cover all levels of government
- [ ] Implement incremental caching: skip re-download if the remote file's `Last-Modified` header has not changed
- [ ] Connect the MCP evaluator skill to auto-update `evaluator_report.md` on each pipeline run
- [ ] Extend the historical OCR pipeline to additional budget years (1965–1990) for long-run trend analysis
- [ ] Add a Plotly time-series chart in Tab 2 showing Avance % evolution across months once multi-period data is available

## License

MIT © 2025 Carlos Villanueva
