# v1 Current-Screen Capture — Approved Implementation Contract

**Date:** 2026-05-07  
**Target agent:** @python-implementer  
**Scope:** Current active Android screen only. No recursive crawl, no diff framing, no interaction execution.

---

## 1. Scope and Architectural Boundaries

1. Scope is one active Android screen per run.
2. Capture must include every UIAutomator node on that screen, with no filtering.
3. Preserve nodes with empty text and non-clickable nodes.
4. Do not implement interaction execution in v1.
5. Do not use diff/change detection framing in the v1 output contract.
6. Keep transport, parsing, model, and rendering isolated:
   - **ADB transport**: command execution and raw artifact retrieval only.
   - **Parsing**: XML to canonical in-memory model only.
   - **Rendering**: JSON, Markdown, and HTML generated only from canonical in-memory model.

---

## 2. Canonical In-Memory Model (Single Source for JSON / MD / HTML)

Top-level object: `ScreenSnapshotModel`

Required top-level sections:

1. `capture`
2. `context`
3. `summary`
4. `elements`
5. `diagnostics`

### 2.1 `capture`

| Field | Type | Notes |
|---|---|---|
| `capture_id` | string | See §6 for format |
| `timestamp_utc` | string | ISO-8601 UTC |
| `device_serial` | string | |
| `source.ui_dump_path` | string | Local path to window_dump.xml |
| `source.screenshot_path` | string | Local path to screen.png |
| `origin.parent_capture_id` | string \| null | null for root capture |
| `origin.interacted_element_id` | string \| null | null for root capture |
| `origin.action_type` | string \| null | null for root capture; enum defined in §7 |

### 2.2 `context`

| Field | Type | Notes |
|---|---|---|
| `package_name` | string | From `dumpsys window windows` |
| `activity_name` | string | From `dumpsys window windows` |
| `screen_width` | integer | Derived from max element bounds or wm size |
| `screen_height` | integer | Derived from max element bounds or wm size |
| `screen_density` | integer \| null | From `wm density`, null if unavailable |

### 2.3 `summary`

| Field | Type |
|---|---|
| `xml_node_count` | integer |
| `element_count` | integer |
| `clickable_count` | integer |
| `long_clickable_count` | integer |
| `focusable_count` | integer |
| `focused_count` | integer |
| `enabled_count` | integer |
| `scrollable_count` | integer |
| `selected_count` | integer |
| `checked_count` | integer |
| `checkable_count` | integer |
| `password_count` | integer |
| `interaction_candidate_count` | integer |
| `parity_assertions.xml_equals_elements` | boolean |

If `xml_equals_elements` is false, fail the run with a non-zero exit code and log to `diagnostics.errors`.

### 2.4 `elements` — ElementModel

Every element must include all fields below. No defined field may be omitted.

#### Identity fields

| Field | Type | Nullability |
|---|---|---|
| `element_id` | string | never null |
| `identity_version` | string | fixed `"v1"` |
| `normalized_path` | string | never null |
| `parent_path` | string \| null | null for root node |
| `depth` | integer | never null |
| `sibling_index` | integer | never null |
| `xml_index_preorder` | integer | never null; 0-based |

#### Classification fields

| Field | Type | Nullability |
|---|---|---|
| `class_name` | string \| null | null when absent |
| `resource_id` | string \| null | null when absent |
| `package` | string \| null | null when absent; derived from `resource_id` prefix |
| `view_type_hint` | string \| null | null when absent |

#### Content fields

| Field | Type | Nullability |
|---|---|---|
| `text` | string | empty string when absent (never null) |
| `content_desc` | string | empty string when absent (never null) |
| `hint` | string \| null | null when absent |
| `value` | string \| null | null when absent |
| `input_type` | string \| null | null when absent |

#### Geometry fields

| Field | Type | Nullability |
|---|---|---|
| `bounds_raw` | string | empty string when malformed/absent |
| `bounds.left` | integer | 0 when malformed |
| `bounds.top` | integer | 0 when malformed |
| `bounds.right` | integer | 0 when malformed |
| `bounds.bottom` | integer | 0 when malformed |
| `width` | integer | 0 when malformed |
| `height` | integer | 0 when malformed |
| `center_x` | integer | derived from bounds |
| `center_y` | integer | derived from bounds |

#### State flags (all boolean, false when absent)

| Field |
|---|
| `clickable` |
| `long_clickable` |
| `focusable` |
| `focused` |
| `scrollable` |
| `selected` |
| `enabled` |
| `checked` |
| `checkable` |
| `password` |

#### Interaction candidacy fields

| Field | Type | Notes |
|---|---|---|
| `is_interaction_candidate` | boolean | true if `action_types` is non-empty |
| `action_types` | array of strings | empty array when not a candidate |
| `candidacy_reasons` | array of strings | documents why each action was added |

#### Raw attribute preservation

| Field | Type | Notes |
|---|---|---|
| `source_attributes` | object | All known UIAutomator XML keys normalized; null/empty when absent |
| `source_attributes_extra` | object | Unknown/extra keys copied verbatim; may be empty object |

**Known XML keys to normalize explicitly in `source_attributes`:**

`index`, `text`, `resource-id`, `class`, `package`, `content-desc`, `checkable`, `checked`, `clickable`, `enabled`, `focusable`, `focused`, `scrollable`, `long-clickable`, `password`, `selected`, `bounds`, `hint`, `input-type`, `pane-title`, `drawing-order`

### 2.5 `diagnostics`

| Field | Type | Notes |
|---|---|---|
| `adb_command_log` | array | See below |
| `warnings` | array of strings | Non-fatal issues |
| `limitations` | array of strings | Known scope limits |
| `errors` | array of strings | Fatal issues; triggers non-zero exit |
| `validation.schema_validation_performed` | boolean | |
| `validation.schema_validation_passed` | boolean | |
| `validation.schema_validation_error` | string \| null | |

**ADB command log entry:**

| Field | Type |
|---|---|
| `command` | string |
| `exit_code` | integer |
| `stdout` | string |
| `stderr` | string |
| `started_utc` | string |
| `finished_utc` | string |

---

## 3. Nullability and Normalization Rules

Apply uniformly in the parser and confirmed in all renderers:

1. Never omit a defined field from the output.
2. `text` and `content_desc`: empty string `""` when XML attribute is missing or empty.
3. All other optional string fields (`class_name`, `resource_id`, `package`, `hint`, `value`, `input_type`, `view_type_hint`): `null` when XML attribute is absent.
4. `bounds_raw`: empty string `""` when attribute is missing or unparseable; numeric `bounds.*` fields become `0`.
5. Boolean flags: `false` when XML attribute is absent or value is not `"true"`.
6. `parent_path`: `null` for root node.
7. Provenance root: all three `origin.*` fields set to `null` in v1.
8. `action_types`: always present as array; empty array when not a candidate.
9. `source_attributes`: all 20 known XML keys present; missing ones get `null`/`""` per the content-vs-optional rule above.
10. `source_attributes_extra`: empty object `{}` when no unknown keys exist.

---

## 4. Stable Identity and Normalized Path Strategy

### 4.1 `normalized_path`

1. Traverse the UIAutomator node tree in deterministic preorder DFS.
2. Build path using only `node`-tag entries.
3. Path segment format: `/n[i]` where `i` is the sibling index among `node`-tag siblings.
4. Root: `/n[0]`.
5. Example second-child of root: `/n[0]/n[1]`.
6. `parent_path` is `normalized_path` minus the last segment, or `null` for root.

### 4.2 `element_id` (deterministic, text-independent)

Identity basis string (concatenated with `|` separator):

```
normalized_path | class_name_or_NULL | resource_id_or_NULL | package_or_NULL | bounds.left | bounds.top | bounds.right | bounds.bottom | sibling_index
```

- Do **not** include `text` or `content_desc` in the basis.
- Hash basis with Python standard library `hashlib.sha1`.
- Format: `el_v1_<first16hexchars>`

Keep `xml_index_preorder` alongside `element_id` for ordering and debugging.

---

## 5. Element Ordering Rules

1. Canonical ordering is preorder DFS traversal order from the XML.
2. `elements` array preserves this exact order.
3. `xml_index_preorder` starts at `0`, increments for every `node` element captured.
4. Markdown table rows and HTML table rows must use canonical element order without re-sorting.
5. All summary counts are computed from the canonical ordered list.

---

## 6. Capture Provenance Contract and Identifier Formats

### `capture_id` format

```
cap_<YYYYMMDDTHHMMSSfff>Z_<serial_sanitized>
```

- UTC only.
- Milliseconds with zero-padding.
- `serial_sanitized`: keep alphanumerics, dash, underscore; replace everything else with underscore.
- Example: `cap_20260507T143201042Z_emulator_5554`

### Provenance fields under `capture.origin`

| Field | v1 Value |
|---|---|
| `parent_capture_id` | `null` |
| `interacted_element_id` | `null` |
| `action_type` | `null` |

---

## 7. Interaction Candidacy Rules (Planned, No Execution in v1)

Action type vocabulary:

| Value | Trigger rule |
|---|---|
| `tap` | `clickable == true AND enabled == true` |
| `long_tap` | `long_clickable == true AND enabled == true` |
| `scroll` | `scrollable == true AND enabled == true` |
| `swipe` | `scrollable == true AND enabled == true AND width * height > threshold` |
| `input` | `focusable == true AND enabled == true` |

`candidacy_reasons` examples:
- `"clickable=true, enabled=true → tap"`
- `"focusable=true, enabled=true → input"`

`is_interaction_candidate = len(action_types) > 0`

**Explicit deferment:** No tap, long-tap, scroll, swipe, or input command execution in v1.

---

## 8. Parsing Plan: UIAutomator XML → Canonical Model

1. Call `adb shell uiautomator dump <remote_path>`.
2. Call `adb pull <remote_path> <local_path>`.
3. Call `adb exec-out screencap -p > screen.png` (binary, no shell pipe).
4. Parse XML with `xml.etree.ElementTree` (standard library).
5. Traverse all `node` elements in preorder DFS:
   - Normalize attributes by nullability rule table.
   - Parse bounds string `[left,top][right,bottom]` with regex.
   - Derive `width`, `height`, `center_x`, `center_y`.
   - Compute `normalized_path` and `element_id`.
   - Compute state flags.
   - Compute interaction candidacy.
   - Preserve `source_attributes` and `source_attributes_extra`.
   - Assign `xml_index_preorder`.
6. Track `xml_node_count` as every `node` element visited.
7. Build `elements` list in traversal order.
8. Assert `element_count == xml_node_count`; fail if not equal.

---

## 9. Rendering Plan — JSON / Markdown / HTML Parity

### Single-source pipeline

1. Build `ScreenSnapshotModel` in memory once.
2. Serialize to JSON (canonical artifact).
3. Feed same object to Markdown renderer.
4. Feed same object to HTML renderer.

### Parity guarantees

- Same `element_count` in all outputs.
- Same ordered `element_id` sequence in all outputs.
- Same capture metadata, provenance, and summary counts.
- Same interaction candidacy values.

### Output artifacts per capture run

| File | Format | Notes |
|---|---|---|
| `screen-snapshot.json` | JSON | Canonical; source of truth |
| `report.md` | Markdown | Full element catalog, diagnostics |
| `report.html` | HTML | Full catalog, screenshot overlay, search |
| `window_dump.xml` | XML | Raw ADB artifact |
| `screen.png` | PNG | Raw screenshot |

### Markdown content requirements

- Capture metadata block.
- Summary block with all counts.
- Full element table: every element with all fields from §2.4.
- Interaction candidates summary.
- ADB command log table.
- Warnings, Limitations, Errors sections.

### HTML content requirements

- Capture metadata card.
- Summary badges with all counts.
- Screenshot overlay panel (bounding boxes as percentage-positioned divs).
- Searchable element table with all columns from §2.4.
- Hierarchy/depth column with depth-level indentation cue.
- Diagnostics card.
- Self-contained styling (no external CDN dependencies).

---

## 10. Error Handling Plan

| Failure | Detection | Action |
|---|---|---|
| `adb` not found | `which adb` / `subprocess` raises `FileNotFoundError` | Fail immediately with actionable message and non-zero exit |
| No connected devices | `adb devices` returns empty device list | Fail with guidance; log to `errors` |
| Multiple devices without `--serial` | `adb devices` returns > 1 | Fail; require explicit serial; log to `errors` |
| ADB command non-zero exit | `returncode != 0` | Log full entry to `adb_command_log`; add to `errors`; fail run |
| Malformed XML | `ET.ParseError` | Log to `errors`; fail run |
| Missing XML root or no node elements | Post-parse check | Log to `warnings` if count is 0 |
| `xml_node_count != element_count` | Post-parse assertion | Log to `errors`; set `parity_assertions.xml_equals_elements = false`; fail run |
| Schema validation failure | Post-build check | Log to `diagnostics.validation`; fail run |
| File write failure | `OSError` on write | Log to `errors`; keep partial artifacts; fail run |

---

## 11. File-by-File Implementation Map

Only these six existing files change:

### `scripts/current_screen_report.py`

- Restructure into four isolated layers: ADB transport, XML parser/normalizer, canonical model builder, renderer.
- Implement full `ScreenSnapshotModel` population using all fields in §2.
- Apply all nullability normalization rules from §3.
- Add `xml_node_count` computation and parity assertion.
- Add `element_id` hash computation per §4.2.
- Emit provenance root fields (`null`, `null`, `null`).
- Expand ADB command log to include `stdout`, `started_utc`, `finished_utc`.
- Derive `screen_width` and `screen_height` from `adb shell wm size` or element bounds max.
- All three renderers (JSON, MD, HTML) operate only on the in-memory model.

### `scripts/run_capture_pipeline.py`

- Reframe as v1 current-screen orchestrator only.
- Remove diff as default/primary behavior.
- Keep diff as opt-in auxiliary only, clearly labeled.
- Wrapper: call `generate_report()`, print output paths, exit cleanly.

### `templates/screen-snapshot.schema.json`

- Align schema to full canonical model from §2.
- Encode explicit nullability (`["string","null"]` unions) for all nullable fields.
- Add `origin` with all three nullable fields.
- Add `summary.xml_node_count` and `summary.parity_assertions`.
- Add `diagnostics.errors` array.
- Add `diagnostics.validation` object.
- Add `element.identity_version`, `element.xml_index_preorder`, `element.view_type_hint`.
- Add `element.source_attributes` and `element.source_attributes_extra`.
- Add `element.candidacy_reasons`.
- Add `context.screen_density`.
- Add individual flag counts to `summary` (`long_clickable_count`, `focused_count`, `selected_count`, `checkable_count`, `password_count`).
- Extend `adb_command_log` entry with `stdout`, `started_utc`, `finished_utc`.

### `templates/report-template.md`

- Expand element table to include all fields from §2.4.
- Add provenance section.
- Add parity assertion line.
- Add `xml_node_count` alongside `element_count`.
- Add `interaction_candidates` summary section.
- Ensure diagnostics includes `errors` section.
- No truncation; full catalog is mandatory.

### `templates/report-template.html`

- Expand element table to all columns in §2.4.
- Add `depth` column with visual indent cue (padding or symbol).
- Add `normalized_path` column.
- Add `source_attributes` expandable cell (toggle or truncation with title tooltip).
- Add parity assertion badge in summary card.
- Include `errors` in diagnostics card.
- Screenshot overlay: percentage-positioned divs, hover highlight.
- No external CDN dependencies.

### `README.md`

- Update v1 scope description:
  - Exhaustive single-screen capture; every node preserved.
  - Canonical in-memory model drives all three output formats.
  - Provenance fields present; `origin.*` is null for root v1 capture.
  - Interaction candidacy fields populated; no interaction execution in v1.
  - Diff is auxiliary only; not part of v1 standard output.

---

## 12. Explicit Non-Goals

1. No interaction execution (tap, long-tap, scroll, swipe, input).
2. No recursive crawl or multi-screen traversal.
3. No state graph or loop-avoidance implementation.
4. No diff/change detection as primary artifact.
5. No speculative refactors beyond files listed in §11.
6. No non-Android transport flows (SSH deferred).

---

## 13. Acceptance Checklist for @python-implementer

Before marking implementation complete, verify all of the following:

- [ ] Every element field in §2.4 is present in `screen-snapshot.json` output.
- [ ] Missing XML attributes are normalized to `null` or `""` exactly per §3; no key is absent.
- [ ] `element_id` is computed from normalized path + structural fields + bounds; text is excluded.
- [ ] JSON, Markdown, and HTML element counts are equal for the same run.
- [ ] `xml_node_count == element_count` is asserted; mismatch causes non-zero exit.
- [ ] `capture.origin.parent_capture_id` is `null` in v1 root capture.
- [ ] `capture.origin.interacted_element_id` is `null` in v1 root capture.
- [ ] `capture.origin.action_type` is `null` in v1 root capture.
- [ ] `is_interaction_candidate` and `action_types` are populated; no interaction executed.
- [ ] ADB log includes `command`, `exit_code`, `stdout`, `stderr`, `started_utc`, `finished_utc`.
- [ ] `adb not found` causes immediate informative failure.
- [ ] `multiple devices without --serial` causes immediate informative failure.
- [ ] `xml_node_count` field is present in `summary`.
- [ ] `parity_assertions.xml_equals_elements` field is present in `summary`.
- [ ] `diagnostics.errors` array is present and non-null.
- [ ] `diagnostics.validation` object is present.
- [ ] Exactly the six files in §11 are modified; no other files changed.
