"""Live checks for the open questions in docs/slides-edit-workflow.md.

Each probe creates its own scratch presentations and edits only those. q3, q3b
and q9 also read real decks, reporting only counts (revisionId presence, access
level, autofit types), never content.

Run from the repo root (--frozen keeps uv from rewriting uv.lock):

    uvx --from python-pptx python docs/slides-probe/make_pptx.py   # once, for q2b/q2c/q8
    uv run --frozen python docs/slides-probe/probe.py [q1 q2 ...]

Credentials are the stored OAuth file the server writes, found from
USER_GOOGLE_EMAIL, or set SLIDES_PROBE_CREDENTIALS to its path. Every probe
creates its own scratch deck; delete them from Drive afterwards. Results are
written next to this script, in results.json, which is not committed.
"""

import json
import os
import sys
import time

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

HERE = os.path.dirname(os.path.abspath(__file__))
CRED = os.environ.get("SLIDES_PROBE_CREDENTIALS") or os.path.expanduser(
    f"~/.google_workspace_mcp/credentials/{os.environ['USER_GOOGLE_EMAIL']}.json"
)
OUT = os.path.join(HERE, "results.json")
PPTX = os.path.join(HERE, "autofit.pptx")


def services():
    d = json.load(open(CRED))
    creds = Credentials(
        token=d["token"],
        refresh_token=d["refresh_token"],
        token_uri=d["token_uri"],
        client_id=d["client_id"],
        client_secret=d["client_secret"],
        scopes=d["scopes"],
    )
    if not creds.valid:
        creds.refresh(Request())
    return (
        build("slides", "v1", credentials=creds, cache_discovery=False),
        build("drive", "v3", credentials=creds, cache_discovery=False),
    )


SL, DR = services()
RESULTS = {}


def batch(pid, reqs, rev=None):
    body = {"requests": reqs}
    if rev:
        body["writeControl"] = {"requiredRevisionId": rev}
    return SL.presentations().batchUpdate(presentationId=pid, body=body).execute()


def page(pid, page_id):
    return SL.presentations().pages().get(presentationId=pid, pageObjectId=page_id).execute()


def element(pid, page_id, oid):
    for el in walk(page(pid, page_id).get("pageElements", [])):
        if el["objectId"] == oid:
            return el
    return None


def walk(els):
    for el in els:
        yield el
        yield from walk(el.get("elementGroup", {}).get("children", []))


def paragraphs(shape_el):
    """[(text, paragraphStyle, bullet)] per paragraph, from a shape element."""
    out, cur = [], None
    for te in shape_el["shape"].get("text", {}).get("textElements", []):
        if "paragraphMarker" in te:
            pm = te["paragraphMarker"]
            cur = {"text": "", "style": pm.get("style", {}), "bullet": pm.get("bullet")}
            out.append(cur)
        elif "textRun" in te and cur is not None:
            cur["text"] += te["textRun"]["content"]
        elif "autoText" in te and cur is not None:
            cur["text"] += te["autoText"].get("content", "")
    return out


def summary(paras):
    return [
        {
            "text": p["text"].rstrip("\n"),
            "align": p["style"].get("alignment"),
            "spaceAbove": p["style"].get("spaceAbove", {}).get("magnitude"),
            "indentStart": p["style"].get("indentStart", {}).get("magnitude"),
            "bullet": None
            if not p["bullet"]
            else {"glyph": p["bullet"].get("glyph"), "level": p["bullet"].get("nestingLevel", 0)},
        }
        for p in paras
    ]


def new_deck():
    pres = SL.presentations().create(body={"title": f"slides-probe {time.strftime('%Y%m%d-%H%M%S')}"}).execute()
    pid = pres["presentationId"]
    slide_id = pres["slides"][0]["objectId"]
    return pid, slide_id


def add_box(pid, slide_id, oid, text):
    batch(pid, [
        {"createShape": {"objectId": oid, "shapeType": "TEXT_BOX", "elementProperties": {
            "pageObjectId": slide_id,
            "size": {"width": {"magnitude": 400, "unit": "PT"}, "height": {"magnitude": 200, "unit": "PT"}},
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 50, "translateY": 50, "unit": "PT"}}}},
        {"insertText": {"objectId": oid, "insertionIndex": 0, "text": text}},
    ])


# Paragraphs used by Q1: P0 plain/left, P1 centered + spaceAbove 20 + bullet,
# P2 right-aligned, no bullet.  "AAA\nBBB\nCCC"
#   P0 "AAA\n" [0,4)  P1 "BBB\n" [4,8)  P2 "CCC\n" [8,12)
def q1_setup(pid, slide_id, oid):
    add_box(pid, slide_id, oid, "AAA\nBBB\nCCC")
    batch(pid, [
        {"updateParagraphStyle": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": 4, "endIndex": 8},
                                  "style": {"alignment": "CENTER", "spaceAbove": {"magnitude": 20, "unit": "PT"}},
                                  "fields": "alignment,spaceAbove"}},
        {"createParagraphBullets": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": 4, "endIndex": 8},
                                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}},
        {"updateParagraphStyle": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": 8, "endIndex": 12},
                                  "style": {"alignment": "END"}, "fields": "alignment"}},
    ])


def q1():
    """When a paragraph break is deleted, whose props and bullet survive?"""
    pid, slide_id = new_deck()
    res = {"deck": pid}
    cases = {
        # delete P0's newline (index 3): P0 (plain) merges with P1 (bulleted, centered)
        "merge_plain_into_bulleted": 3,
        # delete P1's newline (index 7): P1 (bulleted, centered) merges with P2 (right, plain)
        "merge_bulleted_into_plain": 7,
    }
    for name, idx in cases.items():
        oid = f"q1_{name}"[:50]
        q1_setup(pid, slide_id, oid)
        before = summary(paragraphs(element(pid, slide_id, oid)))
        batch(pid, [{"deleteText": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": idx, "endIndex": idx + 1}}}])
        after = summary(paragraphs(element(pid, slide_id, oid)))
        res[name] = {"before": before, "after": after}

    # Deleting the final paragraph: the API forbids deleting the terminal newline,
    # so a final paragraph is removed by deleting the *preceding* newline plus its text.
    oid = "q1_delete_last_paragraph"
    q1_setup(pid, slide_id, oid)
    # remove "\nCCC" -> P1 (bulleted) should survive as last paragraph
    batch(pid, [{"deleteText": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": 7, "endIndex": 11}}}])
    res["delete_last_paragraph_via_preceding_newline"] = summary(paragraphs(element(pid, slide_id, oid)))

    # And deleting the whole of P0 including its own newline: does P1 keep its props?
    oid = "q1_delete_first_paragraph"
    q1_setup(pid, slide_id, oid)
    batch(pid, [{"deleteText": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 4}}}])
    res["delete_first_paragraph_with_own_newline"] = summary(paragraphs(element(pid, slide_id, oid)))

    # Deleting a middle paragraph's text plus its own newline: does P2 keep its props?
    oid = "q1_delete_middle_paragraph"
    q1_setup(pid, slide_id, oid)
    batch(pid, [{"deleteText": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": 4, "endIndex": 8}}}])
    res["delete_middle_paragraph_with_own_newline"] = summary(paragraphs(element(pid, slide_id, oid)))
    return res


def autofit_of(el):
    return el["shape"].get("shapeProperties", {}).get("autofit", {})


def sizes_of(el):
    out = []
    for te in el["shape"].get("text", {}).get("textElements", []):
        if "textRun" in te:
            fs = te["textRun"].get("style", {}).get("fontSize", {}).get("magnitude")
            out.append((te["textRun"]["content"][:12], fs))
    return out


def q2():
    """Does setting autofitType UNSPECIFIED after a text edit restore inherited autofit?

    Uses a TITLE_AND_BODY slide, whose BODY placeholder inherits autofit from the
    layout, plus a deliberately overflowing body so that fontScale < 1.
    """
    pid, _ = new_deck()
    res = {"deck": pid}
    batch(pid, [{"createSlide": {"objectId": "q2_slide", "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"}}}])
    pg = page(pid, "q2_slide")
    body = next(el for el in pg["pageElements"] if el["shape"].get("placeholder", {}).get("type") == "BODY")
    bid = body["objectId"]
    res["layout_body_autofit_before_text"] = autofit_of(body)
    long_text = "\n".join(f"Bullet line number {i} with enough words to wrap across the box" for i in range(30))
    batch(pid, [{"insertText": {"objectId": bid, "insertionIndex": 0, "text": long_text}}])
    el = element(pid, "q2_slide", bid)
    res["after_insert"] = {"autofit": autofit_of(el), "first_sizes": sizes_of(el)[:2]}

    # A second, small edit: what does a text edit do to the autofit state?
    batch(pid, [{"insertText": {"objectId": bid, "insertionIndex": 0, "text": "X"}}])
    el = element(pid, "q2_slide", bid)
    res["after_second_edit"] = {"autofit": autofit_of(el), "first_sizes": sizes_of(el)[:2]}

    # Try restoring inheritance.
    try:
        batch(pid, [{"updateShapeProperties": {"objectId": bid, "shapeProperties": {"autofit": {"autofitType": "AUTOFIT_TYPE_UNSPECIFIED"}}, "fields": "autofit.autofitType"}}])
        el = element(pid, "q2_slide", bid)
        res["after_set_unspecified"] = {"autofit": autofit_of(el), "first_sizes": sizes_of(el)[:2]}
    except HttpError as e:
        res["after_set_unspecified"] = {"error": str(e)[:400]}

    # And clearing the field outright (field in mask, value unset).
    try:
        batch(pid, [{"updateShapeProperties": {"objectId": bid, "shapeProperties": {}, "fields": "autofit"}}])
        el = element(pid, "q2_slide", bid)
        res["after_clear_autofit_field"] = {"autofit": autofit_of(el), "first_sizes": sizes_of(el)[:2]}
    except HttpError as e:
        res["after_clear_autofit_field"] = {"error": str(e)[:400]}

    # Does the editor-visible state differ? Render to check shrink is back.
    try:
        th = SL.presentations().pages().getThumbnail(presentationId=pid, pageObjectId="q2_slide",
                                                     thumbnailProperties_thumbnailSize="MEDIUM").execute()
        res["thumbnail_url"] = th.get("contentUrl")
    except HttpError as e:
        res["thumbnail_error"] = str(e)[:200]
    return res


def q3(limit=30):
    """Is revisionId absent only when the caller cannot edit?"""
    files = DR.files().list(
        q="mimeType='application/vnd.google-apps.presentation' and trashed=false",
        pageSize=limit, orderBy="modifiedTime desc",
        fields="files(id,capabilities(canEdit),ownedByMe)",
        supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives",
    ).execute()["files"]
    rows = []
    for f in files:
        row = {"canEdit": f["capabilities"]["canEdit"], "ownedByMe": f.get("ownedByMe", False)}
        try:
            p = SL.presentations().get(presentationId=f["id"], fields="revisionId,slides(objectId)").execute()
            row["presentation_revision"] = bool(p.get("revisionId"))
            slides = p.get("slides", [])
            if slides:
                pg = SL.presentations().pages().get(presentationId=f["id"], pageObjectId=slides[0]["objectId"],
                                                    fields="revisionId").execute()
                row["page_revision"] = bool(pg.get("revisionId"))
        except HttpError as e:
            row["error"] = e.resp.status
        rows.append(row)
    table = {}
    for r in rows:
        k = f"canEdit={r['canEdit']} page_revision={r.get('page_revision')} pres_revision={r.get('presentation_revision')}"
        table[k] = table.get(k, 0) + 1
    return {"n": len(rows), "breakdown": table}


def q4():
    """Does replaceAllText reach speaker notes?"""
    pid, slide_id = new_deck()
    pres = SL.presentations().get(presentationId=pid).execute()
    notes_id = pres["slides"][0]["slideProperties"]["notesPage"]["notesProperties"]["speakerNotesObjectId"]
    add_box(pid, slide_id, "q4_box", "TOKENXYZ on slide")
    batch(pid, [{"insertText": {"objectId": notes_id, "insertionIndex": 0, "text": "TOKENXYZ in notes"}}])
    r = batch(pid, [{"replaceAllText": {"containsText": {"text": "TOKENXYZ", "matchCase": True}, "replaceText": "DONE"}}])
    pres = SL.presentations().get(presentationId=pid).execute()
    notes_page = pres["slides"][0]["slideProperties"]["notesPage"]
    notes_el = next(el for el in notes_page["pageElements"] if el["objectId"] == notes_id)
    res = {
        "deck": pid,
        "occurrencesChanged_all_pages": r["replies"][0].get("replaceAllText", {}).get("occurrencesChanged"),
        "slide_text": "".join(p["text"] for p in paragraphs(element(pid, slide_id, "q4_box"))).strip(),
        "notes_text": "".join(p["text"] for p in paragraphs(notes_el)).strip(),
    }
    # With pageObjectIds = [slide]: is notes included?
    batch(pid, [{"insertText": {"objectId": notes_id, "insertionIndex": 0, "text": "TOKENABC "}},
                {"insertText": {"objectId": "q4_box", "insertionIndex": 0, "text": "TOKENABC "}}])
    r = batch(pid, [{"replaceAllText": {"containsText": {"text": "TOKENABC", "matchCase": True}, "replaceText": "DONE2",
                                        "pageObjectIds": [slide_id]}}])
    res["occurrencesChanged_slide_filter"] = r["replies"][0].get("replaceAllText", {}).get("occurrencesChanged")
    # Notes page id in the filter
    try:
        r = batch(pid, [{"replaceAllText": {"containsText": {"text": "TOKENABC", "matchCase": True}, "replaceText": "DONE3",
                                            "pageObjectIds": [notes_page["objectId"]]}}])
        res["occurrencesChanged_notes_page_filter"] = r["replies"][0].get("replaceAllText", {}).get("occurrencesChanged")
    except HttpError as e:
        res["notes_page_filter_error"] = str(e)[:300]
    return res


def ids_on(pid, slide_id):
    out = {}
    for el in page(pid, slide_id).get("pageElements", []):
        out[el["objectId"]] = [c["objectId"] for c in walk(el.get("elementGroup", {}).get("children", []))]
    return out


def q5():
    """What does deleteObject cascade within groups?"""
    pid, slide_id = new_deck()
    res = {"deck": pid}

    def box(oid, x):
        return {"createShape": {"objectId": oid, "shapeType": "RECTANGLE", "elementProperties": {
            "pageObjectId": slide_id,
            "size": {"width": {"magnitude": 40, "unit": "PT"}, "height": {"magnitude": 40, "unit": "PT"}},
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": 50, "unit": "PT"}}}}

    def group(gid, kids):
        return {"groupObjects": {"groupObjectId": gid, "childrenObjectIds": kids}}

    def run(name, build_reqs, delete_ids):
        prefix = name[:6]
        reqs = build_reqs(prefix)
        batch(pid, reqs)
        before = ids_on(pid, slide_id)
        try:
            batch(pid, [{"deleteObject": {"objectId": i}} for i in delete_ids(prefix)])
            after = ids_on(pid, slide_id)
            res[name] = {"before": before, "after": after}
        except HttpError as e:
            res[name] = {"before": before, "error": str(e)[:300]}
        # clean up
        leftover = list(ids_on(pid, slide_id))
        if leftover:
            batch(pid, [{"deleteObject": {"objectId": i}} for i in leftover])

    # A: two-child group, delete one child
    run("two_child_delete_one",
        lambda p: [box(f"{p}a", 10), box(f"{p}b", 60), group(f"{p}g", [f"{p}a", f"{p}b"])],
        lambda p: [f"{p}a"])
    # B: three-child group, delete one child
    run("three_child_delete_one",
        lambda p: [box(f"{p}a", 10), box(f"{p}b", 60), box(f"{p}c", 110), group(f"{p}g", [f"{p}a", f"{p}b", f"{p}c"])],
        lambda p: [f"{p}a"])
    # C: three-child group, delete two children in one batch
    run("three_child_delete_two",
        lambda p: [box(f"{p}a", 10), box(f"{p}b", 60), box(f"{p}c", 110), group(f"{p}g", [f"{p}a", f"{p}b", f"{p}c"])],
        lambda p: [f"{p}a", f"{p}b"])
    # D: delete the group itself
    run("delete_group",
        lambda p: [box(f"{p}a", 10), box(f"{p}b", 60), group(f"{p}g", [f"{p}a", f"{p}b"])],
        lambda p: [f"{p}g"])
    # E: nested: outer(inner(a,b), c); delete a
    run("nested_delete_inner_child",
        lambda p: [box(f"{p}a", 10), box(f"{p}b", 60), box(f"{p}c", 110),
                   group(f"{p}i", [f"{p}a", f"{p}b"]), group(f"{p}o", [f"{p}i", f"{p}c"])],
        lambda p: [f"{p}a"])
    # F: nested: outer(inner(a,b), c); delete c
    run("nested_delete_outer_leaf",
        lambda p: [box(f"{p}a", 10), box(f"{p}b", 60), box(f"{p}c", 110),
                   group(f"{p}i", [f"{p}a", f"{p}b"]), group(f"{p}o", [f"{p}i", f"{p}c"])],
        lambda p: [f"{p}c"])
    return res


def main():
    wanted = sys.argv[1:] or ["q1", "q2", "q3", "q4", "q5"]
    try:
        RESULTS.update(json.load(open(OUT)))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    for name in wanted:
        print(f"== {name}", flush=True)
        try:
            RESULTS[name] = globals()[name]()
        except HttpError as e:
            RESULTS[name] = {"error": str(e)[:600]}
        except Exception as e:  # keep going so one failure does not hide the rest
            RESULTS[name] = {"error": f"{type(e).__name__}: {e}"[:600]}
        print(json.dumps(RESULTS[name], indent=1, ensure_ascii=False), flush=True)
        json.dump(RESULTS, open(OUT, "w"), indent=1, ensure_ascii=False)



def import_pptx(path, title):
    from googleapiclient.http import MediaFileUpload
    f = DR.files().create(
        body={"name": title, "mimeType": "application/vnd.google-apps.presentation"},
        media_body=MediaFileUpload(path, mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        fields="id",
    ).execute()
    return f["id"]


def q2b():
    """Text edit on a TEXT_AUTOFIT / SHAPE_AUTOFIT shape: what changes?"""
    pid = import_pptx(PPTX, f"slides-probe autofit {time.strftime('%Y%m%d-%H%M%S')}")
    pres = SL.presentations().get(presentationId=pid).execute()
    slide_id = pres["slides"][0]["objectId"]
    res = {"deck": pid}
    for el in page(pid, slide_id)["pageElements"]:
        if "shape" not in el:
            continue
        oid = el["objectId"]
        st = el["shape"].get("text", {}).get("textElements", [])
        line_spacing = next((te["paragraphMarker"]["style"].get("lineSpacing") for te in st if "paragraphMarker" in te), None)
        before = {"autofit": autofit_of(el), "sizes": sizes_of(el)[:1], "lineSpacing": line_spacing,
                  "size": el.get("size"), "scaleY": el["transform"].get("scaleY")}
        # A plain text edit
        batch(pid, [{"insertText": {"objectId": oid, "insertionIndex": 0, "text": "X"}}])
        el2 = element(pid, slide_id, oid)
        st2 = el2["shape"]["text"]["textElements"]
        ls2 = next((te["paragraphMarker"]["style"].get("lineSpacing") for te in st2 if "paragraphMarker" in te), None)
        after = {"autofit": autofit_of(el2), "sizes": sizes_of(el2)[:1], "lineSpacing": ls2,
                 "size": el2.get("size"), "scaleY": el2["transform"].get("scaleY")}
        res[oid] = {"before": before, "after_insertText": after}
    # Does a style-only edit also trip it? Re-import and use updateTextStyle bold.
    pid2 = import_pptx(PPTX, f"slides-probe autofit2 {time.strftime('%Y%m%d-%H%M%S')}")
    s2 = SL.presentations().get(presentationId=pid2).execute()["slides"][0]["objectId"]
    res["deck2"] = pid2
    for el in page(pid2, s2)["pageElements"]:
        if "shape" not in el:
            continue
        oid = el["objectId"]
        batch(pid2, [{"updateTextStyle": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 4},
                                          "style": {"bold": True}, "fields": "bold"}}])
        res[f"{oid}_after_bold_only"] = autofit_of(element(pid2, s2, oid))
        batch(pid2, [{"updatePageElementTransform": {"objectId": oid, "applyMode": "RELATIVE",
                                                     "transform": {"scaleX": 1, "scaleY": 1, "translateX": 5, "translateY": 0, "unit": "PT"}}}])
        res[f"{oid}_after_move_only"] = autofit_of(element(pid2, s2, oid))
    return res


def q6():
    """Revision guard: does our own write invalidate a revision for an untouched slide,
    and what does a stale guard error look like?"""
    pid, slide_id = new_deck()
    batch(pid, [{"createSlide": {"objectId": "q6_slide2"}}])
    add_box(pid, slide_id, "q6_box_a", "on slide 1")
    add_box(pid, "q6_slide2", "q6_box_b", "on slide 2")
    rev1 = page(pid, slide_id)["revisionId"]
    rev2 = page(pid, "q6_slide2")["revisionId"]
    res = {"deck": pid, "page_revisions_equal_across_slides": rev1 == rev2}
    r = batch(pid, [{"insertText": {"objectId": "q6_box_a", "insertionIndex": 0, "text": "X"}}], rev=rev1)
    res["writeControl_in_response"] = sorted(r.get("writeControl", {}).keys())
    new_rev = r.get("writeControl", {}).get("requiredRevisionId")
    res["response_rev_matches_fresh_read"] = new_rev == page(pid, slide_id)["revisionId"]
    try:
        batch(pid, [{"insertText": {"objectId": "q6_box_b", "insertionIndex": 0, "text": "Y"}}], rev=rev2)
        res["stale_guard_on_untouched_slide"] = "accepted"
    except HttpError as e:
        res["stale_guard_on_untouched_slide"] = {"status": e.resp.status, "message": str(e).split('returned "')[-1][:300]}
    # Does a read-only call change the revision?  (render)
    before = page(pid, slide_id)["revisionId"]
    SL.presentations().pages().getThumbnail(presentationId=pid, pageObjectId=slide_id).execute()
    res["revision_stable_across_reads"] = before == page(pid, slide_id)["revisionId"]
    return res


def q7():
    """Bullet behaviours the design relies on."""
    pid, slide_id = new_deck()
    res = {"deck": pid}
    # (a) insert "\nNEW" at end of a bulleted paragraph: does NEW inherit bullet + level?
    add_box(pid, slide_id, "q7_box_a", "one\n\ttwo\nthree")
    batch(pid, [{"createParagraphBullets": {"objectId": "q7_box_a", "textRange": {"type": "ALL"}, "bulletPreset": "NUMBERED_DIGIT_ALPHA_ROMAN"}}])
    res["a_before"] = summary(paragraphs(element(pid, slide_id, "q7_box_a")))
    txt = "".join(p["text"] for p in paragraphs(element(pid, slide_id, "q7_box_a")))
    idx = txt.index("two") + 3  # end of level-1 paragraph
    batch(pid, [{"insertText": {"objectId": "q7_box_a", "insertionIndex": idx, "text": "\nNEW"}}])
    res["a_after_split_level1"] = summary(paragraphs(element(pid, slide_id, "q7_box_a")))

    # (b) createParagraphBullets on a paragraph right after a list with the same preset: joins?
    add_box(pid, slide_id, "q7_box_b", "one\ntwo\nplain")
    batch(pid, [{"createParagraphBullets": {"objectId": "q7_box_b", "textRange": {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 8},
                                            "bulletPreset": "NUMBERED_DIGIT_ALPHA_ROMAN"}}])
    batch(pid, [{"createParagraphBullets": {"objectId": "q7_box_b", "textRange": {"type": "FIXED_RANGE", "startIndex": 8, "endIndex": 13},
                                            "bulletPreset": "NUMBERED_DIGIT_ALPHA_ROMAN"}}])
    el = element(pid, slide_id, "q7_box_b")
    res["b_after_adjacent_same_preset"] = summary(paragraphs(el))
    res["b_list_ids"] = [p["bullet"].get("listId") if p["bullet"] else None for p in paragraphs(el)]

    # (c) deleteParagraphBullets: what indent is left behind?
    add_box(pid, slide_id, "q7_box_c", "one\n\ttwo")
    batch(pid, [{"createParagraphBullets": {"objectId": "q7_box_c", "textRange": {"type": "ALL"}, "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}}])
    res["c_before"] = summary(paragraphs(element(pid, slide_id, "q7_box_c")))
    batch(pid, [{"deleteParagraphBullets": {"objectId": "q7_box_c", "textRange": {"type": "ALL"}}}])
    el = element(pid, slide_id, "q7_box_c")
    res["c_after_delete_bullets"] = [
        {"text": p["text"].rstrip("\n"), "style": p["style"]} for p in paragraphs(el)
    ]
    # (d) lists map: does it expose a preset?
    res["d_lists_sample"] = element(pid, slide_id, "q7_box_a")["shape"]["text"].get("lists")
    return res



def q2c():
    """Move-only and style-only on fresh shrink shapes; and are all runs baked?"""
    res = {}
    for label, req in [
        ("move_only", lambda oid: {"updatePageElementTransform": {"objectId": oid, "applyMode": "RELATIVE",
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 5, "translateY": 0, "unit": "PT"}}}),
        ("paragraph_style_only", lambda oid: {"updateParagraphStyle": {"objectId": oid, "textRange": {"type": "ALL"},
            "style": {"alignment": "CENTER"}, "fields": "alignment"}}),
        ("color_only", lambda oid: {"updateTextStyle": {"objectId": oid, "textRange": {"type": "ALL"},
            "style": {"foregroundColor": {"opaqueColor": {"rgbColor": {"red": 1}}}}, "fields": "foregroundColor"}}),
        ("insert_text", lambda oid: {"insertText": {"objectId": oid, "insertionIndex": 0, "text": "X"}}),
    ]:
        pid = import_pptx(PPTX, f"slides-probe autofit {label}")
        sid = SL.presentations().get(presentationId=pid).execute()["slides"][0]["objectId"]
        shrink = next(el for el in page(pid, sid)["pageElements"]
                      if autofit_of(el).get("autofitType") == "TEXT_AUTOFIT")
        oid = shrink["objectId"]
        batch(pid, [req(oid)])
        el = element(pid, sid, oid)
        res[label] = {"deck": pid, "autofit": autofit_of(el),
                      "distinct_run_sizes": sorted({s for _, s in sizes_of(el)}, key=str)}
    return res



def q8():
    """(a) Within one batch, is autofit baked at the first request, before later explicit sizes?
    (b) Which text did the slide-filtered replaceAllText change in the q4 deck?"""
    res = {}
    pid = import_pptx(PPTX, "slides-probe autofit bake-order")
    sid = SL.presentations().get(presentationId=pid).execute()["slides"][0]["objectId"]
    el = next(e for e in page(pid, sid)["pageElements"] if autofit_of(e).get("autofitType") == "TEXT_AUTOFIT")
    oid = el["objectId"]
    batch(pid, [
        {"insertText": {"objectId": oid, "insertionIndex": 0, "text": "NEW "}},
        {"updateTextStyle": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 4},
                             "style": {"fontSize": {"magnitude": 20, "unit": "PT"}}, "fields": "fontSize"}},
        {"updateParagraphStyle": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 4},
                                  "style": {"lineSpacing": 100}, "fields": "lineSpacing"}},
    ])
    el = element(pid, sid, oid)
    tes = el["shape"]["text"]["textElements"]
    res["bake_order"] = {
        "deck": pid,
        "autofit": autofit_of(el),
        "new_run_size": sizes_of(el)[0],
        "other_run_size": sizes_of(el)[1],
        "first_para_lineSpacing": next(te["paragraphMarker"]["style"].get("lineSpacing") for te in tes if "paragraphMarker" in te),
        "second_para_lineSpacing": [te["paragraphMarker"]["style"].get("lineSpacing") for te in tes if "paragraphMarker" in te][1],
    }
    q4deck = json.load(open(OUT))["q4"]["deck"]
    pres = SL.presentations().get(presentationId=q4deck).execute()
    slide = pres["slides"][0]
    notes_page = slide["slideProperties"]["notesPage"]
    nid = notes_page["notesProperties"]["speakerNotesObjectId"]
    notes_el = next(e for e in notes_page["pageElements"] if e["objectId"] == nid)
    res["q4_final"] = {
        "slide_text": "".join(p["text"] for p in paragraphs(element(q4deck, slide["objectId"], "q4_box"))).strip(),
        "notes_text": "".join(p["text"] for p in paragraphs(notes_el)).strip(),
    }
    return res



def abs_xy(els, oid, parent=None):
    """Absolute translate of oid, composing ancestor transforms (2x3 affine)."""
    def mul(a, b):  # a then b: result = b * a  (both as (sx, shx, tx, shy, sy, ty))
        sx, shx, tx, shy, sy, ty = b
        ax, ashx, atx, ashy, asy, aty = a
        return (sx*ax + shx*ashy, sx*ashx + shx*asy, sx*atx + shx*aty + tx,
                shy*ax + sy*ashy, shy*ashx + sy*asy, shy*atx + sy*aty + ty)
    ident = (1, 0, 0, 0, 1, 0)
    def t(el):
        tr = el.get("transform", {})
        return (tr.get("scaleX", 0), tr.get("shearX", 0), tr.get("translateX", 0),
                tr.get("shearY", 0), tr.get("scaleY", 0), tr.get("translateY", 0))
    def rec(els, acc):
        for el in els:
            m = mul(t(el), acc)
            if el["objectId"] == oid:
                return (round(m[2]), round(m[5]))
            r = rec(el.get("elementGroup", {}).get("children", []), m)
            if r:
                return r
    return rec(els, ident)


def q9():
    """(a) Autofit prevalence on real decks (counts only). (b) Does a promoted child keep its position?"""
    files = DR.files().list(
        q="mimeType='application/vnd.google-apps.presentation' and trashed=false",
        pageSize=30, orderBy="modifiedTime desc", fields="files(id)",
        supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives",
    ).execute()["files"]
    counts, decks_with_shrink, shrink_scales = {}, 0, []
    for f in files:
        try:
            p = SL.presentations().get(presentationId=f["id"],
                fields="slides(pageElements(shape(shapeType,text(textElements(endIndex)),shapeProperties(autofit))))").execute()
        except HttpError:
            continue
        has = False
        for sl in p.get("slides", []):
            for el in sl.get("pageElements", []):
                sh = el.get("shape")
                if not sh or "text" not in sh:
                    continue
                af = sh.get("shapeProperties", {}).get("autofit", {})
                k = af.get("autofitType", "absent")
                counts[k] = counts.get(k, 0) + 1
                if k == "TEXT_AUTOFIT":
                    has = True
                    shrink_scales.append(af.get("fontScale", 1))
        decks_with_shrink += has
    below_one = sum(1 for x in shrink_scales if x < 1)
    res = {"decks": len(files), "text_shapes_by_autofit": counts, "decks_with_any_TEXT_AUTOFIT": decks_with_shrink,
           "TEXT_AUTOFIT_with_fontScale_below_1": below_one}

    pid, slide_id = new_deck()
    def box(oid, x):
        return {"createShape": {"objectId": oid, "shapeType": "RECTANGLE", "elementProperties": {
            "pageObjectId": slide_id,
            "size": {"width": {"magnitude": 40, "unit": "PT"}, "height": {"magnitude": 40, "unit": "PT"}},
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": 50, "unit": "PT"}}}}
    batch(pid, [box("pos_a", 10), box("pos_b", 200), {"groupObjects": {"groupObjectId": "pos_g", "childrenObjectIds": ["pos_a", "pos_b"]}}])
    # move the group so children have non-trivial ancestor transforms
    batch(pid, [{"updatePageElementTransform": {"objectId": "pos_g", "applyMode": "RELATIVE",
        "transform": {"scaleX": 1.5, "scaleY": 1.5, "translateX": 30, "translateY": 40, "unit": "PT"}}}])
    before = abs_xy(page(pid, slide_id)["pageElements"], "pos_b")
    batch(pid, [{"deleteObject": {"objectId": "pos_a"}}])
    after_els = page(pid, slide_id)["pageElements"]
    res["promoted_child"] = {"deck": pid, "abs_before_emu": before, "abs_after_emu": abs_xy(after_els, "pos_b"),
                             "top_level_after": [e["objectId"] for e in after_els]}
    return res



def q3b(limit=60):
    """revisionId presence vs canEdit / canComment, over more decks."""
    files = DR.files().list(
        q="mimeType='application/vnd.google-apps.presentation' and trashed=false",
        pageSize=limit, orderBy="modifiedTime desc",
        fields="files(id,capabilities(canEdit,canComment))",
        supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives",
    ).execute()["files"]
    table = {}
    for f in files:
        c = f["capabilities"]
        try:
            p = SL.presentations().get(presentationId=f["id"], fields="revisionId").execute()
            rev = bool(p.get("revisionId"))
        except HttpError as e:
            rev = f"error {e.resp.status}"
        k = f"canEdit={c['canEdit']} canComment={c['canComment']} revision={rev}"
        table[k] = table.get(k, 0) + 1
    return {"n": len(files), "breakdown": table}



INHERITED_PPTX = os.path.join(HERE, "inherited_autofit.pptx")


def resolved_sizes(el, styles_from_parent=None):
    """[(excerpt, local fontSize)] and the set of distinct local sizes."""
    pairs = sizes_of(el)
    return {"first": pairs[:1], "distinct": sorted({s for _, s in pairs}, key=str)}


def para_spacing(el):
    return [te["paragraphMarker"]["style"].get("lineSpacing")
            for te in el["shape"].get("text", {}).get("textElements", []) if "paragraphMarker" in te][:2]


def body_of(pid, slide_id):
    return next(el for el in page(pid, slide_id)["pageElements"]
                if el.get("shape", {}).get("placeholder", {}).get("type") == "BODY")


def layout_body(pid, layout_id):
    return next(el for el in page(pid, layout_id)["pageElements"]
                if el.get("shape", {}).get("placeholder", {}).get("type") == "BODY")


def snapshot(pid, slide_id, oid):
    el = element(pid, slide_id, oid)
    return {"autofit": autofit_of(el), "sizes": resolved_sizes(el), "lineSpacing": para_spacing(el)}


def q10():
    """Inherited shrink: what the slide shape reports, and what each kind of
    first request does to it."""
    res = {}

    def fresh():
        pid = import_pptx(INHERITED_PPTX, f"slides-probe inherited {time.strftime('%H%M%S')}")
        pres = SL.presentations().get(presentationId=pid).execute()
        return pid, pres

    pid, pres = fresh()
    res["deck"] = pid
    for i, sl in enumerate(pres["slides"]):
        sid = sl["objectId"]
        body = body_of(pid, sid)
        lb = layout_body(pid, sl["slideProperties"]["layoutObjectId"])
        res[f"slide{i}_read"] = {
            "slide_autofit": autofit_of(body),
            "slide_has_local_autofit_key": "autofit" in body["shape"].get("shapeProperties", {}),
            "layout_autofit": autofit_of(lb),
            "placeholder_parent": body["shape"]["placeholder"].get("parentObjectId") == lb["objectId"],
            "sizes": resolved_sizes(body),
            "lineSpacing": para_spacing(body),
            "layout_sizes": resolved_sizes(lb),
        }

    # Each experiment on a fresh deck so no earlier edit has frozen anything.
    experiments = {
        "explicit_none_first": lambda oid: [
            {"updateShapeProperties": {"objectId": oid, "shapeProperties": {"autofit": {"autofitType": "NONE"}},
                                       "fields": "autofit.autofitType"}}],
        "insert_text": lambda oid: [{"insertText": {"objectId": oid, "insertionIndex": 0, "text": "X"}}],
        "size_only_style": lambda oid: [
            {"updateTextStyle": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 4},
                                 "style": {"fontSize": {"magnitude": 30, "unit": "PT"}}, "fields": "fontSize"}}],
        "none_then_size": lambda oid: [
            {"updateShapeProperties": {"objectId": oid, "shapeProperties": {"autofit": {"autofitType": "NONE"}},
                                       "fields": "autofit.autofitType"}},
            {"updateTextStyle": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 4},
                                 "style": {"fontSize": {"magnitude": 30, "unit": "PT"}}, "fields": "fontSize"}}],
        "alt_text_only": lambda oid: [
            {"updatePageElementAltText": {"objectId": oid, "title": "t", "description": "d"}}],
    }
    for name, reqs in experiments.items():
        pid, pres = fresh()
        out = {"deck": pid}
        for i, sl in enumerate(pres["slides"][:2]):
            sid = sl["objectId"]
            oid = body_of(pid, sid)["objectId"]
            before = snapshot(pid, sid, oid)
            try:
                batch(pid, reqs(oid))
                out[f"slide{i}"] = {"before": before, "after": snapshot(pid, sid, oid)}
            except HttpError as e:
                out[f"slide{i}"] = {"before": before, "error": str(e).split('returned "')[-1][:200]}
        res[name] = out

    # Local shrink (q2c fixture): style-only size first, and explicit NONE first.
    for name, reqs in [("local_size_only_style", experiments["size_only_style"]),
                       ("local_explicit_none_first", experiments["explicit_none_first"]),
                       ("local_alt_text_only", experiments["alt_text_only"])]:
        pid2 = import_pptx(PPTX, f"slides-probe local {name}")
        sid = SL.presentations().get(presentationId=pid2).execute()["slides"][0]["objectId"]
        oid = next(e for e in page(pid2, sid)["pageElements"]
                   if autofit_of(e).get("autofitType") == "TEXT_AUTOFIT")["objectId"]
        before = snapshot(pid2, sid, oid)
        batch(pid2, reqs(oid))
        res[name] = {"deck": pid2, "before": before, "after": snapshot(pid2, sid, oid)}
    return res


def q11():
    """Real decks, counts only: how often does a slide text shape inherit
    TEXT_AUTOFIT from its placeholder parent, and is a scale ever readable there?"""
    files = DR.files().list(
        q="mimeType='application/vnd.google-apps.presentation' and trashed=false",
        pageSize=30, orderBy="modifiedTime desc", fields="files(id)",
        supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives",
    ).execute()["files"]
    fields = ("slides(pageElements(objectId,shape(placeholder(parentObjectId),text(textElements(endIndex)),"
              "shapeProperties(autofit)))),layouts(pageElements(objectId,shape(shapeProperties(autofit)))),"
              "masters(pageElements(objectId,shape(shapeProperties(autofit))))")
    out = {"decks": 0, "unset_on_slide": 0, "unset_with_parent": 0, "inherits_shrink": 0,
           "inherits_grow": 0, "inherits_none": 0, "parent_unset_too": 0, "decks_inheriting_shrink": 0}
    for f in files:
        try:
            p = SL.presentations().get(presentationId=f["id"], fields=fields).execute()
        except HttpError:
            continue
        out["decks"] += 1
        parents = {}
        for pg in p.get("layouts", []) + p.get("masters", []):
            for el in pg.get("pageElements", []):
                parents[el["objectId"]] = el.get("shape", {}).get("shapeProperties", {}).get("autofit", {}).get("autofitType")
        deck_has = False
        for sl in p.get("slides", []):
            for el in sl.get("pageElements", []):
                sh = el.get("shape")
                if not sh or "text" not in sh:
                    continue
                if sh.get("shapeProperties", {}).get("autofit", {}).get("autofitType"):
                    continue
                out["unset_on_slide"] += 1
                parent = sh.get("placeholder", {}).get("parentObjectId")
                if not parent:
                    continue
                out["unset_with_parent"] += 1
                t = parents.get(parent)
                if t == "TEXT_AUTOFIT":
                    out["inherits_shrink"] += 1
                    deck_has = True
                elif t == "SHAPE_AUTOFIT":
                    out["inherits_grow"] += 1
                elif t == "NONE":
                    out["inherits_none"] += 1
                else:
                    out["parent_unset_too"] += 1
        out["decks_inheriting_shrink"] += deck_has
    return out



IDENTITY = {"scaleX": 1, "scaleY": 1, "shearX": 0, "shearY": 0, "translateX": 0, "translateY": 0, "unit": "EMU"}


def identity_trigger(oid):
    return {"updatePageElementTransform": {"objectId": oid, "applyMode": "RELATIVE", "transform": IDENTITY}}


def geometry(el):
    return {"transform": el["transform"], "size": el.get("size")}


def q12():
    """Is an identity RELATIVE transform a pure freeze trigger: does it bake the
    scale like an automatic freeze, and leave geometry unchanged? Then, are later
    requests in the batch in displayed (baked) units?"""
    res = {}
    for label, path in [("local", PPTX), ("imported_inherited", INHERITED_PPTX)]:
        pid = import_pptx(path, f"slides-probe identity {label}")
        pres = SL.presentations().get(presentationId=pid).execute()
        sid = pres["slides"][0]["objectId"]
        el = next(e for e in page(pid, sid)["pageElements"]
                  if autofit_of(e).get("autofitType") == "TEXT_AUTOFIT")
        oid = el["objectId"]
        before = {**snapshot(pid, sid, oid), **geometry(el)}
        batch(pid, [identity_trigger(oid),
                    {"updateTextStyle": {"objectId": oid, "textRange": {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 4},
                                         "style": {"fontSize": {"magnitude": 30, "unit": "PT"}}, "fields": "fontSize"}}])
        after_el = element(pid, sid, oid)
        res[label] = {"deck": pid, "before": before,
                      "after": {**snapshot(pid, sid, oid), **geometry(after_el)}}
    # Grow shape: identity trigger also freezes it; is the box size unchanged?
    pid = import_pptx(PPTX, "slides-probe identity grow")
    sid = SL.presentations().get(presentationId=pid).execute()["slides"][0]["objectId"]
    el = next(e for e in page(pid, sid)["pageElements"] if autofit_of(e).get("autofitType") == "SHAPE_AUTOFIT")
    batch(pid, [identity_trigger(el["objectId"])])
    el2 = element(pid, sid, el["objectId"])
    res["grow"] = {"deck": pid, "before": {"autofit": autofit_of(el), **geometry(el)},
                   "after": {"autofit": autofit_of(el2), **geometry(el2)}}
    return res


def q13(source_deck_id=None):
    """Native inherited shrink, on a COPY of a real deck (the original is never
    written). Finds a slide text shape with no local autofit whose placeholder
    parent is TEXT_AUTOFIT, then compares an identity trigger with an insertText.
    Reports numbers only, no text."""
    if source_deck_id is None:
        files = DR.files().list(
            q="mimeType='application/vnd.google-apps.presentation' and trashed=false",
            pageSize=100, orderBy="modifiedTime desc", fields="files(id)",
            supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives",
        ).execute()["files"]
    else:
        files = [{"id": source_deck_id}]
    fields = ("slides(objectId,pageElements(objectId,shape(placeholder(parentObjectId),text(textElements(endIndex)),"
              "shapeProperties(autofit)))),layouts(pageElements(objectId,shape(shapeProperties(autofit)))),"
              "masters(pageElements(objectId,shape(shapeProperties(autofit))))")
    for f in files:
        try:
            p = SL.presentations().get(presentationId=f["id"], fields=fields).execute()
        except HttpError:
            continue
        parents = {}
        for pg in p.get("layouts", []) + p.get("masters", []):
            for el in pg.get("pageElements", []):
                parents[el["objectId"]] = el.get("shape", {}).get("shapeProperties", {}).get("autofit", {})
        targets = []
        for sl in p.get("slides", []):
            for el in sl.get("pageElements", []):
                sh = el.get("shape") or {}
                if "text" not in sh or sh.get("shapeProperties", {}).get("autofit", {}).get("autofitType"):
                    continue
                par = parents.get(sh.get("placeholder", {}).get("parentObjectId"), {})
                scaled = sh.get("shapeProperties", {}).get("autofit", {}).get("fontScale", 1) < 1
                if par.get("autofitType") == "TEXT_AUTOFIT" and scaled:
                    targets.append((sl["objectId"], el["objectId"], par))
        if not targets:
            continue
        try:
            copy = DR.files().copy(fileId=f["id"], body={"name": "slides-probe native-inherited COPY"},
                                   supportsAllDrives=True, fields="id").execute()["id"]
            break
        except HttpError as e:
            skipped = getattr(q13, "skipped", 0) + 1
            q13.skipped = skipped
            continue
    else:
        return {"error": "no copyable deck with a natively inherited shrink",
                "uncopyable_candidates": getattr(q13, "skipped", 0)}
    res = {"copy": copy, "candidates": len(targets), "uncopyable_skipped": getattr(q13, "skipped", 0), "shapes": []}
    try:
        # Two shapes: one identity-triggered, one insertText-triggered.
        for (sid, oid, par), how in zip(targets[:2], ["identity", "insert_text"]):
            el = element(copy, sid, oid)
            row = {"how": how, "parent_autofit": par, "slide_shape_lineSpacing_before": para_spacing(el),
                   "slide_shape_shapeProperties_autofit": el["shape"].get("shapeProperties", {}).get("autofit"),
                   "before": {"sizes": resolved_sizes(el)["distinct"], "lineSpacing": para_spacing(el)}}
            req = identity_trigger(oid) if how == "identity" else {
                "insertText": {"objectId": oid, "insertionIndex": 0, "text": "X"}}
            batch(copy, [req])
            el2 = element(copy, sid, oid)
            row["after"] = {"autofit": autofit_of(el2), "sizes": resolved_sizes(el2)["distinct"],
                            "lineSpacing": para_spacing(el2)}
            res["shapes"].append(row)
    finally:
        DR.files().update(fileId=copy, body={"trashed": True}).execute()
        res["copy_trashed"] = True
    return res



def q14():
    """Which other requests trigger the freeze, and does replaceAllText apply
    pre- or post-bake, with and without an identity trigger first?"""
    res = {}
    cases = {
        "fill_only": lambda oid: [{"updateShapeProperties": {"objectId": oid, "shapeProperties": {
            "shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": {"blue": 1}}}}}, "fields": "shapeBackgroundFill.solidFill.color"}}],
        "create_bullets": lambda oid: [{"createParagraphBullets": {"objectId": oid, "textRange": {"type": "ALL"}}}],
        "replace_all_text": lambda oid: [{"replaceAllText": {"containsText": {"text": "Line 0 with", "matchCase": True},
                                                             "replaceText": "Row zero has"}}],
        "identity_then_replace_all_text": lambda oid: [identity_trigger(oid), {"replaceAllText": {
            "containsText": {"text": "Line 0 with", "matchCase": True}, "replaceText": "Row zero has"}}],
    }
    for name, reqs in cases.items():
        pid = import_pptx(PPTX, f"slides-probe trigger {name}")
        sid = SL.presentations().get(presentationId=pid).execute()["slides"][0]["objectId"]
        oid = next(e for e in page(pid, sid)["pageElements"]
                   if autofit_of(e).get("autofitType") == "TEXT_AUTOFIT")["objectId"]
        before = snapshot(pid, sid, oid)
        batch(pid, reqs(oid))
        res[name] = {"before": before, "after": snapshot(pid, sid, oid)}
    return res


if __name__ == "__main__":
    main()
