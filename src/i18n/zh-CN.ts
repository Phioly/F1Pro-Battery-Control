/**
 * 简体中文文案表。
 *
 * 与 `en-US.ts` 的键必须**逐条一致**（由 `MessageKey` 类型约束）。
 * 繁体中文（`zh-TW` / `zh-HK` 等）走这份简体表 —— Steam 上
 * 繁体用户远少于简体，先共用，避免维护一份没人看的口语化用词。
 */
import type { MessageKey } from "./keys";

export const zhCN: Record<MessageKey, string> = {
  // ---- 通用 ----
  "common.unknown": "未知",
  "common.notAvailable": "无",
  "common.notExist": "不存在",
  "common.exists": "存在",
  "common.refresh": "刷新",
  "common.reload": "重新读取",
  "common.hint": "提示",
  "common.diagnostic": "诊断",

  // ---- 充电模式 ----
  "charge.mode.auto": "正常充电",
  "charge.mode.inhibitAwake": "开机旁路",
  "charge.mode.inhibit": "始终旁路",
  "charge.mode.forceDischarge": "强制放电",
  "charge.mode.desc.auto": "正常向电池充电，并遵循下方设置的充电上限。",
  "charge.mode.desc.inhibitAwake":
    "运行时停止充电、直接由电源供电；设备睡眠并接电时恢复充电。",
  "charge.mode.desc.inhibit": "运行和睡眠状态都停止充电，适合长期插电使用。",
  "charge.mode.desc.forceDischarge": "接电状态下强制放电。",

  // ---- 电池状态 ----
  "battery.section": "电池状态",
  "battery.status.charging": "充电中",
  "battery.status.discharging": "放电中",
  "battery.status.full": "已充满",
  "battery.status.notCharging": "未充电",
  "battery.status.supplying": "供电中",
  "battery.summary": "{status} · 上限 {limit}",
  "battery.summary.noLimit": "{status} · 上限 不限制",
  "battery.summary.watts": "{status} · {watts} · 上限 {limit}",
  "battery.currentMode": "当前模式：",
  "battery.noLimit": "不限制",
  "battery.noLimitOption": "不限制（充满到 {value}%）",
  "battery.chargeModeSection": "充电模式",
  "battery.limitSection": "充电上限",
  "battery.limitLabel": "停止充电电量",
  "battery.checked": "✓ {label}",

  // ---- 风扇 ----
  "fan.section": "风扇",
  "fan.mode.auto": "自动",
  "fan.mode.quiet": "静音",
  "fan.mode.balanced": "均衡",
  "fan.mode.performance": "性能",
  "fan.mode.custom": "自定义",
  "fan.currentMode": "当前模式：",
  "fan.manual": "（手动 PWM）",
  "fan.ecAuto": "（EC 自动控温）",
  "fan.editCurve": "编辑风扇曲线 →",
  "fan.unavailableDefault": "未检测到 oxpec 风扇控制接口",
  "fan.noCurveForMode": "没有可用的{label}曲线",
  "fan.curveSection": "自定义风扇曲线",
  "fan.node": "节点 {index} · {temp}°C",
  "fan.tempRange": "温度（{min}–{max}°C）",
  "fan.saveAndApply": "保存为自定义并应用",
  "fan.presetSection": "曲线预设",
  "fan.applyPreset": "应用{label}曲线",
  "fan.restoreDefault": "还原默认曲线",
  "fan.diag.controller": "控制器：{value}",
  "fan.diag.tempSensor": "温度传感器：{value}",
  "fan.diag.pwmNode": "PWM 节点：{value}",
  "fan.diag.pwmEnable": "pwm1_enable：{value}",
  "fan.diag.pwmEnableLabel": "（{value}）",
  "fan.diag.controlMethod": "控制方式：{value}",
  "fan.diag.safetyTemp": "安全阈值：{value}°C（达到即满速）",
  "fan.diag.safetyNote": "",
  "fan.back": "← 返回",

  // ---- 设置 ----
  "settings.section": "设置",
  "settings.autoRestore": "Decky 启动时自动恢复",
  "settings.autoRestoreDesc": "重新应用上次保存的充电模式、充电上限与风扇模式",

  // ---- 诊断 ----
  "diag.section": "诊断",
  "diag.ac": "外接电源：{value}",
  "diag.acOnline": "在线",
  "diag.acOffline": "离线",
  "diag.acNoNode": "无节点",
  "diag.batteryNodeMissing": "未找到电池节点",
  "diag.thresholdNode": "threshold 节点：{value}",
  "diag.rawNode": "{name}：{value}",
  "diag.reportSeparator": "；",
  "diag.fan": "风扇：{value}",
  "diag.fanUnavailable": "不可用",
  "diag.fanManual": "手动",
  "diag.fanEcAuto": "EC 自动",
  "diag.fanNotDetected": "未检测",
  "diag.noAwakeBypass": "内核未提供 inhibit-charge-awake",

  // ---- 错误 / 提示 ----
  "error.readBattery": "读取电池状态失败",
  "error.readFan": "读取风扇状态失败",
  "error.readState": "读取状态失败",
  "error.switchChargeMode": "切换充电模式失败",
  "error.setChargeLimit": "设置充电上限失败",
  "error.saveSettings": "保存设置失败",
  "error.switchFanMode": "切换风扇模式失败",
  "error.curveNotIncreasing": "曲线节点的温度必须严格递增（每个节点都要比前一个更高）",
  "error.saveCurve": "保存自定义曲线失败",
  "error.curveSavedSwitchFailed":
    "曲线已保存，但切换为自定义模式失败，尚未在风扇上生效",

  // ---- 启动恢复报告（与后端 `_MSG["zh"]` 里同名键保持一致）----
  // `{mode}` 由前端查 fan.mode.* / charge.mode.* 翻译成当前语言后填入。
  "restore.fanEcAuto": "风扇已确认处于 EC 自动控制",
  "restore.fanHandoverUnconfirmed": "风扇交还 EC 后未确认生效",
  "restore.fanModeRestored": "风扇模式已恢复为「{mode}」",
  "restore.fanManualUnconfirmed": "风扇切换为手动控制后未确认生效（目标「{mode}」）",
  "restore.autoOff": "启动自动恢复已关闭",
  "restore.batteryMissing": "未找到电池节点：{exc}",
  "restore.noThresholdNode": "内核无 charge_control_end_threshold，跳过上限恢复",
  "restore.thresholdRestored": "充电上限已恢复为 {value}%",
  "restore.thresholdUnconfirmed": "充电上限恢复后未确认生效（目标 {value}%）",
  "restore.noBehaviourNode": "内核无 charge_behaviour，跳过模式恢复",
  "restore.modeUnsupported": "内核不支持「{mode}」，模式未恢复",
  "restore.modeRestored": "充电模式已恢复为「{mode}」",
  "restore.modeUnconfirmed": "充电模式恢复后未确认生效（目标「{mode}」）",
  "restore.nothing": "没有需要恢复的设置",
  "err.batteryProbe": "未找到支持充电控制的电池节点",
  "err.restoreFailed": "{area}恢复失败：{detail}",
  "common.chargeMode": "充电模式",
  "common.chargeLimit": "充电上限",
  "common.fanControl": "风扇控制",
};
