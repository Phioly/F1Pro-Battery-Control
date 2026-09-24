"""OneXFly F1 Pro (HX 370) 充电与风扇控制后端 —— Decky Loader 插件。

设计约束（与用户需求一致）：
  * 充电控制只操作标准 Linux ``power_supply`` 接口，不依赖 HHD / Anatase / Loadout；
  * 风扇控制只操作 ``oxp_ec`` hwmon 的 ``pwm1`` / ``pwm1_enable``，温度只读
    ``k10temp`` 的 ``temp1_input``；
  * **不触碰 TDP / CPU 调度**（不写 ``platform_profile``、不写电源策略），
    可与 SimpleDeckyTDP 共存；
  * 每次写入后都重新读取内核节点确认是否真正生效。

内核接口说明（Documentation/ABI/testing/sysfs-class-power）::

    auto:                 正常充电，并尊重 charge_control_end_threshold
    inhibit-charge:       接电也不充电（始终旁路）
    inhibit-charge-awake: 仅在设备唤醒时暂停充电（开机旁路，睡眠后恢复充电）
    force-discharge:      接电状态下强制放电

``inhibit-charge-awake`` 是为 OneXPlayer / One-Netbook 系列设备加入 mainline 的行为，
正是 F1 Pro 上「开机旁路」所需要的语义。

风扇接口来自同一个 OneXPlayer EC 驱动 ``drivers/platform/x86/oxpec.c``
（hwmon 名 ``oxp_ec``），其 ``pwm1_enable`` 取值语义与旧版
``drivers/hwmon/oxp-sensors.c`` **不同**，改动前务必看类里 FAN_ENABLE_* 的注释。
"""

import asyncio
import glob
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import decky


class Plugin:
    # ---------------------------------------------------------------- 常量
    SETTINGS_FILE = "f1pro-ec-control.json"

    BEHAVIOUR_FILE = "charge_behaviour"
    THRESHOLD_FILE = "charge_control_end_threshold"

    MODE_AUTO = "auto"
    MODE_ALWAYS = "inhibit-charge"
    MODE_AWAKE = "inhibit-charge-awake"
    MODE_FORCE_DISCHARGE = "force-discharge"

    #: 用户可在界面上选择的模式
    SELECTABLE_MODES = (MODE_AUTO, MODE_AWAKE, MODE_ALWAYS)

    #: 界面上提供的充电上限档位
    PRESETS = (50, 60, 70, 80, 85, 90, 95)

    MIN_THRESHOLD = 50
    MAX_THRESHOLD = 95
    #: 写入该值等价于「不限制充电」，用于解除上限
    THRESHOLD_DISABLED = 100

    #: 判定「外接电源」用的电源类型；Mains（ACPI 适配器）优先于 USB-C 供电节点
    AC_MAINS_TYPE = "Mains"
    AC_USB_PREFIX = "USB"

    #: 写入后最多重读几次、每次间隔多久（部分 EC 需要一点时间才刷新寄存器）
    VERIFY_ATTEMPTS = 4
    VERIFY_DELAY = 0.15

    # ------------------------------------------------------------- 风扇控制
    # F1 Pro (HX 370) 上温度与风扇来自两个不同的 hwmon 设备：
    #   k10temp -> CPU 温度（Tctl，temp1_input）
    #   oxp_ec  -> 风扇 RPM / PWM（fan1_input、pwm1、pwm1_enable）
    # 一律按 hwmon 的 ``name`` 属性查找，不依赖 hwmon 编号——编号会随内核版本、
    # 驱动绑定顺序、热插拔变化（hwmon4/hwmon5 可能变成 hwmon3/hwmon7）。
    FAN_TEMP_SENSOR = "k10temp"
    FAN_CONTROLLER = "oxp_ec"

    FAN_TEMP_FILE = "temp1_input"
    FAN_RPM_FILE = "fan1_input"
    FAN_PWM_FILE = "pwm1"
    FAN_ENABLE_FILE = "pwm1_enable"

    #: oxp_ec 的 ``pwm1_enable`` 取值（drivers/platform/x86/oxpec.c 的 write 回调）::
    #:
    #:     1 -> 手动 PWM（EC 寄存器 PWM_MODE_MANUAL）
    #:     2 -> EC 自动控制（EC 寄存器 PWM_MODE_AUTO）
    #:     0 -> 驱动内部含义是「切手动 + 直接把 PWM 拉到 255 满速」，不是自动！
    #:
    #: 注意旧版 ``drivers/hwmon/oxp-sensors.c`` 用的是 0 = 自动、1 = 手动，
    #: 与这里不同。写错 "自动" 的值不会报错，但会让 EC 卡在手动满速——
    #: 因此本插件先用 charge_behaviour 确认本机是 oxpec 驱动，才允许写风扇。
    FAN_ENABLE_MANUAL = 1
    FAN_ENABLE_AUTO = 2

    #: 判断「当前处于手动」时必须接受的值。
    #: 读回逻辑见 oxpec.c：手动且 pwm 恰好为 255 时驱动**返回 0 而不是 1**
    #: （"Return 0 if at full fan speed, 1 otherwise"）。
    #: 只认 1 的话，风扇刚好在满速时会把"已经成功"误判成失败并抛错。
    FAN_MANUAL_ENABLE_VALUES = (0, 1)

    FAN_MODE_AUTO = "auto"
    FAN_MODE_QUIET = "quiet"
    FAN_MODE_BALANCED = "balanced"
    FAN_MODE_PERFORMANCE = "performance"
    FAN_MODE_CUSTOM = "custom"

    FAN_MODES = (
        FAN_MODE_AUTO,
        FAN_MODE_QUIET,
        FAN_MODE_BALANCED,
        FAN_MODE_PERFORMANCE,
        FAN_MODE_CUSTOM,
    )

    # ------------------------------------------------------------ 界面语言
    #: 当前界面语言。**默认英文**：系统不是简体 / 繁体中文时一律用英文（用户定的规则）。
    #: 前端挂载时探测 Steam 的界面语言，通过 `set_locale` RPC 推过来。
    #:
    #: 注意这是**类属性**：`_normalize_fan_curve` 是 `@classmethod`、只能通过
    #: `cls._lang` 取值，所以 `set_locale` 也必须写类属性（写实例属性会遮蔽它，
    #: 曲线校验的报错就会顽固地留在英文）。
    _lang: str = "en"

    #: 会走中文文案的语言标识。**必须把 Steam 的写法也算进来**：
    #: 前端把 Steam 客户端语言原样透传，而 Steam 用的是 `schinese` /
    #: `tchinese`（不是 `zh-CN`）。只认 `zh` 前缀的话，繁体中文用户会被
    #: 判成英文 —— 界面已经是中文了、后端报错却是英文。
    #: 前端 `src/i18n/index.ts` 的 `CHINESE_LOCALES` 是同一份名单，改动要同步。
    _ZH_LANG_HINTS = (
        "zh",
        "schinese",
        "tchinese",
    )

    #: 充电模式 → 文案键（真正的文案在 `_MSG` 里，按语言取）。
    _MODE_MSG_KEYS: Dict[str, str] = {
        MODE_AUTO: "mode.auto",
        MODE_AWAKE: "mode.inhibitAwake",
        MODE_ALWAYS: "mode.inhibit",
        MODE_FORCE_DISCHARGE: "mode.forceDischarge",
    }

    #: 风扇模式 → 文案键。
    _FAN_MODE_MSG_KEYS: Dict[str, str] = {
        FAN_MODE_AUTO: "fan.mode.auto",
        FAN_MODE_QUIET: "fan.mode.quiet",
        FAN_MODE_BALANCED: "fan.mode.balanced",
        FAN_MODE_PERFORMANCE: "fan.mode.performance",
        FAN_MODE_CUSTOM: "fan.mode.custom",
    }

    #: 全部界面文案（按语言分组）。
    #:
    #: 设计约定：
    #: - 键名 `<区域>.<用途>`，区域含 common / mode / fan / err / restore；
    #: - **中英两表的键集必须完全一致**、占位符也必须一致（`tests/test_backend.py`
    #:   的 `test_i18n` 会逐条核对）—— 缺一条就会在界面上显示成另一门语言或键名；
    #: - 占位符用 `{name}`，由 `_t` 用 `str.format` 替换；
    #: - **日志不放这里**：日志保持中文（面向开发者、不进界面）。
    _MSG: Dict[str, Dict[str, str]] = {
        "zh": {
            # 充电模式名
            "mode.auto": "正常充电",
            "mode.inhibitAwake": "开机旁路",
            "mode.inhibit": "始终旁路",
            "mode.forceDischarge": "强制放电",
            # 风扇模式名
            "fan.mode.auto": "自动",
            "fan.mode.quiet": "静音",
            "fan.mode.balanced": "均衡",
            "fan.mode.performance": "性能",
            "fan.mode.custom": "自定义",
            # 风扇控制权读回标签（随 get_status 下发）
            "fan.control.manual": "手动 PWM",
            "fan.control.auto": "EC 自动",
            "fan.control.unknown": "未知（{value}）",
            # 电池节点
            "err.noBatteryNode": (
                "未找到支持充电控制的电池节点"
                "（需要 /sys/class/power_supply/BAT*/charge_behaviour）"
            ),
            # 通用错误包装
            # 措辞里显式点出 `_root` 是**错的**（写成 `_root` 会被 Decky 降权，
            # 于是所有 sysfs 写入 EACCES 而界面看起来一切正常）。
            # `package.py` 的过时建议扫描会检查这一点：**不要**在提到它的行里
            # 省略这句纠正说明（阴性标记词表见 package.py 的 CORRECTION_MARKERS）。
            "err.noPermission": (
                "没有权限写入{what}节点；请确认 plugin.json 的 flags 包含 \"root\""
                "（不是 _root），且插件由 Decky 加载。"
                "当前 uid={uid}，写 sysfs 需要 uid 0。"
            ),
            "err.writeRejected": "内核拒绝了写入：{exc}",
            # 充电模式
            "err.invalidChargeMode": "无效的充电模式：{mode}",
            "err.noBehaviourNode": "当前内核没有提供 charge_behaviour 节点",
            "err.modeUnsupported": "当前内核不支持「{label}」；该节点可用值：{available}",
            "err.modeNotApplied": "写入后状态未生效：期望 {mode}，内核实际返回 {actual}",
            "err.noneValue": "无",
            # 充电上限
            "err.thresholdNotInt": "充电上限必须是整数",
            "err.thresholdRange": (
                "充电上限必须在 {min}%–{max}% 之间，或使用 {disabled}% 解除限制"
            ),
            "err.noThresholdNode": "当前内核没有提供 charge_control_end_threshold 节点",
            "err.thresholdUnreadable": (
                "已写入 {value}%，但此节点不可读，无法确认是否生效（若设备行为正确可继续使用）"
            ),
            "err.thresholdAdjusted": "内核将上限调整为 {actual}%，没有接受 {value}%",
            # 风扇门禁
            "err.fanGate": (
                "无法确认风扇驱动是 oxpec（电池节点没有 charge_behaviour / 充电上限）："
                "不同驱动的 pwm1_enable 取值含义不同，为避免把 EC 置于手动满速，风扇控制已禁用"
            ),
            "err.fanNodesMissing": "未找到 hwmon「{temp}」或「{controller}」及其风扇属性节点",
            # 曲线校验
            "err.curvePointCount": "曲线必须恰好包含 {count} 个节点",
            "err.curvePointPair": "每个曲线节点必须包含「温度」和「PWM」两个值",
            "err.curvePointInt": "曲线节点的温度与 PWM 必须是整数",
            "err.curveTempRange": "温度必须在 {min}–{max}°C 之间",
            "err.curvePwmRange": "PWM 必须在 {min}–{max} 之间",
            "err.curveNotIncreasing": "曲线节点的温度必须严格递增（每个节点都要比前一个更高）",
            # 风扇模式切换
            "err.invalidFanMode": "无效的风扇模式：{mode}",
            "err.fanHandoverUnconfirmed": "交还 EC 自动控制后未确认生效",
            "err.fanManualUnconfirmed": "切换手动 PWM 控制后未确认生效",
            "err.fanUnloading": "插件正在卸载，本次转换已取消",
            "err.fanThreadStartFailed": "控制线程启动失败（旧线程未退出）",
            # 启动恢复报告
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
            # 电池探测
            "err.batteryProbe": "未找到支持充电控制的电池节点",
            # 风扇不可用的其他原因（`_fan_node` / `_fan_temp_node` 抛出）
            "err.fanControllerNodeMissing": "未找到 F1 Pro 风扇控制节点（oxp_ec）",
            "err.fanTempNodeMissing": "未找到 CPU 温度传感器（k10temp）",
            # 风扇控制循环里会返回给界面的错误
            "err.fanPwmUnreadable": "PWM 写入后无法读取确认",
            "err.fanPwmMismatch": "PWM 写入后读回 {actual}，与目标 {target} 不符",
            "err.fanTempUnreadable": "无法读取 CPU 温度",
            "err.fanReclaimUnconfirmed": "重新取得手动控制权后未确认生效",
            # 名词片段：供 `err.restoreFailed` 拼装，也让错误里能带上"哪个环节"
            "common.chargeMode": "充电模式",
            "common.chargeLimit": "充电上限",
            "common.fanControl": "风扇控制",
            "common.chargeControl": "电池控制",
            "err.restoreFailed": "{area_key}恢复失败：{detail}",
        },
        "en": {
            # mode names
            "mode.auto": "Normal charging",
            "mode.inhibitAwake": "Bypass while on",
            "mode.inhibit": "Bypass always",
            "mode.forceDischarge": "Force discharge",
            "fan.mode.auto": "Auto",
            "fan.mode.quiet": "Quiet",
            "fan.mode.balanced": "Balanced",
            "fan.mode.performance": "Performance",
            "fan.mode.custom": "Custom",
            # fan control authority read-back labels (sent with get_status)
            "fan.control.manual": "Manual PWM",
            "fan.control.auto": "EC auto",
            "fan.control.unknown": "Unknown ({value})",
            # battery node
            "err.noBatteryNode": (
                "No battery node supporting charge control was found "
                "(expected /sys/class/power_supply/BAT*/charge_behaviour)"
            ),
            # generic error wrapping
            # 措辞里显式点出 `_root` 是**错的**（写成 `_root` 会被 Decky 降权，
            # 于是所有 sysfs 写入 EACCES 而界面看起来一切正常）。
            # `package.py` 的过时建议扫描会检查这一点：**不要**在提到它的行里
            # 省略这句纠正说明（阴性标记词表见 package.py 的 CORRECTION_MARKERS）。
            "err.noPermission": (
                "Not permitted to write the {what} node. Make sure plugin.json's "
                "flags contains \"root\" (never write \"_root\") and that the "
                "plugin is loaded by Decky. Current uid={uid}; writing sysfs "
                "requires uid 0."
            ),
            "err.writeRejected": "The kernel rejected the write: {exc}",
            # charge mode
            "err.invalidChargeMode": "Invalid charge mode: {mode}",
            "err.noBehaviourNode": "The kernel does not provide a charge_behaviour node",
            "err.modeUnsupported": (
                "This kernel does not support \"{label}\"; "
                "values available on this node: {available}"
            ),
            "err.modeNotApplied": (
                "The state did not take effect after writing: expected {mode}, "
                "kernel reported {actual}"
            ),
            "err.noneValue": "none",
            # charge limit
            "err.thresholdNotInt": "The charge limit must be an integer",
            "err.thresholdRange": (
                "The charge limit must be between {min}% and {max}%, "
                "or use {disabled}% to remove the limit"
            ),
            "err.noThresholdNode": (
                "The kernel does not provide a charge_control_end_threshold node"
            ),
            "err.thresholdUnreadable": (
                "Wrote {value}%, but this node is not readable so the change could "
                "not be confirmed (safe to keep using it if the device behaves "
                "correctly)"
            ),
            "err.thresholdAdjusted": (
                "The kernel adjusted the limit to {actual}% and did not accept {value}%"
            ),
            # fan gate
            "err.fanGate": (
                "Could not confirm the fan driver is oxpec (the battery node has no "
                "charge_behaviour / charge limit): pwm1_enable means different things "
                "on different drivers, so fan control is disabled to avoid leaving the "
                "EC in manual full speed"
            ),
            "err.fanNodesMissing": (
                "Neither hwmon \"{temp}\" nor \"{controller}\" (with its fan "
                "attribute nodes) was found"
            ),
            # curve validation
            "err.curvePointCount": "The curve must contain exactly {count} points",
            "err.curvePointPair": (
                "Every curve point must contain both a temperature and a PWM value"
            ),
            "err.curvePointInt": (
                "Curve temperatures and PWM values must be integers"
            ),
            "err.curveTempRange": (
                "Temperature must be between {min} and {max} °C"
            ),
            "err.curvePwmRange": "PWM must be between {min} and {max}",
            "err.curveNotIncreasing": (
                "Curve temperatures must strictly increase (each point higher than "
                "the previous one)"
            ),
            # fan mode switching
            "err.invalidFanMode": "Invalid fan mode: {mode}",
            "err.fanHandoverUnconfirmed": (
                "Handing the fan back to EC auto control could not be confirmed"
            ),
            "err.fanManualUnconfirmed": (
                "Switching to manual PWM control could not be confirmed"
            ),
            "err.fanUnloading": "The plugin is unloading; this transition was cancelled",
            "err.fanThreadStartFailed": (
                "Failed to start the control thread (the previous thread did not exit)"
            ),
            # startup restore report
            "restore.fanEcAuto": "Fan confirmed under EC auto control",
            "restore.fanHandoverUnconfirmed": (
                "Fan handover to the EC could not be confirmed"
            ),
            "restore.fanModeRestored": "Fan mode restored to \"{mode}\"",
            "restore.fanManualUnconfirmed": (
                "Fan switch to manual control could not be confirmed (target \"{mode}\")"
            ),
            "restore.autoOff": "Auto restore on startup is off",
            "restore.batteryMissing": "Battery node not found: {exc}",
            "restore.noThresholdNode": (
                "Kernel has no charge_control_end_threshold; skipping limit restore"
            ),
            "restore.thresholdRestored": "Charge limit restored to {value}%",
            "restore.thresholdUnconfirmed": (
                "Charge limit restore could not be confirmed (target {value}%)"
            ),
            "restore.noBehaviourNode": "Kernel has no charge_behaviour; skipping mode restore",
            "restore.modeUnsupported": "Kernel does not support \"{mode}\"; mode not restored",
            "restore.modeRestored": "Charge mode restored to \"{mode}\"",
            "restore.modeUnconfirmed": (
                "Charge mode restore could not be confirmed (target \"{mode}\")"
            ),
            "restore.nothing": "Nothing to restore",
            # battery probing
            "err.batteryProbe": "No battery node supporting charge control was found",
            # other fan-unavailable reasons
            "err.fanControllerNodeMissing": "F1 Pro fan control node (oxp_ec) not found",
            "err.fanTempNodeMissing": "CPU temperature sensor (k10temp) not found",
            # fan control loop errors surfaced to the UI
            "err.fanPwmUnreadable": "Could not read back the PWM value after writing",
            "err.fanPwmMismatch": "PWM read back as {actual} after writing, expected {target}",
            "err.fanTempUnreadable": "Could not read the CPU temperature",
            "err.fanReclaimUnconfirmed": "Reclaiming manual control could not be confirmed",
            # noun fragments used to compose "err.restoreFailed"
            "common.chargeMode": "Charge mode",
            "common.chargeLimit": "Charge limit",
            "common.fanControl": "Fan control",
            "common.chargeControl": "Battery control",
            "err.restoreFailed": "{area_key} restore failed: {detail}",
        },
    }

    def _t(self, key: str, **values: Any) -> str:
        """按当前界面语言取文案。

        `key` 缺失或该语言下没有这条时**返回键名本身** —— 与前端 `translate()`
        一致，界面上一眼能看出漏了哪条，比显示空串好排查。
        """
        table = self._MSG.get(self._lang) or self._MSG["en"]
        template = table.get(key)
        if template is None:
            # 当前语言缺这条时退回英文，再缺才给键名。
            template = self._MSG["en"].get(key)
        if template is None:
            return key
        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError):
            return template

    @classmethod
    def _t_static(cls, key: str, **values: Any) -> str:
        """供 `@classmethod` 使用的取文案入口（不用实例即可调用）。

        `_normalize_fan_curve` 是类方法、没有 `self`，但校验文案又必须跟随
        界面语言 —— 语言状态是类级属性 `_lang`，所以这里直接读 `cls._lang`。
        语义（缺键返回键名、缺语言退回英文）与 `_t` 完全一致。
        """
        lang = getattr(cls, "_lang", "en")
        table = cls._MSG.get(lang) or cls._MSG["en"]
        template = table.get(key)
        if template is None:
            template = cls._MSG["en"].get(key)
        if template is None:
            return key
        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError):
            return template

    def _mode_label(self, mode: str) -> str:
        """充电模式 → 当前语言下的显示名（未知模式原样透出）。"""
        key = self._MODE_MSG_KEYS.get(mode)
        return self._t(key) if key else mode

    def _fan_mode_label(self, mode: str) -> str:
        """风扇模式 → 当前语言下的显示名（未知模式原样透出）。"""
        key = self._FAN_MODE_MSG_KEYS.get(mode)
        return self._t(key) if key else mode

    def _mode_labels(self) -> Dict[str, str]:
        """全部充电模式的本地化名称表（随 `get_status` 一起下发给前端）。"""
        return {mode: self._mode_label(mode) for mode in self._MODE_MSG_KEYS}

    def _fan_mode_labels(self) -> Dict[str, str]:
        """全部风扇模式的本地化名称表。"""
        return {mode: self._fan_mode_label(mode) for mode in self._FAN_MODE_MSG_KEYS}

    #: 可选曲线：五个 [温度 °C, PWM] 节点，写进 EC 前会线性插值。
    #: oxp_fly 板型的 EC PWM 范围就是 [0-255]，驱动不做缩放（其余机型才缩放）。
    FAN_PROFILES = {
        FAN_MODE_QUIET: [
            [40, 40],
            [50, 60],
            [60, 90],
            [70, 140],
            [80, 200],
        ],
        FAN_MODE_BALANCED: [
            [40, 40],
            [50, 75],
            [60, 115],
            [70, 160],
            [80, 255],
        ],
        FAN_MODE_PERFORMANCE: [
            [40, 75],
            [50, 110],
            [60, 160],
            [70, 210],
            [80, 255],
        ],
    }

    #: 「自定义」模式的初始曲线。只是给用户一个可编辑的起点，**不是推荐曲线**。
    FAN_CUSTOM_SEED = [
        [40, 40],
        [50, 75],
        [60, 115],
        [70, 160],
        [80, 255],
    ]

    FAN_CURVE_POINTS = 5

    FAN_MIN_TEMP = 30
    FAN_MAX_TEMP = 100
    FAN_MIN_PWM = 40
    FAN_MAX_PWM = 255

    #: 正常情况下每 3 秒检查一次
    FAN_POLL_INTERVAL = 3.0
    # 停止控制线程的等待上限。超时后**不当作已停止**（见 _stop_fan_controller）。
    FAN_STOP_TIMEOUT = 4.0

    #: 只有目标 PWM 与当前 PWM 的差达到这个值才真正写 sysfs。
    #: 否则温度每变化 0.1°C 都要写一次 EC 寄存器，对 EC 不友好。
    FAN_PWM_CHANGE_THRESHOLD = 8

    #: 安全保护：达到该温度直接满速，任何自定义曲线都不能覆盖它。
    FAN_SAFETY_TEMP = 85

    def __init__(self) -> None:
        self.battery_path: Optional[str] = None
        self._settings_path: Optional[str] = None
        self._restore_report: List[str] = []

        # 风扇 hwmon 路径：动态发现，不写死 hwmon 编号
        self.fan_temp_path: Optional[str] = None
        self.fan_controller_path: Optional[str] = None

        # 风扇控制线程
        self._fan_thread: Optional[threading.Thread] = None
        self._fan_stop_event = threading.Event()
        self._fan_lock = threading.Lock()
        # **转换锁**：覆盖一次"完整的风扇控制权转换"——停线程 → 取/交控制权 →
        # 写 pwm1_enable → 写 pwm1 → 读回验证 → 更新设置 → 启线程。
        #
        # 为什么必须是**可重入**的（RLock）：转换内部的 _fan_handover_to_ec()
        # 会调 _stop_fan_controller()、_fan_apply_manual() 会调 _fan_step()
        # 与 _start_fan_controller()，它们都要拿同一把锁。用普通 Lock 会自死锁。
        #
        # 为什么不能只用 _fan_lock 顶替：_fan_lock 只保护"线程对象的创建"，
        # 两个 RPC（各跑在 asyncio.to_thread 的工作线程里）并发时仍会互相穿插，
        # 实测出现过 EC 停在手动、而设置文件写着 auto 的状态不一致
        # （离线并发探针 .mut/repro_fan_concurrency.py 复现）。
        self._fan_transition_lock = threading.RLock()

        # 某个转换是否正持有转换锁。**只用于测试断言**，不参与任何逻辑判断。
        self._fan_transition_active = False

        # **世代号**：每出现一次"取走风扇控制权"（停线程 / 交还 EC）就 +1。
        #
        # 它是为了堵住一个时序漏洞：``_stop_fan_controller()`` 等待超时（线程
        # 卡在 sysfs 读写里）返回之后，**一个已经开始的 ``_fan_step()`` 会继续
        # 往下走**。它读回 ``pwm1_enable`` 发现"不是手动"（因为交还刚把 EC 写回
        # 自动），于是走进夺回控制权的分支，把 ``pwm1_enable`` 又写回 1 ——
        # 实测现象是：卸载已经返回、EC 也曾交还自动，风扇随后却停在手动 PWM 上。
        #
        # 有了它，``_fan_step()`` 在进入时记下当时的世代号，并在每次写节点**之前**
        # 比对：只要中间发生过"取走控制权"，这一步就整体作废、一个字都不写。
        # 正常路径的世代号不会变，因此不会受影响（有反向断言守着）。
        self._fan_epoch = 0

        # **卸载标志**：插件一旦开始卸载就置位，此后控制步骤**不再自动夺回**
        # 控制权（``_fan_step`` 遇到"不是手动"时不会重写 ``pwm1_enable=1``）。
        #
        # 为什么世代号不够：世代号只能作废"在途的**那一步**"，挡不住 RPC 转换的
        # **后续步骤**。真实的交错是这样的（离线探针 .mut/repro_fan_leftovers.py
        # 的 probe_f 复现）：
        #
        #   1. 一个 RPC 拿着转换锁、卡在 ``_fan_step()`` 的 sysfs 读写里；
        #   2. 卸载取锁**超时** → 走"仍尝试交还 EC"的分支 → 交还成功、随后返回；
        #      此时 EC 是自动，看起来一切正常；
        #   3. 那个 RPC 从 I/O 里出来，``_fan_step()`` 被世代号作废（这一步没写），
        #      但 ``_fan_apply_manual()`` **继续往下走**：``_start_fan_controller()``
        #      起线程 → ``_set_fan_mode_sync`` 返回 ``ok=True``；
        #   4. 结果：**卸载已经结束，EC 却被切回手动**，而且报告成功。
        #
        # 所以卸载必须在"取走控制权那一刻"留下一个持久信号。而正常路径**不能**
        # 因此失去夺权能力（否则风扇再也切不了手动），所以这个标志**只在卸载时置位**，
        # 重启插件即随对象一起消失。
        self._fan_unloading = False

    # ------------------------------------------------------------ 基础读写
    @staticmethod
    def _read(path: str) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()

    @classmethod
    def _read_or_none(cls, path: str) -> Optional[str]:
        """读取节点；节点不存在或内核拒绝读取时返回 None。

        注意：某些 EC 驱动（例如 oxpec）的 ``charge_control_end_threshold``
        读会返回 EINVAL，但写入是有效的，因此「读不到」不等于「不支持」。
        """
        try:
            return cls._read(path)
        except OSError:
            return None

    @staticmethod
    def _write(path: str, value: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{value}\n")

    @staticmethod
    def _node_exists(path: str) -> bool:
        return os.path.isfile(path)

    def _write_node(self, path: str, value: str, attempts: int = 3) -> None:
        """写入 sysfs 节点，失败时短暂退避重试。"""
        last_error: Optional[Exception] = None
        for index in range(attempts):
            try:
                self._write(path, value)
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.1 * (index + 1))
        raise last_error if last_error is not None else RuntimeError("写入失败")

    def _wait_for(self, predicate, timeout: float = None) -> bool:
        """轮询等待内核节点收敛到期望状态。"""
        timeout = timeout if timeout is not None else self.VERIFY_ATTEMPTS * self.VERIFY_DELAY
        deadline = time.monotonic() + timeout
        while True:
            if predicate():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.VERIFY_DELAY)

    # --------------------------------------------------------- 风扇设备发现
    @staticmethod
    def _hwmon_paths() -> List[str]:
        """系统上所有 hwmon 设备目录（离线测试会替换掉这一层）。"""
        return sorted(glob.glob("/sys/class/hwmon/hwmon*"))

    @classmethod
    def _find_hwmon_by_name(cls, name: str) -> Optional[str]:
        """按 hwmon 的 ``name`` 属性查找设备，避免依赖 hwmon 编号。"""
        for path in cls._hwmon_paths():
            device_name = cls._read_or_none(os.path.join(path, "name"))
            if device_name and device_name.strip() == name:
                return path
        return None

    def _refresh_fan_nodes(self) -> bool:
        """重新寻找 F1 Pro 的温度传感器与 EC 风扇控制器。

        任何必需节点缺失就整体判为不可用（失败关闭），不让调用方拿着半套节点去写。
        """
        temp_path = self._find_hwmon_by_name(self.FAN_TEMP_SENSOR)
        controller_path = self._find_hwmon_by_name(self.FAN_CONTROLLER)

        if not temp_path or not controller_path:
            self.fan_temp_path = None
            self.fan_controller_path = None
            return False

        required = (
            os.path.join(temp_path, self.FAN_TEMP_FILE),
            os.path.join(controller_path, self.FAN_RPM_FILE),
            os.path.join(controller_path, self.FAN_PWM_FILE),
            os.path.join(controller_path, self.FAN_ENABLE_FILE),
        )
        if not all(self._node_exists(path) for path in required):
            self.fan_temp_path = None
            self.fan_controller_path = None
            return False

        self.fan_temp_path = temp_path
        self.fan_controller_path = controller_path
        return True

    def _fan_node(self, filename: str) -> str:
        if not self.fan_controller_path and not self._refresh_fan_nodes():
            raise RuntimeError(self._t("err.fanControllerNodeMissing"))
        return os.path.join(self.fan_controller_path, filename)

    def _fan_temp_node(self) -> str:
        if not self.fan_temp_path and not self._refresh_fan_nodes():
            raise RuntimeError(self._t("err.fanTempNodeMissing"))
        return os.path.join(self.fan_temp_path, self.FAN_TEMP_FILE)

    def _fan_driver_ok(self) -> bool:
        """确认本机跑的是那个同时提供 charge_behaviour 的 oxpec 驱动。

        ``pwm1_enable`` 的取值语义是**驱动相关**的：oxpec 用 ``2`` 表示 EC 自动、
        ``0`` 表示「手动 + 满速」；而旧的 ``drivers/hwmon/oxp-sensors.c`` 用 ``0``
        表示自动。两者读回值无法可靠区分（在 oxpec 上写 0 后读回也是 0，看起来
        "生效了"，实际是手动满速），所以只能先确认驱动身份，再决定写什么值。

        判定依据：oxpec 会给电池节点挂上 ``charge_behaviour`` /
        ``charge_control_end_threshold``，旧的风扇驱动则不会。
        """
        try:
            path = self._battery()
        except Exception:  # noqa: BLE001 - 找不到电池时按"无法确认"处理
            return False
        return self._node_exists(
            os.path.join(path, self.BEHAVIOUR_FILE)
        ) or self._node_exists(os.path.join(path, self.THRESHOLD_FILE))

    def _fan_unavailable_reason(self) -> Optional[str]:
        """风扇控制不可用的原因；可用时返回 None。"""
        if not self._fan_driver_ok():
            return self._t("err.fanGate")
        if not self._refresh_fan_nodes():
            return self._t(
                "err.fanNodesMissing",
                temp=self.FAN_TEMP_SENSOR,
                controller=self.FAN_CONTROLLER,
            )
        return None

    def _require_fan_support(self) -> None:
        reason = self._fan_unavailable_reason()
        if reason:
            raise RuntimeError(reason)

    def _fan_enable_raw(self) -> Optional[int]:
        """``pwm1_enable`` 的原始读回值。"""
        return self._parse_int(self._read_or_none(self._fan_node(self.FAN_ENABLE_FILE)))

    @classmethod
    def _fan_control_mode(cls, enable: Optional[int]) -> Tuple[bool, bool]:
        """把 ``pwm1_enable`` 的**读回值**翻译成 ``(是否手动, 是否 EC 自动)``。

        这是全插件唯一一处做这个翻译的地方，其它代码一律调它，避免判断散落
        在各个分支里各写一遍、然后慢慢漂移。

        为什么不能直接比较写入值：oxpec 的读回调做了兼容转换，
        ``2`` = EC 自动、``1`` = 手动且 PWM < 255、**``0`` = 手动且 PWM == 255**。
        所以只认 ``enable == 1`` 会在风扇满速时把手动误判成"非手动"，
        进而把已成功的切换报成失败、界面显示成 EC 自动。
        """
        return enable in cls.FAN_MANUAL_ENABLE_VALUES, enable == cls.FAN_ENABLE_AUTO

    def _fan_is_manual(self) -> bool:
        return self._fan_enable_raw() in self.FAN_MANUAL_ENABLE_VALUES

    def _fan_is_auto(self) -> bool:
        return self._fan_enable_raw() == self.FAN_ENABLE_AUTO

    def _read_fan_temperature(self) -> Optional[float]:
        """CPU 温度（°C）。k10temp 的 temp1_input 单位是毫摄氏度。"""
        value = self._parse_int(self._read_or_none(self._fan_temp_node()))
        if value is None:
            return None
        return value / 1000.0

    def _read_fan_status_sync(self) -> Dict[str, Any]:
        settings = self._load_settings()
        mode = settings.get("fan_mode", self.FAN_MODE_AUTO)
        if mode not in self.FAN_MODES:
            mode = self.FAN_MODE_AUTO

        status: Dict[str, Any] = {
            "mode": mode,
            "mode_label": self._fan_mode_label(mode),
            "controller": self.FAN_CONTROLLER,
            "temp_sensor": self.FAN_TEMP_SENSOR,
            "safety_temp": self.FAN_SAFETY_TEMP,
            "modes": list(self.FAN_MODES),
            "mode_labels": self._fan_mode_labels(),
        }

        # 节点不可用不是"操作失败"，而是这台机器没有这个能力：
        # 返回 available=False 让界面安静地降级，不要每次轮询都弹错误。
        reason = self._fan_unavailable_reason()
        if reason:
            status["available"] = False
            status["reason"] = reason
            return status

        # 只读一次 enable，再统一翻译，避免两次读取之间状态变化导致
        # manual 与 auto 同时为真/为假这种自相矛盾的结果。
        enable = self._fan_enable_raw()
        manual, auto = self._fan_control_mode(enable)
        status.update(
            {
                "available": True,
                "reason": None,
                "temperature": self._read_fan_temperature(),
                "rpm": self._parse_int(self._read_or_none(self._fan_node(self.FAN_RPM_FILE))),
                "pwm": self._parse_int(self._read_or_none(self._fan_node(self.FAN_PWM_FILE))),
                "pwm_enable": enable,
                "pwm_enable_label": (
                    self._t("fan.control.manual")
                    if manual
                    else (
                        self._t("fan.control.auto")
                        if auto
                        else self._t("fan.control.unknown", value=enable)
                    )
                ),
                "manual": manual,
                "auto": auto,
                "custom_curve": self._get_fan_curve(self.FAN_MODE_CUSTOM),
            }
        )
        if mode != self.FAN_MODE_AUTO:
            status["curve"] = self._get_fan_curve(mode)
        return status

    # ------------------------------------------------------- 电池设备发现
    @staticmethod
    def _candidates() -> List[str]:
        return sorted(glob.glob("/sys/class/power_supply/BAT*"))

    def _score_battery(self, path: str) -> int:
        """给候选电池节点打分，避免选中外设电池或错误的设备。"""
        score = 0
        if os.path.basename(path).startswith("BAT"):
            score += 10
        if self._read_or_none(os.path.join(path, "type")) == "Battery":
            score += 20
        if self._read_or_none(os.path.join(path, "scope")) == "Device":
            score += 5
        if self._node_exists(os.path.join(path, self.BEHAVIOUR_FILE)):
            score += 1000
        elif self._node_exists(os.path.join(path, self.THRESHOLD_FILE)):
            score += 100
        return score

    @staticmethod
    def _ac_candidates() -> List[str]:
        """非电池的 power_supply 节点（适配器、USB-C 供电等）。"""
        return [
            path
            for path in sorted(glob.glob("/sys/class/power_supply/*"))
            if not os.path.basename(path).startswith("BAT")
        ]

    def _ac_online(self) -> Tuple[Optional[bool], Optional[str]]:
        """外接电源是否在线，返回 ``(online, 节点名)``。

        优先看 ``type == "Mains"`` 的节点（ACPI 适配器），没有再退回 USB-C 供电节点；
        两者都不存在时返回 ``(None, None)``，由调用方降级判断。

        同一组里有多个节点时（例如机器上挂了多个 USB-C source-psy），只要有一个在线就算在线，
        且**节点名要取实际在线的那个**，否则诊断区会出现"USB-C2 在线"而 USB-C2 其实是离线口
        这种自相矛盾的显示。整组都离线时才退回第一个节点名，仅用于展示。
        """
        mains: List[Tuple[str, bool]] = []
        usb: List[Tuple[str, bool]] = []
        for path in self._ac_candidates():
            kind = (self._read_or_none(os.path.join(path, "type")) or "").strip()
            if kind == self.AC_MAINS_TYPE:
                bucket = mains
            elif kind.startswith(self.AC_USB_PREFIX):
                bucket = usb
            else:
                continue
            online = self._parse_int(self._read_or_none(os.path.join(path, "online")))
            if online is not None:
                bucket.append((os.path.basename(path), bool(online)))
        for group in (mains, usb):
            if not group:
                continue
            for name, flag in group:
                if flag:
                    return True, name
            return False, group[0][0]
        return None, None

    def _find_battery(self) -> Optional[str]:
        """返回最适合做充电控制的电池节点；都不支持时返回 None。"""
        candidates = self._candidates()
        if not candidates:
            return None
        best = max(candidates, key=self._score_battery)
        return best if self._score_battery(best) >= 100 else None

    def _battery(self, refresh: bool = False) -> str:
        if refresh or not self.battery_path:
            found = self._find_battery()
            if not found:
                raise RuntimeError(self._t("err.noBatteryNode"))
            self.battery_path = found
        return self.battery_path

    # --------------------------------------------------------- 状态解析
    @staticmethod
    def _parse_behaviours(raw: Optional[str]) -> Dict[str, Any]:
        """解析 ``charge_behaviour``，当前生效值由方括号标记。

        形如 ``auto inhibit-charge [inhibit-charge-awake]``。
        """
        supported: List[str] = []
        active: Optional[str] = None
        for token in re.findall(r"\[[^\]]*\]|\S+", raw or ""):
            if token.startswith("[") and token.endswith("]"):
                value = token[1:-1].strip()
                active = value
            else:
                value = token.strip()
            if value and value not in supported:
                supported.append(value)
        return {"supported": supported, "active": active}

    @staticmethod
    def _parse_int(raw: Optional[str]) -> Optional[int]:
        if raw is None:
            return None
        match = re.match(r"-?\d+", raw)
        return int(match.group()) if match else None

    def _read_state(self) -> Dict[str, Any]:
        path = self._battery(refresh=True)
        ac_online, ac_node = self._ac_online()
        state: Dict[str, Any] = {
            "battery": os.path.basename(path),
            "battery_path": path,
            "read_at": time.time(),
            "ac_online": ac_online,
            "ac_node": ac_node,
        }

        for name in (
            "capacity",
            "status",
            "power_now",
            "voltage_now",
            "energy_now",
            "energy_full",
            "charge_now",
            "charge_full",
        ):
            value = self._read_or_none(os.path.join(path, name))
            if value is not None:
                state[name] = value

        # power_now 恒为正值：ACPI 电池驱动会对 _BST 的 rate 取绝对值
        # （drivers/acpi/battery.c: battery->rate_now = abs((s16)rate_now)），
        # 所以方向必须依据 status 补回来。
        # 内核 ABI 约定：正值 = 电流流入电池（充电），负值 = 电池放电。
        power_uw = self._parse_int(state.get("power_now"))
        if power_uw is not None:
            if power_uw > 0 and (state.get("status") or "").strip() == "Discharging":
                power_uw = -power_uw
            state["power_watts"] = round(power_uw / 1_000_000, 2)

        # ---- charge_behaviour ----
        behaviour_raw = self._read_or_none(os.path.join(path, self.BEHAVIOUR_FILE))
        parsed = self._parse_behaviours(behaviour_raw)
        supported = parsed["supported"]
        state["charge_behaviour"] = parsed
        state["behaviour_raw"] = behaviour_raw
        state["behaviour_node"] = self._node_exists(os.path.join(path, self.BEHAVIOUR_FILE))
        state["supports_normal"] = self.MODE_AUTO in supported
        state["supports_awake_bypass"] = self.MODE_AWAKE in supported
        state["supports_bypass"] = self.MODE_ALWAYS in supported
        state["supports_force_discharge"] = self.MODE_FORCE_DISCHARGE in supported

        # ---- charge_control_end_threshold ----
        threshold_node = os.path.join(path, self.THRESHOLD_FILE)
        state["threshold_node"] = self._node_exists(threshold_node)
        state["threshold"] = self._parse_int(self._read_or_none(threshold_node))
        # 以「节点存在」而非「可读」判定支持：部分 EC 可写但不可读。
        state["supports_threshold"] = state["threshold_node"]

        # ---- 持久化设置 ----
        settings = self._load_settings()
        state["auto_restore"] = bool(settings.get("auto_restore", True))
        state["saved_threshold"] = settings.get("threshold")
        state["saved_mode"] = settings.get("mode")
        state["restore_report"] = list(self._restore_report)
        state["mode_labels"] = self._mode_labels()
        state["presets"] = list(self.PRESETS)
        state["threshold_disabled_value"] = self.THRESHOLD_DISABLED
        return state

    # --------------------------------------------------------- 设置持久化
    def _settings_file(self) -> str:
        if self._settings_path:
            return self._settings_path
        settings_dir = getattr(decky, "DECKY_PLUGIN_SETTINGS_DIR", None)
        if not settings_dir:
            settings_dir = os.path.join(
                os.path.expanduser("~"), ".config", "decky", "settings"
            )
        os.makedirs(settings_dir, exist_ok=True)
        self._settings_path = os.path.join(settings_dir, self.SETTINGS_FILE)
        return self._settings_path

    def _load_settings(self) -> Dict[str, Any]:
        try:
            with open(self._settings_file(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_settings(self, data: Dict[str, Any]) -> None:
        path = self._settings_file()
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def _update_setting(self, key: str, value: Any) -> None:
        settings = self._load_settings()
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value
        self._save_settings(settings)

    # ------------------------------------------------------------- 错误信息
    def _error_message(self, exc: Exception, what: Optional[str] = None) -> str:
        # `what` 需要在**当前语言**下取值，而语言是运行期才定的 —— 所以默认值
        # 不能写成中文字面量，只能留 `None`、进来再解析。
        if what is None:
            what = self._t("common.chargeControl")
        if isinstance(exc, PermissionError):
            return self._t(
                "err.noPermission", what=what, uid=self._current_uid()
            )
        if isinstance(exc, OSError):
            return self._t("err.writeRejected", exc=exc)
        return str(exc)

    @staticmethod
    def _current_uid() -> Any:
        """当前进程的有效 uid；非 POSIX 平台（开发机上的 Windows）返回 ``"n/a"``。

        写 ``/sys/class/power_supply/*`` 需要 uid 0。Decky 仅在 plugin.json 的
        ``flags`` 包含 ``"root"`` 时让插件进程保持 root，否则会 setuid 降权到
        deck（uid 1000），此时所有写入都会 EACCES。
        """
        geteuid = getattr(os, "geteuid", None)
        return geteuid() if geteuid is not None else "n/a"

    # ---------------------------------------------------------------- 对外 RPC
    async def get_status(self) -> Dict[str, Any]:
        try:
            return {"ok": True, "data": await asyncio.to_thread(self._read_state)}
        except Exception as exc:  # noqa: BLE001 - 需要把任何异常转为界面提示
            decky.logger.error(f"F1Pro EC Control: 读取状态失败: {exc}")
            return {"ok": False, "error": self._error_message(exc)}

    async def set_charge_mode(self, mode: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self._set_charge_mode_sync, mode)

    def _set_charge_mode_sync(self, mode: str) -> Dict[str, Any]:
        allowed = set(self.SELECTABLE_MODES) | {self.MODE_FORCE_DISCHARGE}
        if mode not in allowed:
            return {"ok": False, "error": self._t("err.invalidChargeMode", mode=mode)}
        try:
            path = self._battery()
            node = os.path.join(path, self.BEHAVIOUR_FILE)
            if not self._node_exists(node):
                raise RuntimeError(self._t("err.noBehaviourNode"))

            supported = self._parse_behaviours(self._read(node))["supported"]
            if mode not in supported:
                label = self._mode_label(mode)
                available = " ".join(supported) if supported else self._t("err.noneValue")
                raise RuntimeError(
                    self._t("err.modeUnsupported", label=label, available=available)
                )

            self._write_node(node, mode)

            def _applied() -> bool:
                return self._parse_behaviours(self._read_or_none(node))["active"] == mode

            if not self._wait_for(_applied):
                actual = self._parse_behaviours(self._read_or_none(node))["active"]
                raise RuntimeError(
                    self._t("err.modeNotApplied", mode=mode, actual=actual)
                )

            self._update_setting("mode", mode)
            return {"ok": True, "data": self._read_state()}
        except Exception as exc:  # noqa: BLE001
            decky.logger.error(f"F1Pro EC Control: 切换模式失败: {exc}")
            return {"ok": False, "error": self._error_message(exc)}

    async def set_charge_threshold(self, value: int) -> Dict[str, Any]:
        try:
            value = int(value)
        except (TypeError, ValueError):
            return {"ok": False, "error": self._t("err.thresholdNotInt")}
        return await asyncio.to_thread(self._set_threshold_sync, value)

    def _set_threshold_sync(self, value: int) -> Dict[str, Any]:
        if value != self.THRESHOLD_DISABLED and not (
            self.MIN_THRESHOLD <= value <= self.MAX_THRESHOLD
        ):
            return {
                "ok": False,
                "error": self._t(
                    "err.thresholdRange",
                    min=self.MIN_THRESHOLD,
                    max=self.MAX_THRESHOLD,
                    disabled=self.THRESHOLD_DISABLED,
                ),
            }
        try:
            path = self._battery()
            node = os.path.join(path, self.THRESHOLD_FILE)
            if not self._node_exists(node):
                raise RuntimeError(self._t("err.noThresholdNode"))

            self._write_node(node, str(value))

            def _applied() -> bool:
                return self._parse_int(self._read_or_none(node)) == value

            if not self._wait_for(_applied):
                actual = self._parse_int(self._read_or_none(node))
                if actual is None:
                    raise RuntimeError(
                        self._t("err.thresholdUnreadable", value=value)
                    )
                raise RuntimeError(
                    self._t("err.thresholdAdjusted", actual=actual, value=value)
                )

            # 100% 表示「不限制」，不需要在启动时恢复。
            self._update_setting(
                "threshold", None if value == self.THRESHOLD_DISABLED else value
            )
            return {"ok": True, "data": self._read_state()}
        except Exception as exc:  # noqa: BLE001
            decky.logger.error(f"F1Pro EC Control: 设置上限失败: {exc}")
            return {"ok": False, "error": self._error_message(exc)}

    async def set_auto_restore(self, enabled: bool) -> Dict[str, Any]:
        def _apply() -> Dict[str, Any]:
            self._update_setting("auto_restore", bool(enabled))
            return {"ok": True, "data": self._read_state()}

        return await asyncio.to_thread(_apply)

    async def set_locale(self, lang: str) -> Dict[str, Any]:
        """记录界面语言（由前端挂载时探测后告知）。

        只接受中文 / 英文两类，其余（含取不到）一律落回 ``"en"`` ——
        不要因为前端传了个没见过的值就让后端文案变成键名。

        **必须写类属性、不能用 `self._lang = ...`**：后者会创建一个同名**实例**
        属性去遮蔽类属性，而 `_normalize_fan_curve` 是 `@classmethod`、
        只能通过 `cls._lang` 取值 —— 走 `self` 赋值的话，用户切到中文后
        曲线校验的报错仍然吐英文（离线已复现）。语言本来就是"这套插件安装"
        的全局状态（Decky 只会有一个实例），放类上语义也对。
        """
        normalized = str(lang).strip().lower()
        Plugin._lang = (
            "zh" if normalized.startswith(self._ZH_LANG_HINTS) else "en"
        )
        decky.logger.info(f"F1Pro EC Control: 界面语言设为 {Plugin._lang}")
        return {"ok": True}

    # --------------------------------------------------------- 风扇曲线计算
    @classmethod
    def _normalize_fan_curve(cls, curve: Any) -> List[List[int]]:
        """校验并规范化五点曲线；不合法时抛 ``ValueError``。

        温度必须**按原样**严格递增，否则插值时会出现除零或来回横跳。

        这里**刻意不做任何重排**。旧的实现会先把节点按温度排一遍再查重复，
        等于把"顺序错了"当成"顺序无所谓"静默修好 —— 而前端把乱序判为错误。
        结果是同一条曲线走 UI 报错、走 API 通过，语义不一致；更糟的是
        重排会让每个节点的 PWM 跟着搬家（节点 1 的 40 跑到节点 2 上），
        等于悄悄改乱用户的设置。规则前后端必须一致：乱序就报错。
        """
        if not isinstance(curve, (list, tuple)) or len(curve) != cls.FAN_CURVE_POINTS:
            raise ValueError(
                cls._t_static("err.curvePointCount", count=cls.FAN_CURVE_POINTS)
            )

        points: List[List[int]] = []
        for point in curve:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError(cls._t_static("err.curvePointPair"))
            try:
                temperature = int(point[0])
                pwm = int(point[1])
            except (TypeError, ValueError):
                raise ValueError(cls._t_static("err.curvePointInt")) from None
            if not cls.FAN_MIN_TEMP <= temperature <= cls.FAN_MAX_TEMP:
                raise ValueError(
                    cls._t_static(
                        "err.curveTempRange",
                        min=cls.FAN_MIN_TEMP,
                        max=cls.FAN_MAX_TEMP,
                    )
                )
            if not cls.FAN_MIN_PWM <= pwm <= cls.FAN_MAX_PWM:
                raise ValueError(
                    cls._t_static(
                        "err.curvePwmRange",
                        min=cls.FAN_MIN_PWM,
                        max=cls.FAN_MAX_PWM,
                    )
                )
            points.append([temperature, pwm])

        # 按原样校验严格递增：既不排序，也不容忍相等。
        # 注意这里检查的是**原始顺序**，与前端 validateCurve 的规则完全一致。
        for index in range(1, len(points)):
            if points[index][0] <= points[index - 1][0]:
                raise ValueError(cls._t_static("err.curveNotIncreasing"))
        return points

    @classmethod
    def _interpolate_curve(cls, curve: List[List[int]], temperature: float) -> int:
        """按五点曲线线性插值出 PWM。"""
        try:
            points = cls._normalize_fan_curve(curve)
        except ValueError:
            points = cls._normalize_fan_curve(cls.FAN_CUSTOM_SEED)

        if temperature <= points[0][0]:
            return points[0][1]
        if temperature >= points[-1][0]:
            return points[-1][1]

        for index in range(len(points) - 1):
            t1, p1 = points[index]
            t2, p2 = points[index + 1]
            if t1 <= temperature <= t2:
                if t2 == t1:
                    return p2
                ratio = (temperature - t1) / (t2 - t1)
                return int(round(p1 + (p2 - p1) * ratio))
        return points[-1][1]

    def _get_fan_curve(self, mode: str) -> List[List[int]]:
        if mode in self.FAN_PROFILES:
            return [list(point) for point in self.FAN_PROFILES[mode]]

        if mode == self.FAN_MODE_CUSTOM:
            stored = self._load_settings().get("fan_custom_curve")
            try:
                return self._normalize_fan_curve(stored)
            except ValueError:
                # 设置文件被改坏 / 是旧版本格式：退回初始曲线，不要崩。
                return [list(point) for point in self.FAN_CUSTOM_SEED]

        return [list(point) for point in self.FAN_PROFILES[self.FAN_MODE_BALANCED]]

    # --------------------------------------------------------- 风扇控制线程
    def _stop_fan_controller(self) -> bool:
        """停止控制线程。返回是否**确认已退出**。

        **不能 join 超时就无条件把 ``_fan_thread`` 清成 None。**
        旧写法正是这样做的，于是当线程因为 sysfs 读写阻塞而迟迟不退出时，
        ``_fan_thread`` 被清空 → 后续 ``_start_fan_controller()`` 看到"没有旧线程"
        → **又起一个**，两个线程同时写 ``pwm1``（离线探针复现过）。
        真实硬件上 I/O 阻塞不是臆想，风扇会抽搐。

        现在的语义：
        - 线程确认退出 → 清引用、返回 True。
        - 线程仍在跑 → **保留引用**、返回 False，让调用方知道"没停干净"。
          调用方据此拒绝启动新线程（见 ``_start_fan_controller``），
          宁可这一次转换失败（会有明确日志），也不能让两个线程并写同一个 EC。

        无论走哪条路，都**先把世代号推进一步**（见 ``_fan_epoch``）：
        这是"作废在途步骤"的开关，而且必须在 join **之前**推进 ——
        线程可能正卡在 I/O 里、join 根本等不到它。
        """
        self._fan_epoch += 1
        self._fan_stop_event.set()
        thread = self._fan_thread
        if thread is None:
            return True
        if not thread.is_alive() or thread is threading.current_thread():
            # 已经不在跑（或就是自己——控制线程内部调用时不能 join 自己）。
            self._fan_thread = None
            return True

        thread.join(timeout=self.FAN_STOP_TIMEOUT)
        if thread.is_alive():
            # **保留引用**：这是关键。清掉它就等于"假装停掉了"。
            decky.logger.error(
                f"F1Pro Fan: 控制线程 {thread.name} 在 {self.FAN_STOP_TIMEOUT}s 内未退出，"
                "保留引用并拒绝启动新线程"
            )
            return False

        self._fan_thread = None
        return True

    def _start_fan_controller(self) -> bool:
        """启动控制线程。若旧线程仍在跑则**拒绝启动**。"""
        with self._fan_lock:
            if self._fan_thread is not None:
                if self._fan_thread.is_alive():
                    if not self._fan_stop_event.is_set():
                        # 本来就活着、也没要求停止：无需重启，算成功。
                        return True
                    # 要求它停但它还活着：**绝不覆盖引用去起第二个**。
                    # 两个线程同时写 pwm1 比"这一次没起来"危险得多。
                    decky.logger.error(
                        f"F1Pro Fan: 旧控制线程 {self._fan_thread.name} 仍在运行，"
                        "拒绝启动新的控制线程"
                    )
                    return False
                # 已停止但引用还在：清掉，继续走正常启动。
                self._fan_thread = None

            self._fan_stop_event.clear()
            self._fan_thread = threading.Thread(
                target=self._fan_control_loop,
                name="F1ProFanController",
                daemon=True,
            )
            self._fan_thread.start()
            return True

    def _fan_cancelled(self, epoch: int) -> bool:
        """这次转换/这一步是否已被"取走控制权"取消（见 ``_fan_epoch``）。

        两个判据合在一处，因为**所有**写节点前都必须同时满足：
        - ``_fan_step_invalidated(epoch)``：中途发生过停线程 / 交还 EC（在途步骤作废）；
        - ``_fan_unloading``：插件已进入卸载流程（**整段转换**作废，不只是这一步）。

        后者是必需的：世代号只作废"在途的那一步"，而一次 RPC 转换在
        ``_fan_step()`` 之后还有 ``_start_fan_controller()`` 与返回成功 ——
        卸载取锁超时返回后，那个 RPC 照样能把 EC 抢回手动（探针 probe_f 复现过）。
        """
        return self._fan_step_invalidated(epoch) or self._fan_unloading

    def _fan_step_invalidated(self, epoch: int) -> bool:
        """本步的世代号是否已被"取走控制权"的操作作废（见 ``_fan_epoch``）。

        单独抽出来是为了让"作废"这件事只有一个判据：调用点有两处
        （夺回控制权之前、写 pwm1 之前），两处必须完全一致。
        """
        return epoch != self._fan_epoch

    def _fan_step(self) -> Optional[int]:
        """控制循环的单步：确认门禁 → 确认手动 → 读温度 → 算 PWM → 必要时写入。

        返回本次写入的 PWM；差值未达阈值（没写）时返回 None。
        单独抽出来是为了能在离线测试里直接驱动一次，不必启动线程、不必等 3 秒。

        **每一步都要能被"取走控制权"作废**：进入时记下当时的 ``_fan_epoch``，
        每次写节点之前再比对一次。只要中间发生过停线程 / 交还 EC，这一步就
        整体作废、一个字都不写。理由是并发下会出现"交还刚落地、在途的这一步
        又把控制权抢回手动"（离线探针复现过：卸载已返回、EC 却又变回手动）。
        """
        epoch = self._fan_epoch
        mode = self._load_settings().get("fan_mode", self.FAN_MODE_AUTO)
        if mode == self.FAN_MODE_AUTO:
            return None
        if mode not in self.FAN_MODES:
            mode = self.FAN_MODE_BALANCED

        # 每一步都重新过一遍硬件身份门禁，这是**纵深防御**：三条调用路径
        # （线程、切模式、切曲线）的入口都查过了，但控制线程会在后台跑很久，
        # 期间硬件判据可能变化（电池节点消失、驱动被换）。写错 pwm1_enable
        # 不会报错、只会让 EC 卡在手动满速，所以宁可多查一次。
        self._require_fan_support()

        # 确认控制权还在我们手里。EC 复位、或其它工具把 pwm1_enable 改回自动后，
        # 继续写 pwm1 是**不会生效**的——那会变成"界面说手动、实际自动"。
        #
        # 判据必须是"**已确认是手动**"，不能反过来用 `_fan_is_auto()` 取非：
        # 那个读回值有三种含义完全不同的取值——2（自动）、1/0（手动）、
        # **None（读不回来：读 OSError、内容为空、值非数字）**。
        # `None` 取非会得到"非自动"，于是"夺回控制权"被整段跳过、直接去写 pwm1，
        # 而 EC 寄存器里可能根本不是手动模式——真机上这次写入不生效，
        # 控制线程却认为成功了（离线已复现）。读不到 ≠ 确认是手动。
        enable = self._fan_enable_raw()
        is_manual, is_auto = self._fan_control_mode(enable)
        if not is_manual:
            # 夺回控制权**之前**再比对一次世代号：这一刻恰好有转换把控制权
            # 交还给了 EC（于是我们读到的才是"非手动"），这一步就不能再把
            # 它抢回来 —— 那正是"卸载返回后风扇又变手动"的成因。
            if self._fan_cancelled(epoch):
                return None
            # **卸载期间一律不自动夺权**（见 _fan_unloading）：
            # 世代号只作废"在途的这一步"，挡不住 RPC 转换的后续步骤
            # （_fan_apply_manual 起线程、_set_fan_mode_sync 报 ok=True）。
            # 插件已经要退出了，此刻把 EC 抢回手动是最坏的结果 ——
            # 卸载结束后没人再交还它，风扇会永久停在手动 PWM 上。
            if self._fan_unloading:
                decky.logger.info(
                    "F1Pro Fan: 插件正在卸载，不再夺回风扇控制权（保持 EC 自动）"
                )
                return None
            if is_auto:
                decky.logger.warning("F1Pro Fan: EC 已收回风扇控制权，重新切回手动")
            else:
                # 读回不可用：无法确认当前模式。写 pwm1_enable=1 是幂等的
                # （本来手动就再写一次），做完再确认一次即可。
                decky.logger.warning("F1Pro Fan: pwm1_enable 读回不可用，重新声明手动控制")
            self._write_node(
                self._fan_node(self.FAN_ENABLE_FILE), str(self.FAN_ENABLE_MANUAL)
            )
            # 写完必须确认真的回到手动，否则下面的 pwm1 写入是空转：
            # 界面会显示"手动"、温度也在读，但 PWM 根本没生效。
            # 确认失败就抛异常，交给调用方走 fail-safe。
            if not self._wait_for(self._fan_is_manual):
                raise RuntimeError(self._t("err.fanReclaimUnconfirmed"))

        temperature = self._read_fan_temperature()
        if temperature is None:
            raise RuntimeError(self._t("err.fanTempUnreadable"))

        # 安全保护优先于任何曲线：达到 85°C 一律满速。
        is_safety_override = temperature >= self.FAN_SAFETY_TEMP
        if is_safety_override:
            target_pwm = self.FAN_MAX_PWM
            decky.logger.warning(
                f"F1Pro Fan: CPU {temperature:.1f}°C 达到安全阈值，风扇满速"
            )
        else:
            target_pwm = self._interpolate_curve(self._get_fan_curve(mode), temperature)

        target_pwm = max(self.FAN_MIN_PWM, min(self.FAN_MAX_PWM, target_pwm))

        current_pwm = self._parse_int(
            self._read_or_none(self._fan_node(self.FAN_PWM_FILE))
        )
        # 死区只用来抑制**曲线插值**带来的微小抖动，**不能拦安全保护**：
        # 84.8°C 时曲线可能刚好插值到 250，跳到 85.2°C 后目标 255，
        # 差值 5 < 8 会被死区挡下——而只要曲线插值仍落在 250 附近，
        # 之后每一轮都会继续被挡，风扇永远升不到满速，安全保护形同虚设。
        # 所以安全保护必须无条件写入。
        if not is_safety_override and current_pwm is not None and (
            abs(target_pwm - current_pwm) < self.FAN_PWM_CHANGE_THRESHOLD
        ):
            return None

        # 写 pwm1 之前最后确认一次"这一步还有效"：中间可能已经有人把控制权
        # 交还 EC 了，此时再写 pwm1 只会让 EC 与界面不一致。
        if self._fan_cancelled(epoch):
            return None

        self._write_node(self._fan_node(self.FAN_PWM_FILE), str(target_pwm))
        # 读回**必须等于目标**才算写入生效。只检查"读得到"是不够的：
        # 驱动器可以接受写入却不让寄存器变化（EC 忙、模式被外部改回自动、
        # 驱动 bug），此时读回仍是旧值。若把旧值当成功返回，
        # 控制循环会认为这一轮已经调好了，而风扇实际没动。
        # 更隐蔽的后果是死区：它靠"读回值 ≈ 上一次目标"来抑制抖动，
        # 读回不可信时这个判断基础就没了。
        actual = self._parse_int(self._read_or_none(self._fan_node(self.FAN_PWM_FILE)))
        if actual is None:
            raise RuntimeError(self._t("err.fanPwmUnreadable"))
        if actual != target_pwm:
            raise RuntimeError(
                self._t("err.fanPwmMismatch", actual=actual, target=target_pwm)
            )
        return actual

    def _fan_acquire_transition_or_stop(self) -> bool:
        """取转换锁，但**随时响应停止请求**。

        返回 False 表示收到了停止请求，调用方应当直接退出、不要再写任何节点。

        为什么不能直接 ``with self._fan_transition_lock`` 了事：
        取走控制权的转换（交还 EC / 停线程）是**拿着这把锁**去 join 控制线程的。
        控制线程若正卡在等锁上，就变成"它等我退出、我等它放锁"的循环等待，
        只能靠 join 超时挣脱 —— 于是一次本来正常的转换被**误判成失败**，
        还要多花一个超时的时间。所以等锁期间要盯着停止标志：一旦有人要收走
        控制权，就放弃这一步（这也是"旧线程不得在交还后再执行一步"的一部分）。
        """
        while not self._fan_stop_event.is_set():
            if self._fan_transition_lock.acquire(timeout=0.25):
                return True
        return False

    def _fan_control_loop(self) -> None:
        while not self._fan_stop_event.is_set():
            try:
                mode = self._load_settings().get("fan_mode", self.FAN_MODE_AUTO)
                if mode == self.FAN_MODE_AUTO:
                    return
                # 单步写入也要在**转换锁**里做，否则控制线程会在某个 RPC 转换
                # 的中途插进来写 pwm1。典型后果：RPC 刚写入手动、正在验证，
                # 线程按旧曲线写了一次 PWM；或者 RPC 正在交还 EC、线程又把
                # pwm1_enable 切回手动。锁是 RLock，线程不会与自己死锁。
                if not self._fan_acquire_transition_or_stop():
                    return
                try:
                    self._fan_step()
                finally:
                    self._fan_transition_lock.release()
            except Exception as exc:  # noqa: BLE001 - 风扇线程不能带异常退出
                decky.logger.error(f"F1Pro Fan: 控制异常：{exc}")
                # 走统一的失败兜底，**不要在这里自己写 pwm1_enable**。
                #
                # 关键原因：_fan_step() 的异常来源之一就是门禁失效
                # （_require_fan_support 抛错）。此时驱动身份已经无法确认，
                # 而 pwm1_enable 的取值语义是驱动相关的——再写 2 有可能被
                # 旧驱动解释成"手动"，反而把风扇钉在手动状态上。
                # _fan_fail_safe() 内部会重新执行门禁检查，门禁失效时不写任何
                # 风扇节点，正好是这个场景需要的语义。
                self._fan_fail_safe()
                self._update_setting("fan_mode", self.FAN_MODE_AUTO)
                return
            self._fan_stop_event.wait(self.FAN_POLL_INTERVAL)

    def _fan_handover_to_ec(self) -> bool:
        """停止控制线程并把控制权交还 EC（``pwm1_enable = 2``）。

        这是"把风扇交回去"的**唯一底层入口**，所以门禁放在自己身上，
        不指望调用方先检查：只要驱动身份无法确认，就一个字都不写。
        理由是 ``pwm1_enable`` 的语义是驱动相关的（见 ``_fan_driver_ok``），
        在错误的驱动上写 ``2`` 可能被解释成别的含义，反而把风扇钉住。

        目前 ``_fan_fail_safe`` 也在自己的路径上查一次门禁，看似重复——
        但那是**兜底**语义（不写任何节点），这里是**安全闸**语义（不写这个
        节点），两者不能互相替代：以后新增调用方时，只要走到这里就自动受保护。

        **停线程超时怎么办**：不能当作"停好了"继续往下走，也不能因为停不掉
        就干脆不交还。两种选择的后果不一样：

        - 不写：EC 会留在手动 PWM 上，而控制线程迟早会退出（I/O 总会返回），
          于是风扇永久停在"不跟温度走、也没有 85°C 保护"的状态；
        - 继续写：上面 ``_stop_fan_controller()`` 已经推进一步世代号，
          **在途的那一步会在下一个检查点作废**，不会再把控制权抢回手动，
          所以这次写入是安全的，EC 能回到自动。

        所以这里**继续写**，但**如实返回失败**（``confirmed and stopped``）：
        调用方据此走失败分支（不更新设置、界面显示失败），而不是"看起来交还
        成功、实际线程还在跑"。
        """
        stopped = self._stop_fan_controller()
        if not stopped:
            decky.logger.error(
                "F1Pro Fan: 控制线程未在超时内退出，仍尝试交还 EC，但本次转换判为失败"
            )
        try:
            self._require_fan_support()

            node = self._fan_node(self.FAN_ENABLE_FILE)
            # 只有在**读回也确认是自动**、且确实不需要改动时才跳过写入。
            # 不能只凭一次读回值就提前 return：sysfs 读回可能滞后于 EC 寄存器
            # （真实硬件上出现过），那种情况下"看起来是自动"而 EC 其实在手动，
            # 跳过写入就等于把风扇留在手动状态。所以一律写出期望值，
            # 再靠下面的 _wait_for 确认——多写一次自动是幂等的，代价远小于漏写。
            self._write_node(node, str(self.FAN_ENABLE_AUTO))
            confirmed = self._wait_for(self._fan_is_auto)
            return confirmed and stopped
        except Exception as exc:  # noqa: BLE001
            decky.logger.error(f"F1Pro Fan: 交还 EC 失败：{exc}")
            return False

    def _fan_fail_safe(self) -> bool:
        """出错时的兜底：停掉控制线程并把控制权交还 EC。

        任何一步失败后都必须走这里。最坏的情况是"已写入 ``pwm1_enable = 1``
        但控制线程没起来"——那台风扇会停在手动 PWM 上不跟温度走、也没有
        85°C 保护，比直接交还 EC 危险得多。
        """
        self._stop_fan_controller()
        if self._fan_unavailable_reason():
            return False
        return self._fan_handover_to_ec()

    def _fan_apply_manual(self, mode: str) -> bool:
        """切到手动 PWM，立即按当前温度写一次，然后启动控制线程。

        返回 False 意味着**风扇可能已经在手动模式、却没有人管它**，
        所以调用方必须走兜底（交还 EC），不能只记一条日志。三条失败路径：
          - 写 pwm1_enable 之后没确认回到手动；
          - ``_fan_step()`` 抛异常（读温度 / 写 PWM / 读回不符）；
          - **控制线程起不来**。这一条原先被漏掉了：``_start_fan_controller()``
            已经会正确返回 False（旧线程没停干净时拒绝再起一个），
            但这里忽略了它的返回值、照样 ``return True`` ——
            于是 RPC 报 ``ok=True``、设置与 EC 都是手动模式，却没有控制循环：
            风扇不跟温度走、85 °C 保护也失效，比直接交还 EC 危险得多。
        """
        node = self._fan_node(self.FAN_ENABLE_FILE)
        self._write_node(node, str(self.FAN_ENABLE_MANUAL))
        # 读回值 0 也代表手动（满速），所以这里必须用 _fan_is_manual 而不是 == 1。
        if not self._wait_for(self._fan_is_manual):
            return False
        self._update_setting("fan_mode", mode)
        self._fan_step()  # 立即生效，不让用户等一个轮询周期
        # **卸载已经开始：整个转换作废。** 走到这里说明上面的 _fan_step()
        # 期间（可能正卡在 I/O 里）卸载已经取走控制权并交还了 EC，
        # 此刻再起控制线程就等于把风扇从 EC 手里抢回来，而且卸载已经返回、
        # 没人会再交还它（探针 probe_f 复现过这个交错）。
        if self._fan_unloading:
            decky.logger.error("F1Pro Fan: 插件正在卸载，放弃启动控制线程")
            return False
        if not self._start_fan_controller():
            decky.logger.error(
                "F1Pro Fan: 控制线程启动失败（旧线程未退出），切换手动控制判为失败"
            )
            return False
        return True

    # ----------------------------------------------------------- 风扇 RPC
    async def get_fan_status(self) -> Dict[str, Any]:
        try:
            return {
                "ok": True,
                "data": await asyncio.to_thread(self._read_fan_status_sync),
            }
        except Exception as exc:  # noqa: BLE001
            decky.logger.error(f"F1Pro Fan: 读取状态失败：{exc}")
            return {"ok": False, "error": self._error_message(exc, self._t("common.fanControl"))}

    async def get_fan_profiles(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "data": {
                "profiles": {
                    mode: [list(point) for point in curve]
                    for mode, curve in self.FAN_PROFILES.items()
                },
                "custom_seed": [list(point) for point in self.FAN_CUSTOM_SEED],
                # **当前已保存的自定义曲线**。前端原先只能从 ``custom_seed``
                # 或预设里推，于是刚保存的曲线在某些时序下会被默认曲线覆盖
                # （profiles 里没有 "custom" 这个键，取不到就退回 seed）。
                # 这里直接把真值给出去，前端才有唯一可信来源。
                "custom_curve": [
                    list(point) for point in self._get_fan_curve(self.FAN_MODE_CUSTOM)
                ],
                "modes": list(self.FAN_MODES),
                "mode_labels": self._fan_mode_labels(),
                "constraints": {
                    "min_temp": self.FAN_MIN_TEMP,
                    "max_temp": self.FAN_MAX_TEMP,
                    "min_pwm": self.FAN_MIN_PWM,
                    "max_pwm": self.FAN_MAX_PWM,
                    "points": self.FAN_CURVE_POINTS,
                    "safety_temp": self.FAN_SAFETY_TEMP,
                },
            },
        }

    async def set_fan_mode(self, mode: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self._set_fan_mode_sync, mode)

    def _set_fan_mode_sync(self, mode: str) -> Dict[str, Any]:
        if mode not in self.FAN_MODES:
            return {"ok": False, "error": self._t("err.invalidFanMode", mode=mode)}
        # **整段转换都在转换锁里**：停止线程 → 取/交控制权 → 写 enable →
        # 写 pwm1 → 读回验证 → 更新设置 → 启线程。
        # 只锁 _start_fan_controller() 是不够的——两个 RPC 并发时会互相穿插，
        # 实测出现过"一个刚写入手动、另一个马上交还 EC"，
        # 最终 EC 在手动而设置文件写着 auto（界面与硬件不一致）。
        with self._fan_transition_lock:
            self._fan_transition_active = True
            try:
                self._require_fan_support()

                if mode == self.FAN_MODE_AUTO:
                    if not self._fan_handover_to_ec():
                        raise RuntimeError(self._t("err.fanHandoverUnconfirmed"))
                    self._update_setting("fan_mode", self.FAN_MODE_AUTO)
                else:
                    if not self._fan_apply_manual(mode):
                        raise RuntimeError(self._t("err.fanManualUnconfirmed"))

                # **卸载优先于"成功"**：上面整段转换期间插件可能已经开始卸载
                # （卸载取锁超时后走"仍尝试交还 EC"的分支并返回）。
                # 此刻若照常返回 ok=True，界面会显示成功、设置里留着手动模式，
                # 而 EC 其实已经被交还 —— 探针 probe_f 复现的正是这个交错。
                if self._fan_unloading:
                    raise RuntimeError(self._t("err.fanUnloading"))

                return {"ok": True, "data": self._read_fan_status_sync()}
            except Exception as exc:  # noqa: BLE001
                decky.logger.error(f"F1Pro Fan: 切换模式失败：{exc}")
                # 失败时绝不能把机器留在「已写入手动 PWM 但无人控制」的状态：
                # 停线程 → 交还 EC。把保存的模式也改回自动同样重要，
                # 否则下次启动还会照着手动模式再恢复一次。
                self._fan_fail_safe()
                self._update_setting("fan_mode", self.FAN_MODE_AUTO)
                return {"ok": False, "error": self._error_message(exc, self._t("common.fanControl"))}
            finally:
                self._fan_transition_active = False

    async def set_fan_custom_curve(self, curve: List[List[int]]) -> Dict[str, Any]:
        return await asyncio.to_thread(self._set_fan_custom_curve_sync, curve)

    def _set_fan_custom_curve_sync(self, curve: List[List[int]]) -> Dict[str, Any]:
        # 校验失败只是"这条曲线不合法"，风扇该继续按旧曲线跑：
        # 不能因为用户填错一个数就把控制权交还 EC。
        try:
            normalized = self._normalize_fan_curve(curve)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        # 与 _set_fan_mode_sync 共用同一把转换锁：保存曲线时若正处在自定义模式，
        # 会立刻应用一次曲线（写 pwm1）并确保线程活着——这同样是"转换"，
        # 必须与并发的模式切换互斥，否则设置文件的保存顺序会与硬件实际状态错开。
        with self._fan_transition_lock:
            self._fan_transition_active = True
            try:
                self._update_setting("fan_custom_curve", normalized)

                # 控制循环每轮都会重新读曲线，所以正在跑自定义模式时新曲线最多
                # 3 秒内自动生效；这里只是顺手把它立刻应用一次，并保证线程还活着。
                if self._load_settings().get("fan_mode") == self.FAN_MODE_CUSTOM:
                    self._fan_step()
                    # **必须检查启线程的结果**（与 _fan_apply_manual 一致）：
                    # 旧线程没停干净时 _start_fan_controller() 会拒绝启动第二个，
                    # 若在这里直接返回，就变成"曲线保存报成功、却没有任何控制循环"——
                    # 风扇不跟温度走、85 °C 保护也失效，比交还 EC 危险得多
                    # （离线探针 .mut/repro_fan_leftovers.py 的 probe_g 复现过）。
                    if self._fan_unloading:
                        decky.logger.error("F1Pro Fan: 插件正在卸载，放弃启动控制线程")
                        raise RuntimeError(self._t("err.fanUnloading"))
                    if not self._start_fan_controller():
                        raise RuntimeError(self._t("err.fanThreadStartFailed"))

                return {"ok": True, "data": {"curve": normalized}}
            except Exception as exc:  # noqa: BLE001
                # 走到这里说明是"应用"环节炸了（读温度、写 PWM 等），
                # 与上面一样必须收敛到"交还 EC"，不能留手动状态无人控制。
                decky.logger.error(f"F1Pro Fan: 应用自定义曲线失败：{exc}")
                self._fan_fail_safe()
                self._update_setting("fan_mode", self.FAN_MODE_AUTO)
                return {"ok": False, "error": self._error_message(exc, self._t("common.fanControl"))}
            finally:
                self._fan_transition_active = False

    async def get_diagnostics(self) -> Dict[str, Any]:
        """返回原始节点信息，便于在非标准内核上排查问题。"""

        def _collect() -> Dict[str, Any]:
            report: Dict[str, Any] = {
                "candidates": self._candidates(),
                "battery_path": None,
                "settings_path": self._settings_file(),
                "nodes": {},
            }
            ac_online, ac_node = self._ac_online()
            report["ac"] = {"node": ac_node, "online": ac_online}

            # 风扇节点单独收集：属性名/驱动不符时，用户在诊断区一眼就能看出来，
            # 不必去猜"为什么风扇面板是灰的"。
            fan: Dict[str, Any] = {
                "hwmon": self._hwmon_paths(),
                "temp_sensor": self._find_hwmon_by_name(self.FAN_TEMP_SENSOR),
                "controller": self._find_hwmon_by_name(self.FAN_CONTROLLER),
                "temp_path": None,
                "controller_path": None,
                "unavailable_reason": None,
                "nodes": {},
            }
            reason = self._fan_unavailable_reason()
            fan["unavailable_reason"] = reason
            if reason is None:
                fan["temp_path"] = self.fan_temp_path
                fan["controller_path"] = self.fan_controller_path
                for node_path in (
                    self._fan_temp_node(),
                    self._fan_node(self.FAN_RPM_FILE),
                    self._fan_node(self.FAN_PWM_FILE),
                    self._fan_node(self.FAN_ENABLE_FILE),
                ):
                    fan["nodes"][os.path.basename(node_path)] = {
                        "path": node_path,
                        "exists": self._node_exists(node_path),
                        "readable": self._read_or_none(node_path) is not None,
                        "value": self._read_or_none(node_path),
                        "writable": os.access(node_path, os.W_OK),
                    }
            report["fan"] = fan

            path = self._find_battery()
            report["battery_path"] = path
            if not path:
                return report
            for name in (
                self.BEHAVIOUR_FILE,
                self.THRESHOLD_FILE,
                "capacity",
                "status",
                "power_now",
            ):
                node = os.path.join(path, name)
                report["nodes"][name] = {
                    "exists": self._node_exists(node),
                    "readable": self._read_or_none(node) is not None,
                    "value": self._read_or_none(node),
                    "writable": os.access(node, os.W_OK),
                }
            return report

        try:
            return {"ok": True, "data": await asyncio.to_thread(_collect)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": self._error_message(exc)}

    # -------------------------------------------------------------- 启动恢复
    @staticmethod
    def _report_entry(key: str, **params: Any) -> Dict[str, Any]:
        """把一条恢复提示表示成**结构化**条目（键 + 原始参数），而不是成品文案。

        为什么非要结构化（v0.6.14 修的真机缺陷）：启动恢复发生在 ``_main()``，
        它**早于**前端把界面语言推给后端（``set_locale``）。若在这里就
        ``self._t(...)`` 渲染，用的是默认 ``"en"`` —— 中文界面下
        「设置 → Decky → 启动时自动恢复」那行提示恒为英文，而且**重开插件也不会变**
        （报告只在启动时生成一次，之后直接复用 ``_restore_report``）。

        参数一律传**原始值**（模式名 ``balanced``，而不是本地化过的
        ``"均衡"`` / ``"Balanced"``），由前端按当前语言查表渲染。
        """
        return {"key": key, "params": dict(params)}

    def _restore_fan_report(self, report: List[Dict[str, Any]]) -> None:
        """按保存的设置恢复风扇；不该接管时把控制权交还 EC。

        这里有个**安全兜底**：即使保存的模式是「自动」，也要检查 EC 当前是否被
        留在手动 PWM。上一次插件崩溃 / 被 kill -9 时 ``_unload`` 不会执行，
        EC 就会停在手动模式——那种状态下风扇不再跟随温度、85°C 保护也失效。
        """
        if self._fan_unavailable_reason():
            return

        settings = self._load_settings()
        saved = settings.get("fan_mode", self.FAN_MODE_AUTO)
        if saved not in self.FAN_MODES:
            saved = self.FAN_MODE_AUTO

        handover = saved == self.FAN_MODE_AUTO or not settings.get("auto_restore", True)
        if handover:
            # 这里**不要**先看一眼 _fan_is_auto() 就 return。
            #
            # 那是"信任一次读回"的做法，而 sysfs 读回可能滞后于 EC 寄存器：
            # 文件说"自动"、EC 其实还在手动，早退就等于把风扇永久留在
            # 不跟温度走、也没有 85°C 保护的手动 PWM 上。
            # 一律交给 _fan_handover_to_ec()：它写出期望值再读回确认，
            # 多写一次"自动"是幂等的，代价远小于漏写。
            #
            # 提示措辞只分两种，**不依据写入前的读回值**：
            # 那个值本来就是不可信的（"读回滞后"正是我们要防的情形，
            # 它会显示成自动），拿它判断"是否残留手动"必然误判。
            # 所以：交还失败就说失败；成功则统一说"已确认处于自动控制"。
            # 分不清是真本来就自动、还是刚纠正了一个滞后读数，但两种情况
            # 的结论一样——现在确实处于 EC 自动控制，这不影响用户判断。
            if self._fan_handover_to_ec():
                report.append(self._report_entry("restore.fanEcAuto"))
            else:
                report.append(self._report_entry("restore.fanHandoverUnconfirmed"))
            return

        try:
            if self._fan_apply_manual(saved):
                # 参数传**模式名**（前端查 fan.mode.* 翻译），不传本地化标签。
                report.append(self._report_entry("restore.fanModeRestored", mode=saved))
            else:
                # 注意这里是**已经写入了 pwm1_enable=1** 之后才失败的
                # （_fan_apply_manual 先写 enable、再 _wait_for 确认）。
                # 也就是 EC 已经在手动模式、而控制线程没起来——风扇挂在
                # 一个不跟温度走、也没有 85°C 保护的手动 PWM 上。
                # 所以必须兜底，不能只写一行日志。
                report.append(
                    self._report_entry("restore.fanManualUnconfirmed", mode=saved)
                )
                self._fan_fail_safe()
        except Exception as exc:  # noqa: BLE001
            report.append(
                self._report_entry(
                    "err.restoreFailed",
                    area_key="common.fanControl",
                    detail=self._error_message(exc, self._t("common.fanControl")),
                )
            )
            self._fan_fail_safe()

    def _restore_sync(self) -> None:
        report: List[Dict[str, Any]] = []

        # 风扇放在电池判断之前，且不受 auto_restore 影响（见方法文档里的安全兜底）。
        try:
            self._restore_fan_report(report)
        except Exception as exc:  # noqa: BLE001
            report.append(
                self._report_entry(
                    "err.restoreFailed",
                    area_key="common.fanControl",
                    detail=self._error_message(exc, self._t("common.fanControl")),
                )
            )

        settings = self._load_settings()
        if not settings.get("auto_restore", True):
            report.append(self._report_entry("restore.autoOff"))
            self._restore_report = report
            return

        try:
            path = self._battery()
        except Exception as exc:  # noqa: BLE001
            # 注意 `exc` 是异常对象，不是字符串 —— 结构化条目里统一转成文本，
            # 免得前端拿到没法渲染的对象（JSON 化时也会失败）。
            report.append(
                self._report_entry("restore.batteryMissing", exc=str(exc))
            )
            self._restore_report = report
            return

        saved_threshold = settings.get("threshold")
        if isinstance(saved_threshold, int) and (
            self.MIN_THRESHOLD <= saved_threshold <= self.MAX_THRESHOLD
        ):
            node = os.path.join(path, self.THRESHOLD_FILE)
            if not self._node_exists(node):
                report.append(self._report_entry("restore.noThresholdNode"))
            else:
                try:
                    self._write_node(node, str(saved_threshold))
                    if self._wait_for(
                        lambda: self._parse_int(self._read_or_none(node)) == saved_threshold
                    ):
                        report.append(
                            self._report_entry(
                                "restore.thresholdRestored", value=saved_threshold
                            )
                        )
                    else:
                        report.append(
                            self._report_entry(
                                "restore.thresholdUnconfirmed", value=saved_threshold
                            )
                        )
                except Exception as exc:  # noqa: BLE001
                    report.append(
                        self._report_entry(
                            "err.restoreFailed",
                            area_key="common.chargeLimit",
                            detail=self._error_message(exc),
                        )
                    )

        saved_mode = settings.get("mode")
        if isinstance(saved_mode, str) and saved_mode in self.SELECTABLE_MODES:
            node = os.path.join(path, self.BEHAVIOUR_FILE)
            if not self._node_exists(node):
                report.append(self._report_entry("restore.noBehaviourNode"))
            else:
                try:
                    supported = self._parse_behaviours(self._read(node))["supported"]
                    if saved_mode not in supported:
                        report.append(
                            self._report_entry(
                                "restore.modeUnsupported", mode=saved_mode
                            )
                        )
                    else:
                        self._write_node(node, saved_mode)
                        if self._wait_for(
                            lambda: self._parse_behaviours(self._read_or_none(node))["active"]
                            == saved_mode
                        ):
                            report.append(
                                self._report_entry(
                                    "restore.modeRestored", mode=saved_mode
                                )
                            )
                        else:
                            report.append(
                                self._report_entry(
                                    "restore.modeUnconfirmed", mode=saved_mode
                                )
                            )
                except Exception as exc:  # noqa: BLE001
                    report.append(
                        self._report_entry(
                            "err.restoreFailed",
                            area_key="common.chargeMode",
                            detail=self._error_message(exc),
                        )
                    )

        if not report:
            report.append(self._report_entry("restore.nothing"))
        self._restore_report = report

    # ------------------------------------------------------------ Decky 生命周期
    async def _main(self) -> None:
        self._restore_report = []
        try:
            path = await asyncio.to_thread(self._find_battery)
        except Exception as exc:  # noqa: BLE001
            decky.logger.error(f"F1Pro EC Control: 电池探测异常: {exc}")
            path = None

        if not path:
            self.battery_path = None
            self._restore_report = [self._report_entry("err.batteryProbe")]
            decky.logger.warning("F1Pro EC Control: 未找到支持充电控制的电池节点")
            return

        self.battery_path = path
        await asyncio.to_thread(self._restore_sync)
        decky.logger.info(
            f"F1Pro EC Control 已启动，uid={self._current_uid()}（写 sysfs 需要 0），"
            f"电池节点：{path}；恢复结果：{self._restore_report}"
        )

        fan_mode = self._load_settings().get("fan_mode", self.FAN_MODE_AUTO)
        if self._fan_unavailable_reason() is None:
            decky.logger.info(
                f"F1Pro Fan: 温度节点={self.fan_temp_path}，"
                f"风扇节点={self.fan_controller_path}，保存的模式={fan_mode}"
            )
        else:
            decky.logger.info(f"F1Pro Fan: 风扇控制不可用（{self._fan_unavailable_reason()}）")

    async def _unload(self) -> None:
        # 插件退出时必须把风扇控制权交还 EC，否则卸载后风扇会一直停在
        # 最后一次写入的手动 PWM 上，既不跟随温度也没有安全保护。
        #
        # **必须与模式转换串行化**：卸载若插进某个转换（比如用户刚点了切手动）
        # 的中途，就可能"卸载刚交还 EC、那个转换随后又把控制权抢回手动" ——
        # 卸载已经返回，风扇却仍在手动 PWM 上（离线探针 .mut/repro_fan_unload.py
        # 复现：卸载返回后 EC 的 mode_register 仍是 1）。
        try:

            def _release() -> None:
                if self._fan_unavailable_reason():
                    return
                # **在取走控制权那一刻就把卸载标志置位**（见 _fan_unloading）：
                # 之后无论转换锁有没有拿到，控制步骤都不会再把 EC 抢回手动、
                # 在途的 RPC 也不会再报成功。这一步必须在这里做，不能挪到
                # 取锁成功之后 —— 取锁超时的那条路径恰恰是最需要它的。
                self._fan_unloading = True
                # 转换锁**带超时**取：控制线程可能正卡在 sysfs 读写里、
                # 长时间占着锁，此时不能无限期阻塞卸载流程。取不到也照样
                # 往下走 —— 真正的防线是"卸载标志 + 世代号"：
                # 卸载标志让后续任何转换步骤整体作废，世代号作废在途的那一步；
                # 锁在这里的作用是"尽量不与转换交错"，不是唯一保障。
                acquired = self._fan_transition_lock.acquire(
                    timeout=self.FAN_STOP_TIMEOUT
                )
                if not acquired:
                    decky.logger.warning(
                        "F1Pro Fan: 卸载时未能在超时内取得转换锁"
                        "（控制线程可能卡在 I/O 里），仍尝试交还 EC"
                    )
                try:
                    if self._fan_handover_to_ec():
                        decky.logger.info("F1Pro Fan: 已恢复 EC 自动控制")
                    else:
                        decky.logger.warning("F1Pro Fan: 恢复 EC 自动控制后未确认生效")
                finally:
                    if acquired:
                        self._fan_transition_lock.release()

            await asyncio.to_thread(_release)
        except Exception as exc:  # noqa: BLE001
            decky.logger.error(f"F1Pro Fan: 卸载时恢复 EC 自动失败：{exc}")

        decky.logger.info("F1Pro EC Control 已卸载")

    async def _migration(self, previous_version: Optional[str] = None) -> None:
        decky.logger.info(f"F1Pro EC Control 迁移自 {previous_version}")
