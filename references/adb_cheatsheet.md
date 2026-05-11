# ADB Cheatsheet

## Connectivity

```bash
adb version
adb devices -l
adb -s <serial> get-state
```

## UI and Screen Capture

```bash
adb -s <serial> shell uiautomator dump /sdcard/window_dump.xml
adb -s <serial> pull /sdcard/window_dump.xml ./output/window_dump.xml
adb -s <serial> exec-out screencap -p > ./output/screen.png
```

## App Context

```bash
adb -s <serial> shell dumpsys window windows | grep -E "mCurrentFocus|mFocusedApp"
adb -s <serial> shell dumpsys activity activities | grep -E "ResumedActivity|mResumedActivity"
```

## Packages

```bash
adb -s <serial> shell pm list packages
adb -s <serial> shell pm list packages -3
adb -s <serial> shell dumpsys package <package_name>
```

## Logs

```bash
adb -s <serial> logcat -d
adb -s <serial> logcat -c
adb -s <serial> logcat <tag>:E *:S
```

## Input

```bash
adb -s <serial> shell input tap <x> <y>
adb -s <serial> shell input swipe <x1> <y1> <x2> <y2> <duration_ms>
adb -s <serial> shell input keyevent KEYCODE_BACK
```

## Notes

- Prefer explicit `-s <serial>` in automation.
- Always capture command stderr when collecting diagnostics.
