import markdown
from xhtml2pdf import pisa

SRC = r"D:\newapp_ing\AI_Leave_System_Design.md"
OUT = r"D:\newapp_ing\AI_Leave_System_Design.pdf"

with open(SRC, "r", encoding="utf-8") as f:
    md_text = f.read()

html_body = markdown.markdown(
    md_text,
    extensions=["tables", "fenced_code", "toc"],
)

CSS = """
@page { size: A4; margin: 1.8cm 1.6cm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt; color: #222; line-height: 1.45; }
h1 { font-size: 20pt; color: #1F3A5F; border-bottom: 2px solid #1F3A5F; padding-bottom: 4px; }
h2 { font-size: 14pt; color: #1F3A5F; margin-top: 18px; border-bottom: 1px solid #ccd6e0; padding-bottom: 3px; }
h3 { font-size: 12pt; color: #2c4a6e; margin-top: 12px; }
p { margin: 6px 0; }
a { color: #1F3A5F; text-decoration: none; }
code { font-family: Courier, monospace; font-size: 9pt; background: #f2f4f7; padding: 1px 3px; }
pre { background: #f2f4f7; border: 1px solid #d9dee3; padding: 8px; font-family: Courier, monospace;
      font-size: 8pt; line-height: 1.3; -pdf-keep-with-next: false; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9pt; }
th { background: #1F3A5F; color: #ffffff; border: 1px solid #1F3A5F; padding: 5px 6px; text-align: left; }
td { border: 1px solid #c7ced6; padding: 5px 6px; vertical-align: top; }
tr:nth-child(even) td { background: #f5f7fa; }
hr { border: none; border-top: 1px solid #d9dee3; margin: 14px 0; }
ul, ol { margin: 6px 0 6px 18px; }
li { margin: 3px 0; }
blockquote { border-left: 3px solid #1F3A5F; margin: 8px 0; padding: 4px 10px; background: #f5f7fa; color: #444; }
"""

html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{CSS}</style></head><body>{html_body}</body></html>"""

with open(OUT, "w+b") as out_file:
    result = pisa.CreatePDF(html, dest=out_file, encoding="utf-8")

if result.err:
    raise SystemExit(f"PDF generation failed with {result.err} error(s)")
print("PDF written:", OUT)
