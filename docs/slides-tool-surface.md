# Slides Tool Surface (Design)

Status: draft, not implemented.

## Problem

Reading a Google Slides deck through this MCP is expensive in tokens, and the
expensive path is not the one anyone would choose deliberately:

1. An agent asks for a deck. The flat text dump it gets back has no element IDs,
   no title/body distinction, and reports visual-only slides as `text: empty`.
2. It cannot act on what it read, and cannot see what it could not read.
3. It exports the deck to PPTX, rasterises it, and reads every slide with vision.

Three measurements from the current implementation motivate the redesign. All
are from a synthetic 24-slide deck run through the real formatters, counted with
`o200k_base`:

| Current path | Tokens |
| --- | --- |
| `get_presentation` (flat text dump, no IDs) | 4,013 |
| `get_presentation` + `get_page` for all 24 slides | 9,125 |
| `get_page(include_styles=True)` - text emitted twice | 9,646 |
| `get_page(raw=True)`, one trivial unstyled slide | 746 |

The API exposes a complete JSON model of every slide, and `get_page(raw=True)`
already returns it. So the problem is not a missing representation. It is that
the representation available by default is lossy in the ways that matter for
acting on the deck, and the complete one is only reachable in a way that costs
more than it saves.

## Principles

1. **The Page resource is the intermediate representation.** No invented format.
   The read body is the Slides API `Page` resource; the write body is the same
   resource, edited. Google's documentation is the specification, so the surface
   does not drift as the API grows.
2. **Progressive disclosure across tools, not flags.** Three rungs of detail,
   each a separate tool whose name says which rung it is. A tool's name and first
   description line are the entire steering surface an agent sees, so the ladder
   has to be legible from a tool listing.
3. **Scope is inferred from the payload.** An edit touches exactly the elements
   present in the submitted markup. An agent that fetched one element can edit
   that element without ever holding the rest of the page.

## Tool surface

| Tool | Rung | Returns |
| --- | --- | --- |
| `get_presentation` | deck | Slide IDs, titles, element counts, notes state |
| `get_page` | page content | Every element with ID, all text, cells, notes, image URLs |
| `get_page_markup` | element/page IR | Editable projection, whole page or scoped to elements; `raw` for the `Page` resource |
| `edit_page` | write | Applies edited markup back, to one or more pages |
| `replace_text` | write | Deck-wide find and replace |
| `batch_update_presentation` | write | Escape hatch: raw requests behind a required revision guard |
| `render_page_to_image` | visual | Rendered page as image content |
| `create_presentation` | structure | New deck |
| `add_slide`, `duplicate_slide`, `delete_slide`, `move_slide` | structure | Deck-level slide operations |

Replaced and removed by this design: `get_page` (old text/detail renderer),
`get_page_thumbnail`, and the `include_geometry` / `include_styles` / `raw`
flag set on `get_page`. `batch_update_presentation` is kept as the escape hatch,
with its revision guard made mandatory.

The write path, `get_page_markup`'s output and `replace_text` are specified in
`docs/slides-edit-workflow.md`, which supersedes the corresponding sections
below.

## `get_page`

Content digest. Lossless on content, lossy on presentation: every element
appears with its ID, all text appears in full, and no geometry, font, colour or
z-order detail appears at all. This is the default read.

**Input**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `presentation_id` | string | yes | |
| `page_id` | string | yes | The page's `objectId` |

No other parameters. The tool deliberately has no dial.

**Output body**

```
page g1a2b3c4d5_1_0  SLIDE  2 elements  revision abc123

- shape g1a2b3c4d5_1_1  TEXT_BOX  placeholder=TITLE
  Checkout conversion is up 12% QoQ
- shape g1a2b3c4d5_1_2  TEXT_BOX  placeholder=BODY
  - Conversion improved 12% QoQ, driven by the address autocomplete change
  - Mobile web still trails the app by 6 points on the same funnel
  - Recommendation: ship the wallet-backed form to mobile web in Q1

- table g1a2b3c4d5_2_1  3x3
  [0,0] Segment    [0,1] Conversion  [0,2] Delta vs Q3
  [1,0] iOS app    [1,1] 18.4%       [1,2] +2.1 pts

- chart g1a2b3c4d5_3_1  spreadsheet=1AbCdEfGh  chart=148230971
- image g1a2b3c4d5_3_2  src=https://example.com/chart.png  url=https://lh7-us.googleusercontent.com/...

notes shape g1a2b3c4d5_1_notes_shape:
  Frame the ask: one decision, two options. Keep to 15 minutes.
```

Rules:

- Elements appear in z-order, recursing into `elementGroup.children`.
- Every element kind needs a header line so that completeness is checkable from
  the output alone: `shape`, `table`, `image`, `chart`, `line`, `wordArt`,
  `video`, `speakerSpotlight`, `group` (as a container with its children
  nested), plus an explicit `unknown` fallback. A shape with no text still gets
  its header. No element is ever silently omitted.
- Text is never truncated. Paragraph structure and bullet nesting are preserved
  because they are content; inline emphasis, fonts and colours are omitted
  because they are presentation.
- WordArt reports `renderedText`; autotext reports its type and rendered content
  (slide numbers are content).
- Table cell text is always included. Cells are keyed by `cell.location`
  (`rowIndex`, `columnIndex`), not by array position, and carry `rowSpan` /
  `columnSpan` when merged. One cell per line, so multi-line cell text cannot be
  confused with the next cell.
- Images carry `src=` (`sourceUrl`) when present and `url=` (`contentUrl`)
  always. `contentUrl` has a 30-minute lifetime and is tagged to the requesting
  account, so a sandboxed client may not be able to fetch it; that caveat belongs
  in the tool description.
- Alt text appears only when non-empty. Empty alt is not reported.
- `notes` appears only when notes exist, with the notes shape ID needed to write
  them.
- Visual content that is inherited rather than local — page background, and
  anything drawn by the layout or master — has no element ID on the slide. The
  digest states that such content exists and that rendering is the only way to
  see it, so "no more IDs" is not misread as "nothing else is visible".

**Invariant.** Parity is defined over the elements the slide owns: the set of
`objectId`s in the digest equals the set in `get_page_markup` for the same page
at the same revision, with notes-page elements accounted for separately because
they live under `slideProperties.notesPage` rather than `pageElements`. This is
the property that makes the digest trustworthy as a "you have seen everything"
signal, and it is the regression test for the original bug.

## `get_presentation`

Deck digest. No slide content beyond the title.

**Input**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `presentation_id` | string | yes | |

**Output body**

```
Presentation: Mobile Checkout Refresh - Q4 Readout
ID: 1xYz_MobileCheckoutQ4Readout
URL: https://docs.google.com/presentation/d/1xYz_MobileCheckoutQ4Readout/edit
Slides: 4   Page size: 9144000 x 5143500 EMU   Revision: abc123

1. g1a2b3c4d5_0_0  Mobile Checkout Refresh
   2 shapes  notes: 142 chars
2. g1a2b3c4d5_1_0  Checkout conversion is up 12% QoQ
   1 shape  notes: -
3. g1a2b3c4d5_2_0  (no title)
   1 table (3x3)  notes: -
4. g1a2b3c4d5_3_0  (no title)
   1 chart, 1 image  notes: -
```

Titles come from the title placeholder when the slide has one, otherwise from the
first text-bearing element in z-order. The fallback matters because decks built
through this MCP are bare text boxes with no placeholder.

The revision ID is included so a later `edit_page` can be guarded without a
second read.

## `get_page_markup`

> **Superseded.** `get_page_markup` returns an index-free projection by
> default, with the `Page` resource below only under `raw=true`, and the scope
> parameter is `targets`. See `docs/slides-edit-workflow.md`. This section
> still describes the `raw` output.

The `Page` resource, for editing.

**Input**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `presentation_id` | string | yes | |
| `page_id` | string | yes | |
| `element_object_ids` | string[] | no | Scope the response to these elements and their descendants |

**Output body**

```json
{
  "presentationId": "1xYz_MobileCheckoutQ4Readout",
  "pageSize": {
    "width":  {"magnitude": 9144000, "unit": "EMU"},
    "height": {"magnitude": 5143500, "unit": "EMU"}
  },
  "page": {
    "objectId": "g1a2b3c4d5_1_0",
    "pageType": "SLIDE",
    "revisionId": "abc123",
    "slideProperties": {"layoutObjectId": "LAYOUT_1", "masterObjectId": "MASTER_1"},
    "pageElements": []
  }
}
```

`page` is the API resource unmodified, except that `pageElements` is pruned to
the requested scope. `pageSize` is included because element `transform` and
`size` are raw EMU against it, and without it those numbers cannot be
interpreted. `revisionId` is included so `edit_page` can be guarded.

When `element_object_ids` is given, the response carries the requested elements
and their descendants only. Everything else on the page is absent, so an agent
can retrieve and rewrite a single text box without ever holding the page.

## `edit_page`

> **Superseded.** The write path is redesigned in
> `docs/slides-edit-workflow.md`, which replaces the markup-in/resource-out
> contract below with an index-free projection, per-element version hashes
> instead of a revision-only guard, content-aligned text diffing, and a
> compile-then-verify write. Its field matrix replaces the diff-coverage table
> below. This section is kept for the API constraints it records (notes,
> groups, `size`, splicing), which still apply.

Applies edited markup.

**Input**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `presentation_id` | string | yes | |
| `page_id` | string | yes | |
| `markup` | object | yes | A body of the shape `get_page_markup` returns |
| `delete_object_ids` | string[] | no | Elements to delete, validated against the page |
| `dry_run` | boolean | no | Return the requests that would be applied, without applying |

**Scope.** Only elements present in `markup.page.pageElements` are touched.
Everything else on the page is untouched, by construction. An element in the
payload whose `objectId` is not on the page is created, using the
caller-assigned ID (create requests accept one; IDs must be unique
presentation-wide). Omission never deletes; deletion is explicit through
`delete_object_ids`, because an absent element is otherwise indistinguishable
from one the caller did not fetch.

Scope does not cross a group boundary cleanly, and the contract has to say so. A
child element's `transform` is relative to its ancestors, so a child returned
without its ancestors cannot be positioned or interpreted, and hoisting it into
`pageElements` changes what the resource means. Deleting one child of a
two-child group deletes the group itself; deleting a group deletes its
descendants. Either preserve ancestor context in scoped responses or require the
containing group to be the unit of scope, and report the deletion cascade in
`dry_run`.

Notes sit outside `pageElements`, under `slideProperties.notesPage`. They are an
explicitly allowed nested scope: always reported by `get_page_markup`, written
by replacing the text of the BODY shape named by
`notesProperties.speakerNotesObjectId`, never by `createShape` — and the shape ID
can change after it is created implicitly, so it must be re-read rather than
cached. Skip the `deleteText` half when the notes are already empty, which is an
API error.

**Write guard.** `markup.page.revisionId` is passed as
`writeControl.requiredRevisionId`. If the deck changed since the read, the API
rejects the write with 400 rather than applying a stale edit. `presentation_id`
and `page_id` are validated against the body so a mismatched pair cannot target
the wrong page.

The revision ID is not always available: it is populated only when the caller
has edit access to the presentation, and it is user-specific, opaque, and only
guaranteed valid for 24 hours. Live sampling of 16 pages found it present on 13
and absent on 3. A missing revision must fail the edit with a clear message
rather than silently dropping the guard. (Later sampling of 90 decks found the
documentation slightly wrong: it is present for comment access too, and absent
only for view-only access. See `docs/slides-edit-workflow.md`.)

**Text edits must be minimal splices, not clear-and-reinsert.** Clearing a
mixed-style text box deletes its runs, and reinserting text inherits styles from
the surrounding text rather than restoring them, so comparing unchanged style
fields against the original resource does not detect the damage. Consequences to
design for explicitly:

- Change only the runs whose content actually differs, recomputing UTF-16
  indices after each request, and verify per-run styles are preserved.
- Preserve `autoText` elements (slide numbers) rather than deleting over them.
- The shape's text always ends with an implicit newline; the last paragraph
  cannot be removed.
- `createParagraphBullets` strips leading tabs and shifts indices, and arbitrary
  bullet glyphs do not map onto preset names. Reject bullet changes the diff
  cannot express exactly.
- Reconstruction that cannot be done exactly must be rejected, not approximated.

**Diff coverage.** Fields that map to a request are diffed; read-only and derived
fields are ignored, never treated as edits:

| Markup | Requests |
| --- | --- |
| text run content | `deleteText` + `insertText`, as minimal splices (see above) |
| text run style | `updateTextStyle` |
| paragraph style, bullets | `updateParagraphStyle`, `createParagraphBullets`, `deleteParagraphBullets` |
| `transform` | `updatePageElementTransform` (only `objectId`, `transform` and `applyMode` are writable) |
| table cell text | `insertText` / `deleteText` with `cellLocation` |
| alt text (`title`, `description`) | `updatePageElementAltText` |
| notes text | `deleteText` + `insertText` on the speaker notes shape |
| element absent from page, present in markup | `createShape` / `createTable` / `createImage` |
| `delete_object_ids` | `deleteObject` |
| ignored | `layoutProperties`, `masterProperties`, `notesProperties`, computed autofit, `comments`, `revisionId` |

`size` is not directly writable. `updatePageElementTransform` accepts only
`objectId`, `transform` and `applyMode`, so a desired width and height has to be
converted into a scale factor against the element's intrinsic size. Tables
cannot be scaled or sheared at all and have to be resized through row and column
requests. Coordinates may be expressed in PT as well as EMU. The initial
implementation should reject a changed `size` with a clear message rather than
half-implement it.

A changed field with no mapping is **rejected before anything is submitted**, not
silently ignored. Ignoring is safe only for fields the API treats as read-only
and derived; fills, outlines, hyperlinks, image replacement and cropping, page
backgrounds, table structure, merges and borders, z-order, connectors and video
properties are all writable and all currently unmapped. Creating an element from
type, text and geometry alone also loses whatever the source element carried.
Each of these needs a decision: map it, or refuse the edit and say so.

Charts are not wholly read-only. Creation, refresh, positioning and deletion are
supported; only their content is out of reach, because it lives in Sheets.

**Output body**

```
Edit applied to page g1a2b3c4d5_1_0  revision abc123 -> def456
updated: shape g1a2b3c4d5_1_2
created: (none)
deleted: (none)
requests: 4
```

With `dry_run`, the same summary reports `would apply` and lists the requests.

## `render_page_to_image`

**Input**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `presentation_id` | string | yes | |
| `page_id` | string | yes | Any page type, including notes pages |
| `size` | string | no | `SMALL` (200px), `MEDIUM` (800px), `LARGE` (1600px, default), `WIDTH2000_PX` (2000px) |

These are the API's own enum values. `WIDTH2000_PX` is real but undocumented in
the current implementation.

The rendered PNG is returned as MCP image content, so it goes straight into the
model's vision path. Base64 is the MCP wire encoding for images either way; the
difference from returning a URL is that the client decodes it into an image
attachment instead of the model tokenising 130k characters of base64 as text.
Measured cost of the image itself, per slide: ~425-480 tokens at 800px,
~1,445-1,920 at 1600px, ~2,125-3,000 at 2000px.

## Round trips

> **Superseded** by *The loop* in `docs/slides-edit-workflow.md`; `edit_page`
> now takes `pages=[...]` and there is no separate `dry_run` pass in the loop.

**One element, without holding the page.** The common case for a formatting fix.

```
get_page_markup(presentation_id, page_id, element_object_ids=["shape_3"])
edit that element's JSON
edit_page(presentation_id, page_id, markup=<the body above>)
```

**Whole slide.** Same calls, no `element_object_ids`.

```
get_page_markup(presentation_id, page_id)
edit the page JSON
edit_page(presentation_id, page_id, markup=<the body above>, dry_run=True)
edit_page(presentation_id, page_id, markup=<the body above>)
```

## Non-goals

- Authoring a deck from markdown. Google has no deck-level markdown flow (Drive
  exports presentations as PPTX/ODP/PDF/plain text only, and the Slides API has
  no markdown handling), and markdown cannot express geometry, theme or
  placeholder inheritance. The Page resource is the representation instead.
- Layout and master editing. `get_page_markup` accepts any page ID, so layouts
  are readable, but editing them is out of scope.
- An SVG renderer. Google's own renderer is the ground truth for what a slide
  looks like; a second renderer that disagrees with it would be worse than none.

## Open questions

1. ~~Does the `notesPage` embedded in a real `pages.get` response carry
   `pageElements`?~~ **Settled.** Across 16 live `pages.get` responses, every
   embedded `notesPage` carried `objectId`, `pageType`, `pageProperties`,
   `notesProperties` and `pageElements` (2 elements each), with the speaker
   notes shape present in all 16. Notes therefore ride along in the same fetch,
   and `notesProperties.speakerNotesObjectId` is the writable target. Rendering
   a notes page also succeeds, so `render_page_to_image` needs no special case.
2. Should `get_presentation` cover layouts and masters, or slides only? Current
   answer: slides only, but expose layout and master IDs per slide, since they
   are what an agent needs to pick a template or explain an inherited style.
3. Is a flagless `get_page` acceptable? Current answer: yes, provided
   element-scoped `get_page_markup` carries the ancestor context needed to
   interpret a nested element.

New questions raised in review:

4. Should deck-wide content search exist? A title-only deck digest means
   searching for content costs one read per slide. A search tool returning slide
   and element IDs with snippets would remove that.
5. Compact style and geometry proofing was a real capability of the old surface
   (`include_geometry` / `include_styles`). Is it worth a separate tool, given
   that the alternative is a full `get_page_markup` per slide?
