"""OneXFly F1 Pro (HX 370) 充电控制后端 —— Decky Loader 插件。

设计约束（与用户需求一致）：
  * 只操作标准 Linux ``power_supply`` 接口，不依赖 HHD / Anatase / Loadout；
  * 不触碰 TDP、风扇、CPU 调度等其它 hwmon 节点，可与 SimpleDeckyTDP 共存；
  * 每次写入后都重新读取内核节点确认是否真正生效。

内核接口说明（Documentation/ABI/testing/sysfs-class-power）::

    auto:                 正常充电，并尊重 charge_control_end_threshold
    inhibit-charge:       接电也不充电（始终旁路）
    inhibit-charge-awake: 仅在设备唤醒时暂停充电（开机旁路，睡眠后恢复充电）
    force-discharge:      接电状态下强制放电

``inhibit-charge-awake`` 是为 OneXPlayer / One-Netbook 系列设备加入 mainline 的行为，
正是 F1 Pro 上「开机旁路」所需要的语义。
"""

import asyncio
import glob
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import decky


class Plugin:
    # ---------------------------------------------------------------- 常量
    SETTINGS_FILE = "f1pro-battery-control.json"

    BEHAVIOUR_FILE = "charge_behaviour"
    THRESHOLD_FILE = "charge_control_end_threshold"

    MODE_AUTO = "auto"
    MODE_ALWAYS = "inhibit-charge"
    MODE_AWAKE = "inhibit-charge-awake"
    MODE_FORCE_DISCHARGE = "force-discharge"

    #: 用户可在界面上选择的模式
    SELECTABLE_MODES = (MODE_AUTO, MODE_AWAKE, MODE_ALWAYS)

    MODE_LABELS = {
        MODE_AUTO: "正常充电",
        MODE_AWAKE: "开机旁路",
        MODE_ALWAYS: "始终旁路",
        MODE_FORCE_DISCHARGE: "强制放电",
    }

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

    def __init__(self) -> None:
        self.battery_path: Optional[str] = None
        self._settings_path: Optional[str] = None
        self._restore_report: List[str] = []

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
                raise RuntimeError(
                    "未找到支持充电控制的电池节点"
                    "（需要 /sys/class/power_supply/BAT*/charge_behaviour）"
                )
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
        state["mode_labels"] = dict(self.MODE_LABELS)
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
    def _error_message(self, exc: Exception) -> str:
        if isinstance(exc, PermissionError):
            return (
                '没有权限写入电池控制节点；请确认 plugin.json 的 flags 包含 "root"'
                "（不是 _root），且插件由 Decky 加载。"
                f"当前 uid={self._current_uid()}，写 sysfs 需要 uid 0。"
            )
        if isinstance(exc, OSError):
            return f"内核拒绝了写入：{exc}"
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
            decky.logger.error(f"F1Pro Battery: 读取状态失败: {exc}")
            return {"ok": False, "error": self._error_message(exc)}

    async def set_charge_mode(self, mode: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self._set_charge_mode_sync, mode)

    def _set_charge_mode_sync(self, mode: str) -> Dict[str, Any]:
        allowed = set(self.SELECTABLE_MODES) | {self.MODE_FORCE_DISCHARGE}
        if mode not in allowed:
            return {"ok": False, "error": f"无效的充电模式：{mode}"}
        try:
            path = self._battery()
            node = os.path.join(path, self.BEHAVIOUR_FILE)
            if not self._node_exists(node):
                raise RuntimeError("当前内核没有提供 charge_behaviour 节点")

            supported = self._parse_behaviours(self._read(node))["supported"]
            if mode not in supported:
                label = self.MODE_LABELS.get(mode, mode)
                available = " ".join(supported) if supported else "无"
                raise RuntimeError(f"当前内核不支持「{label}」；该节点可用值：{available}")

            self._write_node(node, mode)

            def _applied() -> bool:
                return self._parse_behaviours(self._read_or_none(node))["active"] == mode

            if not self._wait_for(_applied):
                actual = self._parse_behaviours(self._read_or_none(node))["active"]
                raise RuntimeError(
                    f"写入后状态未生效：期望 {mode}，内核实际返回 {actual}"
                )

            self._update_setting("mode", mode)
            return {"ok": True, "data": self._read_state()}
        except Exception as exc:  # noqa: BLE001
            decky.logger.error(f"F1Pro Battery: 切换模式失败: {exc}")
            return {"ok": False, "error": self._error_message(exc)}

    async def set_charge_threshold(self, value: int) -> Dict[str, Any]:
        try:
            value = int(value)
        except (TypeError, ValueError):
            return {"ok": False, "error": "充电上限必须是整数"}
        return await asyncio.to_thread(self._set_threshold_sync, value)

    def _set_threshold_sync(self, value: int) -> Dict[str, Any]:
        if value != self.THRESHOLD_DISABLED and not (
            self.MIN_THRESHOLD <= value <= self.MAX_THRESHOLD
        ):
            return {
                "ok": False,
                "error": f"充电上限必须在 {self.MIN_THRESHOLD}%–{self.MAX_THRESHOLD}% 之间，"
                f"或使用 {self.THRESHOLD_DISABLED}% 解除限制",
            }
        try:
            path = self._battery()
            node = os.path.join(path, self.THRESHOLD_FILE)
            if not self._node_exists(node):
                raise RuntimeError("当前内核没有提供 charge_control_end_threshold 节点")

            self._write_node(node, str(value))

            def _applied() -> bool:
                return self._parse_int(self._read_or_none(node)) == value

            if not self._wait_for(_applied):
                actual = self._parse_int(self._read_or_none(node))
                if actual is None:
                    raise RuntimeError(
                        f"已写入 {value}%，但此节点不可读，无法确认是否生效"
                        "（若设备行为正确可继续使用）"
                    )
                raise RuntimeError(f"内核将上限调整为 {actual}%，没有接受 {value}%")

            # 100% 表示「不限制」，不需要在启动时恢复。
            self._update_setting(
                "threshold", None if value == self.THRESHOLD_DISABLED else value
            )
            return {"ok": True, "data": self._read_state()}
        except Exception as exc:  # noqa: BLE001
            decky.logger.error(f"F1Pro Battery: 设置上限失败: {exc}")
            return {"ok": False, "error": self._error_message(exc)}

    async def set_auto_restore(self, enabled: bool) -> Dict[str, Any]:
        def _apply() -> Dict[str, Any]:
            self._update_setting("auto_restore", bool(enabled))
            return {"ok": True, "data": self._read_state()}

        return await asyncio.to_thread(_apply)

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
    def _restore_sync(self) -> None:
        report: List[str] = []
        settings = self._load_settings()
        if not settings.get("auto_restore", True):
            self._restore_report = ["启动自动恢复已关闭"]
            return

        try:
            path = self._battery()
        except Exception as exc:  # noqa: BLE001
            self._restore_report = [f"未找到电池节点：{exc}"]
            return

        saved_threshold = settings.get("threshold")
        if isinstance(saved_threshold, int) and (
            self.MIN_THRESHOLD <= saved_threshold <= self.MAX_THRESHOLD
        ):
            node = os.path.join(path, self.THRESHOLD_FILE)
            if not self._node_exists(node):
                report.append("内核无 charge_control_end_threshold，跳过上限恢复")
            else:
                try:
                    self._write_node(node, str(saved_threshold))
                    if self._wait_for(
                        lambda: self._parse_int(self._read_or_none(node)) == saved_threshold
                    ):
                        report.append(f"充电上限已恢复为 {saved_threshold}%")
                    else:
                        report.append(f"充电上限恢复后未确认生效（目标 {saved_threshold}%）")
                except Exception as exc:  # noqa: BLE001
                    report.append(f"充电上限恢复失败：{self._error_message(exc)}")

        saved_mode = settings.get("mode")
        if isinstance(saved_mode, str) and saved_mode in self.SELECTABLE_MODES:
            node = os.path.join(path, self.BEHAVIOUR_FILE)
            label = self.MODE_LABELS.get(saved_mode, saved_mode)
            if not self._node_exists(node):
                report.append("内核无 charge_behaviour，跳过模式恢复")
            else:
                try:
                    supported = self._parse_behaviours(self._read(node))["supported"]
                    if saved_mode not in supported:
                        report.append(f"内核不支持「{label}」，模式未恢复")
                    else:
                        self._write_node(node, saved_mode)
                        if self._wait_for(
                            lambda: self._parse_behaviours(self._read_or_none(node))["active"]
                            == saved_mode
                        ):
                            report.append(f"充电模式已恢复为「{label}」")
                        else:
                            report.append(f"充电模式恢复后未确认生效（目标「{label}」）")
                except Exception as exc:  # noqa: BLE001
                    report.append(f"充电模式恢复失败：{self._error_message(exc)}")

        if not report:
            report.append("没有需要恢复的设置")
        self._restore_report = report

    # ------------------------------------------------------------ Decky 生命周期
    async def _main(self) -> None:
        self._restore_report = []
        try:
            path = await asyncio.to_thread(self._find_battery)
        except Exception as exc:  # noqa: BLE001
            decky.logger.error(f"F1Pro Battery: 电池探测异常: {exc}")
            path = None

        if not path:
            self.battery_path = None
            self._restore_report = ["未找到支持充电控制的电池节点"]
            decky.logger.warning("F1Pro Battery: 未找到支持充电控制的电池节点")
            return

        self.battery_path = path
        await asyncio.to_thread(self._restore_sync)
        decky.logger.info(
            f"F1 Pro Battery Control 已启动，uid={self._current_uid()}（写 sysfs 需要 0），"
            f"电池节点：{path}；恢复结果：{self._restore_report}"
        )

    async def _unload(self) -> None:
        decky.logger.info("F1 Pro Battery Control 已卸载")

    async def _migration(self, previous_version: Optional[str] = None) -> None:
        decky.logger.info(f"F1 Pro Battery Control 迁移自 {previous_version}")
