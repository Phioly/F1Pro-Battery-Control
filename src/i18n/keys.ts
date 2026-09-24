/**
 * 文案键的**唯一清单**。
 *
 * 用法：所有界面文案都必须从这里取键，不要直接写字面量 ——
 * `zh-CN.ts` 与 `en-US.ts` 都以它为约束（`Record<MessageKey, string>`），
 * 少一条、多一条都会在 `tsc --noEmit` 阶段报错。
 *
 * 命名规则：`<区域>.<用途>`，区域按界面分块（charge / fan / diag / common…）。
 * 需要插值的键在值里用 `{name}` 占位（见 `format()`）。
 */
export const MESSAGE_KEYS = [
  // ---- 通用 ----
  "common.unknown",
  "common.notAvailable",
  "common.notExist",
  "common.exists",
  "common.refresh",
  "common.reload",
  "common.hint",
  "common.diagnostic",

  // ---- 充电模式 ----
  "charge.mode.auto",
  "charge.mode.inhibitAwake",
  "charge.mode.inhibit",
  "charge.mode.forceDischarge",
  "charge.mode.desc.auto",
  "charge.mode.desc.inhibitAwake",
  "charge.mode.desc.inhibit",
  "charge.mode.desc.forceDischarge",

  // ---- 电池状态 ----
  "battery.section",
  "battery.status.charging",
  "battery.status.discharging",
  "battery.status.full",
  "battery.status.notCharging",
  "battery.status.supplying",
  "battery.summary",
  "battery.summary.noLimit",
  "battery.summary.watts",
  "battery.currentMode",
  "battery.noLimit",
  "battery.noLimitOption",
  "battery.chargeModeSection",
  "battery.limitSection",
  "battery.limitLabel",
  "battery.checked",

  // ---- 风扇 ----
  "fan.section",
  "fan.mode.auto",
  "fan.mode.quiet",
  "fan.mode.balanced",
  "fan.mode.performance",
  "fan.mode.custom",
  "fan.currentMode",
  "fan.manual",
  "fan.ecAuto",
  "fan.editCurve",
  "fan.unavailableDefault",
  "fan.noCurveForMode",
  "fan.curveSection",
  "fan.node",
  "fan.tempRange",
  "fan.saveAndApply",
  "fan.presetSection",
  "fan.applyPreset",
  "fan.restoreDefault",
  "fan.diag.controller",
  "fan.diag.tempSensor",
  "fan.diag.pwmNode",
  "fan.diag.pwmEnable",
  "fan.diag.pwmEnableLabel",
  "fan.diag.controlMethod",
  "fan.diag.safetyTemp",
  "fan.diag.safetyNote",
  "fan.back",

  // ---- 设置 ----
  "settings.section",
  "settings.autoRestore",
  "settings.autoRestoreDesc",

  // ---- 诊断 ----
  "diag.section",
  "diag.ac",
  "diag.acOnline",
  "diag.acOffline",
  "diag.acNoNode",
  "diag.batteryNodeMissing",
  "diag.thresholdNode",
  "diag.rawNode",
  "diag.reportSeparator",
  "diag.fan",
  "diag.fanUnavailable",
  "diag.fanManual",
  "diag.fanEcAuto",
  "diag.fanNotDetected",
  "diag.noAwakeBypass",

  // ---- 错误 / 提示 ----
  "error.readBattery",
  "error.readFan",
  "error.readState",
  "error.switchChargeMode",
  "error.setChargeLimit",
  "error.saveSettings",
  "error.switchFanMode",
  "error.curveNotIncreasing",
  "error.saveCurve",
  "error.curveSavedSwitchFailed",
] as const;

export type MessageKey = (typeof MESSAGE_KEYS)[number];

/**
 * 把 `{name}` 占位替换成实际值。
 *
 * 故意不引入 i18next：Decky 对插件没有官方 i18n（`@decky/ui` 里没有
 * 任何 translation 导出），而这里只需要"取表 + 填占位"两件事，
 * 自己实现反而更可控、也更容易离线测试。
 */
export function format(
  template: string,
  values?: Record<string, string | number>,
): string {
  if (!values) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    Object.prototype.hasOwnProperty.call(values, name)
      ? String(values[name])
      : whole,
  );
}
