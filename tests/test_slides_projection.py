from gslides.slides_projection import project_box, project_page

EMU = 12700


def size(w_pt, h_pt):
    return {
        "width": {"magnitude": w_pt * EMU, "unit": "EMU"},
        "height": {"magnitude": h_pt * EMU, "unit": "EMU"},
    }


def test_box_unrotated_scaled():
    m = (2.0, 0.0, 0.0, 0.5, 10 * EMU, 20 * EMU)
    assert project_box(m, size(100, 40)) == {"x": 10, "y": 20, "w": 200, "h": 20}


def test_box_rotated_90_keeps_centre():
    # 90 degrees clockwise about the box centre (50, 20) for a 100 x 40 box at the origin.
    m = (0.0, 1.0, -1.0, 0.0, 70 * EMU, -30 * EMU)
    assert project_box(m, size(100, 40)) == {
        "x": 0,
        "y": 0,
        "w": 100,
        "h": 40,
        "rot": 90,
    }


def test_box_flip_and_skew():
    assert project_box((1.0, 0.0, 0.0, -1.0, 0, 40 * EMU), size(100, 40))["flip"] == "v"
    assert project_box((1.0, 0.0, 0.5, 1.0, 0, 0), size(100, 40))["skewed"] is True


def run(text, **style):
    return {"textRun": {"content": text, "style": style}}


def para(**bullet):
    marker = {"style": {}}
    if bullet:
        marker["bullet"] = bullet
    return {"paragraphMarker": marker}


def shape_page(elements, **shape_extra):
    return {
        "objectId": "p1",
        "pageElements": [
            {
                "objectId": "e1",
                "size": size(100, 40),
                "transform": {"scaleX": 1, "scaleY": 1, "unit": "EMU"},
                "shape": {
                    "shapeType": "TEXT_BOX",
                    "text": {"textElements": elements},
                    **shape_extra,
                },
            }
        ],
    }


def test_text_bare_strings_styles_and_merge():
    out = project_page(
        shape_page(
            [
                para(),
                run("Checkout", bold=True),
                run(" conversion"),
                run(" is up\n"),
                para(),
                run("Plain\n"),
            ]
        )
    )
    el = out["elements"][0]
    assert out["styles"] == {"s1": {"bold": True}}
    assert el["text"] == [
        {"runs": [{"text": "Checkout", "s": "s1"}, " conversion is up"]},
        "Plain",
    ]
    assert len(el["v"]) == 8


def test_bullets_and_glyphs():
    out = project_page(
        shape_page(
            [
                para(listId="kix.a", nestingLevel=0, glyph="●"),
                run("One\n"),
                para(listId="kix.a", nestingLevel=1, glyph="○"),
                run("Two\n"),
            ]
        )
    )
    el = out["elements"][0]
    assert el["text"] == [
        {"text": "One", "bullet": {"list": "L1", "level": 0}},
        {"text": "Two", "bullet": {"list": "L1", "level": 1}},
    ]
    assert el["lists"] == {"L1": {"glyphs": {"0": "●", "1": "○"}}}


def test_v_ignores_alias_names():
    a = project_page(shape_page([para(), run("x", italic=True), run("y\n", bold=True)]))
    b = project_page(shape_page([para(), run("x", italic=True), run("y\n", bold=True)]))
    assert a["elements"][0]["v"] == b["elements"][0]["v"]


def test_table_merge_and_scoped_group_child():
    def cell(r, c, t, **span):
        return {
            "location": {"rowIndex": r, "columnIndex": c},
            "text": {"textElements": [para(), run(t + "\n")]},
            **span,
        }

    page = {
        "objectId": "p1",
        "pageElements": [
            {
                "objectId": "g1",
                "transform": {
                    "scaleX": 1,
                    "scaleY": 1,
                    "translateX": 10 * EMU,
                    "unit": "EMU",
                },
                "elementGroup": {
                    "children": [
                        {
                            "objectId": "t1",
                            "transform": {
                                "scaleX": 1,
                                "scaleY": 1,
                                "translateY": 5 * EMU,
                                "unit": "EMU",
                            },
                            "table": {
                                "rows": 1,
                                "columns": 2,
                                "tableColumns": [
                                    {
                                        "columnWidth": {
                                            "magnitude": 50 * EMU,
                                            "unit": "EMU",
                                        }
                                    }
                                ]
                                * 2,
                                "tableRows": [
                                    {
                                        "rowHeight": {
                                            "magnitude": 20 * EMU,
                                            "unit": "EMU",
                                        },
                                        "tableCells": [
                                            cell(0, 0, "A", columnSpan=2),
                                            cell(0, 1, ""),
                                        ],
                                    }
                                ],
                            },
                        }
                    ]
                },
            }
        ],
    }
    out = project_page(page, targets=["t1"])
    t = out["elements"][0]
    assert t["group"] == "g1"
    assert t["box"] == {"x": 10, "y": 5}
    assert t["rows"] == [{"r": 0, "cells": [{"text": ["A"], "span": [1, 2]}, None]}]
