"""Test cases for layout-aware PDF analysis (pdfplumber / pypdfium2 replaced by fakes)."""
import unittest
import unittest.mock
from types import SimpleNamespace

from document_processor.pdf_analysis import (
    describe_figure, parse_textract, split_table, table_to_markdown, triage_page
)


def box(x0, top, x1, bottom):
    return {"x0": x0, "top": top, "x1": x1, "bottom": bottom}


class FakePage:
    """Minimal stand-in for a pdfplumber page (612 x 792 points)."""

    def __init__(self, text="", tables=(), images=(), curves=(), rects=(), lines=()):
        self.width, self.height = 612, 792
        self._text, self.images = text, list(images)
        self.curves, self.rects, self.lines = list(curves), list(rects), list(lines)
        self._tables = [SimpleNamespace(bbox=b) for b in tables]

    def find_tables(self):
        return self._tables

    def filter(self, fn):
        return self

    def extract_text(self):
        return self._text


class TestTriage(unittest.TestCase):

    def test_plain_text_page_needs_nothing_extra(self):
        t = triage_page(FakePage(text="Plain policy text. " * 20), 0)
        self.assertEqual((t.table_bboxes, t.figure_bboxes, t.scanned), ([], [], False))

    def test_table_detected(self):
        t = triage_page(FakePage(text="Rates", tables=[(50, 100, 500, 300)]), 1)
        self.assertEqual(t.table_bboxes, [(50, 100, 500, 300)])

    def test_large_image_is_a_figure_small_logo_is_not(self):
        t = triage_page(FakePage(text="Chart below " * 10,
                                 images=[box(50, 100, 400, 400), box(10, 10, 30, 30)]), 2)
        self.assertEqual(t.figure_bboxes, [(50, 100, 400, 400)])

    def test_scanned_page(self):
        t = triage_page(FakePage(text="", images=[box(0, 0, 612, 792)]), 3)
        self.assertTrue(t.scanned)
        self.assertEqual(t.figure_bboxes, [])

    def test_vector_bar_chart_detected_from_shapes(self):
        bars = [box(100 + i * 30, 300, 120 + i * 30, 500) for i in range(11)]
        axes = [box(90, 300 + i * 20, 95, 300 + i * 20) for i in range(6)]
        t = triage_page(FakePage(text="Monthly tickets", rects=bars, lines=axes), 4)
        self.assertEqual(len(t.figure_bboxes), 1)
        self.assertEqual(t.figure_bboxes[0], (90, 300, 420, 500))

    def test_table_grid_lines_do_not_count_as_a_chart(self):
        grid = [box(60, 110 + i * 10, 490, 111 + i * 10) for i in range(20)]
        t = triage_page(FakePage(text="Rates", tables=[(50, 100, 500, 330)], lines=grid), 5)
        self.assertEqual(t.figure_bboxes, [])


def textract_table(rows, top=0.1):
    blocks, cell_ids, n = [], [], 0
    for r, row in enumerate(rows, start=1):
        for c, text in enumerate(row, start=1):
            n += 1
            blocks.append({"Id": f"w{n}", "BlockType": "WORD", "Text": text})
            blocks.append({"Id": f"c{n}", "BlockType": "CELL", "RowIndex": r, "ColumnIndex": c,
                           "Relationships": [{"Type": "CHILD", "Ids": [f"w{n}"]}]})
            cell_ids.append(f"c{n}")
    blocks.append({"Id": "t", "BlockType": "TABLE", "Relationships": [{"Type": "CHILD", "Ids": cell_ids}],
                   "Geometry": {"BoundingBox": {"Left": 0.1, "Top": top, "Width": 0.8, "Height": 0.3}}})
    return blocks


class TestTextract(unittest.TestCase):

    def test_tables_become_markdown_and_table_lines_are_not_duplicated(self):
        blocks = textract_table([["Region", "Rate"], ["EMEA", "90"]]) + [
            {"Id": "l1", "BlockType": "LINE", "Text": "Region Rate",
             "Geometry": {"BoundingBox": {"Left": 0.2, "Top": 0.15, "Width": 0.3, "Height": 0.02}}},
            {"Id": "l2", "BlockType": "LINE", "Text": "Rates are per day.",
             "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.7, "Width": 0.5, "Height": 0.02}}},
        ]
        text, tables = parse_textract(blocks)
        self.assertEqual(text, "Rates are per day.")
        self.assertEqual(tables, ["| Region | Rate |\n|---|---|\n| EMEA | 90 |"])

    def test_markdown_escapes_pipes_and_pads_rows(self):
        self.assertEqual(table_to_markdown([["a|b", "c"], ["d"]]), "| a\\|b | c |\n|---|---|\n| d |  |")
        self.assertEqual(table_to_markdown([["", ""]]), "")


class TestSplitTable(unittest.TestCase):

    def test_small_table_unchanged(self):
        self.assertEqual(split_table("| a |\n|---|\n| 1 |", 100), ["| a |\n|---|\n| 1 |"])

    def test_large_table_split_with_header_repeated(self):
        table = "| name | value |\n|---|---|\n" + "\n".join(f"| row{i} | {i} |" for i in range(50))
        parts = split_table(table, 200)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertTrue(part.startswith("| name | value |\n|---|---|\n"))
            self.assertLessEqual(len(part), 200)
        self.assertEqual(sum(p.count("| row") for p in parts), 50)


class TestDescribeFigure(unittest.TestCase):

    def converse_returning(self, *texts):
        client = unittest.mock.MagicMock()
        client.converse.return_value = {"output": {"message": {"content": [{"reasoningContent": {}}] + [{"text": t} for t in texts]}}}
        return client

    def test_description_returned(self):
        client = self.converse_returning("Line chart of revenue, rising from 10 to 40.")
        self.assertEqual(describe_figure(client, "us.openai.gpt-6-luna", b"png", "r.pdf", 0),
                         "Line chart of revenue, rising from 10 to 40.")
        request = client.converse.call_args.kwargs
        self.assertEqual(request["modelId"], "us.openai.gpt-6-luna")
        self.assertEqual(request["messages"][0]["content"][1]["image"]["format"], "png")

    def test_decorative_images_skipped(self):
        self.assertIsNone(describe_figure(self.converse_returning("DECORATIVE"), "m", b"png", "r.pdf", 0))


if __name__ == "__main__":
    unittest.main()
