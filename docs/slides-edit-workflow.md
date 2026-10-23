# Slides Edit Workflow (Design)

Status: draft, not implemented. Supersedes the `get_page_markup` and `edit_page`
sections of `docs/slides-tool-surface.md`.

## The loop

An agent edits a deck the way a person does: change something, look at the
result. There is no preview step, and no ceremony the agent will not run. So the
whole workflow is one read-modify-write loop, with a shortcut for the most common
edit:

| Step | Tool | Cost |
| --- | --- | --- |
| 0. Plain text swap? Stop here | `replace_text` | one call, no read |
| 1. Find the content | `get_page` | cheap digest, whole slide |
| 2. Fetch what will be edited | `get_page_markup` | one element, or one page |
| 3. Edit the JSON in context | — | no tool call |
| 4. Apply | `edit_page` | one call, one atomic write |
| 5. Look at the result | `get_page` or `render_page_to_image` | optional |

Step 4 returns a receipt that says what changed, whether a read-back matched, and
the version tokens needed to edit the same elements again. So step 5 is a choice
rather than a requirement, and a second edit needs no second read.

## Walkthrough

**Step 1.** `get_page(presentation_id, page_id)`:

```
page g1a2b3c4d5_1_0  SLIDE  2 elements  revision AKXv...

- shape g1a2b3c4d5_1_1  TEXT_BOX  placeholder=TITLE
  Checkout conversion is up 12% QoQ
- shape g1a2b3c4d5_1_2  TEXT_BOX  placeholder=BODY
  - iOS app conversion is 18.4%, up 2.1 points
  - Mobile web trails at 12.4%
```

**Step 2.** `get_page_markup(presentation_id, page_id, targets=["g1a2b3c4d5_1_1"])`:

```json
{
  "page_id": "g1a2b3c4d5_1_0",
  "page_size": {"w": 720, "h": 405},
  "styles": {
    "s1": {"font": "Google Sans", "size": 32, "color": "#202124"}
  },
  "elements": [
    {
      "id": "g1a2b3c4d5_1_1",
      "v": "7f3a9c01",
      "kind": "shape",
      "shape": "TEXT_BOX",
      "placeholder": "TITLE",
      "box": {"x": 54, "y": 31.5, "w": 612, "h": 78.75},
      "text": [
        {"text": "Checkout conversion is up 12% QoQ", "s": "s1"}
      ]
    }
  ]
}
```

**Step 3.** The agent edits the JSON: 12% becomes 14%, and "Checkout" is bolded.

```json
      "text": [
        {"runs": [
          {"text": "Checkout", "s": "s1", "bold": true},
          " conversion is up 14% QoQ"
        ]}
      ]
```

The second run is a bare string, so it keeps the style the text already had. The
agent never copies a style object it did not mean to change, never sees or
computes a text index, and never emits a request.

**Step 4.** `edit_page(presentation_id, pages=[<the edited body>])`:

```
applied  revision AKXv... -> AKXw...  4 requests  verified
page g1a2b3c4d5_1_0
  g1a2b3c4d5_1_1 (TITLE)  v=c41e0b77
    text   "...is up 12% QoQ" -> "...is up 14% QoQ"
    style  bold on "Checkout"
  1 other element on this page unchanged (omitting an element never deletes it)
```

**Step 5.** Optional. Re-read the digest, or render the page, to see the result
the way a person would.

## The edit payload is a projection, not the raw resource

This is the one place the design departs from "the raw Page resource is the IR"
in `slides-tool-surface.md`, and it is deliberate. In the raw resource, text is a
flat array of `textElements`, each carrying `startIndex` and `endIndex`, and
geometry is an affine matrix in EMU against an intrinsic size. Asking a model to
edit text and renumber indices, or to resize a box by solving for a scale
factor, is asking it to do exactly the arithmetic that produces silent
corruption. So the payload the agent edits is **index-free and matrix-free**.

The projection is lossless for the writable subset (see the field matrix below).
Everything it omits — shadows, image crop, chart data, page backgrounds — is
carried through untouched by the server because no emitted request touches it.

### Page body

| Field | Meaning |
| --- | --- |
| `page_id` | The page. Required on write. |
| `page_size` | In points. A 16:9 deck is 720 x 405. |
| `styles` | Text style table referenced by runs (below). Only styles used by the returned elements are included. |
| `elements` | Elements in z-order, bottom first. |
| `notes` | Speaker notes body, when fetched. |
| `delete` | Write only. Element IDs to delete. |

### Units and geometry

All lengths are points, rounded to 0.01 pt. Points are one-fifth the digits of
EMU and are what the Slides editor shows; the server converts at 12,700 EMU per
point. A value left unchanged is compared in rounded points, so a read-write
round trip never rewrites geometry.

`box` is `{"x", "y", "w", "h"}`: the element's unrotated bounding box on the
page, as the editor's size-and-position panel shows it, plus `"rot"` (degrees,
clockwise, about the box centre) when non-zero and `"flip": "h" | "v" | "hv"`
when mirrored. The server owns the conversion to and from the affine transform:

- Read: decompose the element's absolute transform (its own transform
  pre-concatenated with every ancestor group's) into translate, scale, rotation
  and flip. Displayed width is `size.width * |scaleX|` for an unrotated element.
- Write: rebuild the matrix from the edited box, keeping the rotation centre at
  the box centre, convert to the element's parent coordinate space if it is in a
  group, and emit `updatePageElementTransform` with `applyMode: ABSOLUTE`.
- An element whose matrix does not decompose into scale, rotation and flip (a
  true skew) is projected with `"skewed": true`. Its `x` and `y` remain writable
  (a translation keeps the linear part); `w`, `h` and `rot` are read-only.
- Tables cannot be scaled. A table's `w` and `h` are read-only and derived; it
  is resized through `col_widths` and `row_heights`.

### Text

A text body (`text` on a shape, a table cell, or `notes`) is a list of
paragraphs. The shapes are chosen so that the common case is a bare string:

```json
"text": [
  "A plain paragraph",
  {"text": "A one-run paragraph in style s2", "s": "s2", "align": "CENTER"},
  {"runs": ["Mixed ", {"text": "bold", "bold": true}, " runs"],
   "bullet": {"list": "L1", "level": 0}}
]
```

- A **paragraph** is a string, `{"text", "s", ...props}` for a single run, or
  `{"runs": [...], ...props}`.
- A **run** is a string, or `{"text", "s", ...style overrides}`.
- On **read**, a run with no local style is a bare string, and a run with local
  style references a `styles` entry, one entry per distinct local style. Inline
  style fields never appear on read; they are a write-side convenience.
- An **autoText** atom (a slide number) is a run `{"auto": "SLIDE_NUMBER"}`. It
  can be restyled but not removed or edited, because no request creates one.
- **Paragraph props** are `align`, `space_above`, `space_below`,
  `line_spacing`, `indent_start`, `indent_first_line`, `indent_end`,
  `direction` and `bullet`. Read emits only the ones set locally.
- `\n` inside run text is refused: "split it into two paragraphs, or use
  `\u000b` for a line break inside the paragraph". Tabs are literal.
- The body always has at least one paragraph; `[]` means `[""]`. The terminal
  newline is implicit and never appears.

### Style: carry by default, explicit to change

This is the rule that matters most for agents, because agents do not copy style
objects back. A rule under which an omitted style means *reset* resets fonts,
sizes and colours on every run an agent retypes, and decks imported from PPTX set
all of those on every run. So:

| In the payload | Meaning |
| --- | --- |
| Run is a bare string, or has no `s` | **Carry**: the text keeps, or takes, the style it would get from typing in the editor (below) |
| `"s": "s2"` | The run's local style becomes exactly the `s2` table entry |
| `"s": "plain"` | The run's local style becomes empty, inheriting everything from the placeholder or theme |
| An inline field, e.g. `"bold": true` | Overrides that one field on top of the carried or `s` style |
| An inline field set to `null` | Resets that one field to inherited |
| A style field removed from the payload | **Nothing.** Removing a key never removes formatting; set it to `null` |

Carry resolves exactly as typing in the editor does, so no case depends on what
the API infers:

1. Text that **replaces** old text takes the style of the first character it
   replaces, link included. Changing a link's text keeps the link.
2. Text **inserted** inside a paragraph takes the style of the preceding
   character, or of the following character at the start of a paragraph. Links
   do not extend: an insertion next to a link is not linked.
3. A **new paragraph** takes its text style from the last character of the
   paragraph it follows, or the first character of the paragraph it precedes at
   the top of the body.
4. Text in an **empty body** takes the style of the body's terminal newline,
   which the API reports.
5. A **new element** has nothing to carry and inherits placeholder or theme
   defaults. To match existing text, reference an existing `s` entry, which is
   the other reason the style table exists.

The same rule holds for every property in the payload, not only text style:
paragraph props, `fill`, `outline`, `alt`, `link`. An absent key carries, `null`
resets to inherited, a value sets. A new paragraph carries the props of the paragraph it was split from,
which is also what the API does when a newline is inserted.

Editing a `styles` entry restyles every run in the payload that references it.
"Make every title 28 pt" is one changed number.

**Style fields** (in the table and as inline overrides): `bold`, `italic`,
`underline`, `strike`, `small_caps`, `baseline` (`SUPERSCRIPT` / `SUBSCRIPT`),
`font`, `weight` (only when not 400), `size` (pt), `color`, `bg`, `link`.

- Colours are `"#RRGGBB"`, a theme colour name (`"ACCENT1"`), or
  `"transparent"`. RGB is compared in hex, so the API's float channels
  round-trip without spurious writes; a changed colour is written as `k/255`.
- `link` is `"https://..."`, `"slide:<page_id>"`, or a relative link
  (`"NEXT_SLIDE"`, `"PREVIOUS_SLIDE"`, `"FIRST_SLIDE"`, `"LAST_SLIDE"`). Links
  are in the projection because an agent splitting or merging runs would
  otherwise drop or extend them without seeing it.

### Bullets

The API does not expose a list's preset: a `lists` entry carries only the text
style of each level's glyph, and the glyph itself appears only as rendered text
on each paragraph (`"●"`, `"1."`, `"a."`). No request sets a nesting level
directly: `createParagraphBullets` derives the level from leading tabs, strips
them, and joins the preceding list when its preset matches. All three were
verified live (see *Verified API behaviour*). So the projection exposes what can
be read and the writes that can be made exactly:

- Read: `"bullet": {"list": "L1", "level": 0}`. `L1` is a short alias for the
  API `listId`, stable within one response. The element carries
  `"lists": {"L1": {"glyphs": {"0": "1.", "1": "a."}}}`, read only, built from
  the glyphs rendered on its paragraphs, so the agent can see what the list looks
  like.
- Bulleted paragraphs report an `indentStart` the bullet itself put there (36 pt
  per level). The projection omits `indent_start` and `indent_first_line` on a
  bulleted paragraph when they equal what its level implies, so a bullet's
  indent is not mistaken for, and carried as, a paragraph prop.
- **Extend a list.** A new paragraph next to a paragraph of `L1` is compiled as a
  split of that neighbour, which inherits its bullet and level. No bullet request
  is emitted. By the carry rule, a bare string inserted into a list joins it;
  `"bullet": null` makes it a plain paragraph instead. A new paragraph that
  names the list at a different level than its neighbour is refused, as below.
- **Remove a bullet.** `"bullet": null` emits `deleteParagraphBullets`. That
  request leaves a nested paragraph indented by its old level minus one (a
  level-1 paragraph keeps `indentStart` and `indentFirstLine` of 36 pt), so the
  compiler follows it with an `updateParagraphStyle` resetting both to inherited,
  unless the payload sets them.
- **Start a list.** Declare it with a preset, `"lists": {"L2": {"preset":
  "NUMBERED_DIGIT_ALPHA_ROMAN"}}`, and give paragraphs `{"list": "L2", "level":
  n}`. The server inserts `n` leading tabs per paragraph and emits one
  `createParagraphBullets` per contiguous run of paragraphs. A new list placed
  directly after an existing list with the same preset is merged into it by the
  API, numbering included. The compiler cannot prevent that, so it refuses the
  edit with the remedy "put a non-list paragraph between them, or extend the
  existing list".
- **Change the level or list of an existing bulleted paragraph:** refused,
  because the list's preset cannot be read and recreating it could change its
  glyphs. The remedy in the error: declare a new list with a preset for all of
  that list's paragraphs, which makes the glyph change explicit.

### Tables

```json
{
  "id": "g1a2b3c4d5_2_1", "v": "1b9e44a2", "kind": "table",
  "box": {"x": 40, "y": 120, "w": 640, "h": 96},
  "col_widths": [240, 200, 200],
  "row_heights": [32, 32, 32],
  "columns": [0, 1, 2],
  "rows": [
    {"r": 0, "cells": ["Segment", "Conversion", "Delta vs Q3"]},
    {"r": 1, "cells": ["iOS app", "18.4%", "+2.1 pts"]},
    {"r": 2, "cells": ["Mobile web", {"text": ["12.4%"], "span": [1, 2]}, null]}
  ]
}
```

- Each cell is a text body: a string for one plain paragraph, or
  `{"text": [...]}`. Cell text is diffed exactly like shape text, addressed with
  `cellLocation`.
- Rows carry their original index `r`, and `columns` lists the original column
  indices. A row with no `r` is new; an `r` that disappears is a deleted row; the
  same holds for `columns`, where a new column is `null`. Keys, not positions,
  so inserting a row in the middle is never read as rewriting every row below
  it.
- A merged cell carries `"span": [rows, cols]` and the cells it covers are
  `null`. Merges and unmerges are refused in v1.
- `col_widths` and `row_heights` (minimum heights) are writable, which is how a
  table is resized.

### Notes, alt text, shape properties, images

- `notes` is `{"v": "...", "text": [...]}`, a text body with its own `v`, written to the shape named by
  `notesProperties.speakerNotesObjectId`. The server re-reads that ID on every
  edit rather than trusting a cached one, and never emits a zero-length
  `deleteText`, so writing notes on a slide that has none is not an API error.
- `alt` is `{"title", "description"}`, emitted only when non-empty.
- `fill` (`"#RRGGBB"`, a theme colour, or `"none"`), `outline`
  (`{"color", "weight", "dash"}` or `"none"`), `content_align` and `link` on a
  shape are emitted only when set locally; absent means inherited.
- An image carries `src`, its `sourceUrl`. Changing it emits `replaceImage`
  with `CENTER_INSIDE`, or with `CENTER_CROP` when the payload sets
  `"fit": "crop"`.
- `autofit` is projected read-only; see *Autofit* below for its form and why it
  cannot be written.

### Groups

A group is `{"kind": "group", "id", "v", "children": [...]}`. Children's boxes
are absolute page coordinates, so they compare directly with their siblings and
with ungrouped elements; the server converts back to the parent's space on write.
A child fetched on its own carries `"group": "<id>"`, read only. Grouping and
ungrouping are refused in v1.

### Strict schema

Unknown keys and changed read-only keys are refused, never ignored: a `"fill"` on
a run, a `"shape": "ELLIPSE"` on an existing element, a `"placeholder"` edit, or a
string `"size": "24pt"`. The error names the path, the field, and the fields that
are allowed there. A silent no-op that reports success is the failure this whole
design exists to prevent.

## Concurrency without server state

The server keeps no snapshot between calls. Snapshots held in memory do not
survive a restart, are not shared between replicas, and do not fit
`WORKSPACE_MCP_STATELESS_MODE`. Each element instead carries `v`, the first 8
hex digits of a SHA-256 over its canonical projection (below). The page is its
own snapshot:

1. `edit_page` fetches the page fresh.
2. For each element in the payload, it projects the current element and
   compares its `v` with the payload's.
3. **Match:** the current element is what the agent read, so the payload is
   diffed against it directly.
4. **Mismatch:** if diffing the payload against the current element produces no
   requests, the edit was already applied and the element is reported
   as `already applied`. That makes a blind retry after a lost response safe
   without an `operation_id`. Otherwise it is a conflict, and the whole call is
   refused.
5. The batch is written with `writeControl.requiredRevisionId` set to the
   revision from step 1. If another write lands in the milliseconds between
   fetch and write, the API rejects it, and the server repeats from step 1 up
   to three times. The repeat is safe because the `v` check runs again.

Conflicts are per element, not per presentation. `revisionId` changes on every
write to any slide, so guarding on it alone would make an agent's own edit to
slide 2 invalidate the markup it already holds for slide 3, and would make every
edit on a deck someone else is working in fail. With `v`, an edit conflicts only
when an element it touches has changed.

A conflict response carries the current projection of each conflicting element,
with its new `v`, so the agent can redo the edit without another read. It says,
in so many words, to reapply the change to that version and not to resend the
previous payload, which would overwrite the other change.

**Canonical form**, used for both `v` and equality: styles resolved through the
table, adjacent runs with identical resolved styles merged, empty runs dropped,
string and object forms of the same run unified, lengths rounded to 0.01 pt,
colours in hex. A table alias like `s1` or `L1` is a name, not content, so
renaming one never changes `v`.

**Missing revision.** The API reference says `revisionId` is populated only for
callers with edit access. Live, it is populated for anyone who can *comment*,
and absent only for view-only access: across 90 decks it was present on all 45
the caller could edit and on all 37 it could only comment on, and absent on all
8 it could only view. So:

- No revision on a fresh read means the caller is view-only. `edit_page` fails
  before compiling: "you have view-only access to this presentation". There is
  no unguarded write path.
- A revision does not mean the caller can edit. A commenter's write fails with
  403, which the receipt reports as "you have comment-only access", not as a
  generic API error.

The guard itself behaves as documented. Every page of a deck reports the same
`revisionId`. A stale guard fails with 400 and the message "The required
revision ID '...' does not match the latest revision", which is what step 5
matches before retrying. Reads, renders included, never change the revision.
The `batchUpdate` response carries the new revision in
`writeControl.requiredRevisionId`, so the receipt reports it without another
read.

## What `edit_page` does

**Input**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `presentation_id` | string | yes | |
| `pages` | object[] | yes | One or more edited `get_page_markup` bodies, each with `page_id`. Elements may be a subset of what was fetched. Optional `delete` per page. |
| `dry_run` | boolean | no | Validate and compile, return the receipt with the planned requests, write nothing. |

Several pages go into one batch: one atomic write, one revision bump. Changing a
date on ten slides is one call.

**Steps**

1. **Fetch and check `v`** for every element in every page (above).
2. **Validate everything before writing anything.** Schema, read-only fields,
   unknown IDs, refused changes. All refusals are reported together, each with
   its path, reason and remedy, so the agent fixes them in one pass. Nothing is
   partially applied.
3. **Diff** each payload element against its current projection (below).
4. **Compile** to requests (below).
5. **Write** one batch with the revision guard.
6. **Read back** the touched pages, project the touched elements, and compare
   with the expected result in canonical form.
7. **Return the receipt.**

### Element identity

- An element whose `id` exists on the page is an edit.
- An element marked `"new": true` is created. `id` is optional; if given, it must
  be unused presentation-wide and match the API's ID rules (5–50 characters,
  `[a-zA-Z0-9_]` then `[a-zA-Z0-9_-:]`). The receipt returns generated IDs.
  Supported kinds: shape, table, image, line.
- An element with an unknown `id` and no `"new"` is refused with the closest
  existing IDs on the page. A mistyped ID never creates a duplicate.
- An element on the page but not in the payload is untouched. The receipt says
  how many there were and that omission never deletes, so an agent that dropped
  an element to delete it finds out in the same response.
- `delete` lists IDs to delete. The API's cascade, verified live, is:
  - Deleting a group deletes all its descendants. Agents may expect this to
    ungroup instead, so deleting a group is refused unless its descendants are
    named too.
  - A group left with fewer than two children is dissolved. The group ID
    disappears, and its remaining child (which may itself be a group) is
    promoted to the group's parent at the same position on the page. Nothing
    visible is lost, so this is not refused. The receipt reports it as
    `group g dissolved; b promoted`, and the promoted element's `v` is
    unchanged, because its `box` is absolute.
  - Deletes are emitted after every other request in the batch, so an edit to a
    group in the same payload never targets an ID that a delete has dissolved.
- An element in both `elements` and `delete` is refused.
- A new element is created at the top of the stack, unless the payload is a
  whole page, where its position in `elements` sets its stacking order.

**Inside** an element, the payload's *content* is the complete new state: a
removed paragraph, run, table row, column or group child is deleted. Its
*properties* follow carry: a removed key changes nothing. Across elements,
omission means *not sent*. Content is what the agent is visibly rewriting;
properties are what it forgets to copy.

### Creating elements

A new element is the same projection as an existing one, with `"new": true`
and without `v`, which only exists for something already read. Validation
refuses anything not listed here, naming the field.

| Kind | Required | Optional (default) | Refused |
| --- | --- | --- | --- |
| `shape` | `shape` (an API shape type, e.g. `TEXT_BOX`, `RECTANGLE`), `box` | `text` (empty), `fill`, `outline`, `content_align`, `link`, `alt` (all inherited from the theme) | `placeholder` (placeholders come only from a layout), `autofit` (read only) |
| `table` | `box` (`x`, `y` only), `rows` | `col_widths`, `row_heights` (API defaults), `alt` | `span` (merges are refused in v1), `w`, `h` (derived) |
| `image` | `src` (a public `http(s)` URL the API fetches once), `box` | `fit` (`"inside"`), `alt` | data URIs, Drive file IDs |
| `line` | `box`, where `x`, `y` is the start, `w`, `h` the extent, and `flip` the direction | `category` (`STRAIGHT`, `BENT` or `CURVED`; default `STRAIGHT`) | `rot`, connections to other elements (v2) |

Rules:

- **Tables** take their shape from `rows`: every row has the same number of
  cells, and new rows and columns carry no `r` or `columns` index, since there
  is nothing to key them to. A cell is a text body, so `"cells": ["A", "B"]`
  creates a two-column row.
- **Text** in a new element follows carry rule 5: it starts from placeholder or
  theme defaults. To match existing text, reference an existing `s` entry from
  the same response.
- **Stacking order:** a new element goes on top of the stack, unless the
  payload is a whole page, where its position in `elements` sets the order.
- **IDs:** with no `id`, the server generates one from the page ID and a
  counter, and the receipt returns it. A caller-supplied `id` is checked against
  every object ID in the presentation before anything is written, because the
  API rejects a duplicate only at write time.
- **Groups, charts, videos, WordArt and placeholders** can't be created in v1.
  Each refusal names the request that exists (`groupObjects`,
  `createSheetsChart`, `createVideo`) or says it isn't creatable.
- **The compile order** is `createShape` / `createTable` / `createImage` /
  `createLine`, then the element's text, styles and properties as for an edit,
  all against the new ID in the same batch.

### Text diff: aligned by content, not position

Comparing paragraph 3 with paragraph 3 fails on the commonest list edit.
Inserting a bullet at the top of a five-bullet body shifts every paragraph down
one, so a positional diff rewrites them all and moves each paragraph's formatting
onto its neighbour's text. The diff aligns by content instead:

1. **Paragraphs.** Align old and new paragraph texts with
   `difflib.SequenceMatcher` (`autojunk=False`). Equal paragraphs anchor. Inside
   each region between anchors, pair old and new paragraphs in order by
   similarity (ratio ≥ 0.5). A paired paragraph keeps its identity, and so its
   props. An unpaired new paragraph is an insertion; an unpaired old one is a
   deletion.
2. **Runs.** Within a paired paragraph, flatten both sides to characters with
   resolved styles, then diff word tokens (letter runs, single whitespace,
   single punctuation; autoText is one atomic token). Equal tokens keep their
   characters, so formatting travels with text rather than with a position, and
   run boundaries do not matter. The walkthrough splits one run into two and
   changes one digit, and the diff touches only that digit and that word.
3. **Minimal splice.** Inside each replaced token span, trim the common prefix
   and suffix on grapheme boundaries, so "12%" to "14%" splices one character
   and never splits an emoji or a combining mark.
4. **Styles.** For every character whose target style differs from its current
   style, compute the differing fields. An `s` reference sets every field in the
   table entry and resets every field not in it.

### Compile

| Diff result | Emitted |
| --- | --- |
| Replaced span | `deleteText` + `insertText`, then `updateTextStyle` with `fields: "*"` and the carried or explicit style |
| Inserted span | `insertText`, then `updateTextStyle` with `fields: "*"` |
| Deleted span | `deleteText` |
| Equal span, style differs | `updateTextStyle` with a mask of exactly the differing fields; fields reset to inherited are in the mask and unset |
| New paragraph | `insertText` of `"\n" + text` at the end of the preceding paragraph's text, or `text + "\n"` at index 0 |
| Deleted paragraph | `deleteText` over its text and its own newline, or over the preceding newline and its text if it is last; see *Paragraph merges* |
| Paragraph props differ | `updateParagraphStyle` with a mask of the differing fields |
| Bullets | as in *Bullets* above |
| `box` differs | `updatePageElementTransform`, `ABSOLUTE` |
| `col_widths` / `row_heights` differ | `updateTableColumnProperties` / `updateTableRowProperties` |
| Table rows / columns added or removed | `insertTableRows` / `insertTableColumns` / `deleteTableRow` / `deleteTableColumn` |
| `fill`, `outline`, shape `link`, `content_align` differ | `updateShapeProperties` with a mask |
| `alt` differs | `updatePageElementAltText` |
| Image `src` differs | `replaceImage` |
| Element order differs | `updatePageElementsZOrder`; see below |
| `"new": true` | `createShape` / `createTable` / `createImage` / `createLine`, then its text, styles and props |
| `delete` | `deleteObject` |

**Paragraph merges.** When the newline between two paragraphs is deleted, the
merged paragraph keeps the props and bullet of the **second** paragraph, the one
whose newline survives (verified live). The compiler relies on that:

- Deleting a paragraph removes its own text and newline, so the paragraph after
  it keeps its own props and bullet unchanged.
- Deleting the last paragraph removes the preceding newline and its text, so the
  paragraph before it keeps its own newline and props.
- Joining two paired paragraphs into one (the agent concatenated their text)
  keeps the second's props. When the payload's props for the joined paragraph
  match the first's, the compiler writes them back with `updateParagraphStyle`.
  When the first had a bullet and the second did not, the bullet cannot be
  restored without its preset, so the edit is refused with the remedy "keep them
  as two paragraphs, or remove the bullet".

**Ordering.** Each text body is compiled in three phases, so no running offset
is ever maintained:

1. **Text.** `deleteText` and `insertText`, from the end of the body to the
   start. Every index is an index into the *original* text, valid because
   everything after it has already been processed and everything before it is
   untouched. Leading tabs for new bullets are inserted here.
2. **Style.** `updateTextStyle` and `updateParagraphStyle`, indexed against the
   *final* text, which the server knows exactly because it is the payload's
   text plus those tabs.
3. **Bullets.** `createParagraphBullets` and `deleteParagraphBullets`, last
   paragraph first, because removing a paragraph's leading tabs shifts only the
   indices after it.

Indices are UTF-16 code units, matching the API.

**Stacking order** is significant only when the payload holds every top-level
element on the page, which a whole-page fetch does. Reordering `elements` then
compiles to the shortest sequence of `updatePageElementsZOrder` calls. In a
scoped payload, elements must stay in their current relative order, and a change
is refused with the remedy "fetch the whole page to change stacking order".

### Autofit: freeze at displayed size

**The promise: an edit never changes how big a shape's text looks. If the shape
auto-fits, it stops auto-fitting, and the receipt says so.**

The API leaves no option to keep autofit on. Any request that touches a shape
turns it off, and it cannot be turned back on. So the choices are what the text
looks like afterwards. Resetting it to the unshrunk size makes text nobody
touched jump and overflow. Refusing would block edits to the 360-plus grow boxes
in a typical sample, with no workaround, since the raw API behaves the same.
Freezing is the only choice under which nothing visibly changes, and it is what
the API's own trigger does when nothing interferes. Every rule below was
verified live (see *Verified API behaviour*).

**What the API does.** Any request touching a text shape sets `autofitType` to
`NONE`: text, text style, paragraph style, bullets, fills, `replaceAllText`, and
a transform, even an identity one. Alt text is the exception. Both
`TEXT_AUTOFIT` (shrink text on overflow) and `SHAPE_AUTOFIT` (resize the shape
to its text) are turned off. For shrink, the first such request bakes the
displayed size into every paragraph and run of the shape:

- `size` becomes `size * fontScale`, including sizes the run inherited, which
  become local values (an inherited 12 pt became a local 11.1 pt at scale
  0.925).
- `line_spacing` becomes `line_spacing - 100 * lineSpacingReduction` (150 became
  130 at 0.2), applied to the inherited 100 when it is unset.
- The triggering request is applied **before** the bake. A 30 pt
  `updateTextStyle` as the first request came out at 18.75 pt.
- Requests after the trigger in the same batch apply to the baked state, at face
  value.

Two ways to turn autofit off do **not** freeze. Writing `autofitType: NONE`
explicitly turns shrink off without the bake, so the text jumps back to its
unshrunk size. And writing any other `autofitType` fails ("Autofit types other
than NONE are not supported").

**Read: the projection shows effective autofit and displayed sizes.**

- `autofit` is `{"type": "shrink", "scale": 0.625}` or `"grow"`, read only,
  whether it is set on the shape or inherited from its placeholder. The server
  resolves inheritance through `placeholder.parentObjectId`. The slide shape
  reports the scale it is displayed at, also when the type is inherited (53 of
  221 inheriting shapes in 99 real decks were actively shrunk).
- In a shrink shape, every `size` and `line_spacing` in the projection is the
  displayed value: local or inherited value times `fontScale`, line spacing less
  the reduction. The projection then matches the rendered slide, "set this to
  14 pt" means 14 pt on screen, and the agent never multiplies by anything.
  The deck author's unshrunk 20 pt is not shown, because nobody sees it.
- A shrink shape's runs whose size is inherited project their displayed size as
  a local value, since that is what the freeze will write.

**Write: the compiler freezes first, on purpose.**

- For each shrink or grow shape the payload changes, the compiler emits an
  identity `updatePageElementTransform` (`RELATIVE`, scale 1, no translation)
  before any other request for that shape. It is the only trigger verified to
  change nothing but the freeze: geometry is byte-for-byte unchanged, sizes are
  baked, and the bake happens before anything the payload writes.
- Every request after it is in displayed units, which are the payload's units.
  So the payload's values are written at face value, and the read-back compares
  them directly. No scaling rule exists anywhere in the compiler.
- `v` is computed over displayed values, so freezing a shape the agent holds does
  not change its `v` unless the agent's edit does. Edits chained on the agent's
  own copy keep working.
- `replace_text` cannot freeze first: `replaceAllText` has no per-shape order,
  and it freezes every shape it touches. The bake still applies, and the result
  is the same: replaced text keeps its displayed size. The receipt lists the
  shapes that stopped auto-fitting.

**Report: what happened, not what was predicted.**

- The receipt reads autofit back after the write and states it as fact: "autofit
  was shrink (scale 0.625); it is now off and the text is fixed at its displayed
  size; longer text may overflow" or "autofit was grow; the box no longer
  resizes to its text". Both say to check with `render_page_to_image`.
- `dry_run` lists the shapes the edit will freeze. The triggers above are
  verified, so it says "will freeze" rather than "may".
- An edit that touches a shape only through `alt`, which does not freeze, leaves
  autofit on and reports nothing.

How often this matters, from 99 recent decks: 367 text shapes set shrink
locally, 34 of them actively shrunk; 221 inherit shrink, 53 actively shrunk;
grow is more common still. Most text shapes have no autofit at all.

### Read-back and receipt

```
applied  revision AKXv... -> AKXw...  6 requests  verified
page g1a2b3c4d5_1_0
  g1a2b3c4d5_1_2 (BODY)  v=0e5d7a13
    text   + paragraph 1 "Android app conversion is 15.9%"
    note   autofit was shrink (scale 0.9); it is now off and the text is fixed at its
           displayed size. Longer text may overflow: check with render_page_to_image.
  1 other element on this page unchanged (omitting an element never deletes it)
```

- **Status**: `applied`, `already applied`, `conflict`, `refused`, or `unknown`
  (the network result was lost; resend the same payload, which is safe).
- **Revision** before and after, for `batch_update_presentation`.
- **Per element**: the change summary, the new `v`, and the fresh projection
  whenever the result differs from the submitted payload (a verification
  mismatch). A freeze does not count: the payload was already in displayed
  sizes, so the agent's copy is still exact, apart from `autofit`, which it
  cannot write anyway.
- **Verification**: `verified`, or `MISMATCH` with expected and actual for each
  differing paragraph or field. Slides has no rollback, so a mismatch is
  reported with the current state and fixed forward, never hidden.
- **Generated IDs** for new elements.
- With `dry_run`, `would apply` and the request list.

## Field matrix

Resolves the "map or refuse, never ignore" decision for v1.

| Field | v1 | Request or reason |
| --- | --- | --- |
| Text (shapes, cells, notes) | mapped | `insertText` / `deleteText` |
| Run style, including links | mapped | `updateTextStyle` |
| Paragraph props | mapped | `updateParagraphStyle` |
| Bullets: extend, remove, new list with preset | mapped | split inheritance, `deleteParagraphBullets`, `createParagraphBullets` |
| Bullets: change level or list | refused | preset unreadable; remedy: declare a new list |
| `box` | mapped | `updatePageElementTransform` (tables: `x`, `y` only) |
| Table widths and heights | mapped | `updateTableColumnProperties`, `updateTableRowProperties` |
| Table rows and columns | mapped | `insertTable*`, `deleteTable*` |
| Table merges, cell fills, borders | refused | `mergeTableCells`, `updateTableCellProperties`, `updateTableBorderProperties` exist; v2 |
| Alt text | mapped | `updatePageElementAltText` |
| Shape fill, outline, link, content alignment | mapped | `updateShapeProperties` |
| Image source | mapped | `replaceImage` |
| Stacking order | mapped | `updatePageElementsZOrder`, whole-page payload only |
| Create shape, table, image, line | mapped | `create*` |
| Delete | mapped | `deleteObject` |
| Group, ungroup | refused | `groupObjects`, `ungroupObjects` exist; v2 |
| Line properties, routing | refused, not projected | `updateLineProperties`, `rerouteLine` exist; v2 |
| Video properties | refused, not projected | `updateVideoProperties` exists; v2 |
| Page background | refused, not projected | `updatePageProperties` exists; v2 |
| autoText content | read only | no request creates one |
| Autofit | read only, frozen on edit | only `NONE` is writable, and writing it skips the bake; see *Autofit* |
| Shadow, image crop | read only, not projected | read only in the API |
| Chart content | not projected | lives in Sheets |

Every v2 row is reachable today through the escape hatch, and the refusal says so
by naming the request.

## `get_page_markup`

**Input**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `presentation_id` | string | yes | |
| `page_id` | string | yes | |
| `targets` | string[] | no | Element IDs, and `"notes"`. Default: the whole page, notes included. |
| `raw` | boolean | no | Return the API `Page` resource pruned to `targets` instead of the projection, for writing raw requests. |

A target inside a group returns that element with absolute geometry and its
`group` ID. A target that is a group returns its descendants.

## `replace_text`

Deck-wide find and replace, the cheapest edit there is and the first tool the
descriptions point to for "change X to Y".

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `presentation_id` | string | yes | |
| `find` | string | yes | Literal text |
| `replace` | string | yes | |
| `match_case` | boolean | no | Default true |
| `page_ids` | string[] | no | Limit to these slides |
| `include_notes` | boolean | no | Default true: speaker notes are content |

Compiles to one `replaceAllText`. Verified live: with no filter it replaces in
speaker notes as well as on slides. `pageObjectIds` limits it to exactly the
pages named, so a slide ID alone skips that slide's notes, while a notes page ID
is accepted and reaches them. The server therefore expands each of `page_ids` to
the slide plus its notes page when `include_notes` is true. With no `page_ids`
and `include_notes` false, it passes every slide ID.

The API returns only a total `occurrencesChanged`. Before writing, the server
reads the deck (a server-side read that costs the agent nothing) to report where
the matches are and which of those shapes have autofit:

```
replaced 4 occurrences of "Q3" with "Q4": slides 2, 5, 7; notes of slide 5  revision AKXw... -> AKXx...
note  2 of the shapes had autofit on; it is now off, text fixed at its displayed size.
```

## Escape hatch

`batch_update_presentation(presentation_id, requests, required_revision_id)`
takes raw Google requests behind a mandatory revision guard and does not
guarantee formatting preservation. The revision comes from `get_page`,
`get_presentation`, or any receipt, and `get_page_markup(raw=true)` provides the
resource to write against. It exists so that a refusal is never a dead end.

## Tool descriptions

Only the first line of a description reliably reaches the model
(`google_workspace_mcp-rv6`), so each first line has to carry the routing:

| Tool | First line |
| --- | --- |
| `replace_text` | Replace text everywhere in a deck, or on chosen slides, keeping formatting. Use this first for any plain text change. |
| `get_page` | Read one slide's content: every element ID and all text. Start here. |
| `get_page_markup` | Fetch editable JSON for one slide or chosen elements, to change and pass to edit_page. |
| `edit_page` | Apply edited get_page_markup JSON to one or more slides. Only what differs is written; formatting is kept. |
| `batch_update_presentation` | Last resort: raw Slides API requests behind a revision guard. Does not preserve formatting. |

## Where agents go wrong, and what catches it

| Agent behaviour | Design response |
| --- | --- |
| Retypes text without copying styles | Carry: the text keeps its style |
| Inserts, removes or reorders paragraphs | Content-aligned diff: formatting stays with its text |
| Deletes a style key to remove formatting | Documented no-op; `null` resets. The receipt shows no style change |
| Fetches several slides, edits them in turn | Per-element `v`: earlier edits do not invalidate later markup |
| Reuses a stale `v` after its own edit | Conflict with the current element attached; the receipt gave the new `v` |
| Resends an old payload after a conflict | The conflict message says not to; the next `v` check catches a second attempt |
| Mistypes an element ID | Refused with suggestions; never creates a duplicate |
| Drops an element to delete it | Receipt: "omitting an element never deletes it" |
| Computes geometry in EMU or from `size` | Points and a resolved box; no matrix in the payload |
| Puts `\n` in run text | Refused with the two correct alternatives |
| Changes a bullet's level | Refused with the new-list remedy |
| Edits a slide number | Refused: autoText cannot be recreated |
| Edits or moves an autofit box | Freeze at displayed size: nothing visibly changes; `autofit` visible before, `dry_run` says it will freeze, receipt notes overflow risk |
| Adds a field the tool cannot write | Refused by the strict schema, naming the escape-hatch request |
| Retries after a timeout | Same payload is safe: `already applied` |
| Uses edit_page for a word swap across slides | Descriptions route to `replace_text` first |

## Cost

The loop's token cost is dominated by step 2, which is why it is scoped: an agent
changing one text box fetches one text box, and the raw resource (746 tokens for
a trivial unstyled slide in measurement) never enters the loop. The projection is
built to be small:

- Points instead of EMU, and one flat `box` instead of a transform plus a
  wrapped size.
- A style table, so a style shared by twenty runs is written once.
- String shorthands for runs, paragraphs and cells with no local formatting.
- Only locally set properties; inherited values are absent.
- `v` is 8 hex characters per element. Hashes go in plain hex, never base64,
  which tokenises badly.

On writes, carry means an agent sends back less than it read: a changed run
needs only its text.

The projection's size has not been measured yet. The ticket requires measuring
it on the same 24-slide fixture used for the earlier numbers, for a whole page
and for one element, against `get_page(raw=True)`.

## Verified API behaviour

Checked live on 2026-10-22 against scratch decks and against copies of real
decks (trashed after each run), plus read-only metadata from up to 99 real
decks, with the probe script kept at `docs/slides-probe/probe.py`. Each result
is folded into the section named.

| Behaviour | Result | Section |
| --- | --- | --- |
| Paragraph merge on newline delete | Second paragraph's props and bullet survive | *Paragraph merges* |
| Newline inserted inside a list | New paragraph inherits list, level and indent; numbering continues | *Bullets* |
| `createParagraphBullets` after a same-preset list | Joins that list, numbering continues | *Bullets* |
| `deleteParagraphBullets` on a nested paragraph | Leaves indent of (level - 1) x 36 pt | *Bullets* |
| `lists` map | Per-level glyph text style only; no preset, no glyph | *Bullets* |
| What turns autofit off | Any request touching the shape (text, styles, bullets, fill, `replaceAllText`, any transform); not alt text | *Autofit* |
| Shrink baking | `size * fontScale` and `line_spacing - 100 * reduction` on every run and paragraph, inherited values written locally | *Autofit* |
| Bake order | Triggering request applied before the bake; later requests at face value | *Autofit* |
| Explicit `autofitType: NONE` | Turns autofit off **without** baking: text jumps to unshrunk size | *Autofit* |
| Identity transform | Freezes and bakes; geometry unchanged | *Autofit* |
| Re-enabling autofit | Refused: only `NONE` is writable; `autofit` mask is read only | *Autofit* |
| Inherited shrink | Slide shape reports its displayed `fontScale`; baked like local shrink | *Autofit* |
| `revisionId` presence | Present for edit and comment access, absent for view only | *Concurrency* |
| Revision scope | Same on every page; stale guard is a 400 with a fixed message; reads do not change it | *Concurrency* |
| `replaceAllText` and notes | Unfiltered: reaches notes. Slide filter: skips notes. Notes page ID: accepted | *`replace_text`* |
| `deleteObject` in groups | Group delete removes descendants; a group left with one child dissolves and promotes it in place | *Element identity* |
| Object ID length | Under 5 characters is a 400 | *Element identity* |

## Open questions

None open against the API. The checks in *Verified API behaviour* answered every
question the design depended on.
