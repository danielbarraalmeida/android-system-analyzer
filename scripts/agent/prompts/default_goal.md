Map this Android device's system state. Produce a comprehensive but
concise system profile suitable for future debugging and feature
discovery.

Cover at minimum:

- Device identity (manufacturer, model, build, SDK).
- Hardware / SoC.
- Notable installed packages — especially OEM, vehicle, vendor, or
  pre-installed-but-non-AOSP applications.
- Registered system services.
- Audio, display, and connectivity subsystem highlights.
- Any automotive-specific (`car_*`, `android.car.*`) presence.

When you've assembled enough evidence, call `finish` with a structured
markdown summary.
