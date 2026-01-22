# ============ services/converter_service.py (FULLY FIXED – Unicode Safe) ============
from html.parser import HTMLParser
import io
from typing import Tuple

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
import markdown
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# =======================
# HTML PARSER (UNICODE SAFE)
# =======================
class StructuredHTMLParser(HTMLParser):
    """结构化 HTML 解析器（完整支持中文 / Unicode + 表格）"""

    def __init__(self):
        super().__init__()
        self.elements = []
        self.current_text = []
        self.tag_stack = []
        self.list_level = 0
        self._in_table = False
        self._current_table = []
        self._current_row = []

    def _flush_text(self):
        if self.current_text:
            text = "".join(self.current_text).strip()
            if text:
                self.elements.append(
                    {
                        "type": "text",
                        "content": text,
                        "tags": list(self.tag_stack),
                        "list_level": self.list_level,
                    }
                )
            self.current_text = []

    def handle_starttag(self, tag, attrs):
        self._flush_text()

        if tag == "table":
            self._in_table = True
            self._current_table = []
            return

        if tag == "tr" and self._in_table:
            self._current_row = []
            return

        if tag in ("td", "th") and self._in_table:
            self.current_text = []
            return

        self.tag_stack.append(tag)

        if tag in ("ul", "ol"):
            self.list_level += 1

        if tag == "br":
            self.elements.append({"type": "break"})

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_table:
            cell = "".join(self.current_text).strip()
            self._current_row.append(cell)
            self.current_text = []
            return

        if tag == "tr" and self._in_table:
            if self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = []
            return

        if tag == "table" and self._in_table:
            self.elements.append({"type": "table", "rows": self._current_table})
            self._current_table = []
            self._in_table = False
            return

        self._flush_text()

        if self.tag_stack and self.tag_stack[-1] == tag:
            self.tag_stack.pop()

        if tag in ("ul", "ol"):
            self.list_level = max(0, self.list_level - 1)

        if tag in (
            "p",
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "pre",
            "blockquote",
        ):
            self.elements.append(
                {"type": "paragraph_end", "tag": tag, "list_level": self.list_level}
            )

    def handle_data(self, data):
        self.current_text.append(data)

    def get_elements(self):
        self._flush_text()
        return self.elements


# =======================
# CONVERTER SERVICE
# =======================
class ConverterService:
    # ---------- ReportLab XML 安全转义 ----------
    @staticmethod
    def _escape_xml(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # ---------- Markdown / Text ----------
    @staticmethod
    def markdown_to_html(md_content: str) -> str:
        return markdown.markdown(
            md_content, extensions=["extra", "tables", "fenced_code"]
        )

    @staticmethod
    def text_to_html(text_content: str) -> str:
        return "".join(
            f"<p>{line}</p>" if line.strip() else ""
            for line in text_content.split("\n")
        )

    # ---------- HTML → WORD ----------
    @staticmethod
    def html_to_word(html_content: str) -> io.BytesIO:
        doc = Document()
        parser = StructuredHTMLParser()
        parser.feed(html_content)
        elements = parser.get_elements()

        # 默认中文字体
        style = doc.styles["Normal"]
        style.font.name = "Source Han Sans SC"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Source Han Sans SC")
        style.font.size = Pt(11)

        current_paragraph = None

        for el in elements:
            if el["type"] == "table":
                # 创建表格
                rows = el["rows"]
                if rows:
                    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
                    table.style = "Light Grid Accent 1"

                    for i, row in enumerate(rows):
                        for j, cell_text in enumerate(row):
                            cell = table.rows[i].cells[j]
                            cell.text = cell_text
                            # 设置中文字体
                            for paragraph in cell.paragraphs:
                                for run in paragraph.runs:
                                    run.font.name = "Source Han Sans SC"
                                    run._element.rPr.rFonts.set(
                                        qn("w:eastAsia"), "Source Han Sans SC"
                                    )
                current_paragraph = None

            elif el["type"] == "text":
                tags = el["tags"]
                text = el["content"]

                # 标题直接用 Word Heading
                if "h1" in tags:
                    doc.add_heading(text, level=1)
                    current_paragraph = None
                    continue
                if "h2" in tags:
                    doc.add_heading(text, level=2)
                    current_paragraph = None
                    continue
                if "h3" in tags:
                    doc.add_heading(text, level=3)
                    current_paragraph = None
                    continue

                if current_paragraph is None:
                    if "li" in tags:
                        current_paragraph = doc.add_paragraph(style="List Bullet")
                    else:
                        current_paragraph = doc.add_paragraph()

                run = current_paragraph.add_run(text)
                run.font.name = "Source Han Sans SC"
                run._element.rPr.rFonts.set(qn("w:eastAsia"), "Source Han Sans SC")

                if "strong" in tags or "b" in tags:
                    run.bold = True
                if "em" in tags or "i" in tags:
                    run.italic = True
                if "code" in tags:
                    run.font.name = "Courier New"
                    run.font.size = Pt(10)
                    run.font.color.rgb = RGBColor(220, 50, 47)

            elif el["type"] == "paragraph_end":
                current_paragraph = None

        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        return buffer

    @staticmethod
    def html_to_pdf(html_content: str) -> io.BytesIO:
        buffer = io.BytesIO()

        # 注册内置中文字体
        # 检查系统中是否有思源黑体，如果没有则使用默认字体
        try:
            from reportlab.pdfbase.ttfonts import TTFont

            # 注意：这里需要根据实际环境调整字体文件路径
            # pdfmetrics.registerFont(TTFont('SourceHanSansSC', '/path/to/SourceHanSansSC.ttf'))
            # 如果成功加载了思源黑体，则使用它；否则回退到原来的字体
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            font_name = "STSong-Light"
        except:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            font_name = "STSong-Light"

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()

        styles.add(
            ParagraphStyle(
                name="NormalCN",
                fontName=font_name,
                fontSize=11,
                leading=16,
            )
        )

        styles.add(
            ParagraphStyle(
                name="HeadingCN",
                parent=styles["NormalCN"],
                fontSize=18,
                leading=22,
                spaceBefore=12,
                spaceAfter=12,
            )
        )

        styles.add(
            ParagraphStyle(
                name="CodeCN",
                fontName=font_name,
                fontSize=9,
                backColor=colors.HexColor("#f5f5f5"),
                leftIndent=20,
                rightIndent=20,
                leading=14,
            )
        )

        styles.add(
            ParagraphStyle(
                name="TableCellCN",
                fontName=font_name,
                fontSize=10,
                leading=14,
            )
        )

        parser = StructuredHTMLParser()
        parser.feed(html_content)
        elements = parser.get_elements()

        story = []
        current = []
        current_tags = []

        for el in elements:
            if el["type"] == "table":
                # 渲染表格
                rows = el["rows"]
                if rows:
                    # 将文本包装在 Paragraph 中以支持 Unicode
                    table_data = []
                    for row in rows:
                        table_data.append(
                            [
                                Paragraph(
                                    ConverterService._escape_xml(cell),
                                    styles["TableCellCN"],
                                )
                                for cell in row
                            ]
                        )

                    table = Table(table_data)
                    table.setStyle(
                        TableStyle(
                            [
                                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                                ("FONTNAME", (0, 0), (-1, -1), font_name),
                                ("FONTSIZE", (0, 0), (-1, -1), 10),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                                ("TOPPADDING", (0, 0), (-1, -1), 8),
                                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                            ]
                        )
                    )
                    story.append(table)
                    story.append(Spacer(1, 12))

            elif el["type"] == "text":
                current.append(ConverterService._escape_xml(el["content"]))
                current_tags = el["tags"]

            elif el["type"] == "paragraph_end" and current:
                text = "".join(current)
                tag = el["tag"]

                if tag in ("h1", "h2", "h3"):
                    para = Paragraph(text, styles["HeadingCN"])
                elif tag == "pre" or "code" in current_tags:
                    para = Preformatted(text, styles["CodeCN"])
                else:
                    para = Paragraph(text, styles["NormalCN"])

                story.append(para)
                story.append(Spacer(1, 8))
                current = []
                current_tags = []

        doc.build(story)
        buffer.seek(0)
        return buffer

    # ---------- HTML → PDF（中文完全支持） ----------
    def convert(
        self, content: str, input_format: str, output_format: str
    ) -> tuple[io.BytesIO, str, str]:
        if input_format == "md":
            html = self.markdown_to_html(content)
        else:
            html = self.text_to_html(content)

        if output_format == "word":
            return (
                self.html_to_word(html),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "docx",
            )

        if output_format == "pdf":
            return (self.html_to_pdf(html), "application/pdf", "pdf")

        raise ValueError(f"Unsupported format: {output_format}")


converter_service = ConverterService()
