/**
 * English (en-US) messages.
 *
 * Keys must match `zh-CN.ts` one-to-one — enforced by the `MessageKey` type.
 * This is also the **fallback** table: any language that is not Chinese
 * resolves here.
 */
import type { MessageKey } from "./keys";

export const enUS: Record<MessageKey, string> = {
  // ---- common ----
  "common.unknown": "Unknown",
  "common.notAvailable": "N/A",
  "common.notExist": "absent",
  "common.exists": "present",
  "common.refresh": "Refresh",
  "common.reload": "Reload",
  "common.hint": "Notice",
  "common.diagnostic": "Diagnostics",

  // ---- charge modes ----
  "charge.mode.auto": "Normal charging",
  "charge.mode.inhibitAwake": "Bypass while on",
  "charge.mode.inhibit": "Bypass always",
  "charge.mode.forceDischarge": "Force discharge",
  "charge.mode.desc.auto":
    "Charges the battery normally, respecting the charge limit below.",
  "charge.mode.desc.inhibitAwake":
    "Stops charging while running and runs off wall power; resumes charging when the device sleeps on AC.",
  "charge.mode.desc.inhibit":
    "Stops charging both while running and while asleep. Best for long-term docked use.",
  "charge.mode.desc.forceDischarge": "Forces discharge while plugged in.",

  // ---- battery status ----
  "battery.section": "Battery",
  "battery.status.charging": "Charging",
  "battery.status.discharging": "Discharging",
  "battery.status.full": "Full",
  "battery.status.notCharging": "Not charging",
  "battery.status.supplying": "On AC power",
  "battery.summary": "{status} · limit {limit}",
  "battery.summary.noLimit": "{status} · no limit",
  "battery.summary.watts": "{status} · {watts} · limit {limit}",
  "battery.currentMode": "Current mode: ",
  "battery.noLimit": "No limit",
  "battery.noLimitOption": "No limit (charge to {value}%)",
  "battery.chargeModeSection": "Charge mode",
  "battery.limitSection": "Charge limit",
  "battery.limitLabel": "Stop charging at",
  "battery.checked": "✓ {label}",

  // ---- fan ----
  "fan.section": "Fan",
  "fan.mode.auto": "Auto",
  "fan.mode.quiet": "Quiet",
  "fan.mode.balanced": "Balanced",
  "fan.mode.performance": "Performance",
  "fan.mode.custom": "Custom",
  "fan.currentMode": "Current mode: ",
  "fan.manual": " (manual PWM)",
  "fan.ecAuto": " (EC auto)",
  "fan.editCurve": "Edit fan curve →",
  "fan.unavailableDefault": "No oxpec fan control interface detected",
  "fan.noCurveForMode": "No {label} curve available",
  "fan.curveSection": "Custom fan curve",
  "fan.node": "Point {index} · {temp}°C",
  "fan.tempRange": "Temperature ({min}–{max}°C)",
  "fan.saveAndApply": "Save as custom and apply",
  "fan.presetSection": "Curve presets",
  "fan.applyPreset": "Load {label} curve",
  "fan.restoreDefault": "Restore default curve",
  "fan.diag.controller": "Controller: {value}",
  "fan.diag.tempSensor": "Temp sensor: {value}",
  "fan.diag.pwmNode": "PWM node: {value}",
  "fan.diag.pwmEnable": "pwm1_enable: {value}",
  "fan.diag.pwmEnableLabel": " ({value})",
  "fan.diag.controlMethod": "Control: {value}",
  "fan.diag.safetyTemp": "Safety threshold: {value}°C (full speed at or above)",
  "fan.diag.safetyNote": "",
  "fan.back": "← Back",

  // ---- settings ----
  "settings.section": "Settings",
  "settings.autoRestore": "Restore on Decky startup",
  "settings.autoRestoreDesc":
    "Re-apply the last saved charge mode, charge limit and fan mode",

  // ---- diagnostics ----
  "diag.section": "Diagnostics",
  "diag.ac": "AC adapter: {value}",
  "diag.acOnline": "online",
  "diag.acOffline": "offline",
  "diag.acNoNode": "no node",
  "diag.batteryNodeMissing": "Battery node not found",
  "diag.thresholdNode": "threshold node: {value}",
  "diag.rawNode": "{name}: {value}",
  "diag.reportSeparator": "; ",
  "diag.fan": "Fan: {value}",
  "diag.fanUnavailable": "unavailable",
  "diag.fanManual": "manual",
  "diag.fanEcAuto": "EC auto",
  "diag.fanNotDetected": "not detected",
  "diag.noAwakeBypass": "kernel does not provide inhibit-charge-awake",

  // ---- errors / notices ----
  "error.readBattery": "Failed to read battery status",
  "error.readFan": "Failed to read fan status",
  "error.readState": "Failed to read status",
  "error.switchChargeMode": "Failed to switch charge mode",
  "error.setChargeLimit": "Failed to set charge limit",
  "error.saveSettings": "Failed to save settings",
  "error.switchFanMode": "Failed to switch fan mode",
  "error.curveNotIncreasing":
    "Curve temperatures must strictly increase (each point higher than the previous one)",
  "error.saveCurve": "Failed to save custom curve",
  "error.curveSavedSwitchFailed":
    "Curve saved, but switching to Custom mode failed — not yet active on the fan",

  // ---- Startup restore report (mirrors the same keys in the backend table) ----
  "restore.fanEcAuto": "Fan confirmed under EC auto control",
  "restore.fanHandoverUnconfirmed":
    "Fan handover to the EC could not be confirmed",
  "restore.fanModeRestored": 'Fan mode restored to "{mode}"',
  "restore.fanManualUnconfirmed":
    'Fan switch to manual control could not be confirmed (target "{mode}")',
  "restore.autoOff": "Auto restore on startup is off",
  "restore.batteryMissing": "Battery node not found: {exc}",
  "restore.noThresholdNode":
    "Kernel has no charge_control_end_threshold; skipping limit restore",
  "restore.thresholdRestored": "Charge limit restored to {value}%",
  "restore.thresholdUnconfirmed":
    "Charge limit restore could not be confirmed (target {value}%)",
  "restore.noBehaviourNode":
    "Kernel has no charge_behaviour; skipping mode restore",
  "restore.modeUnsupported":
    'Kernel does not support "{mode}"; mode not restored',
  "restore.modeRestored": 'Charge mode restored to "{mode}"',
  "restore.modeUnconfirmed":
    'Charge mode restore could not be confirmed (target "{mode}")',
  "restore.nothing": "Nothing to restore",
  "err.batteryProbe": "No battery node supporting charge control was found",
  "err.restoreFailed": "{area} restore failed: {detail}",
  "common.chargeMode": "Charge mode",
  "common.chargeLimit": "Charge limit",
  "common.fanControl": "Fan control",
};
