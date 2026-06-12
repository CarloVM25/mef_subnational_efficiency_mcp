import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import httpx
from fastmcp import FastMCP

BASE_URL = "https://datosabiertos.gob.pe"
PDF_LANDING_URL = (
    "https://fuenteshistoricasdelperu.com/2021/08/12/"
    "ministerio-de-hacienda-y-comercio-presupuesto-balance-y-cuenta-general-de-la-republica/"
)
RAW_PDFS_DIR = Path(__file__).parent.parent / "data" / "raw_pdfs"
PDF_1964_PATH = RAW_PDFS_DIR / "presupuesto_1964.pdf"

mcp = FastMCP(
    "datosabiertos-peru",
    instructions="MCP server for the Peruvian Open Data Portal (datosabiertos.gob.pe). "
    "Provides tools to search, inspect, and analyze public datasets.",
)


@mcp.tool()
async def buscar_datasets(query: str) -> Dict[str, Any]:
    """Busca datasets en el Portal de Datos Abiertos del Perú via CKAN package_search."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{BASE_URL}/api/3/action/package_search",
                params={"q": query},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Request failed: {str(e)}"}


@mcp.tool()
async def obtener_detalle_dataset(dataset_id: str) -> Dict[str, Any]:
    """Obtiene detalles y URLs de descarga directa de los recursos de un dataset via CKAN package_show."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{BASE_URL}/api/3/action/package_show",
                params={"id": dataset_id},
            )
            response.raise_for_status()
            data = response.json()
            if data.get("success") and data.get("result"):
                resources = data["result"].get("resources", [])
                data["download_urls"] = [
                    {
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "url": r.get("url"),
                        "format": r.get("format"),
                        "size": r.get("size"),
                    }
                    for r in resources
                ]
            return data
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Request failed: {str(e)}"}


@mcp.tool()
async def inspeccionar_esquema_csv(resource_url: str) -> Dict[str, Any]:
    """Abre un stream parcial de un recurso CSV para capturar solo las cabeceras y las primeras 5 filas."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            async with client.stream("GET", resource_url) as response:
                response.raise_for_status()
                buffer = b""
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    buffer += chunk
                    if buffer.count(b"\n") >= 7:
                        break

            text = buffer.decode("utf-8", errors="replace")
            reader = csv.reader(io.StringIO(text))
            rows = [row for i, row in enumerate(reader) if i < 6]

            if not rows:
                return {"success": False, "error": "No data found in CSV"}

            return {
                "success": True,
                "headers": rows[0],
                "sample_rows": rows[1:6],
                "column_count": len(rows[0]),
            }
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Request failed: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"CSV parsing error: {str(e)}"}


@mcp.tool()
async def consultar_datastore_filtrado(
    resource_id: str,
    limit: int = 100,
    filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Realiza consultas filtradas al datastore de un recurso via CKAN datastore_search."""
    params: Dict[str, Any] = {"resource_id": resource_id, "limit": limit}
    if filters:
        params["filters"] = json.dumps(filters)

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{BASE_URL}/api/3/action/datastore_search",
                params=params,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Request failed: {str(e)}"}


@mcp.tool()
async def listar_entidades_publicas() -> Dict[str, Any]:
    """Obtiene la lista de entidades públicas activas via CKAN organization_list."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{BASE_URL}/api/3/action/organization_list",
                params={"all_fields": True, "include_extras": True},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Request failed: {str(e)}"}


@mcp.tool()
async def obtener_ultimas_actualizaciones(limit: int = 20) -> Dict[str, Any]:
    """Obtiene los datasets actualizados recientemente via CKAN recently_changed_packages_activity_list."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{BASE_URL}/api/3/action/recently_changed_packages_activity_list",
                params={"limit": limit},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Request failed: {str(e)}"}


@mcp.tool()
async def descargar_documento_1964() -> Dict[str, Any]:
    """Descarga el PDF histórico de 1964 del Ministerio de Hacienda y Comercio al directorio data/raw_pdfs/."""
    RAW_PDFS_DIR.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        try:
            page_response = await client.get(PDF_LANDING_URL)
            page_response.raise_for_status()

            pdf_links = re.findall(
                r'href=["\']([^"\']*\.pdf[^"\']*)["\']',
                page_response.text,
                re.IGNORECASE,
            )
            if not pdf_links:
                return {
                    "success": False,
                    "error": "No PDF link found on the landing page",
                    "page_url": PDF_LANDING_URL,
                }

            pdf_url = pdf_links[0]
            if not pdf_url.startswith("http"):
                pdf_url = urljoin(PDF_LANDING_URL, pdf_url)

            pdf_response = await client.get(pdf_url)
            pdf_response.raise_for_status()

            PDF_1964_PATH.write_bytes(pdf_response.content)

            return {
                "success": True,
                "path": str(PDF_1964_PATH),
                "size_bytes": len(pdf_response.content),
                "source_url": pdf_url,
            }
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Request failed: {str(e)}"}


PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"


@mcp.tool()
async def procesar_ocr_paginas_1964() -> Dict[str, Any]:
    """Ejecuta PaddleOCR sobre exactamente 15 páginas del PDF histórico de 1964."""
    if not PDF_1964_PATH.exists():
        return {
            "success": False,
            "error": f"PDF not found at {PDF_1964_PATH}. Run descargar_documento_1964 first.",
        }

    try:
        import fitz  # PyMuPDF
    except ImportError:
        return {
            "success": False,
            "error": "PyMuPDF not installed.",
            "fix": "pip install pymupdf",
        }

    try:
        from paddleocr import PaddleOCR
    except ImportError:
        return {
            "success": False,
            "error": "PaddleOCR not installed.",
            "fix": "pip install paddleocr paddlepaddle",
        }

    try:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        ocr = PaddleOCR(use_angle_cls=True, lang="es", show_log=False)

        MAX_PAGES = 15
        doc = fitz.open(str(PDF_1964_PATH))
        total_pages = len(doc)
        pages_to_process = min(MAX_PAGES, total_pages)

        results = []
        for page_num in range(pages_to_process):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=200)
            img_path = PROCESSED_DIR / f"page_1964_{page_num + 1:03d}.png"
            pix.save(str(img_path))

            ocr_result = ocr.ocr(str(img_path), cls=True)
            lines = []
            if ocr_result and ocr_result[0]:
                for line in ocr_result[0]:
                    # Each line is [[bbox], [text, confidence]]
                    if line and len(line) >= 2:
                        lines.append(line[1][0])

            results.append({"page": page_num + 1, "text": "\n".join(lines)})

        doc.close()

        return {
            "success": True,
            "pages_processed": pages_to_process,
            "total_pages_in_pdf": total_pages,
            "results": results,
        }
    except Exception as e:
        return {"success": False, "error": f"OCR processing failed: {str(e)}"}


@mcp.tool()
async def descargar_y_analizar_estadisticas(
    dataset_id: str,
    resource_url: str,
) -> Dict[str, Any]:
    """Descarga un dataset CSV y retorna agregaciones y resúmenes estadísticos descriptivos."""
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        try:
            response = await client.get(resource_url)
            response.raise_for_status()

            text = response.content.decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)

            if not rows:
                return {"success": False, "dataset_id": dataset_id, "error": "Empty dataset"}

            headers = list(reader.fieldnames or [])
            row_count = len(rows)

            numeric_stats: Dict[str, Any] = {}
            for col in headers:
                values = []
                for row in rows:
                    raw = (row.get(col) or "").strip().replace(",", ".")
                    try:
                        values.append(float(raw))
                    except (ValueError, TypeError):
                        pass

                if len(values) >= 2:
                    values_sorted = sorted(values)
                    n = len(values)
                    mean_val = sum(values) / n
                    mid = n // 2
                    median_val = (values_sorted[mid] + values_sorted[~mid]) / 2
                    numeric_stats[col] = {
                        "count": n,
                        "min": values_sorted[0],
                        "max": values_sorted[-1],
                        "mean": round(mean_val, 4),
                        "median": round(median_val, 4),
                        "missing": row_count - n,
                    }

            return {
                "success": True,
                "dataset_id": dataset_id,
                "row_count": row_count,
                "column_count": len(headers),
                "columns": headers,
                "numeric_summary": numeric_stats,
            }
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Request failed: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Analysis failed: {str(e)}"}


@mcp.tool()
async def listar_categorias_tematicas() -> Dict[str, Any]:
    """Obtiene los grupos temáticos del portal de datos abiertos via CKAN group_list."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{BASE_URL}/api/3/action/group_list",
                params={"all_fields": True, "include_extras": True},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Request failed: {str(e)}"}


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
