# Scripts

## current_screen_report.py

Captures the active Android screen and generates synchronized artifacts:

- `screen-snapshot.json` (canonical dataset)
- `report.md` (narrative report)
- `report.html` (searchable visual report)
- `report.html` includes screenshot overlay boxes for extracted element bounds.

### Usage

```bash
python scripts/current_screen_report.py
```

### Options

- `--serial <device_serial>`: target one device when multiple are connected.
- `--output-dir <path>`: change output root (default: `output/captures`).
- `--adb-root <auto|required|never>`: root policy for ADB shell commands.
  - `auto` (default): attempts `adb root`; continues if unavailable.
  - `required`: fails fast if root is unavailable.
  - `never`: skips root attempt.

### Output Structure

`output/captures/<capture-id>/`

- `window_dump.xml`
- `screen.png`
- `screen-snapshot.json`
- `report.md`
- `report.html`

## diff_captures.py

Compares two `screen-snapshot.json` files and reports element changes by normalized `path`.

### Usage

```bash
python scripts/diff_captures.py output/captures/<old-id>/screen-snapshot.json output/captures/<new-id>/screen-snapshot.json
```

Markdown output:

```bash
python scripts/diff_captures.py output/captures/<old-id>/screen-snapshot.json output/captures/<new-id>/screen-snapshot.json --format md
```

Write to file:

```bash
python scripts/diff_captures.py output/captures/<old-id>/screen-snapshot.json output/captures/<new-id>/screen-snapshot.json --format md --output output/captures/diff-report.md
```

## run_capture_pipeline.py

Runs current-screen capture and then auto-generates a diff against a previous capture.

### Usage

```bash
python scripts/run_capture_pipeline.py
```

Target a specific device:

```bash
python scripts/run_capture_pipeline.py --serial <device_serial>
```

Require root on the target device:

```bash
python scripts/run_capture_pipeline.py --serial <device_serial> --adb-root required
```

Diff with a specific capture directory:

```bash
python scripts/run_capture_pipeline.py --diff-with output/captures/<old-id>
```

Skip diff generation:

```bash
python scripts/run_capture_pipeline.py --skip-diff
```

### Notes

- Requires `adb` available in `PATH`.
- If more than one device is connected, `--serial` is required.
- v1 scope is current-screen only (no recursive navigation).

## run_tests_report.py

Runs the offline pytest suite (unit + component) without ADB, aggregates results,
and renders a self-contained HTML report.

### Usage

```bash
python scripts/run_tests_report.py
```

Forward extra arguments to pytest after `--`:

```bash
python scripts/run_tests_report.py -- -k candidacy -vv
```

### Output

`output/test-results/`

- `junit.xml`         — raw JUnit XML.
- `test-results.json` — aggregated machine-readable summary.
- `test-report.html`  — human-readable report (uses `templates/test-report-template.html`).

### Notes

- No real ADB or device is exercised; transport functions are tested via `subprocess.run` monkeypatching.
- Exit code mirrors pytest's exit code (0 on green).
