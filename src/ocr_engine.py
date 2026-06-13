#!/usr/bin/env python3
"""
OCR engine for the 1964 Peruvian Ministry of Finance historical budget PDF.

Extracts financial text from 15 selected pages using PaddleOCR, then saves
structured JSON and summary CSV, and produces two matplotlib visualizations.

Usage:
    python -m src.ocr_engine
    python -m src.ocr_engine --page-start 5 --page-end 20
"""

import argparse
import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

# Agg backend must be set before any pyplot import so the script works
# in headless environments without a display.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import pandas as pd

# ── paths ──────────────────────────────────────────────────────────────────────
PDF_PATH      = Path("data/raw_pdfs/presupuesto_1964.pdf")
PROCESSED_DIR = Path("data/processed")

JSON_OUT      = PROCESSED_DIR / "historical_1964.json"
PARTIAL_JSON  = PROCESSED_DIR / "historical_1964_partial.json"
CSV_OUT    = PROCESSED_DIR / "historical_1964_summary.csv"
CHART1_OUT = PROCESSED_DIR / "chart_numbers_per_page.png"
CHART2_OUT = PROCESSED_DIR / "chart_top15_keywords.png"

# ── processing parameters ──────────────────────────────────────────────────────
# Pages 5-19 (1-indexed) are most likely to contain financial tables in a
# 1960s Peruvian budget PDF (skips cover, index, and preamble pages).
DEFAULT_PAGE_START = 4   # 0-indexed
DEFAULT_PAGE_END   = 19  # 0-indexed, exclusive
N_PAGES            = 15
DPI                = 150

# ── regex patterns ─────────────────────────────────────────────────────────────
# Matches numbers with thousands separators (1,500,000 / 1.500.000),
# large plain integers (≥5 digits), and decimal amounts (1500.50).
_NUM_RE = re.compile(
    r"\b\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?\b"
    r"|\b\d{5,}\b"
    r"|\b\d+[.,]\d{2}\b"
)

# Tokens used in government/budget documents that mark category headings
_CATEGORY_KEYWORDS = frozenset({
    "MINISTERIO", "GOBIERNO", "PRESUPUESTO", "GASTO", "GASTOS",
    "INGRESO", "INGRESOS", "REPUBLICA", "CAPITAL", "CORRIENTE",
    "HACIENDA", "COMERCIO", "PLIEGO", "PARTIDA", "SUBPARTIDA",
    "PERSONAL", "BIENES", "SERVICIOS", "TESORO", "FONDO",
    "EDUCACION", "SALUD", "OBRAS", "PUBLICA", "NACIONAL",
    "DEPARTAMENTO", "SECCION", "CAPITULO", "ARTICULO",
})

# Short function-word stopwords for the keyword frequency chart
_STOPWORDS = frozenset({
    "DE", "DEL", "LA", "EL", "LOS", "LAS", "EN", "Y", "A",
    "POR", "PARA", "CON", "QUE", "SE", "UN", "UNA", "AL",
    "NO", "SU", "ES", "O", "LO", "LE", "SI", "MAS", "PERO",
    "THE", "OF", "AND", "OR", "TO", "IN",
})

# ── logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── OCR initialisation ─────────────────────────────────────────────────────────

def _init_ocr():
    """
    Return a PaddleOCR instance configured for historical Spanish documents.

    enable_mkldnn=False is required on Windows + PaddlePaddle 3.x to avoid a
    NotImplementedError in the OneDNN PIR executor when running on CPU.
    """
    from paddleocr import PaddleOCR  # deferred import: heavy initialisation
    logger.info("Initialising PaddleOCR (lang=es, mkldnn=disabled)...")
    return PaddleOCR(lang="es", enable_mkldnn=False)


# ── PDF → image ────────────────────────────────────────────────────────────────

def _page_to_image(doc: fitz.Document, page_num: int) -> Path:
    """Render one PDF page at DPI, save as PNG, and return the saved path."""
    page = doc[page_num]
    scale = DPI / 72  # fitz native resolution is 72 pt/inch
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    out_path = PROCESSED_DIR / f"page_1964_{page_num + 1:03d}.png"
    pix.save(str(out_path))
    return out_path


# ── OCR result parsing ─────────────────────────────────────────────────────────

def _run_ocr(ocr, img_path: Path) -> list[str]:
    """
    Run PaddleOCR on an image and return a flat list of recognised text lines.

    Uses the modern predict() API (PaddleOCR 3.x). Each result item is an
    OCRResult object that supports dict-style .get() access; rec_texts holds
    the recognised string for each detected text region.
    """
    result = ocr.predict(str(img_path))
    if not result or not result[0]:
        return []
    return list(result[0].get("rec_texts", []))


def _save_partial(pages_data: list[dict[str, Any]]) -> None:
    """
    Persist the pages processed so far to data/processed/historical_1964_partial.json.
    Called after each successful page so progress survives a crash.
    At most 15 small dicts, so rewriting the whole file each time is negligible.
    """
    PARTIAL_JSON.write_text(
        json.dumps(
            {"pages_completed": len(pages_data), "pages": pages_data},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _extract_numbers(lines: list[str]) -> list[str]:
    """Return all numeric tokens (budget amounts, quantities) from OCR lines."""
    found: list[str] = []
    for line in lines:
        found.extend(_NUM_RE.findall(line))
    return found


def _extract_categories(lines: list[str]) -> list[str]:
    """
    Return lines that are likely category headings: either mostly uppercase
    words (≥2 tokens of len ≥3) or containing a known financial keyword.
    """
    categories: list[str] = []
    for line in lines:
        stripped = line.strip()
        if len(stripped) < 4:
            continue
        upper_tokens = [w for w in stripped.split() if w.isupper() and len(w) >= 3]
        keyword_hit = any(kw in stripped.upper() for kw in _CATEGORY_KEYWORDS)
        if len(upper_tokens) >= 2 or keyword_hit:
            categories.append(stripped)
    return categories


# ── main extraction loop ───────────────────────────────────────────────────────

def process_pages(
    ocr,
    page_start: int = DEFAULT_PAGE_START,
    page_end: int = DEFAULT_PAGE_END,
) -> list[dict[str, Any]]:
    """
    Render and OCR exactly N_PAGES pages from the PDF (pages page_start to
    min(page_end, total_pages), 0-indexed).

    Returns a list of per-page result dicts; failed pages are included with
    an 'error' key and empty data fields so downstream code always gets 15 rows.
    """
    if not PDF_PATH.exists():
        raise FileNotFoundError(
            f"PDF not found: {PDF_PATH}. Run descargar_documento_1964 first."
        )
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(PDF_PATH))
    total_pages = len(doc)
    page_indices = list(range(page_start, min(page_end, total_pages)))

    logger.info(
        "PDF loaded: %d total pages.  Processing pages %d–%d (%d pages).",
        total_pages, page_start + 1, page_indices[-1] + 1, len(page_indices),
    )

    pages_data: list[dict[str, Any]] = []

    for idx, page_num in enumerate(page_indices, start=1):
        logger.info(
            "Page %d/%d  (PDF page %d)...", idx, len(page_indices), page_num + 1
        )
        try:
            img_path = _page_to_image(doc, page_num)
            lines = _run_ocr(ocr, img_path)
            numbers = _extract_numbers(lines)
            categories = _extract_categories(lines)

            pages_data.append({
                "page_num": page_num + 1,
                "image_path": str(img_path),
                "raw_text": "\n".join(lines),
                "numbers_found": numbers,
                "categories_found": categories,
            })
            _save_partial(pages_data)
            logger.info(
                "  -> %d text lines | %d numbers | %d categories",
                len(lines), len(numbers), len(categories),
            )
        except Exception as exc:
            logger.error(
                "Page %d OCR failed: %s", page_num + 1, exc, exc_info=True
            )
            pages_data.append({
                "page_num": page_num + 1,
                "image_path": None,
                "raw_text": "",
                "numbers_found": [],
                "categories_found": [],
                "error": str(exc),
            })

    doc.close()
    return pages_data


# ── output writers ─────────────────────────────────────────────────────────────

def save_json(pages_data: list[dict[str, Any]]) -> None:
    """Write full extraction results to data/processed/historical_1964.json."""
    payload = {
        "pages_processed": len(pages_data),
        "extraction_date": datetime.now().isoformat(),
        "pages": pages_data,
    }
    JSON_OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("JSON saved -> %s", JSON_OUT)


def save_summary_csv(pages_data: list[dict[str, Any]]) -> None:
    """Write per-page summary to data/processed/historical_1964_summary.csv."""
    rows = [
        {
            "page_num": p["page_num"],
            "total_numbers_found": len(p.get("numbers_found", [])),
            "total_categories_found": len(p.get("categories_found", [])),
            "sample_text": (p.get("raw_text", "")[:200]
                            .replace("\n", " ").strip()),
        }
        for p in pages_data
    ]
    pd.DataFrame(rows).to_csv(CSV_OUT, index=False, encoding="utf-8-sig")
    logger.info("Summary CSV saved -> %s", CSV_OUT)


# ── visualisations ─────────────────────────────────────────────────────────────

def generate_visualizations(pages_data: list[dict[str, Any]]) -> None:
    """
    Produce and save two charts from the extracted data:

    chart1  Bar chart of total numerical values found per page.
    chart2  Horizontal bar chart of top-15 most frequent category keywords
            across all processed pages.
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    page_nums  = [p["page_num"] for p in pages_data]
    num_counts = [len(p.get("numbers_found", [])) for p in pages_data]

    # ── chart 1 ───────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(13, 5))
    bars = ax.bar(
        page_nums, num_counts,
        color="#2c7bb6", edgecolor="white", width=0.7,
    )
    ax.bar_label(bars, padding=3, fontsize=8)
    ax.set_xticks(page_nums)
    ax.set_xlabel("PDF Page Number", fontsize=11)
    ax.set_ylabel("Numerical Values Found", fontsize=11)
    ax.set_title(
        "Numerical Values Extracted per Page — 1964 Peruvian Budget PDF",
        fontsize=13, pad=12,
    )
    ax.yaxis.grid(True, linestyle="--", alpha=0.5)
    ax.set_axisbelow(True)
    plt.tight_layout()
    fig.savefig(CHART1_OUT, dpi=150)
    plt.close(fig)
    logger.info("Chart 1 saved -> %s", CHART1_OUT)

    # ── chart 2: keyword frequency ────────────────────────────────────────────
    word_counter: Counter = Counter()
    for p in pages_data:
        for cat_line in p.get("categories_found", []):
            for token in cat_line.split():
                clean = re.sub(r"[^\w]", "", token).upper()
                if len(clean) >= 4 and clean not in _STOPWORDS:
                    word_counter[clean] += 1

    top15 = word_counter.most_common(15)
    if not top15:
        logger.warning("No category keywords found — chart 2 skipped.")
        return

    labels, counts = zip(*reversed(top15))

    fig, ax = plt.subplots(figsize=(10, 7))
    bars = ax.barh(list(labels), list(counts), color="#d7191c", edgecolor="white")
    ax.bar_label(bars, padding=3, fontsize=9)
    ax.set_xlabel("Frequency", fontsize=11)
    ax.set_title(
        "Top 15 Category Keywords — 1964 Peruvian Budget PDF",
        fontsize=13, pad=12,
    )
    ax.xaxis.grid(True, linestyle="--", alpha=0.5)
    ax.set_axisbelow(True)
    plt.tight_layout()
    fig.savefig(CHART2_OUT, dpi=150)
    plt.close(fig)
    logger.info("Chart 2 saved -> %s", CHART2_OUT)


# ── entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OCR engine for the 1964 Peruvian budget PDF",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--page-start", type=int, default=DEFAULT_PAGE_START,
        metavar="N",
        help="First page to process (0-indexed)",
    )
    parser.add_argument(
        "--page-end", type=int, default=DEFAULT_PAGE_END,
        metavar="N",
        help="Page after the last page to process (0-indexed, exclusive)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()
    logging.getLogger().setLevel(args.log_level)

    ocr = _init_ocr()
    pages_data = process_pages(ocr, args.page_start, args.page_end)
    save_json(pages_data)
    save_summary_csv(pages_data)
    generate_visualizations(pages_data)
    logger.info("Done. %d pages processed.", len(pages_data))


if __name__ == "__main__":
    main()
