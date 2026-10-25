"""
Layout-aware PDF analysis for ingestion.

Nothing needs to be known about a PDF in advance. Every page goes through a cheap
local triage with pdfplumber, which finds:
  - tables (ruling lines / aligned columns)        -> Amazon Textract table analysis
  - embedded images large enough to be content     -> described by a vision model
  - pages that are scans (almost no text layer)    -> Textract text + tables
  - vector-drawn charts (many curves / bars)       -> described by a vision model
Plain-text pages are extracted locally at no extra cost. In "full" mode every page
goes through Textract regardless of triage.

The result is a list of elements (text / table / figure) in page order; tables come
back as Markdown so rows and columns survive chunking and embedding.
"""
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pdfplumber
import pypdfium2

logger = logging.getLogger()

BBox = Tuple[float, float, float, float]  # x0, top, x1, bottom in PDF points, origin top-left

FIGURE_PROMPT = """This image comes from page {page} of the document "{file_name}".
If it is a chart, graph, diagram, or a table rendered as an image, describe it so that someone who cannot see it could answer questions about it:
- what kind of figure it is and its title
- axes, labels, legend and units
- the actual values or the most important data points, and the main trend or takeaway
If it is only decorative (a logo, photo of people, icon, background or signature), reply with exactly: DECORATIVE"""


@dataclass
class Element:
    kind: str          # "text", "table" or "figure"
    page: int          # 0-based page index
    content: str


@dataclass
class PageTriage:
    page: int
    text: str
    table_bboxes: List[BBox] = field(default_factory=list)
    figure_bboxes: List[BBox] = field(default_factory=list)
    scanned: bool = False


# =========================
# Triage
# =========================

def _area(b: BBox) -> float:
    return max(b[2] - b[0], 0) * max(b[3] - b[1], 0)


def _union(boxes: List[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _inside(obj: Dict, boxes: List[BBox]) -> bool:
    cx = (obj["x0"] + obj["x1"]) / 2
    cy = (obj["top"] + obj["bottom"]) / 2
    return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)


def triage_page(page, index: int, min_figure_area_ratio: float = 0.04, min_chart_shapes: int = 15) -> PageTriage:
    """Decide what a page contains using only the local PDF structure."""
    page_area = float(page.width) * float(page.height)
    tables = [tuple(t.bbox) for t in page.find_tables()]

    # Text outside tables (table text is extracted separately so it isn't duplicated)
    text_page = page.filter(lambda obj: not _inside(obj, tables)) if tables else page
    text = text_page.extract_text() or ""

    images = [(img["x0"], img["top"], img["x1"], img["bottom"]) for img in page.images]
    figures = [b for b in images if _area(b) / page_area >= min_figure_area_ratio]

    # A page with no real text layer covered by a big image is a scan
    scanned = len(text.strip()) < 30 and any(_area(b) / page_area >= 0.5 for b in images)
    if scanned:
        figures = []

    # Charts drawn as vector graphics have no image object, but many shapes: curves
    # for line charts, rectangles for bars, lines for axes and ticks. Table grid
    # lines are excluded so ruled tables don't count as charts.
    shapes = [(o["x0"], o["top"], o["x1"], o["bottom"])
              for o in list(page.curves) + list(page.rects) + list(page.lines) if not _inside(o, tables)]
    if not scanned and len(shapes) >= min_chart_shapes:
        region = _union(shapes)
        already_covered = _inside({"x0": region[0], "x1": region[2], "top": region[1], "bottom": region[3]}, figures)
        if _area(region) > 0 and not already_covered:
            figures.append(region)

    return PageTriage(page=index, text=text, table_bboxes=tables, figure_bboxes=figures, scanned=scanned)


# =========================
# Textract
# =========================

def table_to_markdown(rows: List[List[str]]) -> str:
    rows = [[" ".join((c or "").split()).replace("|", "\\|") for c in row] for row in rows if any((c or "").strip() for c in row)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _child_ids(block: Dict) -> List[str]:
    return [i for rel in block.get("Relationships", []) if rel["Type"] == "CHILD" for i in rel["Ids"]]


def parse_textract(blocks: List[Dict]) -> Tuple[str, List[str]]:
    """Return (text outside tables, tables as Markdown) from AnalyzeDocument blocks."""
    by_id = {b["Id"]: b for b in blocks}
    tables, table_boxes = [], []

    for block in blocks:
        if block["BlockType"] != "TABLE":
            continue
        cells: Dict[Tuple[int, int], str] = {}
        for cell_id in _child_ids(block):
            cell = by_id.get(cell_id, {})
            if cell.get("BlockType") != "CELL":
                continue
            words = [by_id[w].get("Text", "") for w in _child_ids(cell) if by_id.get(w, {}).get("BlockType") == "WORD"]
            cells[(cell["RowIndex"], cell["ColumnIndex"])] = " ".join(words)
        if cells:
            n_rows = max(r for r, _ in cells)
            n_cols = max(c for _, c in cells)
            grid = [[cells.get((r, c), "") for c in range(1, n_cols + 1)] for r in range(1, n_rows + 1)]
            tables.append(table_to_markdown(grid))
        box = block.get("Geometry", {}).get("BoundingBox")
        if box:
            table_boxes.append((box["Left"], box["Top"], box["Left"] + box["Width"], box["Top"] + box["Height"]))

    lines = []
    for block in blocks:
        if block["BlockType"] != "LINE":
            continue
        box = block.get("Geometry", {}).get("BoundingBox")
        if box and _inside({"x0": box["Left"], "x1": box["Left"] + box["Width"],
                            "top": box["Top"], "bottom": box["Top"] + box["Height"]}, table_boxes):
            continue
        lines.append(block.get("Text", ""))

    return "\n".join(lines), [t for t in tables if t]


def split_table(markdown: str, max_chars: int) -> List[str]:
    """Split a Markdown table by rows, repeating the header row in every part."""
    if len(markdown) <= max_chars:
        return [markdown]
    lines = markdown.split("\n")
    header, rows = lines[:2], lines[2:]
    parts, current = [], []
    for row in rows:
        if current and len("\n".join(header + current + [row])) > max_chars:
            parts.append("\n".join(header + current))
            current = []
        current.append(row)
    if current:
        parts.append("\n".join(header + current))
    return parts


# =========================
# Orchestration
# =========================

def render_png(pdf, index: int, bbox: Optional[BBox] = None, scale: float = 2.0) -> bytes:
    """Render a page (or a region of it) to PNG. pdfplumber points map to pixels by `scale`."""
    image = pdf[index].render(scale=scale).to_pil()
    if bbox:
        pad = 6
        x0, top, x1, bottom = bbox
        image = image.crop((max(0, (x0 - pad) * scale), max(0, (top - pad) * scale),
                            min(image.width, (x1 + pad) * scale), min(image.height, (bottom + pad) * scale)))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def describe_figure(bedrock_runtime, model_id: str, png: bytes, file_name: str, page: int) -> Optional[str]:
    """Describe a figure with a vision model via the Converse API. None if decorative."""
    response = bedrock_runtime.converse(
        modelId=model_id,
        messages=[{
            "role": "user",
            "content": [
                {"text": FIGURE_PROMPT.format(page=page + 1, file_name=file_name)},
                {"image": {"format": "png", "source": {"bytes": png}}},
            ],
        }],
        inferenceConfig={"maxTokens": 2000},
        additionalModelRequestFields={"reasoning": {"effort": "low"}},
    )
    texts = [c["text"] for c in response["output"]["message"]["content"] if "text" in c]
    description = "\n".join(texts).strip()
    if not description or description.upper().startswith("DECORATIVE"):
        return None
    return description


def analyze_pdf(file_path: str, file_name: str, *, textract, bedrock_runtime, vision_model_id: str,
                mode: str = "triage", max_figures: int = 30, max_workers: int = 4,
                min_figure_area_ratio: float = 0.04) -> List[Element]:
    """
    Turn a PDF into ordered text / table / figure elements.

    mode: "triage" (default) sends only flagged pages to Textract / the vision model,
          "full" sends every page to Textract, "off" extracts text only.
    """
    page_text: Dict[int, str] = {}
    scanned_pages = set()
    local_tables: Dict[int, List[str]] = {}
    jobs: List[Tuple[str, int, Callable[[], object]]] = []
    figures_queued = 0

    with pdfplumber.open(file_path) as plumber:
        pdf = pypdfium2.PdfDocument(file_path)
        try:
            for index, page in enumerate(plumber.pages):
                if mode == "off":
                    page_text[index] = page.extract_text() or ""
                    continue

                triage = triage_page(page, index, min_figure_area_ratio)
                page_text[index] = triage.text
                if triage.scanned:
                    scanned_pages.add(index)
                # Free fallback in case Textract fails for this page
                local_tables[index] = [table_to_markdown(t) for t in page.extract_tables()] if triage.table_bboxes else []

                if mode == "full" or triage.scanned or triage.table_bboxes:
                    png = render_png(pdf, index)
                    jobs.append(("textract", index, lambda png=png: textract.analyze_document(
                        Document={"Bytes": png}, FeatureTypes=["TABLES"])))

                for bbox in triage.figure_bboxes:
                    if figures_queued >= max_figures:
                        logger.warning(f"{file_name}: figure limit {max_figures} reached; skipping the rest")
                        break
                    figures_queued += 1
                    png = render_png(pdf, index, bbox)
                    jobs.append(("figure", index, lambda png=png, index=index: describe_figure(
                        bedrock_runtime, vision_model_id, png, file_name, index)))
        finally:
            pdf.close()

    # Remote calls run concurrently; rendering above stays sequential (pdfium isn't thread-safe)
    results: Dict[int, Dict[str, list]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [(kind, index, pool.submit(call)) for kind, index, call in jobs]
        for kind, index, future in futures:
            slot = results.setdefault(index, {"textract": [], "figure": []})
            try:
                slot[kind].append(future.result())
            except Exception as e:
                logger.warning(f"{file_name} page {index + 1}: {kind} failed, using local extraction: {e}")

    elements: List[Element] = []
    for index in sorted(page_text):
        slot = results.get(index, {"textract": [], "figure": []})
        text, tables = page_text[index], local_tables.get(index, [])

        if slot["textract"]:
            ocr_text, textract_tables = parse_textract(slot["textract"][0].get("Blocks", []))
            if textract_tables:
                tables = textract_tables
            # Scanned pages have no text layer, so Textract's lines are the text
            if index in scanned_pages:
                text = ocr_text

        if text.strip():
            elements.append(Element("text", index, text))
        elements.extend(Element("table", index, t) for t in tables if t)
        elements.extend(Element("figure", index, d) for d in slot["figure"] if d)

    return elements
