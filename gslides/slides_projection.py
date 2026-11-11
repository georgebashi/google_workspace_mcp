"""
Read side of the Slides edit projection (docs/slides-edit-workflow.md).

Turns a Slides API ``Page`` resource into the index-free, matrix-free JSON an
agent edits: points instead of EMU, a resolved ``box`` instead of a transform,
paragraphs and runs instead of indexed text elements, and a style table so a
style shared by many runs is written once.

Prototype status: covers the projection and ``v`` for measurement. Not yet
covered: inherited autofit (resolved through ``placeholder.parentObjectId``)
and the displayed-size projection of inherited run sizes in shrink shapes,
which need the layout and master pages.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

EMU_PER_PT = 12700
_BULLET_INDENT_PT = 36.0


def _pt(value: Optional[float]) -> Optional[float]:
    """Round a point value to 0.01 pt; integers print without a fraction."""
    if value is None:
        return None
    rounded = round(value, 2)
    return int(rounded) if rounded == int(rounded) else rounded


def _dim_pt(dim: Optional[Dict[str, Any]]) -> Optional[float]:
    if not dim or "magnitude" not in dim:
        return None
    mag = dim["magnitude"]
    if dim.get("unit", "EMU") == "EMU":
        mag = mag / EMU_PER_PT
    return _pt(mag)


# --- Geometry --------------------------------------------------------------


def _matrix(transform: Optional[Dict[str, Any]]) -> Tuple[float, ...]:
    """(a, b, c, d, tx, ty) in EMU, where x' = a*x + c*y + tx, y' = b*x + d*y + ty."""
    t = transform or {}
    scale = EMU_PER_PT if t.get("unit") == "PT" else 1
    return (
        t.get("scaleX", 1.0),
        t.get("shearY", 0.0),
        t.get("shearX", 0.0),
        t.get("scaleY", 1.0),
        t.get("translateX", 0.0) * scale,
        t.get("translateY", 0.0) * scale,
    )


def _compose(parent: Tuple[float, ...], child: Tuple[float, ...]) -> Tuple[float, ...]:
    """Parent applied after child."""
    a1, b1, c1, d1, e1, f1 = parent
    a2, b2, c2, d2, e2, f2 = child
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def project_box(
    matrix: Tuple[float, ...], size: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """The element's unrotated bounding box on the page, in points."""
    a, b, c, d, tx, ty = matrix
    width = (size or {}).get("width", {}).get("magnitude", 0) or 0
    height = (size or {}).get("height", {}).get("magnitude", 0) or 0

    sx = math.hypot(a, b)
    det = a * d - b * c
    box: Dict[str, Any] = {}
    if sx == 0:
        return {"x": _pt(tx / EMU_PER_PT), "y": _pt(ty / EMU_PER_PT), "w": 0, "h": 0}
    sy = det / sx
    # Shear left after removing rotation: zero for scale + rotation + flip.
    skew = (a * c + b * d) / (sx * sx)
    rot = math.degrees(math.atan2(b, a))
    flip = ""
    if sy < 0:
        # A mirror plus a rotation; report it as a vertical flip plus the
        # remaining rotation, which is one canonical form of the same matrix.
        flip = "v"
        sy = -sy
    w = width * sx
    h = height * sy
    cx = a * width / 2 + c * height / 2 + tx
    cy = b * width / 2 + d * height / 2 + ty
    box["x"] = _pt((cx - w / 2) / EMU_PER_PT)
    box["y"] = _pt((cy - h / 2) / EMU_PER_PT)
    box["w"] = _pt(w / EMU_PER_PT)
    box["h"] = _pt(h / EMU_PER_PT)
    rot = round(rot, 2) % 360
    if rot and abs(rot - 360) > 0.005:
        box["rot"] = _pt(rot)
    if flip:
        box["flip"] = flip
    if abs(skew) > 1e-6:
        box["skewed"] = True
    return box


# --- Colours, links, styles -------------------------------------------------


def _color(color: Optional[Dict[str, Any]]) -> Optional[str]:
    if not color:
        return None
    opaque = color.get("opaqueColor", color)
    if "themeColor" in opaque:
        return opaque["themeColor"]
    rgb = opaque.get("rgbColor")
    if rgb is not None:
        return "#" + "".join(
            f"{round(rgb.get(ch, 0) * 255):02x}" for ch in ("red", "green", "blue")
        )
    if color == {}:
        return "transparent"
    return None


def _link(link: Optional[Dict[str, Any]]) -> Optional[str]:
    if not link:
        return None
    if "url" in link:
        return link["url"]
    if "pageObjectId" in link:
        return f"slide:{link['pageObjectId']}"
    if "relativeLink" in link:
        return link["relativeLink"]
    if "slideIndex" in link:
        return f"slide_index:{link['slideIndex']}"
    return None


def project_text_style(style: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Locally set run style fields, in projection names."""
    s = style or {}
    out: Dict[str, Any] = {}
    for api, name in (
        ("bold", "bold"),
        ("italic", "italic"),
        ("underline", "underline"),
        ("strikethrough", "strike"),
        ("smallCaps", "small_caps"),
    ):
        if s.get(api):
            out[name] = True
    if s.get("baselineOffset") in ("SUPERSCRIPT", "SUBSCRIPT"):
        out["baseline"] = s["baselineOffset"]
    weighted = s.get("weightedFontFamily") or {}
    font = weighted.get("fontFamily") or s.get("fontFamily")
    if font:
        out["font"] = font
    weight = weighted.get("weight")
    if weight and weight != 400:
        out["weight"] = weight
    size = _dim_pt(s.get("fontSize"))
    if size is not None:
        out["size"] = size
    if "foregroundColor" in s:
        color = _color(s["foregroundColor"])
        if color:
            out["color"] = color
    if "backgroundColor" in s:
        bg = _color(s["backgroundColor"])
        if bg and bg != "transparent":
            out["bg"] = bg
    link = _link(s.get("link"))
    if link:
        out["link"] = link
    return out


class StyleTable:
    """Interns run styles as s1, s2, ... in first-use order."""

    def __init__(self) -> None:
        self._by_key: Dict[str, str] = {}
        self.entries: Dict[str, Dict[str, Any]] = {}

    def ref(self, style: Dict[str, Any]) -> str:
        key = json.dumps(style, sort_keys=True)
        if key not in self._by_key:
            alias = f"s{len(self._by_key) + 1}"
            self._by_key[key] = alias
            self.entries[alias] = style
        return self._by_key[key]


class ListTable:
    """Interns API list IDs as L1, L2, ... within one response."""

    def __init__(self) -> None:
        self._by_id: Dict[str, str] = {}

    def ref(self, list_id: str) -> str:
        if list_id not in self._by_id:
            self._by_id[list_id] = f"L{len(self._by_id) + 1}"
        return self._by_id[list_id]


# --- Text -------------------------------------------------------------------


_PARA_FIELDS = (
    ("alignment", "align"),
    ("spaceAbove", "space_above"),
    ("spaceBelow", "space_below"),
    ("lineSpacing", "line_spacing"),
    ("indentStart", "indent_start"),
    ("indentFirstLine", "indent_first_line"),
    ("indentEnd", "indent_end"),
    ("direction", "direction"),
)


def _paragraph_props(style: Dict[str, Any], level: Optional[int]) -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    for api, name in _PARA_FIELDS:
        if api not in style:
            continue
        value = style[api]
        if isinstance(value, dict):
            value = _dim_pt(value)
            if value is None:
                continue
        elif api == "lineSpacing":
            value = _pt(value)
        elif api == "direction" and value == "LEFT_TO_RIGHT":
            continue
        props[name] = value
    if level is not None:
        # The indent a bullet puts there is not a paragraph prop.
        if props.get("indent_start") == _pt(_BULLET_INDENT_PT * (level + 1)):
            props.pop("indent_start")
        if props.get("indent_first_line") == _pt(_BULLET_INDENT_PT * level + 18):
            props.pop("indent_first_line")
    return props


def project_text(
    text: Optional[Dict[str, Any]],
    styles: StyleTable,
    lists: ListTable,
    glyphs: Optional[Dict[str, Dict[str, str]]] = None,
    scale: float = 1.0,
) -> List[Any]:
    """A text body as a list of paragraphs, terminal newline implicit."""
    elements = (text or {}).get("textElements", [])
    paragraphs: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for te in elements:
        if "paragraphMarker" in te:
            marker = te["paragraphMarker"]
            bullet = marker.get("bullet")
            level = None
            props: Dict[str, Any] = {}
            if bullet is not None:
                level = bullet.get("nestingLevel", 0)
                alias = lists.ref(bullet.get("listId", ""))
                props["bullet"] = {"list": alias, "level": level}
                glyph = (bullet.get("glyph") or "").strip()
                if glyphs is not None and glyph:
                    glyphs.setdefault(alias, {}).setdefault(str(level), glyph)
            props.update(_paragraph_props(marker.get("style", {}), level))
            current = {"runs": [], "props": props}
            paragraphs.append(current)
            continue
        if current is None:
            current = {"runs": [], "props": {}}
            paragraphs.append(current)
        if "textRun" in te:
            run = te["textRun"]
            content = run.get("content", "")
            if content.endswith("\n"):
                content = content[:-1]
            if not content:
                continue
            style = project_text_style(run.get("style"))
            if scale != 1.0 and "size" in style:
                style["size"] = _pt(style["size"] * scale)
            current["runs"].append((content, style))
        elif "autoText" in te:
            auto = te["autoText"]
            style = project_text_style(auto.get("style"))
            current["runs"].append(({"auto": auto.get("type", "SLIDE_NUMBER")}, style))

    out: List[Any] = []
    for para in paragraphs:
        runs: List[Any] = []
        for content, style in _merge_runs(para["runs"]):
            if isinstance(content, dict):
                run_out = dict(content)
                if style:
                    run_out["s"] = styles.ref(style)
                runs.append(run_out)
            elif style:
                runs.append({"text": content, "s": styles.ref(style)})
            else:
                runs.append(content)
        props = para["props"]
        if not props and len(runs) <= 1:
            if not runs:
                out.append("")
            elif isinstance(runs[0], str):
                out.append(runs[0])
            else:
                out.append(runs[0] if "auto" not in runs[0] else {"runs": runs})
            continue
        if len(runs) == 1 and isinstance(runs[0], dict) and "text" in runs[0]:
            out.append({**runs[0], **props})
        elif len(runs) == 1 and isinstance(runs[0], str):
            out.append({"text": runs[0], **props})
        elif not runs:
            out.append({"text": "", **props})
        else:
            out.append({"runs": runs, **props})
    # An empty body is [""]; a body that is only an empty paragraph is too.
    return out or [""]


def _merge_runs(
    runs: Iterable[Tuple[Any, Dict[str, Any]]],
) -> List[Tuple[Any, Dict[str, Any]]]:
    merged: List[Tuple[Any, Dict[str, Any]]] = []
    for content, style in runs:
        if (
            merged
            and isinstance(content, str)
            and isinstance(merged[-1][0], str)
            and merged[-1][1] == style
        ):
            merged[-1] = (merged[-1][0] + content, style)
        else:
            merged.append((content, style))
    return merged


# --- Elements ---------------------------------------------------------------


def _shape_props(props: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    fill = props.get("shapeBackgroundFill") or {}
    state = fill.get("propertyState")
    if state == "NOT_RENDERED":
        out["fill"] = "none"
    elif state != "INHERIT" and "solidFill" in fill:
        color = _color(fill["solidFill"].get("color"))
        if color:
            out["fill"] = color
    outline = props.get("outline") or {}
    state = outline.get("propertyState")
    if state == "NOT_RENDERED":
        out["outline"] = "none"
    elif state != "INHERIT" and outline:
        o: Dict[str, Any] = {}
        color = _color(
            (outline.get("outlineFill") or {}).get("solidFill", {}).get("color")
        )
        if color:
            o["color"] = color
        weight = _dim_pt(outline.get("weight"))
        if weight is not None:
            o["weight"] = weight
        if outline.get("dashStyle") and outline["dashStyle"] != "SOLID":
            o["dash"] = outline["dashStyle"]
        if o:
            out["outline"] = o
    if props.get("contentAlignment"):
        out["content_align"] = props["contentAlignment"]
    link = _link(props.get("link"))
    if link:
        out["link"] = link
    autofit = props.get("autofit") or {}
    kind = autofit.get("autofitType")
    if kind == "SHAPE_AUTOFIT":
        out["autofit"] = "grow"
    elif kind == "TEXT_AUTOFIT":
        out["autofit"] = {"type": "shrink", "scale": _pt(autofit.get("fontScale", 1))}
    return out


def _kind(el: Dict[str, Any]) -> str:
    for api, kind in (
        ("shape", "shape"),
        ("table", "table"),
        ("image", "image"),
        ("line", "line"),
        ("elementGroup", "group"),
        ("sheetsChart", "chart"),
        ("video", "video"),
        ("wordArt", "wordArt"),
        ("speakerSpotlight", "speakerSpotlight"),
    ):
        if api in el:
            return kind
    return "unknown"


class Projector:
    def __init__(self) -> None:
        self.styles = StyleTable()
        self.lists = ListTable()

    def element(
        self,
        el: Dict[str, Any],
        parent: Tuple[float, ...] = (1, 0, 0, 1, 0, 0),
        group_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        matrix = _compose(parent, _matrix(el.get("transform")))
        kind = _kind(el)
        out: Dict[str, Any] = {"id": el.get("objectId"), "kind": kind}
        if group_id:
            out["group"] = group_id

        if kind == "group":
            out["children"] = [
                self.element(child, matrix)
                for child in el["elementGroup"].get("children", [])
            ]
        elif kind == "table":
            out.update(self._table(el, matrix))
        else:
            out["box"] = project_box(matrix, el.get("size"))

        if kind == "shape":
            shape = el["shape"]
            out["shape"] = shape.get("shapeType")
            ph = shape.get("placeholder")
            if ph:
                ptype = ph.get("type")
                out["placeholder"] = (
                    f"{ptype}:{ph['index']}" if ph.get("index") else ptype
                )
            props = _shape_props(shape.get("shapeProperties", {}))
            scale = 1.0
            af = props.get("autofit")
            if isinstance(af, dict):
                scale = af.get("scale", 1.0) or 1.0
            out.update(props)
            if "text" in shape:
                glyphs: Dict[str, Dict[str, str]] = {}
                out["text"] = project_text(
                    shape["text"], self.styles, self.lists, glyphs, scale
                )
                if glyphs:
                    out["lists"] = {k: {"glyphs": v} for k, v in glyphs.items()}
        elif kind == "image":
            src = el["image"].get("sourceUrl")
            if src:
                out["src"] = src
        elif kind == "line":
            category = el["line"].get("lineCategory")
            if category and category != "STRAIGHT":
                out["category"] = category

        alt = {}
        if el.get("title"):
            alt["title"] = el["title"]
        if el.get("description"):
            alt["description"] = el["description"]
        if alt:
            out["alt"] = alt
        out["v"] = version(out, self.styles.entries)
        # v reads best right after id.
        return {"id": out.pop("id"), "v": out.pop("v"), **out}

    def _table(self, el: Dict[str, Any], matrix: Tuple[float, ...]) -> Dict[str, Any]:
        table = el["table"]
        a, b, c, d, tx, ty = matrix
        out: Dict[str, Any] = {
            "box": {"x": _pt(tx / EMU_PER_PT), "y": _pt(ty / EMU_PER_PT)},
            "col_widths": [
                _dim_pt(col.get("columnWidth")) for col in table.get("tableColumns", [])
            ],
            "row_heights": [
                _dim_pt(row.get("rowHeight")) for row in table.get("tableRows", [])
            ],
            "columns": list(range(table.get("columns", 0))),
        }
        covered = set()
        rows = []
        for r, row in enumerate(table.get("tableRows", [])):
            cells: List[Any] = []
            for cell in row.get("tableCells", []):
                loc = cell.get("location", {})
                key = (loc.get("rowIndex", r), loc.get("columnIndex", len(cells)))
                if key in covered:
                    cells.append(None)
                    continue
                body = project_text(cell.get("text"), self.styles, self.lists)
                rs, cs = cell.get("rowSpan", 1), cell.get("columnSpan", 1)
                if rs > 1 or cs > 1:
                    for i in range(rs):
                        for j in range(cs):
                            if i or j:
                                covered.add((key[0] + i, key[1] + j))
                if rs > 1 or cs > 1:
                    cells.append({"text": body, "span": [rs, cs]})
                elif len(body) == 1 and isinstance(body[0], str):
                    cells.append(body[0])
                else:
                    cells.append({"text": body})
            rows.append({"r": r, "cells": cells})
        out["rows"] = rows
        return out


def _walk(elements: Iterable[Dict[str, Any]], parent=(1, 0, 0, 1, 0, 0), group=None):
    for el in elements:
        yield el, parent, group
        if "elementGroup" in el:
            matrix = _compose(parent, _matrix(el.get("transform")))
            yield from _walk(
                el["elementGroup"].get("children", []), matrix, el.get("objectId")
            )


def _resolve(value: Any, styles: Dict[str, Dict[str, Any]]) -> Any:
    """Canonical form: style aliases resolved, string and object runs unified."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in ("v", "lists"):
                continue
            if k == "s" and isinstance(v, str):
                out.update(styles.get(v, {}))
            elif k == "bullet" and isinstance(v, dict):
                out["bullet"] = {"level": v.get("level")}
            else:
                out[k] = _resolve(v, styles)
        return out
    if isinstance(value, list):
        return [_resolve(v, styles) for v in value]
    return value


def version(element: Dict[str, Any], styles: Dict[str, Dict[str, Any]]) -> str:
    canonical = json.dumps(
        _resolve(element, styles), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def project_page(
    page: Dict[str, Any],
    page_size: Optional[Dict[str, Any]] = None,
    targets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """The editable projection of a page, whole or scoped to ``targets``.

    ``targets`` are element IDs and ``"notes"``. A target inside a group comes
    back with absolute geometry and its ``group`` ID; a target that is a group
    comes back with its descendants.
    """
    proj = Projector()
    out: Dict[str, Any] = {"page_id": page.get("objectId")}
    if page_size:
        out["page_size"] = {
            "w": _dim_pt(page_size.get("width")),
            "h": _dim_pt(page_size.get("height")),
        }
    elements = page.get("pageElements", [])
    if targets is None:
        out["elements"] = [proj.element(el) for el in elements]
        want_notes = True
    else:
        wanted = set(targets)
        out["elements"] = [
            proj.element(el, parent, group)
            for el, parent, group in _walk(elements)
            if el.get("objectId") in wanted
        ]
        want_notes = "notes" in wanted

    if want_notes:
        notes_page = (page.get("slideProperties") or {}).get("notesPage") or {}
        notes_id = (notes_page.get("notesProperties") or {}).get("speakerNotesObjectId")
        for el in notes_page.get("pageElements", []):
            if el.get("objectId") == notes_id and "shape" in el:
                body = project_text(el["shape"].get("text"), proj.styles, proj.lists)
                if body != [""]:
                    notes = {"text": body}
                    out["notes"] = {"v": version(notes, proj.styles.entries), **notes}
    styles = proj.styles.entries
    if styles:
        return {
            **{k: out[k] for k in ("page_id", "page_size") if k in out},
            "styles": styles,
            **{k: v for k, v in out.items() if k not in ("page_id", "page_size")},
        }
    return out
