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
| `get_presentation` | deck | Slide IDs, titles, element counts, notes state, layouts |
| `get_page` | page content | Every element with ID, all text, cells, notes, image URLs |
| `get_page_markup` | element/page IR | Editable projection, whole page or scoped to elements; `raw` for the `Page` resource |
| `edit_page` | write | Applies edited markup back, to one or more pages |
| `replace_text` | write | Deck-wide find and replace |
| `batch_update_presentation` | write | Escape hatch: raw requests behind a required revision guard |
| `render_page_to_image` | visual | Rendered page as image content |
| `create_presentation` | structure | New deck |
| `add_slide` | structure | New slide from a layout, placeholders optionally filled, in one write |
| `duplicate_slide`, `delete_slide`, `move_slide` | structure | Deck-level slide operations |
| `find_in_presentation` | search | Follow-up, not v1. Slide and element IDs with snippets for a literal match |

Replaced and removed by this design: `get_page` (old text/detail renderer),
`get_page_thumbnail`, and the `include_geometry` / `include_styles` / `raw`
flag set on `get_page` and `get_presentation`. `batch_update_presentation` is
kept as the escape hatch, with its revision guard made mandatory.

Removing `include_styles` drops deck-wide style proofing on purpose (see
*Decisions*, 5). `include_geometry`'s other job, finding a deck's margins
before adding elements, is covered by `get_page_markup`, whose boxes are in
points.

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
   layout p2  2 shapes  notes: 142 chars
2. g1a2b3c4d5_1_0  Checkout conversion is up 12% QoQ
   layout p3  1 shape  notes: -
3. g1a2b3c4d5_2_0  (no title)
   layout p11  1 table (3x3)  notes: -
4. g1a2b3c4d5_3_0  (no title)
   layout p11  1 chart, 1 image  notes: -

Layouts:
  p2   Title slide      CENTERED_TITLE, SUBTITLE   slide 1
  p3   Title and body   TITLE, BODY                slide 2
  p4   Section header   TITLE, BODY                -
  p5   Two columns      TITLE, BODY:1, BODY:2      -
  p11  Blank            -                          slides 3-4
```

Titles come from the title placeholder when the slide has one, otherwise from the
first text-bearing element in z-order. The fallback matters because decks built
through this MCP are bare text boxes with no placeholder.

The revision ID is included so a later `edit_page` can be guarded without a
second read.

**Layouts.** Each slide line names its layout, and a `Layouts` section lists
every layout once: ID, `layoutProperties.displayName`, the placeholder types it
provides, and the slides that use it. This is what `add_slide` needs, because a
slide's placeholders can only come from its layout, and an opaque ID such as
`p3` does not say which layout is "Title and body".

- Placeholder types are listed in the layout's z-order. When a type appears more
  than once, each carries its `placeholder.index` (`BODY:1`, `BODY:2`), which is
  the key `add_slide` uses.
- With one master, the master is not mentioned. With several, layouts are
  grouped under a `Master <id>  <displayName>` heading, because a new slide can
  only use a layout of the master current at its position (see `add_slide`).
- Layout and master content is not shown. `get_page_markup` accepts a layout
  ID for that.
- Corporate templates can carry 20 to 40 layouts. The cost of the section is
  part of the projection measurement. If it is too large, the fallback is
  unused layouts by ID and name only, without placeholder types.

## `add_slide`

New slide from a layout, with its placeholders optionally filled, in one
write.

**Input**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `presentation_id` | string | yes | |
| `layout_id` | string | no | A layout ID from `get_presentation`. Default: the layout of the slide it follows, the commonest choice ("another one like this"). |
| `after` | string | no | Page ID to insert after, or `"start"`. Default: the end. A page ID, not an index, because indices shift as slides are added. |
| `text` | object | no | Placeholder key to text body, e.g. `{"TITLE": "Q4 plan", "BODY": ["One", "Two"]}`. Keys are the layout's placeholder types, with `:index` when a type repeats. A text body is the same shape as in `get_page_markup`. |

**Behaviour**

- Compiles to one `createSlide` with `slideLayoutReference.layoutId` and
  `placeholderIdMappings` giving every placeholder a server-generated ID, then
  `insertText` for each `text` entry against those IDs, in the same batch and
  behind the revision guard. One call, one atomic write.
- The API resolves a layout against the master current at the insertion point:
  the master of the preceding slide, the first slide's master when inserting at
  the start, or the first master when the deck is empty. A layout from another
  master is a 400 at write time, so it is refused before writing, with the
  layouts the current master does have.
- A `text` key the layout does not provide is refused, listing the layout's
  placeholder keys. New text follows carry rule 5 of
  `docs/slides-edit-workflow.md`: it takes placeholder and theme defaults.
- `predefinedLayout` is not accepted. The API does not guarantee that a
  predefined layout exists in a given master, and the deck's own layouts are
  already listed.

**Output body**

The receipt, followed by the new slide's `get_page_markup` projection, so a
further `edit_page` needs no read:

```
added slide g1a2b3c4d5_4_0 at position 3 (after g1a2b3c4d5_1_0)  layout p3 Title and body
revision AKXw... -> AKXx...
```

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

## `find_in_presentation`

Follow-up after v1, not part of it. Deck-wide literal search, so that "which
slide mentions pricing" does not cost a read of every slide.

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `presentation_id` | string | yes | |
| `query` | string | yes | Literal text |
| `match_case` | boolean | no | Default false |
| `include_notes` | boolean | no | Default true |

The Slides API has no search, and Drive full-text search finds files, not
slides. The server fetches the whole deck in one `presentations.get` and
matches server side, so the agent pays only for the hits:

```
3 matches for "pricing" on 2 slides
slide 2  g1a2b3c4d5_1_0  shape g1a2b3c4d5_1_2 (BODY)  "...the new pricing page converts..."
slide 5  g1a2b3c4d5_4_0  table g1a2b3c4d5_4_1 [2,1]  "Pricing"
slide 5  notes g1a2b3c4d5_4_notes_shape  "...ask about pricing before..."
```

Element IDs feed straight into `get_page_markup` `targets`, skipping
`get_page`. Matching is the same code `replace_text` uses to report where its
matches are, so with the same `match_case` the two tools always agree on what
matches. Hits are capped (50), with a count of the rest.

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

## Decisions

Formerly open questions, all settled.

1. **Notes come with the page fetch.** Across 16 live `pages.get` responses, every
   embedded `notesPage` carried `objectId`, `pageType`, `pageProperties`,
   `notesProperties` and `pageElements` (2 elements each), with the speaker
   notes shape present in all 16. Notes therefore ride along in the same fetch,
   and `notesProperties.speakerNotesObjectId` is the writable target. Rendering
   a notes page also succeeds, so `render_page_to_image` needs no special case.
2. **`get_presentation` lists layouts, not their content.** Slide IDs alone
   were the earlier answer, but `add_slide` must pick a layout, placeholders
   come only from a layout, and a layout ID does not say what the layout is.
   Rejected: a separate `list_layouts` tool (a schema in every session and an
   extra call before each `add_slide`) and full layout and master content
   (expensive, and layout editing is a non-goal). See *Layouts* under
   `get_presentation`.
3. **`get_page` stays flagless.** The condition was that element-scoped
   `get_page_markup` carries the context to interpret a nested element. It
   does: a target inside a group comes back with absolute geometry and its
   group ID. Rejected: `targets` on `get_page`, which would duplicate
   `get_page_markup`'s scoping and break the guarantee that a digest shows
   everything on the page.
4. **Deck-wide search is a separate tool, `find_in_presentation`, after v1.**
   Rejected: a `query` parameter on `get_presentation` (a dial on a flagless
   read, and a filtered deck view is hard to describe), and `replace_text` with
   a `dry_run` as search (agents doing read-only tasks would not look under
   "replace").
5. **No proofing tool in v1.** Geometry for placing new elements is covered by
   `get_page_markup`. Deck-wide style proofing, which `include_styles` gave,
   is dropped on purpose: use `get_page_markup` per slide for exact values, or
   `render_page_to_image` (about 450 tokens a slide at 800 px), which also
   shows inherited styling. The PR description says so, since existing callers
   lose it. Revisit a dedicated tool (distinct font, size and colour
   combinations with counts and locations, plus off-margin boxes) once the
   projection's per-slide cost is measured.
