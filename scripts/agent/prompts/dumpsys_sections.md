# Useful `dumpsys` sections

Pick sections deliberately — each one can be expensive. Here's what
each section is good for:

| Section              | Useful for                                                   |
|----------------------|--------------------------------------------------------------|
| `activity`           | Activity manager state, top activity, recents.               |
| `activity_top`       | Alias for `dumpsys activity activities` — focused list.      |
| `package`            | Package manager (HUGE — prefer `inspect_package`).           |
| `display`            | Display devices, density, modes, HDR support.                |
| `input`              | Input devices, touch screens, keyboards.                     |
| `input_method`       | IME state.                                                   |
| `audio`              | Audio policy, devices, streams, focus.                       |
| `media.audio_flinger`| Audio HAL details, mixer threads, routing.                   |
| `media.audio_policy` | Audio policy manager rules.                                  |
| `battery`            | Battery state, charger, voltage, temperature.                |
| `battery_stats`      | Cumulative power statistics.                                 |
| `power`              | Power manager wake locks, idle state.                        |
| `deviceidle`         | Doze / app standby.                                          |
| `alarm`              | Scheduled alarms.                                            |
| `jobscheduler`       | Background jobs.                                             |
| `connectivity`       | Active networks, routes, validation state.                   |
| `wifi`               | Wi-Fi state, scan results, supplicant.                       |
| `bluetooth_manager`  | Bluetooth adapter, bonded devices.                           |
| `telephony.registry` | Cellular state.                                              |
| `netstats`           | Per-app network usage.                                       |
| `sensorservice`      | Available sensors, listeners.                                |
| `cpuinfo`            | CPU load by process.                                         |
| `meminfo`            | Memory usage breakdown.                                      |
| `procstats`          | Process stats over time.                                     |
| `usagestats`         | App usage events.                                            |
| `notification`       | Notification listeners, channels.                            |
| `user`               | User profiles (work, secondary, system).                     |
| `permissionmgr`      | Permission grants per package.                               |
| `settings`           | Currently-set Settings.* values.                             |
| `statusbar`          | Status bar icons, tiles.                                     |
| `window`             | Window manager, focused window, IME, rotation.               |
| `dropbox`            | System logs / crash records.                                 |
| `location`           | Location providers, last fixes.                              |

Sections you might think to call but cannot — they're not allowlisted
(call `run_shell` with a specific `cmd ...` invocation instead).
