"""Build a polished PDF version of the configuration and operations report."""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


BLUE = colors.HexColor("#2E74B5")
DARK_BLUE = colors.HexColor("#1F4D78")
NAVY = colors.HexColor("#17365D")
MUTED = colors.HexColor("#666666")
LIGHT_GRAY = colors.HexColor("#F2F4F7")
PALE_BLUE = colors.HexColor("#E8EEF5")
GRID = colors.HexColor("#C9D2DC")


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("MSYH", r"C:\Windows\Fonts\msyh.ttc"))
    pdfmetrics.registerFont(TTFont("MSYH-Bold", r"C:\Windows\Fonts\msyhbd.ttc"))
    addMapping("MSYH", 0, 0, "MSYH")
    addMapping("MSYH", 1, 0, "MSYH-Bold")


def inline_markup(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r'<font name="Courier" color="#8B1A1A">\1</font>', escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    return escaped


class ReportDocTemplate(BaseDocTemplate):
    def __init__(self, filename: str, **kwargs):
        super().__init__(filename, **kwargs)
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="normal",
        )
        self.addPageTemplates(PageTemplate(id="report", frames=frame, onPage=self.draw_page))

    def draw_page(self, canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("MSYH", 8)
        canvas.setFillColor(MUTED)
        if doc.page > 1:
            canvas.drawString(
                self.leftMargin,
                letter[1] - 0.55 * inch,
                "TABsucks | 软件配置与运维文档",
            )
        canvas.drawRightString(
            letter[0] - self.rightMargin,
            0.48 * inch,
            f"内部项目文档  |  第 {doc.page} 页",
        )
        canvas.restoreState()

    def afterFlowable(self, flowable) -> None:
        if isinstance(flowable, Paragraph):
            style = flowable.style.name
            if style in ("H1", "H2", "H3"):
                # The Markdown source reserves H1 for the document title, so
                # H2 is the report's top-level section in the rendered PDF.
                level = {"H1": 0, "H2": 0, "H3": 1}[style]
                text = flowable.getPlainText()
                key = f"h{level}-{self.seq.nextf('heading')}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level=level, closed=False)
                self.notify("TOCEntry", (level, text, self.page, key))


def make_styles():
    base = getSampleStyleSheet()
    styles = {
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="MSYH",
            fontSize=9.6,
            leading=14.2,
            textColor=colors.black,
            spaceAfter=6,
            wordWrap="CJK",
        ),
        "h1": ParagraphStyle(
            "H1",
            parent=base["Heading1"],
            fontName="MSYH-Bold",
            fontSize=15,
            leading=20,
            textColor=BLUE,
            spaceBefore=15,
            spaceAfter=7,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName="MSYH-Bold",
            fontSize=12,
            leading=17,
            textColor=BLUE,
            spaceBefore=11,
            spaceAfter=5,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "h3": ParagraphStyle(
            "H3",
            parent=base["Heading3"],
            fontName="MSYH-Bold",
            fontSize=10.5,
            leading=15,
            textColor=DARK_BLUE,
            spaceBefore=8,
            spaceAfter=4,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "quote": ParagraphStyle(
            "Quote",
            parent=base["BodyText"],
            fontName="MSYH",
            fontSize=9.2,
            leading=13.5,
            textColor=MUTED,
            leftIndent=18,
            rightIndent=10,
            borderColor=BLUE,
            borderWidth=1.5,
            borderPadding=(5, 8, 5, 8),
            borderRadius=0,
            spaceAfter=8,
            wordWrap="CJK",
        ),
        "code": ParagraphStyle(
            "Code",
            parent=base["Code"],
            fontName="MSYH",
            fontSize=7.8,
            leading=11,
            leftIndent=9,
            rightIndent=9,
            backColor=colors.HexColor("#F6F8FA"),
            borderColor=colors.HexColor("#D8DEE4"),
            borderWidth=0.5,
            borderPadding=7,
            spaceBefore=4,
            spaceAfter=8,
            wordWrap="CJK",
        ),
        "table": ParagraphStyle(
            "TableText",
            parent=base["BodyText"],
            fontName="MSYH",
            fontSize=7.8,
            leading=10.8,
            wordWrap="CJK",
        ),
        "table_head": ParagraphStyle(
            "TableHead",
            parent=base["BodyText"],
            fontName="MSYH-Bold",
            fontSize=8,
            leading=11,
            alignment=TA_CENTER,
            textColor=NAVY,
            wordWrap="CJK",
        ),
        "cover_title": ParagraphStyle(
            "CoverTitle",
            fontName="MSYH-Bold",
            fontSize=25,
            leading=32,
            alignment=TA_CENTER,
            textColor=NAVY,
            spaceAfter=8,
        ),
        "cover_brand": ParagraphStyle(
            "CoverBrand",
            fontName="MSYH-Bold",
            fontSize=15,
            leading=20,
            alignment=TA_CENTER,
            textColor=BLUE,
            spaceAfter=10,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            fontName="MSYH",
            fontSize=10.5,
            leading=15,
            alignment=TA_CENTER,
            textColor=MUTED,
            spaceAfter=45,
        ),
        "toc_title": ParagraphStyle(
            "TOCTitle",
            fontName="MSYH-Bold",
            fontSize=16,
            leading=22,
            textColor=BLUE,
            spaceAfter=12,
        ),
    }
    return styles


def parse_table(lines: list[str]) -> list[list[str]]:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    return [rows[0], *rows[2:]]


def table_widths(rows: list[list[str]], total_width: float) -> list[float]:
    count = len(rows[0])
    weights = []
    for index in range(count):
        longest = max(len(row[index]) if index < len(row) else 0 for row in rows)
        weights.append(max(8, min(longest, 34)))
    total = sum(weights)
    widths = [total_width * weight / total for weight in weights]
    minimum = 0.62 * inch
    widths = [max(minimum, width) for width in widths]
    scale = total_width / sum(widths)
    return [width * scale for width in widths]


def build_table(rows: list[list[str]], styles, width: float) -> Table:
    data = []
    for row_index, row in enumerate(rows):
        style = styles["table_head"] if row_index == 0 else styles["table"]
        data.append([Paragraph(inline_markup(cell), style) for cell in row])
    table = Table(
        data,
        colWidths=table_widths(rows, width),
        repeatRows=1,
        hAlign="LEFT",
        splitByRow=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT_GRAY),
                ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
                ("GRID", (0, 0), (-1, -1), 0.45, GRID),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def add_cover(story, styles, width: float) -> None:
    story.extend(
        [
            Spacer(1, 1.25 * inch),
            Paragraph("TABsucks", styles["cover_brand"]),
            Paragraph("软件配置与运维文档", styles["cover_title"]),
            Paragraph(
                "Configuration Management and Operations Plan",
                styles["cover_subtitle"],
            ),
        ]
    )
    rows = [
        ("文档编号", "TABSUCKS-CMO-001"),
        ("文档版本", "V1.0"),
        ("文档状态", "课程提交候选版"),
        ("项目团队", "TABsucks 项目组"),
        ("组长学号/姓名", "待填写"),
        ("编制日期", "2026-07-18"),
    ]
    data = []
    for label, value in rows:
        data.append(
            [
                Paragraph(f"<b>{label}</b>", styles["table_head"]),
                Paragraph(value, styles["table"]),
            ]
        )
    table = Table(data, colWidths=[1.55 * inch, width - 1.55 * inch], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), PALE_BLUE),
                ("GRID", (0, 0), (-1, -1), 0.5, GRID),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend(
        [
            table,
            Spacer(1, 0.28 * inch),
            Paragraph(
                "适用范围：TABsucks Windows 桌面发行与本地 Web 运行环境",
                ParagraphStyle(
                    "CoverScope",
                    fontName="MSYH",
                    fontSize=8.5,
                    textColor=MUTED,
                    alignment=TA_CENTER,
                ),
            ),
            PageBreak(),
        ]
    )


def add_toc(story, styles) -> None:
    story.append(Paragraph("目录", styles["toc_title"]))
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            "TOC1",
            fontName="MSYH",
            fontSize=8.8,
            leading=13,
            leftIndent=0,
            firstLineIndent=0,
            textColor=NAVY,
        ),
        ParagraphStyle(
            "TOC2",
            fontName="MSYH",
            fontSize=8,
            leading=11,
            leftIndent=14,
            firstLineIndent=0,
        ),
        ParagraphStyle(
            "TOC3",
            fontName="MSYH",
            fontSize=7.5,
            leading=10,
            leftIndent=28,
            firstLineIndent=0,
            textColor=MUTED,
        ),
    ]
    story.extend([toc, PageBreak()])


def markdown_story(markdown: str, styles, width: float):
    story = []
    lines = markdown.splitlines()
    index = 0
    first_title_skipped = False
    body_started = False
    pending_bullets: list[str] = []
    pending_numbers: list[str] = []

    def flush_lists():
        nonlocal pending_bullets, pending_numbers
        if pending_bullets:
            items = [
                ListItem(Paragraph(inline_markup(item), styles["body"]), leftIndent=12)
                for item in pending_bullets
            ]
            story.append(
                ListFlowable(
                    items,
                    bulletType="bullet",
                    start="circle",
                    leftIndent=20,
                    bulletFontName="MSYH",
                    bulletFontSize=7,
                    spaceAfter=5,
                )
            )
            pending_bullets = []
        if pending_numbers:
            items = [
                ListItem(Paragraph(inline_markup(item), styles["body"]), leftIndent=12)
                for item in pending_numbers
            ]
            story.append(
                ListFlowable(
                    items,
                    bulletType="1",
                    leftIndent=24,
                    bulletFontName="MSYH",
                    bulletFontSize=8.5,
                    spaceAfter=5,
                )
            )
            pending_numbers = []

    while index < len(lines):
        line = lines[index].rstrip()
        if not line.strip() or line.strip() == "---":
            flush_lists()
            index += 1
            continue
        if line.startswith("```"):
            flush_lists()
            index += 1
            block = []
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(lines[index])
                index += 1
            story.append(Preformatted("\n".join(block), styles["code"]))
            index += 1
            continue
        if line.startswith("|") and index + 1 < len(lines) and re.match(
            r"^\s*\|?\s*:?-+", lines[index + 1]
        ):
            flush_lists()
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            story.append(build_table(parse_table(table_lines), styles, width))
            story.append(Spacer(1, 6))
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_lists()
            level = len(heading.group(1))
            text = heading.group(2)
            if level == 1 and not first_title_skipped:
                first_title_skipped = True
                index += 1
                continue
            if not body_started:
                if level == 2 and text == "文档控制":
                    body_started = True
                else:
                    index += 1
                    continue
            story.append(Paragraph(inline_markup(text), styles[f"h{level}"]))
            index += 1
            continue
        if not body_started:
            index += 1
            continue
        if line.startswith("> "):
            flush_lists()
            story.append(Paragraph(inline_markup(line[2:]), styles["quote"]))
            index += 1
            continue
        bullet = re.match(r"^-\s+(.+)$", line)
        if bullet:
            if pending_numbers:
                flush_lists()
            pending_bullets.append(bullet.group(1))
            index += 1
            continue
        numbered = re.match(r"^\d+\.\s+(.+)$", line)
        if numbered:
            if pending_bullets:
                flush_lists()
            pending_numbers.append(numbered.group(1))
            index += 1
            continue

        flush_lists()
        parts = [line]
        index += 1
        while index < len(lines):
            candidate = lines[index].rstrip()
            if (
                not candidate.strip()
                or candidate.startswith(("#", "```", "|", "> ", "- "))
                or re.match(r"^\d+\.\s+", candidate)
            ):
                break
            parts.append(candidate)
            index += 1
        story.append(Paragraph(inline_markup(" ".join(x.strip() for x in parts)), styles["body"]))

    flush_lists()
    return story


def build(source: Path, output: Path) -> None:
    register_fonts()
    styles = make_styles()
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = ReportDocTemplate(
        str(output),
        pagesize=letter,
        rightMargin=inch,
        leftMargin=inch,
        topMargin=inch,
        bottomMargin=inch,
        title="TABsucks 软件配置与运维文档",
        author="TABsucks 项目组",
        subject="软件配置管理、版本控制、持续集成、部署与运维",
    )
    story = []
    add_cover(story, styles, doc.width)
    add_toc(story, styles)
    story.extend(markdown_story(source.read_text(encoding="utf-8"), styles, doc.width))
    doc.multiBuild(story)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: build_configuration_operations_pdf.py SOURCE.md OUTPUT.pdf")
    build(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
