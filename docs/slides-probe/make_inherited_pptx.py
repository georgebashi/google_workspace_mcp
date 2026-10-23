"""Build a PPTX whose layout BODY placeholder sets normAutofit (shrink on
overflow), and whose slides' BODY placeholders set no autofit of their own, so
that a Drive import should yield slide shapes that *inherit* TEXT_AUTOFIT.

Slide 1: overflowing body, which PowerPoint would shrink; the slide carries a
fontScale so the shrink is real rather than only enabled.
Slide 2: the same body with no slide-level bodyPr child at all: pure
inheritance, no stored scale.
Slide 3: a short body that fits, for edits that should not need any shrink.
"""
import os

from lxml import etree
from pptx import Presentation

NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
HERE = os.path.dirname(os.path.abspath(__file__))

prs = Presentation()
layout = prs.slide_layouts[1]  # Title and Content
for ph in layout.placeholders:
    if ph.placeholder_format.idx == 1:
        body_pr = ph._element.txBody.find(f"{{{NS}}}bodyPr")
        for child in list(body_pr):
            body_pr.remove(child)
        body_pr.append(etree.fromstring(f'<a:normAutofit xmlns:a="{NS}"/>'))


def body_slide(title, n_lines, slide_autofit_xml=None):
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = title
    body = slide.placeholders[1]
    tf = body.text_frame
    tf.text = "Line 0 with enough words to wrap across the placeholder box"
    for i in range(1, n_lines):
        p = tf.add_paragraph()
        p.text = f"Line {i} with enough words to wrap across the placeholder box"
    body_pr = tf._txBody.find(f"{{{NS}}}bodyPr")
    for child in list(body_pr):
        body_pr.remove(child)
    if slide_autofit_xml:
        body_pr.append(etree.fromstring(slide_autofit_xml))


body_slide("inherited, scaled", 14,
           f'<a:normAutofit xmlns:a="{NS}" fontScale="62500" lnSpcReduction="20000"/>')
body_slide("inherited, no slide autofit", 14)
body_slide("inherited, fits", 2)
prs.save(os.path.join(HERE, "inherited_autofit.pptx"))
print("ok")
