"""Build a PPTX whose text boxes carry normAutofit (shrink on overflow) with a
fontScale < 1, plus spAutoFit (resize shape), so a Drive import yields
TEXT_AUTOFIT and SHAPE_AUTOFIT shapes."""
import os

from pptx import Presentation
from pptx.util import Pt, Emu
from lxml import etree

prs = Presentation()
slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

def box(name, x, autofit_xml, n_lines):
    tb = slide.shapes.add_textbox(Pt(x), Pt(40), Pt(300), Pt(120))
    tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.text = "Line 0 with enough words to wrap a little"
    for i in range(1, n_lines):
        p = tf.add_paragraph(); p.text = f"Line {i} with enough words to wrap a little"
    for p in tf.paragraphs:
        for r in p.runs:
            r.font.size = Pt(20)
    body = tf._txBody.find(f"{{{NS}}}bodyPr")
    for child in list(body):
        body.remove(child)
    body.append(etree.fromstring(autofit_xml))

box("shrink", 20, f'<a:normAutofit xmlns:a="{NS}" fontScale="62500" lnSpcReduction="20000"/>', 12)
box("grow", 360, f'<a:spAutoFit xmlns:a="{NS}"/>', 3)
prs.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "autofit.pptx"))
print("ok")
