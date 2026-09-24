const manifest = {"name":"F1Pro EC Control"};
const API_VERSION = 2;
const internalAPIConnection = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
if (!internalAPIConnection) {
    throw new Error('[@decky/api]: Failed to connect to the loader as as the loader API was not initialized. This is likely a bug in Decky Loader.');
}
let api;
try {
    api = internalAPIConnection.connect(API_VERSION, manifest.name);
}
catch {
    api = internalAPIConnection.connect(1, manifest.name);
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version 1. Some features may not work.`);
}
if (api._version != API_VERSION) {
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version ${api._version}. Some features may not work.`);
}
const callable = api.callable;
const toaster = api.toaster;
const definePlugin = (fn) => {
    return (...args) => {
        return fn(...args);
    };
};

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
/**
 * 把 `{name}` 占位替换成实际值。
 *
 * 故意不引入 i18next：Decky 对插件没有官方 i18n（`@decky/ui` 里没有
 * 任何 translation 导出），而这里只需要"取表 + 填占位"两件事，
 * 自己实现反而更可控、也更容易离线测试。
 */
function format(template, values) {
    if (!values)
        return template;
    return template.replace(/\{(\w+)\}/g, (whole, name) => Object.prototype.hasOwnProperty.call(values, name)
        ? String(values[name])
        : whole);
}

const zhCN = {
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
    "charge.mode.desc.inhibitAwake": "运行时停止充电、直接由电源供电；设备睡眠并接电时恢复充电。",
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
    "error.curveSavedSwitchFailed": "曲线已保存，但切换为自定义模式失败，尚未在风扇上生效",
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

const enUS = {
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
    "charge.mode.desc.auto": "Charges the battery normally, respecting the charge limit below.",
    "charge.mode.desc.inhibitAwake": "Stops charging while running and runs off wall power; resumes charging when the device sleeps on AC.",
    "charge.mode.desc.inhibit": "Stops charging both while running and while asleep. Best for long-term docked use.",
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
    "settings.autoRestoreDesc": "Re-apply the last saved charge mode, charge limit and fan mode",
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
    "error.curveNotIncreasing": "Curve temperatures must strictly increase (each point higher than the previous one)",
    "error.saveCurve": "Failed to save custom curve",
    "error.curveSavedSwitchFailed": "Curve saved, but switching to Custom mode failed — not yet active on the fan",
    // ---- Startup restore report (mirrors the same keys in the backend table) ----
    "restore.fanEcAuto": "Fan confirmed under EC auto control",
    "restore.fanHandoverUnconfirmed": "Fan handover to the EC could not be confirmed",
    "restore.fanModeRestored": 'Fan mode restored to "{mode}"',
    "restore.fanManualUnconfirmed": 'Fan switch to manual control could not be confirmed (target "{mode}")',
    "restore.autoOff": "Auto restore on startup is off",
    "restore.batteryMissing": "Battery node not found: {exc}",
    "restore.noThresholdNode": "Kernel has no charge_control_end_threshold; skipping limit restore",
    "restore.thresholdRestored": "Charge limit restored to {value}%",
    "restore.thresholdUnconfirmed": "Charge limit restore could not be confirmed (target {value}%)",
    "restore.noBehaviourNode": "Kernel has no charge_behaviour; skipping mode restore",
    "restore.modeUnsupported": 'Kernel does not support "{mode}"; mode not restored',
    "restore.modeRestored": 'Charge mode restored to "{mode}"',
    "restore.modeUnconfirmed": 'Charge mode restore could not be confirmed (target "{mode}")',
    "restore.nothing": "Nothing to restore",
    "err.batteryProbe": "No battery node supporting charge control was found",
    "err.restoreFailed": "{area} restore failed: {detail}",
    "common.chargeMode": "Charge mode",
    "common.chargeLimit": "Charge limit",
    "common.fanControl": "Fan control",
};

/**
 * 插件自己的 i18n 实现。
 *
 * **为什么不直接用 Decky 的机制**：Decky 对"插件"没有官方 i18n ——
 * 本地 `@decky/ui@4.12.1` 的类型定义里**没有任何** translation/i18n 导出
 * （Loader 自身用的是 react-i18next，但那是它自己界面的，插件用不上）。
 * 社区通行做法就是各插件自己写一份，这里也一样。
 *
 * 语言探测用 `window.LocalizationManager.m_rgLocalesToUse` —— 它是 Steam
 * 客户端暴露的语言列表（如 `["schinese"]`），并且是 `@decky/ui` **官方声明**
 * 在全局类型里的字段（`@decky/ui/dist/globals/stores.d.ts`），
 * 比 `SteamClient.Settings.GetCurrentLanguage()`（返回 Promise，只能异步取）
 * 更适合在渲染路径上同步调用。
 *
 * 规则（用户定的）：**简/繁中文 → 中文，其余一律英文**。
 *
 * 探测不到时**退回英文**，并且**不抛错** —— 最坏情况是界面变英文，
 * 不该因为 Steam 内部结构变了就让整个 QAM 面板空白。
 */
/** 判定为"中文"的 Steam 语言标识（小写比较）。 */
const CHINESE_LOCALES = [
    "schinese", // 简体中文
    "tchinese", // 繁体中文
    "zh", // 通用中文
    "zh-cn",
    "zh-hans",
    "zh-tw",
    "zh-hant",
    "zh-hk",
];
/**
 * 从 Steam 的语言标识列表里挑出插件要用的语言。
 *
 * `locales` 形如 `["schinese"]`；空数组 / 取不到 → 英文。
 * 只要**任一**个标识是中文就判为中文：Steam 给的是"按优先级排序的列表"，
 * 用户选了中文时它一定在列表里。
 */
function resolveLanguage(locales) {
    if (!locales || locales.length === 0)
        return "en";
    for (const locale of locales) {
        if (typeof locale !== "string")
            continue;
        if (CHINESE_LOCALES.includes(locale.trim().toLowerCase()))
            return "zh";
    }
    return "en";
}
/** 同步读 Steam 的界面语言列表；任何异常都退回空列表。 */
function detectLocales() {
    try {
        const manager = window.LocalizationManager;
        const locales = manager?.m_rgLocalesToUse;
        if (Array.isArray(locales))
            return locales.filter((x) => typeof x === "string");
    }
    catch {
        // 访问 Steam 全局对象在非 Steam 环境（如离线测试）会抛错，
        // 这里静默退回，交给上面的"英文兜底"。
    }
    return [];
}
const TABLES = { zh: zhCN, en: enUS };
/**
 * 按当前语言取文案。
 *
 * 找不到键时**返回键本身**（而不是空串）—— 空串会让界面出现"什么都没有"
 * 的诡异空白，返回键至少能一眼看出是漏了哪条。
 */
function translate(lang, key, values) {
    const table = TABLES[lang] ?? enUS;
    const template = table[key];
    if (typeof template !== "string")
        return key;
    return values ? format(template, values) : template;
}
/** 一次解析出当前语言与取文案函数，供组件在渲染前调用一次。 */
function createTranslator(locales) {
    const lang = resolveLanguage(detectLocales());
    return {
        lang,
        t: (key, values) => translate(lang, key, values),
    };
}

/* ------------------------------------------------------------------ RPC */
const getStatus = callable("get_status");
const setChargeMode = callable("set_charge_mode");
const setChargeThreshold = callable("set_charge_threshold");
const setAutoRestore = callable("set_auto_restore");
/**
 * 把探测到的界面语言告知后端。
 *
 * 后端返回的用户可见文案（错误信息、启动恢复报告、风扇不可用原因）也必须是
 * 双语的，但它没有任何办法知道 Steam 的界面语言 —— 那套 LocalizationManager
 * 只活在渲染进程里。所以由前端在挂载时探测一次、调用 `set_locale` 推送过去。
 *
 * 为什么加载与刷新代码里再调一次：Decky 重新加载插件时**不会**重新挂载
 * 已存在的组件树（只重跑后端的 `_main`），此时 `_lang` 会退回类属性默认值
 * `"en"`；不过前端此时会重新加载并重新执行模块顶层的这段代码，所以这里
 * 补一次即可对齐。失败**不报错**，但会允许下次重试（见下）。
 */
const setLocale = callable("set_locale");
/**
 * 已发出的语言同步请求；`null` 表示"还没成功过、可以再试"。
 *
 * **失败必须复位成 `null`**（这是回归守卫修正的一个真实缺陷）：最初写成
 * `localePushed = Promise.resolve(setLocale(lang)).catch(() => undefined)`，
 * 失败后这个 Promise **照样是"已设置"状态** —— 于是后续每次 `pushLocale`
 * 都在第 140 行被 `if (localePushed) return` 挡掉，永远不会重试。
 * 触发场景是真实存在的：插件刚加载时 Decky 后端可能还没就绪，第一次
 * `set_locale` 会 reject；此后后端一直停在默认英文，用户切中文界面却看到
 * 英文错误文案，**直到手动重载插件**才恢复。所以 catch 里要把它清回 `null`。
 */
let localePushed = null;
const pushLocale = (lang) => {
    if (localePushed)
        return;
    localePushed = Promise.resolve(setLocale(lang)).then(() => undefined, () => {
        // 重置，让下一次 `pushLocale`（下次挂载或轮询重建时）能再试一次。
        localePushed = null;
    });
};
const getFanStatus = callable("get_fan_status");
const getFanProfiles = callable("get_fan_profiles");
const setFanMode = callable("set_fan_mode");
const setFanCustomCurve = callable("set_fan_custom_curve");
/* -------------------------------------------------------------- 辅助函数 */
/**
 * 充电模式 → 文案键。
 *
 * 之前这里是 `Record<string, string>` 的中文字面量；现在只存**键**，
 * 实际文案在渲染时按当前语言取（见 `src/i18n/`）。
 */
const MODE_LABEL_KEYS = {
    auto: "charge.mode.auto",
    "inhibit-charge-awake": "charge.mode.inhibitAwake",
    "inhibit-charge": "charge.mode.inhibit",
    "force-discharge": "charge.mode.forceDischarge",
};
const MODE_DESC_KEYS = {
    auto: "charge.mode.desc.auto",
    "inhibit-charge-awake": "charge.mode.desc.inhibitAwake",
    "inhibit-charge": "charge.mode.desc.inhibit",
    "force-discharge": "charge.mode.desc.forceDischarge",
};
/** 内核 status 字符串 → 文案键。 */
const STATUS_KEYS = {
    Charging: "battery.status.charging",
    Discharging: "battery.status.discharging",
    Full: "battery.status.full",
    "Not charging": "battery.status.notCharging",
    Unknown: "common.unknown",
};
const REFRESH_INTERVAL_MS = 5000;
/** 与后端 THRESHOLD_DISABLED 保持一致：写入该值即解除上限。 */
const THRESHOLD_DISABLED = 100;
/** 失败提示的弹出时长；同时保留在通知中心，便于回看。 */
const TOAST_FAILURE_MS = 6000;
/**
 * 弹出一次失败提示；切换成功时不弹任何通知。
 *
 * 成功不提示的理由：每次写入后后端都会重读内核节点，界面上的模式和上限就是
 * 内核里的真实状态，本身已经是反馈了。
 *
 * 而 Decky 的 toaster 除了右下角弹出，还会把通知塞进 Steam 的通知中心
 * （toaster 内部调用 NotificationStore.ProcessNotification 并 unshift 到 tray），
 * 于是每成功切换一次就留下一条「已切换…」，很快堆成一长串。
 * 只在失败时提示，既没有堆积问题，也不会漏掉真正需要看到的错误。
 *
 * 失败提示**故意不传** `expiration`，让它留在通知中心可回看。
 */
const notifyFailure = (message) => {
    toaster.toast({
        title: "F1Pro EC Control",
        body: message,
        duration: TOAST_FAILURE_MS,
    });
};
const BatteryIcon = () => (SP_JSX.jsxs("svg", { width: "19", height: "19", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "1.9", strokeLinecap: "round", strokeLinejoin: "round", children: [SP_JSX.jsx("rect", { x: "2", y: "7", width: "16", height: "10", rx: "2.5" }), SP_JSX.jsx("path", { d: "M21 10.5v3" }), SP_JSX.jsx("path", { d: "M10.5 9.5L7.5 12.5h3.5l-3 3" })] }));
/* -------------------------------------------------------------- 风扇曲线 */
/** 曲线页里每个节点最多放这么多档 PWM 读取范围 */
const FAN_CURVE_POINTS = 5;
/**
 * 主页面与曲线页共用的模式**文案键**。
 *
 * 后端也给了 `fan.mode_labels`，但那是给状态行走的文案；这里的短名要配按钮，
 * 所以单独定义一份。两边都保留是有意的——不要为了"减少重复"而让按钮文案
 * 跟着后端走，那会让语言选择不受前端控制（后端不知道用户界面语言）。
 */
const FAN_MODE_LABEL_KEYS = {
    auto: "fan.mode.auto",
    quiet: "fan.mode.quiet",
    balanced: "fan.mode.balanced",
    performance: "fan.mode.performance",
    custom: "fan.mode.custom",
};
/**
 * 渲染后端的启动恢复报告。
 *
 * 后端只给**结构化条目**（`{key, params}`），不给自己渲染好的文案 —— 因为
 * 它生成报告时（`_main`）界面语言还没被前端推过去（`set_locale` 发生在插件
 * 加载之后），那时渲染会把整行钉死在默认英文上，中文界面重开插件也不会变。
 *
 * `params.mode` 传的是**模式名**（`balanced` / `inhibit-charge`），这里查表
 * 翻译成当前语言；`params.area_key` 同理（`err.restoreFailed` 的环节名）。
 * 两条查表路径分别覆盖风扇与充电两套模式键。
 */
const renderRestoreReport = (items, t) => (items ?? []).map((item) => {
    const params = { ...(item.params ?? {}) };
    if (typeof params.mode === "string") {
        const mode = params.mode;
        // 先查风扇表（`balanced` 等），再查充电表（`inhibit-charge` 等）。
        const modeKey = FAN_MODE_LABEL_KEYS[mode] ?? MODE_LABEL_KEYS[mode];
        params.mode = modeKey === undefined ? mode : t(modeKey);
    }
    if (typeof params.area_key === "string") {
        params.area = t(params.area_key);
        delete params.area_key;
    }
    // 键是后端给的白名单字符串（`restore.*` / `err.*`），断言成 MessageKey
    // 让 TS 放行；真有漏译时 `createTranslator` 会原样吐回键名，界面上一眼可见。
    return t(item.key, params);
});
/**
 * 主页面上按顺序列出的风扇模式按钮。
 *
 * **每个模式独占一行**，宽度与「编辑风扇曲线」一致。别改回网格：
 * 早期版本用两列网格，真机上先后出现"裸 `1fr` 被 `ButtonItem` 的 min-content
 * 宽度撑破"和"`minmax(0, 1fr)` 下相邻按钮底色块重叠"两种问题 ——
 * `ButtonItem(layout="below")` 内部有一层我们控制不到的宽度约束。
 */
const FAN_MODE_ORDER = [
    "auto",
    "quiet",
    "balanced",
    "performance",
    "custom",
];
/** 自定义曲线的初始值；后端会通过 get_fan_profiles 给出真正的种子曲线。 */
const FAN_CURVE_SEED = [
    [40, 40],
    [50, 75],
    [60, 115],
    [70, 160],
    [80, 255],
];
/**
 * 判断一条曲线是否严格递增。
 *
 * **不要用 `sort` 去"修复"顺序**：把节点按温度重排会让每个节点的 PWM 跟着
 * 它的温度一起搬走，用户看到的是"高温度对应了更低的 PWM"这种静默错乱。
 * 温度是用户直接拖动出来的，顺序错了就该报错让他改，而不是替他猜。
 */
const curveIsStrictlyIncreasing = (points) => points.length > 0 &&
    points.every((point, index) => index === 0 || point[0] > points[index - 1][0]);
/**
 * 温度滑杆的可用上下界：夹在左右邻居之间（留 1°C 间隔，保证严格递增），
 * 同时不越出后端的整体约束。滑不出越界值，比滑完再报错体验好。
 */
const tempBoundsFor = (points, index, minTemp, maxTemp) => ({
    min: index === 0 ? minTemp : points[index - 1][0] + 1,
    max: index === points.length - 1 ? maxTemp : points[index + 1][0] - 1,
});
/** 单行两列的横向排版（温度 / RPM、模式 / PWM 之类的状态行）。 */
const rowStyle = {
    display: "flex",
    justifyContent: "space-between",
    width: "100%",
};
/** 需要换行的长文本块（错误提示、诊断信息）。 */
const blockStyle = {
    whiteSpace: "normal",
    lineHeight: 1.35,
};
/**
 * 把 QAM 面板的滚动位置拉回最顶端。
 *
 * **为什么不能直接 `window.scrollTo`**：Decky 插件并不是一个独立页面，
 * 它渲染在 Gamepad UI 的 QAM 面板里，真正滚动的是**面板自己的那个容器**
 * （在插件的 React 树之外，拿不到 ref）。`window` 上滚动没有任何效果。
 *
 * 所以从插件自己的根节点**向上找第一个可滚动的祖先**再复位。
 * 这个做法对 QAM 改版比较耐受：只要面板还是靠 `overflow` 滚动，
 * 无论具体是哪一层，都能找到。
 *
 * 复位时机也很关键：必须在**新视图渲染之后**（`useEffect` 里），
 * 此时新内容已经撑开、`scrollHeight` 才是对的；在点击那一刻复位会被
 * 随后的渲染覆盖掉。
 *
 * 找不到可滚动祖先时**什么都不做**，不抛错——最坏情况就是回到
 * "切换后位置不重置"的旧行为，不该因为面板结构变了就让整个界面崩掉。
 */
function scrollPanelToTop(node) {
    let current = node?.parentElement ?? null;
    while (current) {
        const overflowY = window.getComputedStyle(current).overflowY;
        const scrollable = overflowY === "auto" || overflowY === "scroll";
        // `scrollHeight > clientHeight` 用来排除"声明了 overflow 但当前没内容可滚"的层，
        // 否则会停在一个空容器上，真正的滚动层反而在更外层。
        if (scrollable && current.scrollHeight > current.clientHeight) {
            current.scrollTop = 0;
            return;
        }
        current = current.parentElement;
    }
}
/* -------------------------------------------------------------- 主界面 */
function Content() {
    /**
     * 当前语言与取文案函数。
     *
     * 用 `useState` 的**惰性初始化**：语言在组件挂载那一刻定型（探测一次），
     * 保证首帧就有正确文案 —— 若放在 `useEffect` 里再设，第一帧会闪一下英文。
     *
     * 不在运行中切换语言：Steam 的语言设置改了要重启客户端才生效，
     * 插件跟随重启后的挂载即可，不必监听变更（也就不会引入监听开销）。
     */
    const [{ t, lang }] = SP_REACT.useState(() => createTranslator());
    /**
     * 把语言推给后端一次（见模块顶层 `pushLocale` 的说明）。
     *
     * 不能放进下面那个轮询 `useEffect`：那里每 5 秒跑一次、还会在 `view`
     * 变化时重建，而语言一辈子只需要同步一次。`pushLocale` 自带幂等保护，
     * 即便 React 的 StrictMode 把组件挂载两次也只发一次请求。
     */
    SP_REACT.useEffect(() => {
        pushLocale(lang);
    }, [lang]);
    /** 按当前语言取充电模式名；未知模式原样透出（比显示"未知"更有诊断价值）。 */
    const modeLabel = (mode) => {
        if (!mode)
            return t("common.unknown");
        const key = MODE_LABEL_KEYS[mode];
        return key ? t(key) : mode;
    };
    /** 内核 status → 本地化名称。 */
    const statusName = (value) => {
        const key = STATUS_KEYS[value ?? ""];
        if (key)
            return t(key);
        return value ?? "--";
    };
    /** 风扇模式短名（按钮 / 状态行共用）。 */
    const fanModeLabel = (mode) => {
        const key = FAN_MODE_LABEL_KEYS[mode];
        return key ? t(key) : mode;
    };
    const [state, setState] = SP_REACT.useState(null);
    const [error, setError] = SP_REACT.useState(null);
    const [busy, setBusy] = SP_REACT.useState(false);
    const [fan, setFan] = SP_REACT.useState(null);
    const [fanProfiles, setFanProfiles] = SP_REACT.useState(null);
    const [fanError, setFanError] = SP_REACT.useState(null);
    const [fanBusy, setFanBusy] = SP_REACT.useState(false);
    const [view, setView] = SP_REACT.useState("main");
    const [curve, setCurve] = SP_REACT.useState(FAN_CURVE_SEED);
    const [curveDirty, setCurveDirty] = SP_REACT.useState(false);
    const [curveError, setCurveError] = SP_REACT.useState(null);
    /**
     * 当前生效的充电模式（`charge_behaviour.active`）。
     *
     * 必须定义在 `state` 之后：这几个本地化辅助函数都依赖它，
     * 放在 `useState` 之前会踩暂时性死区（`TS2448`）。
     */
    const active = state?.charge_behaviour.active ?? null;
    /**
     * 充电模式按钮文案：当前生效的那一项加 `✓ ` 前缀。
     *
     * 勾号走 `battery.checked` 模板而不是直接拼 `"✓ " + label`——
     * 这样"标记符号要不要带空格、用哪个符号"都能按语言分别控制，
     * 语言表里也不会留下硬编码的标点。
     */
    const chargeModeButton = (mode, key) => {
        const label = t(key);
        return active === mode ? t("battery.checked", { label }) : label;
    };
    /** 插件自身那层 DOM，用来向上寻找真正在滚动的祖先。 */
    const rootRef = SP_REACT.useRef(null);
    /**
     * 是否已经由用户手动编辑过曲线。
     *
     * 5 秒一次的轮询会重新拉到后端保存的曲线；如果无脑覆盖本地编辑，用户
     * 拖到一半的滑杆就会被拽回去（后端此刻还是旧曲线）。所以只在用户没编辑过
     * 的时候才同步服务端值。
     */
    const curveTouched = SP_REACT.useRef(false);
    const apply = (result, fallback) => {
        if (result.ok && result.data) {
            setState(result.data);
            setError(null);
            return null;
        }
        const message = result.error ?? fallback;
        setError(message);
        return message;
    };
    const refresh = async () => {
        apply(await getStatus(), t("error.readBattery"));
    };
    /** 拉一次风扇状态；不可用时只记录原因，不当作操作失败弹通知。 */
    const refreshFan = async () => {
        const result = await getFanStatus();
        if (result.ok && result.data) {
            setFan(result.data);
            setFanError(result.data.available ? null : (result.data.reason ?? null));
            return result.data;
        }
        setFanError(result.error ?? t("error.readFan"));
        return null;
    };
    const refreshFanProfiles = async () => {
        const result = await getFanProfiles();
        if (!result.ok || !result.data)
            return;
        setFanProfiles(result.data);
        if (!curveTouched.current) {
            // 取曲线的优先级：**已保存的自定义曲线** > 当前模式的预设 > 默认种子。
            //
            // 顺序不能反：`profiles` 里**只有预设**（没有 "custom" 这个键），
            // 所以旧写法 `profiles[mode] ?? custom_seed` 在自定义模式下必然取不到、
            // 直接退回默认种子 —— 用户刚保存的曲线会被默认曲线覆盖。
            // 现在后端给了 custom_curve，它才是"用户当前真正的曲线"。
            const active = fan?.mode ?? "";
            const fromProfiles = result.data.profiles?.[active];
            const stored = active === "custom"
                ? (result.data.custom_curve ?? fromProfiles ?? result.data.custom_seed)
                : (fromProfiles ?? result.data.custom_curve ?? result.data.custom_seed);
            if (Array.isArray(stored) && stored.length === FAN_CURVE_POINTS) {
                setCurve(stored.map((point) => [...point]));
            }
        }
    };
    // 首次挂载后读取一次，之后定时轮询，保证界面与内核真实状态一致。
    //
    // 用**递归 setTimeout** 而不是 setInterval：setInterval 不看回调是否跑完，
    // 只要到点就再发一次。这里的 tick 要等在途 RPC（sysfs 读 + 可能的 EC 写），
    // 在 EC 卡顿时单次可能耗时远超间隔，请求就会越积越多 —— 界面看起来
    // 是"卡住后自己好了"，其实是排队的响应集中回来，几次状态互相覆盖。
    // 递归写法天然有背压：**上一次 resolve 之后才排下一次**，
    // 最坏情况下轮询频率自动降低，不会堆积。
    SP_REACT.useEffect(() => {
        let cancelled = false;
        let timer = null;
        const tick = async () => {
            if (cancelled)
                return;
            try {
                const batteryResult = await getStatus();
                if (!cancelled)
                    apply(batteryResult, t("error.readBattery"));
            }
            catch (error) {
                // **必须自己捕获**：RPC 层的 Promise 一旦被拒绝，就会变成一次
                // 未处理的 rejection；而且下面读风扇状态的代码会被整段跳过 ——
                // 电池读失败不该把风扇状态一起拖下水（两者是独立的 RPC）。
                // 注意只记原因、不弹通知：轮询每 5 秒一次，EC 卡顿时会刷屏。
                if (!cancelled) {
                    setFanError(error instanceof Error ? error.message : t("error.readState"));
                }
            }
            try {
                const fanResult = await getFanStatus();
                if (cancelled)
                    return;
                if (fanResult.ok && fanResult.data) {
                    setFan(fanResult.data);
                    setFanError(fanResult.data.available ? null : (fanResult.data.reason ?? null));
                    // 用户在曲线页拖滑杆时不要覆盖他的编辑。
                    if (!curveTouched.current) {
                        const active = fanResult.data.mode;
                        const fromProfiles = fanProfiles?.profiles?.[active];
                        const stored = fromProfiles ?? fanResult.data.curve ?? fanResult.data.custom_curve;
                        if (Array.isArray(stored) && stored.length === FAN_CURVE_POINTS) {
                            setCurve(stored.map((point) => [...point]));
                        }
                    }
                }
                else {
                    setFanError(fanResult.error ?? t("error.readFan"));
                }
            }
            catch (error) {
                // 同上：吞掉异常但不吞掉续排（续排由下面的 finally 负责）。
                if (!cancelled) {
                    setFanError(error instanceof Error ? error.message : t("error.readFan"));
                }
            }
            finally {
                // 必须放在 finally 里：中途抛错（RPC 层异常）时若不排下一次，
                // 轮询会**静默停死**，界面停在旧数据上再也不刷新。
                // 用 finally 保证无论成功失败都续上，与递归 setTimeout 的语义一致。
                if (!cancelled) {
                    timer = setTimeout(tick, REFRESH_INTERVAL_MS);
                }
            }
        };
        tick();
        return () => {
            cancelled = true;
            if (timer !== null)
                clearTimeout(timer);
        };
        // fanProfiles 只用于"有预设时优先取预设"，不需要因为它的变化重建轮询。
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    SP_REACT.useEffect(() => {
        refreshFanProfiles();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    // 下面几个操作都是「置 busy → await RPC → 清 busy」。
    // **busy 的清除必须在 finally 里**：RPC 的 Promise 一旦被拒绝，
    // `setBusy(false)` 就不会执行，界面上的按钮会**永久禁用**
    // （离线探针 .mut/repro_busy.mjs 复现过 4/4 个操作都会卡住）。
    // 异常本身也要落到 error / notifyFailure，让用户知道为什么没成功。
    const changeMode = async (mode) => {
        setBusy(true);
        try {
            const problem = apply(await setChargeMode(mode), t("error.switchChargeMode"));
            if (problem)
                notifyFailure(problem);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : t("error.switchChargeMode");
            setError(message);
            notifyFailure(message);
        }
        finally {
            setBusy(false);
        }
    };
    const changeThreshold = async (value) => {
        setBusy(true);
        try {
            const problem = apply(await setChargeThreshold(value), t("error.setChargeLimit"));
            if (problem)
                notifyFailure(problem);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : t("error.setChargeLimit");
            setError(message);
            notifyFailure(message);
        }
        finally {
            setBusy(false);
        }
    };
    const changeAutoRestore = async (enabled) => {
        setBusy(true);
        try {
            const problem = apply(await setAutoRestore(enabled), t("error.saveSettings"));
            if (problem)
                notifyFailure(problem);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : t("error.saveSettings");
            setError(message);
            notifyFailure(message);
        }
        finally {
            setBusy(false);
        }
    };
    /**
     * 切到某个风扇模式（含预设曲线）；成功静默，失败才提示。
     *
     * 切换**预设**模式时顺手把本地曲线换成该预设的节点，界面立刻跟着变，
     * 不必等下一轮轮询（最长 3.5 秒）。切到「自动 / 自定义」时不改曲线：
     * 自动没有曲线，自定义的曲线由服务端负责回填，本地擅自改会覆盖用户正在编辑的内容。
     *
     * **返回值表示这次切换是否成功**：调用方（`saveCurve`）靠它判断曲线是否
     * 真的在风扇上生效了。原先把异常吞掉只弹通知，于是"保存曲线 → 切自定义"
     * 这个两步动作里第二步失败时，调用方完全看不出来。
     */
    const changeFanMode = async (mode) => {
        setFanBusy(true);
        try {
            const problem = await applyFan(await setFanMode(mode), t("error.switchFanMode"));
            if (problem) {
                notifyFailure(problem);
                return false;
            }
            // **只有 RPC 成功之后才动本地状态**：把预设曲线填进滑杆、清掉编辑标记。
            //
            // 原先这段写在 await 之前，于是"切模式失败"时用户的编辑已经被丢掉 ——
            // 曲线被换成预设值、`curveTouched` 变 false（下一次轮询就能覆盖）、
            // `curveDirty` 变 false（「保存为自定义并应用」按钮直接变灰），
            // 而 EC 其实还跑着旧模式。用户在滑杆上的修改**没有任何提示地消失了**
            // （离线探针 .mut/repro_front_leftovers.mjs 的 G1 复现过）。
            const profile = fanProfiles?.profiles?.[mode];
            if (Array.isArray(profile) && profile.length === FAN_CURVE_POINTS) {
                curveTouched.current = false;
                setCurve(profile.map((point) => [...point]));
                setCurveDirty(false);
                setCurveError(null);
            }
            return true;
        }
        catch (error) {
            const message = error instanceof Error ? error.message : t("error.switchFanMode");
            setFanError(message);
            notifyFailure(message);
            return false;
        }
        finally {
            setFanBusy(false);
        }
    };
    /**
     * 换页后把滚动位置拉回顶端。
     *
     * QAM 面板切换内容时**不会**重置滚动位置：从主页滚到底点「编辑风扇曲线」，
     * 子页面虽然换掉了，滚动偏移却留着，于是直接落在界面末端 ——
     * 最上面的「← 返回」都看不见，很像卡住了。
     *
     * 放在 `useEffect` 里（依赖 `view`）而不是点击时立刻调：
     * 要等新视图渲染完、内容撑开之后复位才生效；点击那一刻复位会被
     * 随后的渲染覆盖掉。
     */
    SP_REACT.useEffect(() => {
        scrollPanelToTop(rootRef.current);
    }, [view]);
    const applyFan = async (result, fallback) => {
        if (result.ok && result.data) {
            setFan(result.data);
            setFanError(result.data.available ? null : (result.data.reason ?? null));
            return null;
        }
        const message = result.error ?? fallback;
        setFanError(message);
        return message;
    };
    /** 拖动某个节点的温度或 PWM。 */
    const updateCurvePoint = (index, axis, value) => {
        curveTouched.current = true;
        setCurveDirty(true);
        setCurveError(null);
        setCurve((previous) => previous.map((point, pointIndex) => {
            if (pointIndex !== index)
                return point;
            const next = [...point];
            next[axis] = value;
            return next;
        }));
    };
    /** 保存自定义曲线；校验失败只提示，不影响正在运行的风扇。 */
    const saveCurve = async () => {
        // 按**原样**提交，不做排序：排序会把节点的 PWM 跟着温度一起搬家，
        // 于是"高温对应低 PWM"这种错乱会被悄悄写进设置里。顺序不对就报错。
        const points = curve.map((point) => [...point]);
        if (!curveIsStrictlyIncreasing(points)) {
            const message = t("error.curveNotIncreasing");
            setCurveError(message);
            notifyFailure(message);
            return;
        }
        setFanBusy(true);
        setCurveError(null);
        try {
            const result = await setFanCustomCurve(points);
            if (!result.ok || !result.data) {
                const message = result.error ?? t("error.saveCurve");
                setCurveError(message);
                notifyFailure(message);
                return;
            }
            // **以 RPC 返回值作为唯一 UI 来源**，不要在这之后再拉预设去覆盖它：
            // refreshFanProfiles 取曲线的优先级里含 custom_curve，若后端还没来得及
            // 反映刚写的值，就会把界面拽回旧曲线。这里直接采信这次保存的结果。
            curveTouched.current = false;
            setCurve(result.data.curve.map((point) => [...point]));
            // 「保存为自定义并应用」是**两步**：曲线先落盘（上面这步已经成功），
            // 再切到「自定义」模式才算真正作用在风扇上。
            //
            // 未保存标记必须等**第二步成功之后**才清：
            // 原先 setCurveDirty(false) 夹在这里，于是切模式失败时按钮立刻变灰、
            // 看起来一切就绪，而 EC 还在跑旧模式 —— 用户连重试的入口都没有了。
            // 现在失败时保持 dirty=true（按钮仍可点）、并留下可见原因。
            //
            // 顺序也不能反：changeFanMode 会按预设覆盖曲线，只是 "custom" 不在预设里，
            // 所以这一步不会动刚保存的值。
            if (fan?.mode !== "custom") {
                const applied = await changeFanMode("custom");
                if (!applied) {
                    setCurveError(t("error.curveSavedSwitchFailed"));
                    return;
                }
            }
            else {
                // 已经在自定义模式：刷新一次状态，让 PWM / 温度显示跟上。
                await refreshFan();
            }
            setCurveDirty(false);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : t("error.saveCurve");
            setCurveError(message);
            notifyFailure(message);
        }
        finally {
            setFanBusy(false);
        }
    };
    /**
     * 把预设曲线**载入滑杆**，不直接写进 EC。
     *
     * 这里刻意只改本地 state：让用户先看清这条曲线长什么样、还能接着微调，
     * 真正生效要等他按「保存为自定义并应用」。直接 `changeFanMode(mode)` 的话
     * 会切到**预设模式**（EC 侧按预设跑），滑杆上却没有对应数据，
     * 用户看到的曲线和实际在跑的不是一回事。
     */
    const loadPresetCurve = (mode) => {
        const preset = fanProfiles?.profiles?.[mode];
        if (!Array.isArray(preset) || preset.length !== FAN_CURVE_POINTS) {
            notifyFailure(t("fan.noCurveForMode", { label: fanModeLabel(mode) }));
            return;
        }
        curveTouched.current = true; // 别让下一次轮询把刚载入的值覆盖掉
        setCurve(preset.map((point) => [...point]));
        setCurveError(null);
        setCurveDirty(true);
    };
    /** 还原成后端给出的默认曲线种子（同样只落到滑杆，要保存才生效）。 */
    const restoreSeedCurve = () => {
        const seed = fanProfiles?.custom_seed ?? FAN_CURVE_SEED;
        curveTouched.current = true;
        setCurve(seed.map((point) => [...point]));
        setCurveError(null);
        setCurveDirty(true);
    };
    const thresholdValue = SP_REACT.useMemo(() => {
        if (typeof state?.threshold === "number")
            return state.threshold;
        if (typeof state?.saved_threshold === "number")
            return state.saved_threshold;
        return undefined;
    }, [state?.threshold, state?.saved_threshold]);
    const thresholdOptions = SP_REACT.useMemo(() => [
        ...(state?.presets ?? [50, 60, 70, 80, 85, 90, 95]).map((value) => ({
            label: `${value}%`,
            data: value,
        })),
        {
            label: t("battery.noLimitOption", { value: THRESHOLD_DISABLED }),
            data: THRESHOLD_DISABLED,
        },
    ], [state?.presets]);
    /* ---------------------------------------------------------- 风扇曲线页 */
    if (view === "fan") {
        const constraints = fanProfiles?.constraints;
        const minTemp = constraints?.min_temp ?? 30;
        const maxTemp = constraints?.max_temp ?? 100;
        const minPwm = constraints?.min_pwm ?? 40;
        const maxPwm = constraints?.max_pwm ?? 255;
        const available = fan?.available ?? false;
        // 预设套用**先落到滑杆上**，让用户看清曲线长什么样、还能微调；
        // 真正写进 EC 要等他按「保存为自定义并应用」。
        const presetButtons = ["quiet", "balanced", "performance"];
        return (SP_JSX.jsxs("div", { ref: rootRef, children: [SP_JSX.jsx(DFL.PanelSection, { children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => setView("main"), children: t("fan.back") }) }) }), !available && (SP_JSX.jsx(DFL.PanelSection, { title: t("common.hint"), children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: blockStyle, children: fanError ?? t("fan.unavailableDefault") }) }) })), SP_JSX.jsxs(DFL.PanelSection, { title: t("fan.curveSection"), children: [curve.map((point, index) => {
                            // 温度滑杆夹在左右邻居之间，滑不出重复或倒序；
                            // 这样"顺序错误"在动手时就不可达，保存时的校验只是兜底。
                            const bounds = tempBoundsFor(curve, index, minTemp, maxTemp);
                            return (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: { width: "100%", paddingTop: "4px" }, children: [SP_JSX.jsxs("div", { style: rowStyle, children: [SP_JSX.jsx("span", { children: t("fan.node", { index: index + 1, temp: point[0] }) }), SP_JSX.jsxs("span", { children: ["PWM ", point[1]] })] }), SP_JSX.jsx(DFL.SliderField, { label: t("fan.tempRange", { min: bounds.min, max: bounds.max }), value: point[0], min: bounds.min, max: bounds.max, step: 1, showValue: false, disabled: fanBusy || !available || bounds.min > bounds.max, onChange: (value) => updateCurvePoint(index, 0, value) }), SP_JSX.jsx(DFL.SliderField, { label: "PWM", value: point[1], min: minPwm, max: maxPwm, step: 1, showValue: false, disabled: fanBusy || !available, onChange: (value) => updateCurvePoint(index, 1, value) })] }) }, index));
                        }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: fanBusy || !available || !curveDirty, onClick: saveCurve, children: t("fan.saveAndApply") }) }), curveError && (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: blockStyle, children: curveError }) }))] }), SP_JSX.jsxs(DFL.PanelSection, { title: t("fan.presetSection"), children: [presetButtons.map((mode) => (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: fanBusy || !available, onClick: () => loadPresetCurve(mode), children: t("fan.applyPreset", { label: fanModeLabel(mode) }) }) }, mode))), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: fanBusy || !available || !curveDirty, onClick: restoreSeedCurve, children: t("fan.restoreDefault") }) })] }), SP_JSX.jsxs(DFL.PanelSection, { title: t("common.diagnostic"), children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: {
                                    ...blockStyle,
                                    fontFamily: "monospace",
                                    fontSize: "11px",
                                    opacity: 0.75,
                                }, children: [t("fan.diag.controller", { value: fan?.controller ?? "--" }), SP_JSX.jsx("br", {}), t("fan.diag.tempSensor", { value: fan?.temp_sensor ?? "--" }), SP_JSX.jsx("br", {}), t("fan.diag.pwmNode", {
                                        value: fan?.pwm ?? t("common.notExist"),
                                    }), SP_JSX.jsx("br", {}), t("fan.diag.pwmEnable", {
                                        value: fan?.pwm_enable ?? t("common.notExist"),
                                    }), fan?.pwm_enable_label
                                        ? t("fan.diag.pwmEnableLabel", { value: fan.pwm_enable_label })
                                        : "", SP_JSX.jsx("br", {}), t("fan.diag.controlMethod", {
                                        value: fan?.manual ? t("fan.manual") : t("fan.ecAuto"),
                                    }), SP_JSX.jsx("br", {}), t("fan.diag.safetyTemp", { value: fan?.safety_temp ?? "--" })] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: fanBusy, onClick: refreshFan, children: t("common.refresh") }) })] })] }));
    }
    /* -------------------------------------------------------------- 主页面 */
    if (error && !state) {
        return (SP_JSX.jsxs(DFL.PanelSection, { title: "F1Pro EC Control", children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { whiteSpace: "normal", lineHeight: 1.35 }, children: error }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: refresh, children: t("common.reload") }) })] }));
    }
    const capacity = state?.capacity ?? "--";
    /** 风扇节点是否可用（不可用时整块降级成一行原因，不摆没用的按钮）。 */
    const fanAvailable = fan?.available ?? false;
    const acOnline = state?.ac_online ?? null;
    /**
     * 电池当前没有在充也没有在放：
     *
     * - 旁路模式（内核禁止充电）；
     * - 正常充电模式下内核报 `Not charging`，最常见的是**已达设定上限**。
     *
     * 这两种情况的物理状态完全一样——外接电源供整机、电池闲置，所以界面统一显示
     * 「供电中」，不区分原因（原因在下一行的「当前模式」里已经有了）。
     */
    const onAdapter = active === "inhibit-charge" ||
        active === "inhibit-charge-awake" ||
        state?.status === "Not charging";
    /** 外接电源在线；拿不到探测节点时（acOnline 为 null）退回看 status 是否在放电。 */
    const adapterLive = acOnline === true || (acOnline === null && state?.status !== "Discharging");
    /**
     * 电源直供：外接电源在带整机，电池不充不放。
     *
     * 此时电池侧的 power_now 在内核里不再更新（读数会停在切换前的旧值），
     * 显示它只会误导，所以这一档不出数字。
     *
     * 例外：外接电源**确认已断开**却报 `Not charging`，是自相矛盾的读数，
     * 此时不硬说成「供电中」，退回「未充电」。
     */
    const supplyOnly = onAdapter && adapterLive;
    /**
     * 只有电池确实在充/放电时，power_now 才是可信的实时值。
     *
     * 正数 = 充入电池，负数 = 电池放电；符号由后端依据 status 补齐，
     * 因为 ACPI 电池驱动的 power_now 恒为正值。
     * 已充满、供电中时电池电流≈0，读数没有意义（且往往停留在旧值），所以不出数字。
     */
    const flowing = state?.status === "Charging" || state?.status === "Discharging";
    const watts = !supplyOnly && flowing && typeof state?.power_watts === "number"
        ? `${state.power_watts >= 0 ? "" : "-"}${Math.abs(state.power_watts).toFixed(1)} W`
        : null;
    /**
     * 状态行文案：`状态 · [瓦数 ·] 上限 N%`。
     *
     * 有瓦数时用带 `{watts}` 占位的那条，否则用不带的那条 ——
     * **不要用字符串拼接空瓦数**（会留下多余的 ` · `）。
     */
    const summaryStatus = supplyOnly
        ? t("battery.status.supplying")
        : statusName(state?.status);
    const limitText = typeof thresholdValue === "number" ? `${thresholdValue}%` : t("battery.noLimit");
    const batterySummary = watts
        ? t("battery.summary.watts", { status: summaryStatus, watts, limit: limitText })
        : t("battery.summary", { status: summaryStatus, limit: limitText });
    return (SP_JSX.jsxs("div", { ref: rootRef, children: [SP_JSX.jsxs(DFL.PanelSection, { title: t("battery.section"), children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: { display: "flex", justifyContent: "space-between", width: "100%" }, children: [SP_JSX.jsx("span", { children: state?.battery ?? "BAT" }), SP_JSX.jsxs("span", { children: [capacity, "%"] })] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: { whiteSpace: "normal", lineHeight: 1.35 }, children: [batterySummary, SP_JSX.jsx("br", {}), t("battery.currentMode"), SP_JSX.jsx("b", { children: modeLabel(active) })] }) })] }), SP_JSX.jsxs(DFL.PanelSection, { title: t("battery.chargeModeSection"), children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy || !state?.supports_normal, onClick: () => changeMode("auto"), children: chargeModeButton("auto", "charge.mode.auto") }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy || !state?.supports_awake_bypass, onClick: () => changeMode("inhibit-charge-awake"), children: chargeModeButton("inhibit-charge-awake", "charge.mode.inhibitAwake") }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy || !state?.supports_bypass, onClick: () => changeMode("inhibit-charge"), children: chargeModeButton("inhibit-charge", "charge.mode.inhibit") }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { whiteSpace: "normal", lineHeight: 1.35, opacity: 0.8 }, children: active && MODE_DESC_KEYS[active] ? t(MODE_DESC_KEYS[active]) : "" }) })] }), SP_JSX.jsx(DFL.PanelSection, { title: t("battery.limitSection"), children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.DropdownItem, { label: t("battery.limitLabel"), rgOptions: thresholdOptions, selectedOption: thresholdValue ?? state?.threshold_disabled_value ?? 100, disabled: busy || !state?.supports_threshold, onChange: (option) => changeThreshold(option.data) }) }) }), SP_JSX.jsx(DFL.PanelSection, { title: t("fan.section"), children: fanAvailable ? (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: rowStyle, children: [SP_JSX.jsx("span", { children: fan?.temperature != null ? `${fan.temperature.toFixed(1)}°C` : "--" }), SP_JSX.jsx("span", { children: fan?.rpm != null ? `${fan.rpm} RPM` : "-- RPM" }), SP_JSX.jsx("span", { children: fan?.pwm != null ? `PWM ${fan.pwm}` : "PWM --" })] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: blockStyle, children: [t("fan.currentMode"), SP_JSX.jsx("b", { children: fan?.mode ? fanModeLabel(fan.mode) : t("common.unknown") }), fan?.manual ? t("fan.manual") : t("fan.ecAuto")] }) }), FAN_MODE_ORDER.map((mode) => (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: fanBusy, onClick: () => changeFanMode(mode), children: fan?.mode === mode
                                    ? t("battery.checked", { label: fanModeLabel(mode) })
                                    : fanModeLabel(mode) }) }, mode))), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => setView("fan"), children: t("fan.editCurve") }) })] })) : (
                // 不可用时**只留一行原因**，不要摆一排点了没反应的按钮。
                SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { ...blockStyle, opacity: 0.8 }, children: fan?.reason ?? fanError ?? t("fan.unavailableDefault") }) })) }), SP_JSX.jsxs(DFL.PanelSection, { title: t("settings.section"), children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ToggleField, { label: t("settings.autoRestore"), description: t("settings.autoRestoreDesc"), checked: state?.auto_restore ?? true, disabled: busy, onChange: changeAutoRestore }) }), state?.restore_report && state.restore_report.length > 0 && (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { whiteSpace: "normal", lineHeight: 1.35, opacity: 0.8 }, children: renderRestoreReport(state.restore_report, t).join(t("diag.reportSeparator")) }) }))] }), error && (SP_JSX.jsx(DFL.PanelSection, { title: t("common.hint"), children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { whiteSpace: "normal", lineHeight: 1.35 }, children: error }) }) })), SP_JSX.jsxs(DFL.PanelSection, { title: t("common.diagnostic"), children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: {
                                whiteSpace: "normal",
                                lineHeight: 1.35,
                                fontFamily: "monospace",
                                fontSize: "11px",
                                opacity: 0.75,
                            }, children: [state?.battery_path ?? t("diag.batteryNodeMissing"), SP_JSX.jsx("br", {}), t("diag.ac", { value: state?.ac_node ?? t("diag.acNoNode") }), acOnline === null ? "" : acOnline ? t("diag.acOnline") : t("diag.acOffline"), SP_JSX.jsx("br", {}), t("diag.rawNode", {
                                    name: "status",
                                    value: state?.status ?? t("common.notExist"),
                                }), SP_JSX.jsx("br", {}), t("diag.rawNode", {
                                    name: "charge_behaviour",
                                    value: state?.behaviour_raw ?? t("common.notExist"),
                                }), SP_JSX.jsx("br", {}), t("diag.rawNode", {
                                    name: "power_now",
                                    value: state?.power_now ?? t("common.notExist"),
                                }), SP_JSX.jsx("br", {}), t("diag.thresholdNode", {
                                    value: state?.threshold_node ? t("common.exists") : t("common.notExist"),
                                }), SP_JSX.jsx("br", {}), t("diag.fan", { value: fan?.controller ?? t("diag.fanNotDetected") }), fan?.available === false
                                    ? ` · ${t("diag.fanUnavailable")}`
                                    : fan?.manual
                                        ? ` · ${t("diag.fanManual")}`
                                        : ` · ${t("diag.fanEcAuto")}`, state && !state.supports_awake_bypass ? (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx("br", {}), t("diag.noAwakeBypass")] })) : null] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy, onClick: () => {
                                refresh();
                                refreshFan();
                            }, children: t("common.refresh") }) })] })] }));
}
var index = definePlugin(() => ({
    name: "F1Pro EC Control",
    titleView: SP_JSX.jsx("div", { className: DFL.staticClasses.Title, children: "F1Pro EC Control" }),
    content: SP_JSX.jsx(Content, {}),
    icon: SP_JSX.jsx(BatteryIcon, {}),
}));

export { index as default };
//# sourceMappingURL=index.js.map
