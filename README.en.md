# F1Pro EC Control

English | [中文](README.md)

An EC control plugin for the OneXFly F1 Pro / HX 370 handheld under SteamOS (Decky Loader).

It covers two groups of features: **charge control** (bypass-power switch + charge limit) and **fan control** (five modes + a custom temperature curve).

> Repository: <https://github.com/Phioly/F1Pro-EC-Control>
>
> **The old name `F1 Pro Battery Control` has been renamed.** Decky treats the old and new
> names as two different plugins, so upgrade by removing the old one first — see
> "Upgrading from an older version" at the end of this file.

**Current version**: `v0.6.13` · **Requires**: SteamOS + Decky Loader · **Does not touch** TDP / CPU scheduling (coexists with SimpleDeckyTDP)

**UI language**: follows the Steam client — Simplified / Traditional Chinese show Chinese, every other language falls back to English.

**Just want to install it?** Skip to [Installation](#installation) → [Upgrading from an older version](#upgrading-from-an-older-version-important).
**Something wrong?** See [Troubleshooting](#troubleshooting) first, then [Log locations](#log-locations).

---

## Table of contents

- [Features](#features) — two tables: the kernel values written, and what the UI looks like
- [Prerequisites](#prerequisites) — which kernel and which device you need
- [Installation](#installation) — ZIP / one-shot script / manual copy / uninstall, including [Upgrading from an older version](#upgrading-from-an-older-version-important)
- [Usage](#usage) — [What the status line shows](#what-the-status-line-shows), [How the charge limit relates to each mode](#how-the-charge-limit-relates-to-each-mode-f1-pro-specific-behaviour-easy-to-get-wrong), [The UI](#the-ui)
- [Fan control](#fan-control) — five modes, custom curves, [`pwm1_enable` values are driver-dependent](#pwm1_enable-values-are-driver-dependent-easy-to-get-wrong), [Behaviour contract](#behaviour-contract)
- [Log locations](#log-locations) — where to grab logs when something breaks
- [Troubleshooting](#troubleshooting) — symptom → cause table
- [License](#license)

---

## Features

| Feature | Kernel value written | Notes |
| --- | --- | --- |
| 🔋 Normal charging | `auto` | Charges normally and honours the charge limit |
| ⚡ Bypass while awake | `inhibit-charge-awake` | Stops charging while the device is awake and runs straight off the adapter; **the inhibit is lifted once the device sleeps or powers off, so it charges from the adapter again and follows the charge limit** (F1 Pro oxpec-specific behaviour, see below) |
| 🔌 Always bypass | `inhibit-charge` | Never charges — running, sleeping, or off. Suited to being permanently plugged in |
| 🔢 Charge limit | `charge_control_end_threshold` | 50 / 60 / 70 / 80 / 85 / 90 / 95 %, plus "unlimited" |
| 🌀 Fan control | `pwm1` / `pwm1_enable` of `oxp_ec` | Auto / Quiet / Balanced / Performance / Custom, five-point (temperature → PWM) curve |
| ♻️ Restore on startup | — | Re-applies the last saved mode, limit, and fan mode when Decky starts (can be disabled) |
| 🔔 Error notification | — | Only failures raise a notification; a successful switch is silent |

* After every write the plugin **reads the kernel node back** to confirm the change actually took effect, and reports an explicit error instead of pretending it succeeded.
* State is polled every 5 seconds, so what you see in the UI is the real state in the kernel.
* Charge control does not depend on HHD / Anatase / Loadout and **does not write TDP / CPU scheduling**
  (it never touches `platform_profile`, EPP, or similar nodes), so it coexists with SimpleDeckyTDP.
* Fan control only reads and writes `pwm1` / `pwm1_enable` on `oxp_ec`; temperature is **read-only** from
  `k10temp`. It likewise never touches TDP. See "Fan control" below.
* It must run as root: the `flags` field in `plugin.json` has to be `["root"]`, **never** the invalid
  spelling `_root`; decky-loader only matches `"root" in flags` exactly, so writing `_root` never works
  and silently demotes the plugin — the UI looks fine, but every write fails with `EACCES`.

## Prerequisites

The plugin relies on the standard Linux `power_supply` interface:

```bash
cat /sys/class/power_supply/BAT0/charge_behaviour
```

On an F1 Pro the output should look like:

```text
auto inhibit-charge [inhibit-charge-awake]
```

The value in brackets is the one currently in effect. If this command reports "No such file", the
running kernel does not provide a charge-control driver; the plugin then says so at the top of its
UI rather than failing silently.

### Semantic boundary: what the kernel ABI says vs. what the F1 Pro actually does

This section matters because the two are **not the same thing**, and mixing them up leads to wrong conclusions.

**① The generic kernel ABI definition** (`Documentation/ABI/testing/sysfs-class-power`) only goes this far:

| Value | Definition in the ABI |
| --- | --- |
| `auto` | Charges normally, **and honours** the charge limit |
| `inhibit-charge-awake` | Inhibits charging **only while the device is awake** |
| `inhibit-charge` | Inhibits charging in every state |

For `inhibit-charge-awake` the ABI only states "inhibit while awake" — it **makes no promise** about
what happens after sleep or power-off, and certainly no promise about whether the charge limit is still
honoured. That is an implementation detail of each machine's driver, and cannot be generalised.

**② The F1 Pro's actual behaviour** comes from `drivers/platform/x86/oxpec.c` (the OneXPlayer EC driver;
the F1 is classified under `oxp_fly`). That driver maps `charge_behaviour` straight onto the EC's
charge-inhibit register and handles `charge_control_end_threshold` **separately**. Measured on an F1 Pro:

> Charging pauses while the device is awake; once it sleeps or powers off, the awake-state charge
> inhibit is lifted, so charging resumes from the adapter and the charge limit is governed by the
> EC's threshold setting.

So "bypass-while-awake resumes charging after sleep and stops at the configured limit" is
**specific behaviour of oxpec + the EC**, not a generic promise of the `charge_behaviour` ABI. On a
different machine whose driver implements the same ABI, post-sleep behaviour can easily differ.

The section "How the charge limit relates to each mode" below gives the register-level details of ②
(specific to the F1 Pro). Do not read that table as a general Linux rule.

## Installation

### Option 1: install the ZIP from Decky (recommended)

1. Copy `F1ProECControl-v0.6.13.zip` to the handheld.
2. Open Decky → Settings (gear) → **Developer** → enable "Developer Mode".
3. On that page pick **Install Plugin from ZIP** (or paste the local path of the zip into "Install from URL") and select the zip.
4. Back in the QAM, **F1Pro EC Control** appears in the plugin list.

### Option 2: one-shot script

Extract it in the SteamOS desktop session and run:

```bash
chmod +x install.sh
./install.sh
```

The script copies the plugin into `~/homebrew/plugins/` and restarts Decky.

### Option 3: manual copy

```bash
sudo cp -r "F1Pro EC Control" ~/homebrew/plugins/
sudo systemctl restart plugin_loader
```

### Uninstall

```bash
sudo rm -rf ~/homebrew/plugins/"F1Pro EC Control"
sudo systemctl restart plugin_loader
```

The settings file lives at `~/homebrew/settings/F1Pro EC Control/f1pro-ec-control.json` and can be deleted too.

### Upgrading from an older version (important)

The old name `F1 Pro Battery Control` and the new name are two **independent** plugins: Decky
distinguishes them by folder name. If you don't remove the old one, the QAM will show both entries,
and both will fight over the same EC — two control threads overwrite each other's `pwm1` and the fan
stutters.

Pick one of the two approaches:

**A. Uninstall the old plugin first (recommended)**

```bash
sudo rm -rf ~/homebrew/plugins/"F1 Pro Battery Control"
sudo systemctl restart plugin_loader
```

Then install the new version through Decky as described above. The old settings file
(`~/homebrew/settings/F1 Pro Battery Control/`) can be deleted outright — the new plugin uses a new
settings directory and never reads it:

```bash
rm -rf ~/homebrew/settings/"F1 Pro Battery Control"
```

**B. Use the one-shot script**: `install.sh` cleans up the legacy directory automatically (see
`LEGACY_PLUGIN_NAMES` in the script). The old settings directory still has to be removed by hand
with the line above.

> Decky runs `dist/index.js`. If you copy **straight from this repository's source tree**, make sure
> `dist/` travels with it, otherwise the plugin installs but the UI stays blank.

## Usage

QAM → Decky → F1Pro EC Control:

* **Battery status**: capacity, plus the `status · power · limit N%` line (when plugged in and the
  battery is neither charging nor discharging, the status reads `on adapter`), with the currently
  active charge mode underneath.
* **Charge mode**: three buttons; a ✓ appears in front of the one currently in effect.
* **Charge limit**: a dropdown. Choosing "unlimited" writes 100 % and removes the limit.
* **Settings**: whether Decky should restore the saved state on startup.
* **Diagnostics**: the battery node actually in use, the external-power node, the raw kernel `status`,
  the raw `charge_behaviour` string, the raw `power_now`, and whether the limit node exists —
  hand this section over verbatim when debugging kernel differences.

### A note on notifications: no toast on success

Decky's toaster doesn't just pop up in the bottom-right corner — it also files the notification in
Steam's notification centre. Toast on every switch and the centre rapidly fills with a long list of
"switched to…".

This plugin **only notifies on failure**, and failure toasts are **deliberately kept** in the
notification centre so you can look back at the cause afterwards (failures are rare, so they don't
pile up). A successful switch raises nothing at all — the backend re-reads the kernel node after every
write, so the mode and limit shown in the UI *are* the real kernel state and already serve as feedback.

Failure notifications always use the title **`F1Pro EC Control`**.

### A note on the ownership of the plugin directory

`install.sh` writes the plugin directory with `sudo cp`, so it runs this once the copy is done:

```bash
sudo chown -R "$TARGET_OWNER:$TARGET_GROUP" "$DEST_DIR"
```

Otherwise the files end up owned by `root:root`. The plugin itself runs as root (`flags: ["root"]`),
but Decky also has to list the directory, read assets, and perform updates and deletions as an
ordinary user, and a root owner restricts all of that.

Ownership is resolved with a three-level fallback (earlier wins): `SUDO_USER` → the owner of the
source directory (`stat`) → `$USER`. **The middle level is not redundant**: if the script runs as root
(`su -`, or invoked from systemd), both `SUDO_USER` and `USER` are `root`, and a plain
`${SUDO_USER:-$USER}` silently does the wrong thing.

### What the status line shows

The status line has the form `status · power · limit N%`, and **power only appears when the battery is
genuinely charging or discharging**:

| Display | When it appears | Example |
| --- | --- | --- |
| `charging · 45.9 W` | Charging | `charging · 45.9 W · limit 100%` |
| `on adapter` | **External power is running the device and the battery is neither charging nor discharging**: bypass mode (bypass-while-awake / always-bypass), or normal charging with the battery already at the configured limit | `on adapter · limit 100%` |
| `discharging · -37.6 W` | The charger is unplugged, or the battery is discharging in normal charging mode | `discharging · -37.6 W · limit 100%` |
| `full` | Fully charged and no longer drawing power | `full · limit 100%` |
| `unknown` | The kernel `status` cannot be read | `unknown · limit 100%` |

`on adapter` merges two causes because their physical state is identical — the adapter runs the
device and the battery sits idle. **The only difference is *why* it isn't charging** (the kernel
inhibited it vs. the limit was reached), and that information already appears on the next line under
"current mode", so it doesn't need repeating in the status line. Hence the criterion:
**external power is online and the kernel reports neither charging nor discharging**.

The last three (`on adapter` / `full` / `unknown`) **don't show watts**: the battery current is ≈ 0 in
those cases, `power_now` no longer carries real-time meaning, and the reading tends to be a stale value
from before the switch — displaying it would only mislead.

> The one exception: external power is **confirmed unplugged** (`online = 0`) yet the kernel still says
> `Not charging`. That's a self-contradictory reading; instead of claiming "on adapter", the plugin
> shows `not charging` — a state that shouldn't occur outside hardware faults.

### What the power figure in the status line means

It is the raw reading of the kernel's `power_now` (in µW, converted to W by the plugin), i.e. the
power on the **battery side**:

* **Positive** = current flowing into the battery (charging), e.g. `charging · 46.0 W`
* **Negative** = current flowing out of the battery (discharging), e.g. `discharging · -37.6 W`

> The minus sign is added by the plugin: the ACPI battery driver (`drivers/acpi/battery.c`) takes the
> absolute value of `_BST`'s rate (`battery->rate_now = abs((s16)battery->rate_now)`), so the
> `power_now` you read from this node is **always positive** and the direction can only be inferred
> from `status`. The plugin prepends a minus sign when `status` is `Discharging`, and leaves a
> negative value from the kernel untouched.

It is **not the input power of the charger/adapter**. When plugged in, this number is usually clearly
below what a meter on the charger side shows: the adapter first runs the device, and only the
remainder goes into the battery. In one measurement the charger side read 62.7 W while the battery side
read about 46 W — the difference is the device's own consumption.

**To tell whether bypass is really working, watch a meter on the charger side** (it dropping to
single digits, ≈ the idle draw of the device, means bypass succeeded). The plugin hides its wattage
figure in bypass mode.

Once the charger is unplugged, bypass is meaningless by definition and the status line returns to a
real reading such as `discharging · -37.6 W`.

### How the charge limit relates to each mode (F1 Pro-specific behaviour, easy to get wrong)

> The table and conclusions below describe the **implementation of the oxpec driver on the F1 Pro**,
> not generic Linux semantics (the distinction is covered in "Semantic boundary" above).

The charge mode and the charge limit are both written into EC registers by the OneXPlayer EC driver
`drivers/platform/x86/oxpec.c`. The two are **independent** — there is no simple rule like "the limit
stops mattering while bypassing":

| EC register | `auto` | `inhibit-charge-awake` | `inhibit-charge` |
| --- | --- | --- | --- |
| `0xA4` charge inhibit<br>(bit0 = inhibit while awake, bit1 = also inhibit while off) | `0x00` | `0x01` | `0x03` |
| `0xA3` charge limit | honoured | **honoured only while sleeping / off** | never charges, so it has no effect |

* **Normal charging** (`auto`, inhibit bits `0x00`): `0xA4` doesn't inhibit, so the decision is left to
  the EC's limit logic and charging stops at the configured limit.
* **Bypass while awake** (`inhibit-charge-awake`, inhibit bits `0x01`): only the "inhibit while awake"
  bit is set, so the EC doesn't charge while the device is awake; once it sleeps or powers off the bit
  no longer applies and the EC charges according to the `0xA3` threshold as usual.
* **Always bypass** (`inhibit-charge`, inhibit bits `0x03`): sets both the "inhibit while awake" and
  "inhibit while off" bits, so the EC never enters its charging path in any state — which is why the
  limit genuinely does nothing there.

In other words, the limit is only idle in the "always bypass" mode; normal charging and
bypass-while-awake both have it enforced by the EC. The plugin therefore adds **no** explanatory text
next to the limit in the UI.

## Fan control

The fan and the charger are handled by the **same EC driver**, `drivers/platform/x86/oxpec.c`. The
hwmon device it registers is named `oxp_ec` and exposes just three attributes: `fan1_input` (speed),
`pwm1` (duty cycle 0–255), and `pwm1_enable`. The driver has **no temperature channel**, so the CPU
temperature is read from AMD's `k10temp` (`temp1_input`, Tctl).

Nodes are always looked up by the hwmon `name`, **never by hwmon index** — indices shift with kernel
versions and driver bind order (`hwmon4`/`hwmon5` can become `hwmon3`/`hwmon7`).

### The UI

* The **main page** lays out `battery → charge mode → charge limit → fan → settings → diagnostics` as a
  single scrolling column. The fan block directly gives you: CPU temperature / fan speed / **current
  PWM**, the active mode and how it's being controlled, and **five mode buttons**
  (Auto / Quiet / Balanced / Performance / Custom) — so you don't have to enter a sub-page just to
  change curve profile mid-game.
* **Each of the five mode buttons takes its own row**, the same width as "Edit fan curve".
  A grid is **deliberately avoided** here: laying them out in two columns hit two real-device problems
  in turn — with a bare `1fr`, the min-content width inside `ButtonItem(layout="below")` blew the column
  out and the whole row overflowed the QAM panel (the rightmost button got clipped); switching to
  `minmax(0, 1fr)` stopped the overflow but made **the background blocks of adjacent buttons overlap
  and the labels stack on top of each other**. `ButtonItem` carries another layer of width constraint
  the plugin can't reach, so we stopped fighting it.
* Only **editing a curve** opens a sub-page. The sub-page **no longer repeats the fan status and fan
  mode** (the main page already has them); it goes straight to the five-point curve editor, then
  **"Save as custom and apply"**, and finally the **curve presets** (apply the Quiet / Balanced /
  Performance curves, plus **restore the default curve**).
* **Preset buttons only load a curve into the sliders; they never write to the EC directly.**
  Previously, tapping "apply Quiet curve" switched to *Quiet mode* (the EC ran the preset) while the
  sliders still showed the old curve — what the user saw and what was actually running were two
  different things. Now a preset only fills in the slider values so the user can see what that curve
  looks like and keep fine-tuning it; making it take effect is always done by "Save as custom and
  apply". "Restore the default curve" works the same way — it just puts the default seed curve from the
  backend back into the sliders.
* Page switches use the plugin's internal `view` state; **Decky Router is not used** — one sub-page
  isn't worth an extra routing dependency.
* Curves are edited with 5 pairs of sliders (one "temperature" and one "PWM" each), with no charting or
  drag-and-drop library. A real 2-D curve plot is a possible follow-up; the current version prioritises
  controls with stable, predictable behaviour.
* **A temperature slider is clamped between its neighbours**: each point's usable range is
  `[left neighbour + 1, right neighbour - 1]`, and the bounds are printed right on the slider label.
  Out-of-range values **can't even be dragged to**, which beats dragging and then getting an error; when
  adjacent points end up touching, that slider is greyed out automatically.
* **A mis-ordered curve is an error, never silently re-sorted**: an earlier implementation sorted the
  points by temperature before saving, so when the user dragged point 1 from 40 °C past point 2's 50 °C,
  the curve was silently reordered to `[50 °C, 75] / [55 °C, 40]` — the temperatures were ascending, but
  the **PWMs were inverted** (40 at 45 °C, 75 at 50 °C). That amounts to quietly mangling the user's
  settings. The plugin now **validates ascending order as-is** and, if it fails, reports the problem and
  refuses to save rather than guessing on the user's behalf.
* **Dragging a slider is not overwritten by the 5-second poll**: server values are pushed into the UI
  only when the user hasn't touched the curve, otherwise polling would yank a half-dragged slider back to
  the old value.
* **Switching to a preset mode changes the local curve immediately**: tapping
  "Quiet / Balanced / Performance" also sets the sliders to that preset's points, so you don't wait up to
  5 seconds for the next poll to see the change. Switching to "Auto / Custom" leaves the curve alone —
  Auto has no curve, and Custom's curve is filled in by the server, so changing it locally would
  overwrite what the user is editing.
* When the fan is unavailable (the machine lacks these nodes, or the driver identity can't be confirmed),
  the whole block **degrades to a single line stating the reason** — it **won't display a row of dead
  buttons** and **won't raise an error every 5 seconds**.
* "Save and apply" saves the curve first and then switches to "Custom" mode, so the curve is definitely
  in effect rather than "saved but unused".
* The preset buttons (Quiet / Balanced / Performance) **switch the fan to that mode**; they do not copy
  the curve into a custom one.

### `pwm1_enable` values are driver-dependent (easy to get wrong)

| Value written | `oxpec` (this machine) | Legacy driver `drivers/hwmon/oxp-sensors.c` |
| --- | --- | --- |
| `1` | manual PWM | manual PWM |
| `2` | **EC automatic control** | `EINVAL` |
| `0` | **switches to manual and slams PWM to 255, full speed** | **EC automatic control** |

Writing the wrong value doesn't raise an error, but it leaves the EC stuck at manual full speed. The
plugin therefore **confirms the driver identity first** (does the battery node expose
`charge_behaviour` / a charge limit? that's unique to oxpec) before it will write anything fan-related;
if it can't confirm, fan writes are disabled — better to control nothing than to guess.

That gate is **checked at every step**, not just once at the entrance: the control thread runs in the
background for a long time, and the hardware criteria can change during that time (the battery node
disappears, the driver gets swapped), so `_fan_step()` re-confirms on its own — writing a bad
`pwm1_enable` is silent, and the cost of one extra check is far below the risk of leaving the EC at
manual full speed.

There's also a **read-back trap**: when reading `pwm1_enable`, the driver **returns `0` instead of `1`**
if it's in manual mode and PWM happens to be exactly 255 (`Return 0 if at full fan speed, 1 otherwise`).
So "is it manual?" must accept `{0, 1}`; accepting only `1` would misreport a successful operation as a
failure whenever the fan is at full speed.

### Behaviour contract

* **Auto**: hand back to the EC by writing `pwm1_enable = 2`.
* **Quiet / Balanced / Performance / Custom**: write `pwm1_enable = 1`, then adjust every 3 seconds via
  "CPU temperature → five-point curve → linear interpolation → PWM".
* **Write deadband**: the EC is only written when the target PWM differs from the current PWM by ≥ 8,
  otherwise temperature jitter would hammer the register. The deadband **applies only to the
  interpolated curve result** and never intercepts the safety rule below.
* **Safety rule**: at **85 °C** the fan always goes to full speed 255. **No custom curve can override
  it**, and it is **written unconditionally, outside the write deadband**. The reason: near the safety
  threshold the interpolation itself can already be very high (e.g. 252 at 84.8 °C), so stepping to
  85 °C gives a target of 255 that differs from the current value by only 3 and gets rejected by the
  deadband; and as long as the interpolation stays around 250, every subsequent round is rejected
  again — the fan would never reach full speed and the safety rule would be meaningless. The cost of
  one extra write of 255 is far below leaving the CPU above 85 °C without full airflow.
* **Control self-healing**: if the EC or another tool flips `pwm1_enable` back to automatic, the plugin
  switches it back to manual on the next round (otherwise continuing to write `pwm1` does nothing, and
  the UI would claim "manual" while the EC is actually automatic).
* **Failures still hand back to the EC**: if any step fails while switching mode or curve, the plugin
  stops the control thread, writes `pwm1_enable` back to `2`, and reverts the saved mode to "Auto". It
  never leaves a state of "manual PWM written but nothing is controlling it" — that is more dangerous
  than simply handing over to the EC. The saved mode is reverted too, otherwise the next startup would
  restore manual mode all over again.
* **Failure paths go through the gate as well**: the control thread's exception branch **does not write**
  `pwm1_enable` itself but funnels through `_fan_fail_safe()`, which re-checks the driver identity. The
  reason is that one of the sources of exceptions is the gate failing in the first place — at that
  moment the driver identity is unconfirmable, and since `pwm1_enable` values are driver-dependent,
  writing `2` might be interpreted as manual by some other driver. When the gate is broken, fail-safe
  writes no fan node at all, which is exactly the semantics we want.
* **No early return on "looks like it's already automatic" when handing back**: `sysfs` read-back can lag
  behind the EC register, so skipping the write based on a single read would leave the fan in manual
  mode. Always write the expected value and then confirm — writing "automatic" twice is idempotent and
  far cheaper than missing a write.
* **Confirm after taking control back**: after noticing the EC reclaimed automatic control and writing
  `pwm1_enable = 1` again, the plugin must `_wait_for` confirmation that it really is manual. If the
  write is silently ignored and isn't confirmed, every later `pwm1` write is a no-op — the UI says
  manual and the temperature is being read, but the PWM never actually changes.
* **When the EC is handed back**: switching back to "Auto", unloading the plugin, and **on plugin
  startup when the EC is found left in manual mode**. The last one is a safety net: if the plugin
  crashed or was `kill -9`ed last time, the unload hook never ran and the EC stays at a manual PWM —
  in that state the fan doesn't follow temperature and the 85 °C rule is inert, so startup corrects it
  unconditionally, **independent of the "restore on startup" toggle**.
* A custom curve is five `[temperature °C, PWM]` points, and the temperatures must be **strictly
  ascending as entered** (out-of-order entries raise an error and are never re-sorted); ranges are
  30–100 °C / 40–255. The initial "Custom" curve is only an editable starting point, **not a
  recommended curve**.
* **The minimum PWM is 40 and the plugin offers no full stop (0 PWM)**. Even at 30 °C the curve floor is
  40 — so seeing "30 °C → PWM 40" is **by design, not a bug**. The rationale is that a handheld under
  light load still needs baseline airflow to carry away the heat soaking in the memory and power
  delivery modules, and the F1 Pro's EC doesn't behave fully predictably at 0 PWM. If you need the fan
  to stop entirely, switch back to "Auto" and let the EC decide.
* **Validation failures never touch the control state**: entering a duplicate temperature only says
  "not allowed to repeat" and the fan keeps running the old curve — the user may be tuning a curve in
  manual mode, and one mistyped number shouldn't hand control back to the EC. Only a failure in the
  "apply" step triggers the hand-back fallback above.

## Log locations

Plugin logs (Decky prunes them automatically, keeping the latest 5 timestamped `.log` files):

```bash
ls -lt ~/homebrew/logs/"F1Pro EC Control"/
tail -n 150 ~/homebrew/logs/"F1Pro EC Control"/*.log
```

Decky's own log (load failures and plugin crashes show up here):

```bash
journalctl -u plugin_loader -b --no-pager | tail -n 200
```

When debugging, hand over the contents of both places together.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| "No battery node supporting charge control found" | The kernel has no `charge_behaviour`; you need a kernel with oxpec / charge-control support |
| All three mode buttons are greyed out | Check the raw `charge_behaviour` string in diagnostics to see which values the kernel supports |
| The limit dropdown is greyed out | There is no `charge_control_end_threshold` node |
| "No permission to write to the battery control node" | `flags` in `plugin.json` doesn't contain `"root"` (**writing `_root` never works** — decky-loader only matches `"root" in flags` exactly, and `_root` is demoted: the UI looks fine but every write returns `EACCES`), or the plugin wasn't loaded through Decky |
| "The kernel rejected the write" | The kernel/EC doesn't accept that value; the error carries the specific errno |
| The limit was changed but the charge keeps rising | Bypass-while-awake still charges during sleep / power-off (F1 Pro oxpec behaviour) — this is expected; only "always bypass" stops charging entirely |
| No wattage figure while bypassing on adapter | Expected: `on adapter` shows no number; watch a meter on the charger side instead |
| Shows `on adapter` while plugged in and at the limit | Expected: the adapter runs the device and the battery neither charges nor discharges — the same physical state as bypassing |
| Shows `not charging` | A rare fallback: the external-power node reports unplugged while the kernel still says `Not charging`. Cross-check the "external power" and `status` rows in diagnostics |
| Shows `full` but the limit isn't 100 % | The kernel reported "stopped at the limit" as `Full` (rather than `Not charging`). Check `status` in diagnostics |

## License

MIT, see [LICENSE](LICENSE).

---

## Related links

| Purpose | Link |
| --- | --- |
| Source repository / file an issue | <https://github.com/Phioly/F1Pro-EC-Control> |
| Download the release package (ZIP) | <https://github.com/Phioly/F1Pro-EC-Control/releases> |
| Decky Loader | <https://github.com/SteamDeckHomebrew/decky-loader> |
| Kernel oxpec driver (the lower layer behind fan / battery) | <https://github.com/torvalds/linux/blob/master/drivers/platform/x86/oxpec.c> |
| Kernel ACPI battery driver (source of the `power_now` sign) | <https://github.com/torvalds/linux/blob/master/drivers/acpi/battery.c> |
| Generic hwmon `pwm1_enable` documentation (**note: `oxpec` does not match it**, see [above](#pwm1_enable-values-are-driver-dependent-easy-to-get-wrong)) | <https://docs.kernel.org/hwmon/sysfs-interface.html> |

## Back to top

[↑ Back to table of contents](#table-of-contents) · [↑ Top](#f1pro-ec-control)
