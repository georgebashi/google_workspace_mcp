"""Token cost of the get_presentation:markup projection against today's reads.

Reads the caller's most recently modified decks (read only) and reports token
counts only, never content. Counted with tiktoken's o200k_base, the encoding
the earlier measurements in docs/slides-tool-surface.md used.

Run from the repo root:

    uv run --frozen --with tiktoken python docs/slides-probe/measure_projection.py [--decks 15] [--from-dir DIR]

--from-dir reads presentations.get JSON files saved in DIR instead of calling
the API. Credentials are found as in probe.py. Results are written next to
this script, in measure.json, which is not committed.
"""

import argparse
import glob
import json
import os
import statistics
import sys

import tiktoken

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from gslides.slides_projection import project_page  # noqa: E402
from gslides.slides_tools import _describe_elements  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "measure.json")
ENC = tiktoken.get_encoding("o200k_base")
SLIDES_PER_DECK = 40


def tokens(value) -> int:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return len(ENC.encode(value))


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def indented(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def load_decks(args):
    if args.from_dir:
        for path in sorted(glob.glob(os.path.join(args.from_dir, "*.json")))[
            : args.decks
        ]:
            yield json.load(open(path))
        return
    import probe  # noqa: F401  (credentials and clients)

    files = (
        probe.DR.files()
        .list(
            q="mimeType='application/vnd.google-apps.presentation' and trashed=false",
            pageSize=args.decks,
            orderBy="modifiedTime desc",
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora="allDrives",
        )
        .execute()["files"]
    )
    for f in files:
        yield probe.SL.presentations().get(presentationId=f["id"]).execute()


def sample(slides):
    if len(slides) <= SLIDES_PER_DECK:
        return slides
    step = len(slides) / SLIDES_PER_DECK
    return [slides[int(i * step)] for i in range(SLIDES_PER_DECK)]


def get_page_text(slide, geometry_and_styles=False):
    lines = _describe_elements(
        slide.get("pageElements", []),
        include_geometry=geometry_and_styles,
        include_styles=geometry_and_styles,
    )
    return "\n".join(lines)


def layouts_section(deck, full=True):
    used = {}
    for n, slide in enumerate(deck.get("slides", []), 1):
        lid = (slide.get("slideProperties") or {}).get("layoutObjectId")
        used.setdefault(lid, []).append(n)
    masters = deck.get("masters", [])
    lines = ["Layouts:"]
    by_master = {}
    for layout in deck.get("layouts", []):
        props = layout.get("layoutProperties") or {}
        by_master.setdefault(props.get("masterObjectId"), []).append(layout)
    for mid, layouts in by_master.items():
        if len(masters) > 1:
            name = next(
                (
                    (m.get("masterProperties") or {}).get("displayName", "")
                    for m in masters
                    if m.get("objectId") == mid
                ),
                "",
            )
            lines.append(f"Master {mid}  {name}")
        for layout in layouts:
            lid = layout.get("objectId")
            props = layout.get("layoutProperties") or {}
            slides = used.get(lid)
            if not full and not slides:
                lines.append(f"  {lid}  {props.get('displayName', '')}")
                continue
            kinds = []
            for el in layout.get("pageElements", []):
                ph = (el.get("shape") or {}).get("placeholder")
                if ph:
                    kinds.append(
                        f"{ph.get('type')}:{ph['index']}"
                        if ph.get("index")
                        else ph.get("type")
                    )
            usage = (
                "-"
                if not slides
                else ("slide " if len(slides) == 1 else "slides ")
                + ",".join(map(str, slides[:6]))
                + ("..." if len(slides) > 6 else "")
            )
            lines.append(
                f"  {lid}  {props.get('displayName', '')}  {', '.join(kinds) or '-'}  {usage}"
            )
    return "\n".join(lines)


def text_bearing(el):
    return (
        "shape" in el
        and (el["shape"].get("text") or {}).get("textElements")
        or "table" in el
    )


def summarise(values):
    if not values:
        return None
    values = sorted(values)
    return {
        "n": len(values),
        "median": statistics.median(values),
        "p90": values[int(0.9 * (len(values) - 1))],
        "max": values[-1],
        "total": sum(values),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--decks", type=int, default=15)
    parser.add_argument("--from-dir")
    args = parser.parse_args()

    per_slide = {
        k: []
        for k in (
            "raw",
            "get_page",
            "get_page_geometry_styles",
            "markup_default",
            "markup_compact",
            "markup_indented",
        )
    }
    ratio = []
    per_element = []
    per_element_raw = []
    layouts = []
    decks = 0
    for deck in load_decks(args):
        decks += 1
        size = deck.get("pageSize")
        for slide in sample(deck.get("slides", [])):
            proj = project_page(slide, size)
            raw = tokens(slide)
            per_slide["raw"].append(raw)
            per_slide["get_page"].append(tokens(get_page_text(slide)))
            per_slide["get_page_geometry_styles"].append(
                tokens(get_page_text(slide, True))
            )
            per_slide["markup_default"].append(tokens(proj))
            per_slide["markup_compact"].append(tokens(compact(proj)))
            per_slide["markup_indented"].append(tokens(indented(proj)))
            ratio.append(tokens(compact(proj)) / raw if raw else 1)
            for el in slide.get("pageElements", []):
                if text_bearing(el):
                    per_element.append(
                        tokens(
                            compact(project_page(slide, size, targets=[el["objectId"]]))
                        )
                    )
                    per_element_raw.append(tokens(el))
        layouts.append(
            {
                "layouts": len(deck.get("layouts", [])),
                "masters": len(deck.get("masters", [])),
                "slides": len(deck.get("slides", [])),
                "full": tokens(layouts_section(deck)),
                "fallback": tokens(layouts_section(deck, full=False)),
            }
        )

    result = {
        "decks": decks,
        "per_slide": {k: summarise(v) for k, v in per_slide.items()},
        "markup_compact_over_raw": summarise([round(r, 3) for r in ratio]),
        "one_element_markup_compact": summarise(per_element),
        "one_element_raw": summarise(per_element_raw),
        "layouts_section": sorted(layouts, key=lambda d: d["layouts"]),
    }
    json.dump(result, open(OUT, "w"), indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
