# Android Screen Snapshot Report

## Capture Metadata

- Capture ID: `<capture_id>`
- Timestamp (UTC): `<timestamp_utc>`
- Device Serial: `<device_serial>`
- Package: `<package_name>`
- Activity: `<activity_name>`
- Screen Width: `<screen_width>`
- Screen Height: `<screen_height>`
- Screen Density: `<screen_density>`
- Screenshot: `<screenshot_path>`
- UI Dump: `<ui_dump_path>`

## Provenance

- Parent Capture ID: `<parent_capture_id>` _(null for root v1 captures)_
- Interacted Element ID: `<interacted_element_id>` _(null for root v1 captures)_
- Action Type: `<action_type>` _(null for root v1 captures)_

## Summary

- XML Node Count: `<xml_node_count>`
- Element Count: `<element_count>`
- Parity (xml_equals_elements): `<parity_ok>`
- Interaction Candidates: `<interaction_candidate_count>`
- Clickable: `<clickable_count>`
- Long Clickable: `<long_clickable_count>`
- Focusable: `<focusable_count>`
- Focused: `<focused_count>`
- Enabled: `<enabled_count>`
- Scrollable: `<scrollable_count>`
- Selected: `<selected_count>`
- Checked: `<checked_count>`
- Checkable: `<checkable_count>`
- Password: `<password_count>`

## Interaction Candidates

| element_id | normalized_path | Actions | Reasons |
|------------|-----------------|---------|---------|
| …          | …               | …       | …       |

## Element Catalog

_Full catalog — every UIAutomator node, no truncation._

| IDX | element_id | Ver | normalized_path | parent_path | D | Si | class_name | resource_id | package | view_type_hint | text | content_desc | hint | value | input_type | bounds_raw | L | T | R | B | W | H | CX | CY | C | LC | F | Fo | En | Sc | Se | Ch | Ck | Pw | Candidate | Actions | Reasons |
|-----|------------|-----|-----------------|-------------|---|----|----|-------------|---------|----------------|------|--------------|------|-------|------------|------------|---|---|---|---|---|---|----|----|---|----|----|----|----|----|----|----|----|-----------|---------|---------|
| …   | …          | …   | …               | …           | … | …  | …  | …           | …       | …              | …    | …            | …    | …     | …          | …          | … | … | … | … | … | … | …  | …  | … | …  | …  | …  | …  | …  | …  | …  | …  | …  | …         | …       | …       |

## Diagnostics

### ADB Command Log

| Command | Exit | Started (UTC) | Finished (UTC) | Stdout | Stderr |
|---------|------|---------------|----------------|--------|--------|
| …       | …    | …             | …              | …      | …      |

### Errors

- _(none, or list of fatal errors)_

### Warnings

- _(none, or list of non-fatal warnings)_

### Limitations

- v1 captures only the current visible screen.
- UIAutomator dump may omit privileged or secure overlays.
- No recursive navigation is performed in this version.
- Swipe candidacy threshold is implementation-defined (see `SWIPE_AREA_THRESHOLD`).

### Schema Validation

- Performed: `<schema_validation_performed>`
- Passed: `<schema_validation_passed>`
- Error: `<schema_validation_error>`
