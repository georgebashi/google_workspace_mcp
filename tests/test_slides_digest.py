from gslides.slides_digest import format_page, slide_title


def box(oid, text, size=None, placeholder=None):
    style = {"fontSize": {"magnitude": size, "unit": "PT"}} if size else {}
    shape = {
        "shapeType": "TEXT_BOX",
        "text": {
            "textElements": [
                {"paragraphMarker": {}},
                {"textRun": {"content": text + "\n", "style": style}},
            ]
        },
    }
    if placeholder:
        shape["placeholder"] = {"type": placeholder}
    return {"objectId": oid, "shape": shape}


def test_title_prefers_placeholder_then_largest_font():
    label, headline = box("a", "ANNOUNCEMENT", 8), box("b", "Commerce Review Bot", 17)
    assert slide_title({"pageElements": [label, headline]}) == "Commerce Review Bot"
    title = box("c", "Real title", 12, placeholder="TITLE")
    assert slide_title({"pageElements": [label, headline, title]}) == "Real title"


def test_page_lists_image_source_but_not_content_url():
    image = {
        "objectId": "i1",
        "image": {
            "sourceUrl": "https://example.com/a.png",
            "contentUrl": "https://lh7/long",
        },
    }
    out = format_page({"objectId": "p1", "pageElements": [image]}, revision="r1")
    assert "- image i1  src=https://example.com/a.png" in out
    assert "lh7" not in out
