"""F1Pro EC Control 后端的离线测试。

在开发机上用一棵假的 ``/sys/class/power_supply`` 目录树替换真实 sysfs，
并模拟内核的两个关键语义：

  1. ``charge_behaviour`` 用方括号标记当前生效值，写入不支持的值会返回 EINVAL；
  2. 某些节点可能"写入被静默忽略"或"被内核夹到别的值"——
     用来验证插件「写入后必须重新读取确认」这条核心逻辑真的会报错。

运行方式（不需要 pytest）::

    python tests/test_backend.py
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
from typing import Any, Dict, List
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))

# --------------------------------------------------------------------------
# 必须在 import main 之前伪造 decky 模块
# --------------------------------------------------------------------------
SETTINGS_DIR = tempfile.mkdtemp(prefix="f1pro-settings-")

decky_stub = types.ModuleType("decky")
decky_stub.DECKY_PLUGIN_SETTINGS_DIR = SETTINGS_DIR
LOGS = []


class _Logger:
    def _emit(self, level, message):
        LOGS.append((level, str(message)))

    def info(self, message):
        self._emit("info", message)

    def warning(self, message):
        self._emit("warning", message)

    def error(self, message):
        self._emit("error", message)

    def debug(self, message):
        self._emit("debug", message)


decky_stub.logger = _Logger()
sys.modules["decky"] = decky_stub

import main  # noqa: E402
from main import Plugin  # noqa: E402

SETTINGS_FILE = Path(SETTINGS_DIR) / Plugin.SETTINGS_FILE

# --------------------------------------------------------------------------
# 假内核
# --------------------------------------------------------------------------


class FakeKernel:
    """模拟内核节点在收到写入之后的行为。"""

    def __init__(self, battery_dir, behaviours, active, threshold):
        self.dir = battery_dir
        self.behaviours = behaviours
        self.active = active
        self.threshold = threshold
        self.silently_ignore = set()  # 文件名 -> 写入被静默丢弃
        self.reject_with_einval = set()  # 文件名 -> 写入抛 EINVAL
        self.clamp = None  # 形如 lambda v: 90

    def render_behaviour(self):
        tokens = [f"[{b}]" if b == self.active else b for b in self.behaviours]
        (self.dir / "charge_behaviour").write_text(" ".join(tokens) + "\n", encoding="utf-8")

    def write(self, path, value):
        name = os.path.basename(path)
        if name in self.reject_with_einval:
            raise OSError(22, "Invalid argument")
        if name in self.silently_ignore:
            return
        if name == "charge_behaviour":
            if value not in self.behaviours:
                raise OSError(22, "Invalid argument")
            self.active = value
            self.render_behaviour()
        elif name == "charge_control_end_threshold":
            value = int(value)
            if self.clamp is not None:
                value = self.clamp(value)
            self.threshold = value
            (self.dir / name).write_text(f"{value}\n", encoding="utf-8")
        else:
            (self.dir / name).write_text(f"{value}\n", encoding="utf-8")


class FakeFanEC:
    """模拟 oxpec 的 oxp_ec hwmon 行为。

    刻意复刻源码里的两个关键语义（drivers/platform/x86/oxpec.c）：
      1. 写 pwm1_enable：1 = 手动、2 = 自动、0 = 切手动并直接满速 255，
         其它值 = EINVAL；
      2. **读回 pwm1_enable：自动 -> 2；手动 -> 满速时 0，否则 1**
         （"Check for auto and return 2" / "Return 0 if at full fan speed"）。
         这是最容易踩的坑：只看 ``== 1`` 会把满速误判成失败。
    """

    def __init__(self, ec_dir):
        self.dir = ec_dir
        # EC 侧寄存器（与 sysfs 读回值不同！）：0x00 = 自动，0x01 = 手动
        self.mode_register = 0
        self.pwm = 100
        self.rpm = 3200
        self.ignored_writes = set()  # 文件名 -> 写入被静默丢弃
        # 文件名 -> 写入**生效**（EC 寄存器真的变了）但读回文件不刷新。
        # 用来构造"已经写入手动 PWM、但确认失败"这种最危险的场景：
        # 真实硬件上 EC 可能接受了写入，而 sysfs 的读回因为时序/驱动 bug
        # 暂时还是旧值。此时若不做兜底，控制线程不会启动，风扇就停在
        # 一个不跟温度走、也没有安全保护的手动 PWM 上。
        self.stale_reads = set()

    # EC 寄存器 -> sysfs 读回值
    def read(self, name):
        if name == "pwm1_enable":
            if self.mode_register == 0:
                return "2"
            return "0" if self.pwm == 255 else "1"
        if name == "pwm1":
            return str(self.pwm)
        if name == "fan1_input":
            return str(self.rpm)
        return None

    def render(self, names=("fan1_input", "pwm1", "pwm1_enable")):
        """把 EC 寄存器状态刷进 sysfs 文件。"""
        for name in names:
            path = self.dir / name
            if path.parent == self.dir and path.exists():
                if name in self.stale_reads:
                    continue  # 保持旧值，模拟读回滞后
                path.write_text(f"{self.read(name)}\n", encoding="utf-8")

    def write(self, path, value):
        name = os.path.basename(path)
        # 写入是"真实的动作"：无论成功、被忽略还是抛错，都说明读回文件
        # 不再可信（EC 寄存器与文件可能不一致）。所以在写入路径上清掉
        # stale 标记，避免它泄露到后续步骤，让别的断言莫名其妙地空转。
        self.stale_reads.discard("pwm1_enable")
        if name in self.ignored_writes:
            return
        if name == "pwm1_enable":
            if value == "1":
                self.mode_register = 1
            elif value == "2":
                self.mode_register = 0
            elif value == "0":
                self.mode_register = 1
                self.pwm = 255
            else:
                raise OSError(22, "Invalid argument")
        elif name == "pwm1":
            if self.mode_register != 1:
                return  # EC 非手动模式时，写 pwm 不生效
            self.pwm = int(value)
        else:
            raise OSError(22, "Invalid argument")
        self.render()


CURRENT = {
    "kernel": None,
    "candidates": [],
    "ac": [],
    "root": None,
    "hwmon": [],
    "fan": None,
}


def install_fake_sysfs(root: Path, kernel: FakeKernel, candidates, ac=(), hwmon=(), fan=None):
    """把 Plugin 的电池/适配器/风扇发现与写入重定向到假 sysfs。"""
    CURRENT["kernel"] = kernel
    CURRENT["root"] = root
    CURRENT["candidates"] = [str(p) for p in candidates]
    CURRENT["ac"] = [str(p) for p in ac]
    CURRENT["hwmon"] = [str(p) for p in hwmon]
    CURRENT["fan"] = fan
    Plugin._candidates = staticmethod(lambda: list(CURRENT["candidates"]))
    Plugin._ac_candidates = staticmethod(lambda: list(CURRENT["ac"]))
    Plugin._hwmon_paths = staticmethod(lambda: list(CURRENT["hwmon"]))

    def _write(path, value):
        if CURRENT["fan"] is not None and Path(path).parent == CURRENT["fan"].dir:
            return CURRENT["fan"].write(Path(path), value)
        return CURRENT["kernel"].write(Path(path), value)

    Plugin._write = staticmethod(_write)


def make_ac(root: Path, name="ACAD", online=True, kind="Mains"):
    """造一个适配器 / USB-C 供电节点。"""
    ac = root / name
    ac.mkdir(parents=True, exist_ok=True)
    (ac / "type").write_text(f"{kind}\n", encoding="utf-8")
    (ac / "online").write_text(f"{1 if online else 0}\n", encoding="utf-8")
    return ac


def make_battery(root: Path, name="BAT0", behaviours=None, active="auto", threshold=95):
    battery = root / name
    battery.mkdir(parents=True, exist_ok=True)
    (battery / "type").write_text("Battery\n", encoding="utf-8")
    (battery / "scope").write_text("Device\n", encoding="utf-8")
    (battery / "capacity").write_text("72\n", encoding="utf-8")
    (battery / "status").write_text("Charging\n", encoding="utf-8")
    (battery / "power_now").write_text("12500000\n", encoding="utf-8")
    (battery / "charge_control_end_threshold").write_text(f"{threshold}\n", encoding="utf-8")
    kernel = FakeKernel(
        battery,
        behaviours=behaviours or ["auto", "inhibit-charge", "inhibit-charge-awake"],
        active=active,
        threshold=threshold,
    )
    kernel.render_behaviour()
    return battery, kernel


def make_fan(
    root: Path,
    temp_c=55.0,
    temp_name="k10temp",
    ctrl_name="oxp_ec",
    hwmon_ids=("hwmon3", "hwmon7"),
    missing=(),
    pwm=100,
):
    """造一组假 hwmon：CPU 温度传感器 + EC 风扇控制器。

    默认把温度设备放在 ``hwmon3``、风扇设备放在 ``hwmon7``，编号与文件名故意
    错开，用来证明插件是按 ``name`` 而不是按编号找设备。
    """
    temp_dir = root / hwmon_ids[0]
    ctrl_dir = root / hwmon_ids[1]
    for path in (temp_dir, ctrl_dir):
        path.mkdir(parents=True, exist_ok=True)

    (temp_dir / "name").write_text(f"{temp_name}\n", encoding="utf-8")
    (ctrl_dir / "name").write_text(f"{ctrl_name}\n", encoding="utf-8")

    if "temp1_input" not in missing:
        (temp_dir / "temp1_input").write_text(f"{int(temp_c * 1000)}\n", encoding="utf-8")

    ec = FakeFanEC(ctrl_dir)
    ec.pwm = pwm
    if "fan1_input" not in missing:
        (ctrl_dir / "fan1_input").write_text(f"{ec.rpm}\n", encoding="utf-8")
    if "pwm1" not in missing:
        (ctrl_dir / "pwm1").write_text(f"{pwm}\n", encoding="utf-8")
    if "pwm1_enable" not in missing:
        (ctrl_dir / "pwm1_enable").write_text("2\n", encoding="utf-8")
    ec.render()
    return [temp_dir, ctrl_dir], ec, temp_dir


def set_temp(temp_dir: Path, celsius: float):
    (temp_dir / "temp1_input").write_text(f"{int(celsius * 1000)}\n", encoding="utf-8")


def reset_settings():
    if SETTINGS_FILE.exists():
        SETTINGS_FILE.unlink()


def isolated_settings(plugin, tmp: Path) -> Path:
    """给这个夹具一个**独占的设置目录**，返回它的设置文件路径。

    为什么要独占（v0.6.11 修的测试夹具缺陷）：`Plugin` 的 ``_settings_path`` 是
    **实例属性**，默认全都指向 import 时那个全局 ``DECKY_PLUGIN_SETTINGS_DIR``。
    于是同一个测试函数里先后建出来的好几个夹具、以及它们**各自还在后台跑的控制
    线程**，会读写**同一个** JSON 文件。后果有两个：

    - **跨场景污染**：前一个场景留下的模式 / 曲线会被后一个场景读到；
    - **Windows 文件锁放大**：只要任何一个后台线程恰好正在写这个文件，
      ``reset_settings()`` 的 unlink 就会抛 `PermissionError: [WinError 32]`。

    这个失败是**竞态**（同一个测试有时过、有时不过），所以不能靠"重跑一次"糊过去。
    给每个场景独立目录后，场景之间互不影响；剩下的"本场景自己的线程必须先停"
    由 `teardown_plugins()` 负责。
    """
    settings_dir = tmp / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    plugin._settings_path = str(settings_dir / plugin.SETTINGS_FILE)
    return Path(plugin._settings_path)


def teardown_plugins(*plugins, timeout: float = 5.0) -> bool:
    """停掉这些夹具的控制线程，**并等到它们真正退出**。返回是否全部停干净。

    为什么不能只调 ``_stop_fan_controller()``：那个方法在 join 超时时会
    **保留引用**并返回 False（这是实现里正确的行为 —— 假装停掉了会导致双线程
    并写同一个 EC）。但测试夹具的诉求不同：**必须确认线程没了**，否则
    - 它可能继续写设置文件 → 后面 ``reset_settings()`` 撞 `WinError 32`；
    - 它可能继续写假 EC → 污染下一个场景的断言。

    所以这里在 ``_stop_fan_controller()`` 之上再加一层"**再等一会儿**"：
    控制线程的循环步进很快，正常情况下给足时间一定能退出。
    """
    all_clean = True
    for plugin in plugins:
        if plugin is None:
            continue
        # 先把停止标志置上：控制线程在等转换锁时会盯着它（见 main.py
        # `_fan_acquire_transition_or_stop`），不置标志的话 join 会白等一个轮询周期。
        plugin._fan_stop_event.set()
        if not plugin._stop_fan_controller():
            thread = plugin._fan_thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=timeout)
            if thread is not None and thread.is_alive():
                all_clean = False
                print(
                    f"      [夹具警告] 控制线程 {thread.name} 在 "
                    f"{timeout}s 内仍未退出，无法安全清理"
                )
                continue
            plugin._fan_thread = None
    return all_clean


# **残留线程记账**：每个测试函数跑完后由 `main()` 检查。
# 为什么需要它：上面那个 `WinError 32` 是**竞态**——忘了收尾的夹具有时能侥幸通过
# （线程那一刻恰好没在写文件），于是"忘了 teardown"这种错误会被随机掩盖。
# 有了这条确定性检查，"离开场景时还有活着的控制线程"会**每次**都红，
# 而不是看运气撞文件锁。
LEAKED_THREADS = []


def assert_no_leaked_threads(test_name: str) -> None:
    """检查本场景是否留下了仍在运行的控制线程。"""
    leaked = [
        t
        for t in threading.enumerate()
        if t.is_alive() and t is not threading.current_thread() and t.name.startswith("F1Pro")
    ]
    if leaked:
        names = ", ".join(sorted({t.name for t in leaked}))
        LEAKED_THREADS.append((test_name, names))
        check(
            f"场景结束时不得留下仍在运行的控制线程（{test_name}）",
            False,
            f"仍在运行：{names}",
        )
    else:
        check(
            f"场景结束时不得留下仍在运行的控制线程（{test_name}）",
            True,
        )


def read_behaviour(battery: Path):
    return (battery / "charge_behaviour").read_text(encoding="utf-8").strip()


# --------------------------------------------------------------------------
# 迷你测试框架
# --------------------------------------------------------------------------
PASSED = []
FAILED = []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} :: {detail}")


def section(title):
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------------
# 测试
# --------------------------------------------------------------------------


def test_status_and_modes():
    section("1. 状态读取与模式切换")
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-1-"))
    battery, kernel = make_battery(tmp)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery])

    status = asyncio.run(plugin.get_status())
    check("get_status 返回 ok", status["ok"] is True, str(status))
    data = status["data"]
    check("识别到 BAT0", data["battery"] == "BAT0", str(data.get("battery")))
    check("解析当前模式 auto", data["charge_behaviour"]["active"] == "auto")
    check(
        "解析支持列表",
        data["charge_behaviour"]["supported"]
        == ["auto", "inhibit-charge", "inhibit-charge-awake"],
        str(data["charge_behaviour"]["supported"]),
    )
    check("supports_awake_bypass 为真", data["supports_awake_bypass"] is True)
    check("读取到容量 72", data["capacity"] == "72")
    check("功率换算为 12.5W", data["power_watts"] == 12.5, str(data.get("power_watts")))
    check("读取到上限 95", data["threshold"] == 95)

    result = asyncio.run(plugin.set_charge_mode("inhibit-charge-awake"))
    check("切换到开机旁路成功", result["ok"] is True, str(result))
    check("内核节点已变为开机旁路", "[inhibit-charge-awake]" in read_behaviour(battery))
    check("返回数据中的 active 正确", result["data"]["charge_behaviour"]["active"] == "inhibit-charge-awake")

    result = asyncio.run(plugin.set_charge_mode("inhibit-charge"))
    check("切换到始终旁路成功", result["ok"] is True, str(result))
    # 内核按枚举顺序输出，因此始终旁路位于列表中间。
    check("内核节点已变为始终旁路", "[inhibit-charge]" in read_behaviour(battery), read_behaviour(battery))

    result = asyncio.run(plugin.set_charge_mode("force-discharge"))
    check("内核不支持 force-discharge 时报错", result["ok"] is False, str(result))
    check("报错文案指明了不支持", "不支持" in result.get("error", ""), result.get("error", ""))

    result = asyncio.run(plugin.set_charge_mode("随便写的"))
    check("非法模式被拒绝", result["ok"] is False and "无效" in result["error"], str(result))

    shutil.rmtree(tmp, ignore_errors=True)


def test_write_verification():
    section("2. 写入后必须重新读取确认（核心逻辑）")
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-2-"))
    battery, kernel = make_battery(tmp)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery])

    # 内核静默忽略写入：插件必须报错，而不是假装成功。
    kernel.silently_ignore.add("charge_control_end_threshold")
    result = asyncio.run(plugin.set_charge_threshold(80))
    check("写入被忽略时返回失败", result["ok"] is False, str(result))
    check("报错指出内核没有接受", "没有接受" in result.get("error", ""), result.get("error", ""))
    check("失败时未写入设置文件", not SETTINGS_FILE.exists() or "threshold" not in json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    kernel.silently_ignore.clear()

    # 内核把值夹到别处：也必须报错并说明实际值。
    kernel.clamp = lambda value: 90
    result = asyncio.run(plugin.set_charge_threshold(95))
    check("被内核夹到 90% 时返回失败", result["ok"] is False, str(result))
    check("报错带上实际值", "90" in result.get("error", ""), result.get("error", ""))
    kernel.clamp = None

    # 内核直接 EINVAL。
    kernel.reject_with_einval.add("charge_behaviour")
    result = asyncio.run(plugin.set_charge_mode("inhibit-charge"))
    check("EINVAL 时返回失败", result["ok"] is False, str(result))
    check("EINVAL 报错被包装", "内核拒绝了写入" in result.get("error", ""), result.get("error", ""))
    kernel.reject_with_einval.clear()

    # 正常写入应当成功并落盘。
    result = asyncio.run(plugin.set_charge_threshold(80))
    check("正常写入成功", result["ok"] is True, str(result))
    check("内核值已更新", (battery / "charge_control_end_threshold").read_text().strip() == "80")
    saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    check("上限已持久化", saved.get("threshold") == 80, str(saved))

    shutil.rmtree(tmp, ignore_errors=True)


def test_threshold_bounds():
    section("3. 上限取值范围与解除限制")
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-3-"))
    battery, kernel = make_battery(tmp)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery])

    result = asyncio.run(plugin.set_charge_threshold(40))
    check("低于 50% 被拒绝", result["ok"] is False, str(result))

    result = asyncio.run(plugin.set_charge_threshold("80"))
    check("字符串数字被接受", result["ok"] is True, str(result))

    result = asyncio.run(plugin.set_charge_threshold("不是数字"))
    check("非数字被拒绝", result["ok"] is False, str(result))

    result = asyncio.run(plugin.set_charge_threshold(100))
    check("写入 100 即解除限制并成功", result["ok"] is True, str(result))
    check("内核节点为 100", (battery / "charge_control_end_threshold").read_text().strip() == "100")
    saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    check("解除限制后不再持久化上限", "threshold" not in saved, str(saved))

    shutil.rmtree(tmp, ignore_errors=True)


def test_restore_on_start():
    section("4. 启动恢复（模拟 Decky 重启）")
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-4-"))
    battery, kernel = make_battery(tmp)
    install_fake_sysfs(tmp, kernel, [battery])

    # 第一次运行：用户设置上限 70% 并切到「始终旁路」。
    plugin = Plugin()
    asyncio.run(plugin.set_charge_threshold(70))
    asyncio.run(plugin.set_charge_mode("inhibit-charge"))
    saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    check("模式已持久化", saved.get("mode") == "inhibit-charge", str(saved))

    # 模拟重启：内核复位成默认值，并新建一个 Plugin 实例。
    kernel.active = "auto"
    kernel.render_behaviour()
    kernel.threshold = 100
    (battery / "charge_control_end_threshold").write_text("100\n", encoding="utf-8")

    fresh = Plugin()
    asyncio.run(fresh._main())
    check("重启后上限被恢复为 70", (battery / "charge_control_end_threshold").read_text().strip() == "70")
    check("重启后模式被恢复为始终旁路", "[inhibit-charge]" in read_behaviour(battery))
    check(
        "恢复报告同时提到上限与模式",
        any("70" in line for line in fresh._restore_report)
        and any("始终旁路" in line for line in fresh._restore_report),
        str(fresh._restore_report),
    )

    # 关闭自动恢复后不应再改动内核状态。
    asyncio.run(fresh.set_auto_restore(False))
    kernel.active = "auto"
    kernel.render_behaviour()
    kernel.threshold = 100
    (battery / "charge_control_end_threshold").write_text("100\n", encoding="utf-8")

    again = Plugin()
    asyncio.run(again._main())
    check("关闭自动恢复后不写上限", (battery / "charge_control_end_threshold").read_text().strip() == "100")
    check("关闭自动恢复后不写模式", "[auto]" in read_behaviour(battery))
    check("报告说明已关闭", any("关闭" in line for line in again._restore_report), str(again._restore_report))

    # 通知策略已改为「成功静默、仅失败提示」，不再有开关；确认状态里没有残留字段。
    check(
        "状态里不再残留 toast_enabled",
        "toast_enabled" not in asyncio.run(again.get_status())["data"],
    )

    shutil.rmtree(tmp, ignore_errors=True)


def test_unsupported_kernel():
    section("5. 内核不支持时的降级行为")
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-5-"))
    battery, kernel = make_battery(tmp)
    # 只保留正常的 auto，且没有阈值节点。
    (battery / "charge_control_end_threshold").unlink()
    kernel.behaviours = ["auto"]
    kernel.active = "auto"
    kernel.render_behaviour()

    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery])

    status = asyncio.run(plugin.get_status())
    check("仍能读取状态", status["ok"] is True, str(status))
    data = status["data"]
    check("标记不支持开机旁路", data["supports_awake_bypass"] is False)
    check("标记不支持始终旁路", data["supports_bypass"] is False)
    check("标记不支持充电上限", data["supports_threshold"] is False)
    check("上限节点不存在", data["threshold_node"] is False)
    check("上限制为 None", data["threshold"] is None)

    result = asyncio.run(plugin.set_charge_threshold(80))
    check("设置上限时明确报错", result["ok"] is False and "charge_control_end_threshold" in result["error"], str(result))

    # 完全找不到电池节点。
    empty = Path(tempfile.mkdtemp(prefix="f1pro-5b-"))
    plugin2 = Plugin()
    install_fake_sysfs(empty, kernel, [])
    status = asyncio.run(plugin2.get_status())
    check("没有电池节点时返回失败", status["ok"] is False, str(status))
    check("错误文案说明原因", "未找到" in status.get("error", ""), status.get("error", ""))

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(empty, ignore_errors=True)


def test_battery_selection():
    section("6. 多电池候选时的选择与诊断")
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-6-"))
    real, kernel = make_battery(tmp, name="BAT0")
    # 一个只有电量的外设电池，绝不能抢走控制权。
    other = tmp / "BAT1"
    other.mkdir(parents=True, exist_ok=True)
    (other / "type").write_text("Battery\n", encoding="utf-8")
    (other / "capacity").write_text("40\n", encoding="utf-8")

    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [other, real])

    status = asyncio.run(plugin.get_status())
    check("选中真正支持充电控制的 BAT0", status["data"]["battery"] == "BAT0", str(status["data"]["battery"]))

    diag = asyncio.run(plugin.get_diagnostics())
    check("诊断返回 ok", diag["ok"] is True, str(diag))
    nodes = diag["data"]["nodes"]
    check("诊断包含 charge_behaviour", nodes["charge_behaviour"]["exists"] is True)
    check("诊断包含原始值", nodes["charge_behaviour"]["value"] is not None, str(nodes["charge_behaviour"]))
    check("诊断列出全部候选", len(diag["data"]["candidates"]) == 2, str(diag["data"]["candidates"]))

    shutil.rmtree(tmp, ignore_errors=True)


def test_power_sign_and_ac_state():
    section("7. 功率符号与外接电源识别")
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-7-"))
    battery, kernel = make_battery(tmp)
    ac = make_ac(tmp, "ACAD", online=True)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [ac])

    # 内核的 power_now 恒为正值（ACPI 驱动取过绝对值），方向要靠 status 补。
    data = asyncio.run(plugin.get_status())["data"]
    check("充电时功率为正", data["power_watts"] == 12.5, str(data.get("power_watts")))
    check("识别到在线适配器", data["ac_online"] is True, str(data.get("ac_online")))
    check("记录适配器节点名", data["ac_node"] == "ACAD", str(data.get("ac_node")))

    (battery / "status").write_text("Discharging\n", encoding="utf-8")
    data = asyncio.run(plugin.get_status())["data"]
    check("放电时功率翻为负", data["power_watts"] == -12.5, str(data.get("power_watts")))

    (battery / "status").write_text("Full\n", encoding="utf-8")
    data = asyncio.run(plugin.get_status())["data"]
    check("已充满时不改动符号", data["power_watts"] == 12.5, str(data.get("power_watts")))

    # 内核若本来就给出负值，不能被二次翻转。
    (battery / "power_now").write_text("-7000000\n", encoding="utf-8")
    (battery / "status").write_text("Discharging\n", encoding="utf-8")
    data = asyncio.run(plugin.get_status())["data"]
    check("内核已给负值时保持不变", data["power_watts"] == -7.0, str(data.get("power_watts")))

    # 拔掉充电线：离电放电，功率带负号。
    (ac / "online").write_text("0\n", encoding="utf-8")
    (battery / "power_now").write_text("37600000\n", encoding="utf-8")
    data = asyncio.run(plugin.get_status())["data"]
    check("拔电后 ac_online 为假", data["ac_online"] is False, str(data.get("ac_online")))
    check("离电功率为 -37.6W", data["power_watts"] == -37.6, str(data.get("power_watts")))

    # 没有适配器节点时返回 None，交给界面降级判断，而不是猜一个值。
    plugin2 = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [])
    data = asyncio.run(plugin2.get_status())["data"]
    check("无适配器节点时返回 None", data["ac_online"] is None, str(data.get("ac_online")))
    check("无适配器节点时无节点名", data["ac_node"] is None, str(data.get("ac_node")))

    # 只有 USB-C 供电节点时回退使用它。
    usb = make_ac(tmp, "ucsi-source-psy-1", online=True, kind="USB")
    plugin3 = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [usb])
    data = asyncio.run(plugin3.get_status())["data"]
    check("回退识别 USB-C 供电", data["ac_online"] is True, str(data.get("ac_online")))

    # Mains 节点存在时优先于 USB，且以 Mains 的状态为准。
    kernel_usb = make_ac(tmp, "ucsi-source-psy-2", online=True, kind="USB")
    plugin4 = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [kernel_usb, ac])
    data = asyncio.run(plugin4.get_status())["data"]
    check("Mains 优先于 USB", data["ac_online"] is False, str(data.get("ac_online")))
    check("Mains 节点名被采用", data["ac_node"] == "ACAD", str(data.get("ac_node")))

    # 同一组里有多个 USB-C 节点时，节点名必须跟着"在线"走，不能固定取第一个。
    usb_a = make_ac(tmp, "ucsi-source-psy-A", online=False, kind="USB")
    usb_b = make_ac(tmp, "ucsi-source-psy-B", online=True, kind="USB")
    usb_c = make_ac(tmp, "ucsi-source-psy-C", online=False, kind="USB")
    plugin5 = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [usb_a, usb_b, usb_c])
    data = asyncio.run(plugin5.get_status())["data"]
    check("多 USB 节点时有一个在线即为在线", data["ac_online"] is True, str(data.get("ac_online")))
    check(
        "在线时节点名取真正在线的那个（不是第一个）",
        data["ac_node"] == "ucsi-source-psy-B",
        str(data.get("ac_node")),
    )

    # 整组都离线时仍要能报出"离线"，并退回第一个节点名供展示。
    plugin6 = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [usb_a, usb_c])
    data = asyncio.run(plugin6.get_status())["data"]
    check("多 USB 节点全离线时判为离线", data["ac_online"] is False, str(data.get("ac_online")))
    check(
        "全离线时退回首个节点名",
        data["ac_node"] == "ucsi-source-psy-A",
        str(data.get("ac_node")),
    )

    shutil.rmtree(tmp, ignore_errors=True)


def test_fan_discovery():
    section("8. 风扇设备发现（按 hwmon name，不依赖编号）")
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-8-"))
    battery, kernel = make_battery(tmp)
    hwmon, ec, _temp = make_fan(tmp)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [], hwmon, ec)

    data = asyncio.run(plugin.get_fan_status())["data"]
    check("风扇控制可用", data["available"] is True, str(data.get("reason")))
    check(
        "温度设备按 name 找到（k10temp 在 hwmon3）",
        plugin.fan_temp_path == str(hwmon[0]),
        str(plugin.fan_temp_path),
    )
    check(
        "风扇设备按 name 找到（oxp_ec 在 hwmon7）",
        plugin.fan_controller_path == str(hwmon[1]),
        str(plugin.fan_controller_path),
    )
    check("读到 CPU 温度", data["temperature"] == 55.0, str(data.get("temperature")))
    check("读到风扇转速", data["rpm"] == 3200, str(data.get("rpm")))
    check("读到当前 PWM", data["pwm"] == 100, str(data.get("pwm")))
    check("pwm1_enable=2 判为 EC 自动", data["auto"] is True and data["manual"] is False, str(data))
    check("默认模式是自动", data["mode"] == "auto", str(data.get("mode")))

    # 缺任一必需节点 → 整体不可用，不允许拿半套节点去写。
    tmp2 = Path(tempfile.mkdtemp(prefix="f1pro-8b-"))
    battery2, kernel2 = make_battery(tmp2)
    hwmon2, ec2, _ = make_fan(tmp2, missing=("pwm1_enable",))
    plugin2 = Plugin()
    install_fake_sysfs(tmp2, kernel2, [battery2], [], hwmon2, ec2)
    data2 = asyncio.run(plugin2.get_fan_status())["data"]
    check("缺 pwm1_enable 时判为不可用", data2["available"] is False, str(data2))
    rpc2 = asyncio.run(plugin2.set_fan_mode("balanced"))
    check(
        "不可用时明确拒绝并说明原因",
        rpc2["ok"] is False and "未找到" in rpc2["error"],
        str(rpc2),
    )

    # 无法确认驱动身份（电池节点既无 charge_behaviour 也无上限）时必须禁用风扇：
    # 旧风扇驱动 0 = 自动、oxpec 0 = 手动满速，写错不会报错但会让 EC 卡手动满速。
    tmp3 = Path(tempfile.mkdtemp(prefix="f1pro-8c-"))
    plain = tmp3 / "BAT0"
    plain.mkdir(parents=True, exist_ok=True)
    (plain / "type").write_text("Battery\n", encoding="utf-8")
    (plain / "capacity").write_text("50\n", encoding="utf-8")
    (plain / "status").write_text("Discharging\n", encoding="utf-8")
    hwmon3, ec3, _ = make_fan(tmp3)
    plugin3 = Plugin()
    install_fake_sysfs(tmp3, FakeKernel(plain, [], None, None), [plain], [], hwmon3, ec3)
    data3 = asyncio.run(plugin3.get_fan_status())["data"]
    check(
        "无法确认 oxpec 时禁用风扇",
        data3["available"] is False and "oxpec" in data3["reason"],
        str(data3),
    )
    rpc3 = asyncio.run(plugin3.set_fan_mode("performance"))
    check("禁用时拒绝切换模式", rpc3["ok"] is False, str(rpc3))
    check(
        "禁用时没有碰过 EC 寄存器",
        (ec3.dir / "pwm1_enable").read_text(encoding="utf-8").strip() == "2",
        (ec3.dir / "pwm1_enable").read_text(encoding="utf-8"),
    )

    for path in (tmp, tmp2, tmp3):
        shutil.rmtree(path, ignore_errors=True)


def test_fan_curve_and_control():
    section("9. 风扇曲线、死区、安全保护与模式切换")
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-9-"))
    battery, kernel = make_battery(tmp)
    hwmon, ec, temp_dir = make_fan(tmp, temp_c=55.0, pwm=100)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [], hwmon, ec)

    # ---- pwm1_enable 读回值的翻译（全插件唯一来源）----
    translate = Plugin._fan_control_mode
    check("读回 1 → 手动", translate(1) == (True, False), str(translate(1)))
    check("读回 0（手动满速）→ 仍算手动", translate(0) == (True, False), str(translate(0)))
    check("读回 2 → EC 自动", translate(2) == (False, True), str(translate(2)))
    check("读不到 → 既非手动也非自动", translate(None) == (False, False), str(translate(None)))

    interp = Plugin._interpolate_curve
    balanced = Plugin.FAN_PROFILES["balanced"]
    check("曲线在节点上取原值（50°C → 75）", interp(balanced, 50) == 75, str(interp(balanced, 50)))
    check("曲线在节点间线性插值（55°C → 95）", interp(balanced, 55) == 95, str(interp(balanced, 55)))
    check("低于首节点取首值", interp(balanced, 20) == 40, str(interp(balanced, 20)))
    check("高于末节点取末值", interp(balanced, 99) == 255, str(interp(balanced, 99)))
    check(
        "静音曲线比均衡保守",
        interp(Plugin.FAN_PROFILES["quiet"], 70) < interp(balanced, 70),
        str((interp(Plugin.FAN_PROFILES["quiet"], 70), interp(balanced, 70))),
    )
    check(
        "性能曲线比均衡激进",
        interp(Plugin.FAN_PROFILES["performance"], 60) > interp(balanced, 60),
        str((interp(Plugin.FAN_PROFILES["performance"], 60), interp(balanced, 60))),
    )

    # ---- 切手动：写 pwm1_enable=1，并立刻按当前温度算一次 ----
    rpc = asyncio.run(plugin.set_fan_mode("balanced"))
    plugin._stop_fan_controller()  # 停掉线程，后面的断言才能确定性进行
    check("切换均衡返回成功", rpc["ok"] is True, str(rpc))
    check("写入 pwm1_enable=1（手动）", ec.mode_register == 1, str(ec.mode_register))
    check("状态标记为手动控制", rpc["data"]["manual"] is True, str(rpc["data"]))
    check("保存了风扇模式", plugin._load_settings().get("fan_mode") == "balanced")
    # 55°C → 目标 95，当前 100，差值 5 < 阈值 8 → 不写
    check("差值未达阈值时不写 PWM", ec.pwm == 100, str(ec.pwm))

    # ---- 死区：变化足够大才写 ----
    set_temp(temp_dir, 65.0)
    written = plugin._fan_step()
    check("变化足够大时写入 PWM", written is not None, str(written))
    check("65°C 均衡曲线 → 138", ec.pwm == 138, str(ec.pwm))
    check("写入值经过读回确认", written == 138, str(written))

    # ---- 安全保护：任何曲线都不能覆盖 ----
    # 特意用一条顶端很保守的自定义曲线：它自己在 80°C 只给 100 PWM。
    # 这样"≥85°C 直接 255"才是唯一能把风扇拉到满速的原因，断言不会空转
    # （若用均衡曲线，80°C 本来就已经是 255，安全保护加不加都一样）。
    asyncio.run(
        plugin.set_fan_custom_curve([[40, 40], [50, 45], [60, 60], [70, 80], [80, 100]])
    )
    asyncio.run(plugin.set_fan_mode("custom"))
    plugin._stop_fan_controller()
    set_temp(temp_dir, 70.0)
    plugin._fan_step()
    check("安全温度以下自定义曲线正常生效（70°C → 80）", ec.pwm == 80, str(ec.pwm))
    set_temp(temp_dir, 86.0)
    plugin._fan_step()
    check("≥85°C 时安全保护覆盖自定义曲线并满速", ec.pwm == 255, str(ec.pwm))

    # ---- 回归：手动 + 满速时 oxpec 读回 0 而不是 1 ----
    data = asyncio.run(plugin.get_fan_status())["data"]
    check("满速时 pwm1_enable 读回 0", data["pwm_enable"] == 0, str(data.get("pwm_enable")))
    check(
        "读回 0 仍判定为手动控制",
        data["manual"] is True and data["auto"] is False,
        str(data),
    )
    rpc = asyncio.run(plugin.set_fan_mode("quiet"))
    plugin._stop_fan_controller()
    check("满速状态下切换模式不会误报失败", rpc["ok"] is True, str(rpc))

    # ---- 外部改回自动后必须夺回控制权，否则写 pwm1 是无效的 ----
    asyncio.run(plugin.set_fan_mode("balanced"))
    plugin._stop_fan_controller()
    ec.mode_register = 0
    ec.render()
    set_temp(temp_dir, 50.0)
    plugin._fan_step()
    check("检测到 EC 收回控制权后重新切回手动", ec.mode_register == 1, str(ec.mode_register))

    # ---- 纵深防御：_fan_step 自己也要过一遍硬件身份门禁 ----
    # 控制线程在后台跑很久，期间"电池节点带 charge_behaviour"这个判据可能失效
    # （节点消失、驱动被换）。写错 pwm1_enable 不报错、只会让 EC 卡手动满速，
    # 所以每一步都重新确认，不能只依赖三个入口各查一次。
    #
    # 用独立的临时目录做：本函数后面的断言还要继续用 battery，不能被这里删坏。
    tmp_gate = Path(tempfile.mkdtemp(prefix="f1pro-9-gate-"))
    battery_gate, kernel_gate = make_battery(tmp_gate)
    hwmon_gate, ec_gate, temp_gate = make_fan(tmp_gate, temp_c=70.0, pwm=100)
    plugin_gate = Plugin()
    install_fake_sysfs(tmp_gate, kernel_gate, [battery_gate], [], hwmon_gate, ec_gate)
    reset_settings()
    asyncio.run(plugin_gate.set_fan_mode("balanced"))
    plugin_gate._stop_fan_controller()
    ec_gate.pwm = 100
    (battery_gate / "charge_behaviour").unlink()  # 门禁判据失效
    (battery_gate / "charge_control_end_threshold").unlink()
    set_temp(temp_gate, 70.0)
    before = ec_gate.pwm
    try:
        plugin_gate._fan_step()
        raised = False
    except Exception:
        raised = True
    check("门禁失效时 _fan_step 拒绝写入（而不是静默乱写）", raised, f"异常={raised}")
    check("门禁失效期间 PWM 未被改动", ec_gate.pwm == before, str(ec_gate.pwm))

    # 回到本函数的主目录，后面继续用原来的 plugin / ec。
    install_fake_sysfs(tmp, kernel, [battery], [], hwmon, ec)
    reset_settings()

    # ---- 切回自动：交还 EC ----
    rpc = asyncio.run(plugin.set_fan_mode("auto"))
    check("切回自动返回成功", rpc["ok"] is True, str(rpc))
    check("EC 寄存器恢复自动（0x00）", ec.mode_register == 0, str(ec.mode_register))
    check(
        "读回 pwm1_enable=2",
        (ec.dir / "pwm1_enable").read_text(encoding="utf-8").strip() == "2",
        (ec.dir / "pwm1_enable").read_text(encoding="utf-8"),
    )
    check("控制线程已停止", plugin._fan_thread is None, str(plugin._fan_thread))

    # ---- 自定义曲线校验 ----
    ok = asyncio.run(
        plugin.set_fan_custom_curve([[40, 50], [50, 80], [60, 120], [70, 180], [80, 240]])
    )
    check("保存合法自定义曲线", ok["ok"] is True, str(ok))
    check(
        "曲线被规范化后持久化",
        plugin._load_settings().get("fan_custom_curve")[2] == [60, 120],
        str(plugin._load_settings().get("fan_custom_curve")),
    )

    bad = asyncio.run(
        plugin.set_fan_custom_curve([[60, 50], [60, 80], [70, 120], [80, 180], [90, 240]])
    )
    check("拒绝温度重复的曲线", bad["ok"] is False and "递增" in bad["error"], str(bad))

    # 乱序必须被拒绝，且**不得被静默重排**。
    # 旧实现先 points.sort() 再查重复：这条曲线会被排成 [[40,40],[50,75],...] 后
    # 顺利通过——节点 2 的 75 被搬到了节点 1 上，等于悄悄改乱用户的设置；
    # 而前端把乱序判为错误，前后端语义不一致。这里锁住"按原样校验"。
    # 注意 PWM 要全部落在合法区间内，否则会先被 PWM 范围拦下、测不到递增规则。
    bad = asyncio.run(
        plugin.set_fan_custom_curve([[50, 75], [40, 40], [60, 120], [70, 180], [80, 240]])
    )
    check(
        "拒绝温度乱序的曲线（不得静默重排）",
        bad["ok"] is False and "递增" in bad["error"],
        str(bad),
    )
    check(
        "乱序曲线不会被写进设置（重排后通过就等于改乱用户设置）",
        plugin._load_settings().get("fan_custom_curve")[0] == [40, 50],
        str(plugin._load_settings().get("fan_custom_curve")),
    )

    # PWM 与温度同时乱序时，PWM 也得跟着原节点走、不能被重排搬家。
    # 上一条只证明"被拒"，这条直接对归一化结果本身求值，防止有人把 sort 加回来。
    from main import Plugin as _P

    try:
        _P._normalize_fan_curve([[60, 120], [40, 40], [80, 240], [50, 75], [70, 160]])
        reordered_ok = True
    except ValueError:
        reordered_ok = False
    check(
        "归一化不做重排（乱序输入一律抛 ValueError）",
        reordered_ok is False,
        "被重排后通过了，说明 sort 又回来了",
    )

    bad = asyncio.run(
        plugin.set_fan_custom_curve([[40, 50], [50, 80], [60, 120], [70, 180], [80, 300]])
    )
    check("拒绝 PWM 越界的曲线", bad["ok"] is False and "PWM" in bad["error"], str(bad))

    bad = asyncio.run(plugin.set_fan_custom_curve([[40, 50], [50, 80], [60, 120], [70, 180]]))
    check("拒绝节点数不是 5 的曲线", bad["ok"] is False, str(bad))

    # 曲线不合法只是"这条数据不行"，不该把正在跑的手动控制踢回 EC。
    asyncio.run(plugin.set_fan_mode("custom"))
    plugin._stop_fan_controller()
    check("前置：已处于手动控制", ec.mode_register == 1, str(ec.mode_register))
    bad = asyncio.run(
        plugin.set_fan_custom_curve([[60, 50], [60, 80], [70, 120], [80, 180], [90, 240]])
    )
    check("非法曲线仍然被拒绝", bad["ok"] is False, str(bad))
    check(
        "曲线校验失败不影响正在运行的手动控制",
        ec.mode_register == 1 and plugin._load_settings().get("fan_mode") == "custom",
        str((ec.mode_register, plugin._load_settings())),
    )
    plugin._stop_fan_controller()

    # ---- 卸载必须交还 EC ----
    asyncio.run(plugin.set_fan_mode("performance"))
    asyncio.run(plugin._unload())
    check("插件卸载后 EC 回到自动控制", ec.mode_register == 0, str(ec.mode_register))

    shutil.rmtree(tmp, ignore_errors=True)


def test_fan_restore():
    section("10. 风扇启动恢复与残留手动状态的安全兜底")
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-10-"))
    battery, kernel = make_battery(tmp)
    hwmon, ec, temp_dir = make_fan(tmp)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [], hwmon, ec)

    # 模拟上次插件崩溃 / 被 kill -9：EC 停在手动 PWM，_unload 没跑过。
    ec.mode_register = 1
    ec.pwm = 120
    ec.render()
    check(
        "前置条件：EC 处于残留的手动模式",
        (ec.dir / "pwm1_enable").read_text(encoding="utf-8").strip() == "1",
    )

    plugin._restore_sync()
    check("启动时发现残留手动并交还 EC", ec.mode_register == 0, str(ec.mode_register))
    check(
        "恢复报告说明风扇已在 EC 手上",
        any("已确认处于 EC 自动控制" in line for line in plugin._restore_report),
        str(plugin._restore_report),
    )
    # 交还后读回文件也必须真的是自动——只改寄存器不算数（同一处防的正是
    # "读回滞后"，所以这里连读回一起校验）。
    check(
        "交还后读回 pwm1_enable=2",
        (ec.dir / "pwm1_enable").read_text(encoding="utf-8").strip() == "2",
        (ec.dir / "pwm1_enable").read_text(encoding="utf-8"),
    )

    # 保存的是手动模式 → 应恢复并立刻开始控制。
    plugin._update_setting("fan_mode", "balanced")
    set_temp(temp_dir, 70.0)
    plugin._restore_sync()
    plugin._stop_fan_controller()
    check("启动恢复手动模式", ec.mode_register == 1, str(ec.mode_register))
    check("恢复时立即按温度写了一次 PWM（70°C → 160）", ec.pwm == 160, str(ec.pwm))
    check(
        "恢复报告包含风扇模式",
        any("风扇模式已恢复" in line for line in plugin._restore_report),
        str(plugin._restore_report),
    )

    # 关闭「启动恢复」时仍必须纠正残留手动——这是安全兜底，不是偏好设置。
    reset_settings()
    ec.mode_register = 1
    ec.pwm = 130
    ec.render()
    plugin._update_setting("auto_restore", False)
    plugin._restore_sync()
    check(
        "关闭启动恢复时仍会交还 EC",
        ec.mode_register == 0,
        str(ec.mode_register),
    )

    shutil.rmtree(tmp, ignore_errors=True)


def test_fan_failure_rollback():
    section("11. 风扇切换失败时必须交还 EC（不得留在无人控制的手动状态）")
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-11-"))
    battery, kernel = make_battery(tmp)
    hwmon, ec, temp_dir = make_fan(tmp, temp_c=60.0, pwm=100)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [], hwmon, ec)

    check("前置：EC 处于自动控制", ec.mode_register == 0, str(ec.mode_register))

    # 温度节点存在但内容不可解析。关键在于异常发生在「已经写入 pwm1_enable=1」
    # 之后：此时若不做兜底，风扇就停在手动 PWM 上，既不跟温度走、也没有安全保护。
    (temp_dir / "temp1_input").write_text("N/A\n", encoding="utf-8")

    rpc = asyncio.run(plugin.set_fan_mode("balanced"))
    check("切换失败时如实返回错误", rpc["ok"] is False, str(rpc))
    check("失败后已把控制权交还 EC", ec.mode_register == 0, str(ec.mode_register))
    check(
        "读回 pwm1_enable=2（EC 自动）",
        (ec.dir / "pwm1_enable").read_text(encoding="utf-8").strip() == "2",
        (ec.dir / "pwm1_enable").read_text(encoding="utf-8"),
    )
    check("失败后没有残留控制线程", plugin._fan_thread is None, str(plugin._fan_thread))
    check(
        "保存的模式已回到自动（否则下次启动会再恢复成手动）",
        plugin._load_settings().get("fan_mode") == "auto",
        str(plugin._load_settings()),
    )
    data = asyncio.run(plugin.get_fan_status())["data"]
    check("界面状态不再声称处于手动", data["manual"] is False and data["auto"] is True, str(data))

    # ---- 同一条兜底规则也要覆盖「应用自定义曲线」这条路径 ----
    reset_settings()
    (temp_dir / "temp1_input").write_text("55000\n", encoding="utf-8")
    rpc = asyncio.run(plugin.set_fan_mode("custom"))
    plugin._stop_fan_controller()
    check("前置：自定义模式已生效", rpc["ok"] is True and ec.mode_register == 1, str(rpc))

    (temp_dir / "temp1_input").write_text("N/A\n", encoding="utf-8")
    rpc = asyncio.run(plugin.set_fan_custom_curve([[40, 40], [50, 60], [60, 90], [70, 140], [80, 200]]))
    check("应用曲线失败时如实返回错误", rpc["ok"] is False, str(rpc))
    check("应用曲线失败后同样交还 EC", ec.mode_register == 0, str(ec.mode_register))
    check(
        "应用曲线失败后保存的模式回到自动",
        plugin._load_settings().get("fan_mode") == "auto",
        str(plugin._load_settings()),
    )

    shutil.rmtree(tmp, ignore_errors=True)


def test_fan_safety_paths():
    section("12. 三条安全路径：线程异常/启动恢复失败/夺权确认都必须过门禁")

    # ---- ① 控制线程异常时不得绕过门禁直接写 pwm1_enable ----
    # 异常来源之一就是门禁失效（_fan_step 里的 _require_fan_support 抛错）。
    # 那一刻驱动身份已无法确认，而 pwm1_enable 的取值语义是驱动相关的：
    # 再写 2 有可能被其它驱动解释成手动，反而把风扇钉住。必须走 fail-safe，
    # 由它重新查门禁、门禁失效时不写任何风扇节点。
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-12a-"))
    battery, kernel = make_battery(tmp)
    hwmon, ec, temp_dir = make_fan(tmp, temp_c=60.0, pwm=100)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [], hwmon, ec)

    asyncio.run(plugin.set_fan_mode("balanced"))
    plugin._stop_fan_controller()
    check("前置：已进入手动控制", ec.mode_register == 1, str(ec.mode_register))

    # 门禁判据失效 → _fan_step 抛错 → 走控制线程的异常分支。
    (battery / "charge_behaviour").unlink()
    (battery / "charge_control_end_threshold").unlink()
    before_pwm, before_enable = ec.pwm, ec.mode_register

    plugin._fan_stop_event.clear()
    plugin._fan_control_loop()  # 单次进入：抛错后应立刻 return

    check(
        "门禁失效时线程异常分支不写 pwm1_enable（保持原寄存器）",
        ec.mode_register == before_enable,
        f"写入前={before_enable} 现在={ec.mode_register}",
    )
    check("门禁失效时也不碰 pwm1", ec.pwm == before_pwm, str(ec.pwm))
    check(
        "保存的模式已回到自动（否则下次启动会再恢复成手动）",
        plugin._load_settings().get("fan_mode") == "auto",
        str(plugin._load_settings()),
    )
    check("异常后线程已退出", plugin._fan_thread is None, str(plugin._fan_thread))

    # 门禁正常时，线程异常仍应交还 EC（两条分支都要走对）。
    reset_settings()
    tmp_b = Path(tempfile.mkdtemp(prefix="f1pro-12b-"))
    battery_b, kernel_b = make_battery(tmp_b)
    hwmon_b, ec_b, temp_b = make_fan(tmp_b, temp_c=60.0, pwm=100)
    plugin_b = Plugin()
    install_fake_sysfs(tmp_b, kernel_b, [battery_b], [], hwmon_b, ec_b)
    asyncio.run(plugin_b.set_fan_mode("balanced"))
    plugin_b._stop_fan_controller()
    check("前置：已进入手动控制（门禁正常）", ec_b.mode_register == 1, str(ec_b.mode_register))
    (temp_b / "temp1_input").write_text("N/A\n", encoding="utf-8")  # 温度读不出来
    plugin_b._fan_stop_event.clear()
    plugin_b._fan_control_loop()
    check(
        "门禁正常时线程异常仍交还 EC 自动",
        ec_b.mode_register == 0,
        str(ec_b.mode_register),
    )

    # ---- ② 启动恢复里 _fan_apply_manual 返回 False 必须兜底 ----
    # 关键点：_fan_apply_manual 是**先写 pwm1_enable=1、再确认**，返回 False
    # 意味着 EC 已经在手动、而控制线程没起来。只记日志会留下无人控制的手动风扇。
    #
    # 这里必须区分两种失败，否则断言会空转：
    #   * ignored_writes：写入被丢弃，EC 寄存器**根本没进手动** → 没什么可兜底的；
    #   * stale_reads  ：写入**生效了**（EC 真进手动），只是读回没刷新 → 这才是
    #                    真正危险的场景，也正是修复要覆盖的那条路径。
    reset_settings()
    tmp2 = Path(tempfile.mkdtemp(prefix="f1pro-12c-"))
    battery2, kernel2 = make_battery(tmp2)
    hwmon2, ec2, temp2 = make_fan(tmp2, temp_c=60.0, pwm=100)
    plugin2 = Plugin()
    install_fake_sysfs(tmp2, kernel2, [battery2], [], hwmon2, ec2)

    plugin2._update_setting("fan_mode", "balanced")

    ec2.stale_reads.add("pwm1_enable")  # 写入生效，但读回仍是旧的自动值

    # 这里要专门构造「_fan_apply_manual 返回 False」——也就是"EC 已经在手动、
    # 但确认失败"这条分支。它和"_fan_apply_manual 抛异常"是**不同**的分支
    # （前者进 else 兜底，后者进 except 兜底），必须分别覆盖。
    #
    # 注意不能用"替换 _fan_apply_manual 为直接 return False 的桩"：
    # 那样 EC 根本没进手动，也就没有"无人控制的手动 PWM"要兜底，
    # 断言会变成测一个不存在的情形（变异验证会证明它是空转的）。
    # 正确做法是**真实现先跑一遍**（这样 EC 确实进了手动、enable 文件也确实
    # 被写成 1，只是被 stale_reads 挡住读回），再让它返回 False。
    real_apply = plugin2._fan_apply_manual

    def apply_then_report_failure(mode):
        real_apply(mode)
        return False  # 真实失败形态：已写入手动，但确认没通过

    plugin2._fan_apply_manual = apply_then_report_failure
    plugin2._start_fan_controller = lambda: None  # 真线程会搅乱状态，禁掉

    plugin2._restore_sync()
    # 写入路径会清掉 stale 标记（夹具的防泄露设计），这里重新置上，
    # 让下面的读回断言检查的是"写完之后文件仍然是滞后的旧值"。
    ec2.stale_reads.add("pwm1_enable")

    # 先确认测试夹具真的把"观察点"摆在预期位置，否则下面的断言会测错东西：
    # 必须落在 else 分支（"未确认生效"），而不是 except 分支（"恢复失败"）。
    check(
        "测试夹具奏效：失败点落在「已写入手动但未确认」这一分支",
        plugin2._restore_report
        and all("未确认生效" in line for line in plugin2._restore_report),
        str(plugin2._restore_report),
    )
    check(
        "启动恢复失败时已交还 EC（而不是留下无人控制的手动）",
        ec2.mode_register == 0,
        f"寄存器={ec2.mode_register}",
    )
    check(
        "兜底还停掉了控制线程（不留后台线程继续写 PWM）",
        plugin2._fan_thread is None,
        str(plugin2._fan_thread),
    )
    check(
        "读回滞后时也不会以为已经是自动就跳过写回",
        (ec2.dir / "pwm1_enable").read_text(encoding="utf-8").strip() == "2",
        (ec2.dir / "pwm1_enable").read_text(encoding="utf-8"),
    )
    check(
        "启动恢复失败的报告如实说明",
        any("未确认生效" in line or "恢复失败" in line for line in plugin2._restore_report),
        str(plugin2._restore_report),
    )
    check("启动恢复失败后没有残留控制线程", plugin2._fan_thread is None, str(plugin2._fan_thread))

    # ---- ②b `_fan_handover_to_ec` 不得凭一次滞后的读回就提前返回 ----
    # 这是个独立的场景，必须单独构造：如果交还 EC 时看到"读回说是自动"就直接
    # return True，那么当**寄存器其实还在手动、只有读回文件是旧值**时，
    # 根本不会真的写回 2 —— 风扇就被留在无人控制的手动状态。
    # 上面 ② 的夹具用不到这条（那里是固定的失败返回），所以单独来一遍。
    reset_settings()
    tmp2b = Path(tempfile.mkdtemp(prefix="f1pro-12c2-"))
    battery2b, kernel2b = make_battery(tmp2b)
    hwmon2b, ec2b, temp2b = make_fan(tmp2b, temp_c=60.0, pwm=100)
    plugin2b = Plugin()
    install_fake_sysfs(tmp2b, kernel2b, [battery2b], [], hwmon2b, ec2b)

    # 直接手工构造"寄存器在手动、读回文件却说自动"这种不一致：
    # 寄存器手动（残留状态），但 sysfs 文件停在 "2"。
    # 不能靠 render() 去刷——那只会把文件改成与寄存器一致。
    ec2b.mode_register = 1
    ec2b.pwm = 120
    (ec2b.dir / "pwm1_enable").write_text("2\n", encoding="utf-8")
    (ec2b.dir / "pwm1").write_text("120\n", encoding="utf-8")

    check(
        "测试夹具奏效：读回说是自动、寄存器其实还是手动",
        (ec2b.dir / "pwm1_enable").read_text(encoding="utf-8").strip() == "2"
        and ec2b.mode_register == 1,
        f"读回={(ec2b.dir / 'pwm1_enable').read_text(encoding='utf-8').strip()} "
        f"寄存器={ec2b.mode_register}",
    )

    plugin2b._update_setting("fan_mode", "auto")

    # 直接驱动交还函数本身。走 _restore_sync 会先撞上 `_restore_fan_report`
    # 里"读回说是自动就直接 return"的早退（见下文 ②c），那条路径在这一步
    # 之前就返回了，测不到交还函数自己的行为。
    handed = plugin2b._fan_handover_to_ec()
    check(
        "读回滞后时交还 EC 仍然真的写回 2（寄存器回到自动）",
        ec2b.mode_register == 0,
        f"寄存器={ec2b.mode_register} 返回值={handed}",
    )
    check(
        "交还 EC 仍然读回确认（不假装成功）",
        handed is True and (ec2b.dir / "pwm1_enable").read_text(encoding="utf-8").strip() == "2",
        f"返回值={handed} 读回={(ec2b.dir / 'pwm1_enable').read_text(encoding='utf-8').strip()}",
    )

    # ---- ③ 夺回控制权后必须确认真的回到手动 ----
    # 只写 pwm1_enable=1 不确认的话，若写入被静默忽略，后续 pwm1 写入全是空转：
    # 界面显示手动、温度也在读，实际 PWM 没生效。
    reset_settings()
    tmp3 = Path(tempfile.mkdtemp(prefix="f1pro-12d-"))
    battery3, kernel3 = make_battery(tmp3)
    hwmon3, ec3, temp3 = make_fan(tmp3, temp_c=60.0, pwm=100)
    plugin3 = Plugin()
    install_fake_sysfs(tmp3, kernel3, [battery3], [], hwmon3, ec3)

    asyncio.run(plugin3.set_fan_mode("balanced"))
    plugin3._stop_fan_controller()
    ec3.mode_register = 0
    ec3.render()  # 外部把控制权改回自动
    ec3.ignored_writes.add("pwm1_enable")  # 且夺回写入被静默忽略
    set_temp(temp3, 70.0)
    before = ec3.pwm
    try:
        plugin3._fan_step()
        raised = False
    except Exception:
        raised = True
    check("夺权确认失败时抛异常（交给 fail-safe，不假装成功）", raised, f"异常={raised}")
    check("夺权确认失败时不继续写 pwm1", ec3.pwm == before, str(ec3.pwm))

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(tmp_b, ignore_errors=True)
    shutil.rmtree(tmp2, ignore_errors=True)
    shutil.rmtree(tmp3, ignore_errors=True)


def test_fan_safety_overrides_deadband():
    section("13. 安全保护（85°C）必须无条件写入，不能被写入死区拦下")
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-13-"))
    battery, kernel = make_battery(tmp)
    hwmon, ec, temp_dir = make_fan(tmp, temp_c=70.0, pwm=100)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [], hwmon, ec)

    # 使用一条在安全温度附近非常平缓的曲线：[80, 250] → [90, 255]。
    # 这样"温度刚过 85"和"温度远高于 85"的目标 PWM 都是 255，差别只在当前
    # 转速 250 与 255 的落差（5 < 死区 8）——正是死区会误拦的场景。
    asyncio.run(
        plugin.set_fan_custom_curve(
            [[40, 40], [60, 90], [80, 250], [90, 255], [100, 255]]
        )
    )
    asyncio.run(plugin.set_fan_mode("custom"))
    plugin._stop_fan_controller()

    # 先把风扇稳定在 252：84.8°C 走曲线插值 → 252（[80,250] 与 [90,255] 之间），
    # 与初始 100 差距很大，会写。
    set_temp(temp_dir, 84.8)
    plugin._fan_step()
    check("前置：84.8°C 时曲线把风扇拉到 252", ec.pwm == 252, str(ec.pwm))

    # 关键一步：温度刚越过 85°C，目标 255，与当前 252 只差 3（< 8）。
    # 死区若拦截，这里就不会写入，安全保护失效——而且只要曲线插值仍给出
    # 252，之后每一轮都会继续被拦，风扇永远升不到满速。
    set_temp(temp_dir, 85.2)
    written = plugin._fan_step()
    check(
        "刚越过 85°C 时安全保护写入满速（不被死区拦下）",
        ec.pwm == 255,
        f"PWM={ec.pwm} 返回值={written}",
    )
    check("安全保护写入后返回值经读回确认", written == 255, str(written))

    # 边界：恰好 85.0°C 也要满速（判据是 >= 而非 >）。
    ec.pwm = 245
    ec.render()
    set_temp(temp_dir, 85.0)
    plugin._fan_step()
    check("恰好 85.0°C 触发安全保护满速", ec.pwm == 255, str(ec.pwm))

    # 反向验证：非安全温度下死区**依然有效**，没有被这次修改顺手关掉。
    ec.pwm = 248
    ec.render()
    set_temp(temp_dir, 84.8)  # 曲线插值 252，与 248 差 4 < 8
    before = ec.pwm
    plugin._fan_step()
    check(
        "安全温度以下死区仍然生效（小差值不写，保护 EC）",
        ec.pwm == before,
        f"写入前={before} 现在={ec.pwm}",
    )

    # 反向验证二：差值够大时，非安全温度下照常写入。
    ec.pwm = 100
    ec.render()
    plugin._fan_step()
    check("安全温度以下差值够大时仍会写入（死区不是死锁）", ec.pwm == 252, str(ec.pwm))

    # 断言构造前提本身成立：253 与 255 的差确实小于死区阈值，
    # 否则"安全保护绕过死区"这条断言就是在测一个不存在的情形。
    interp_here = Plugin._interpolate_curve(
        [[40, 40], [60, 90], [80, 250], [90, 255], [100, 255]], 85.2
    )
    check(
        "构造前提成立：安全阈值附近的目标差值确实小于死区阈值",
        abs(Plugin.FAN_MAX_PWM - interp_here) < Plugin.FAN_PWM_CHANGE_THRESHOLD,
        str((interp_here, Plugin.FAN_MAX_PWM, Plugin.FAN_PWM_CHANGE_THRESHOLD)),
    )

    shutil.rmtree(tmp, ignore_errors=True)


def test_fan_handover_gate_and_no_early_exit():
    section("14. 交还 EC：自带门禁 + 启动恢复不得凭一次读回早退")

    # 这一节守两件事：
    #   ① `_fan_handover_to_ec()` 自己也查门禁——它是"把风扇交回去"的唯一
    #      底层入口，不能指望每个调用方都记得先查；
    #   ② 启动恢复不得因为"读回说是自动"就提前 return，因为读回可能滞后。
    #
    # ② 的难点在于：**真自动**和**读回滞后的假自动**在函数入口看起来一模一样
    # （都是一次 `_fan_is_auto() == True`）。所以必须分成两组对照来测，
    # 否则断言会退化成"测一个不存在的情形"。

    # ---- ①-a 门禁失效时，交还函数一个字都不许写 ----
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-14a-"))
    battery, kernel = make_battery(tmp)
    hwmon, ec, temp_dir = make_fan(tmp, temp_c=60.0, pwm=100)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [], hwmon, ec)

    ec.mode_register = 1  # 制造一个"残留手动"的现场，好让下面的"没写"有分辨力
    ec.pwm = 120
    ec.render()
    (battery / "charge_behaviour").unlink()
    (battery / "charge_control_end_threshold").unlink()

    handover = plugin._fan_handover_to_ec()
    check("门禁失效时交还 EC 直接失败（不假装成功）", handover is False, f"返回值={handover}")
    check(
        "门禁失效时不去写 pwm1_enable（这正是加门禁要防的事）",
        ec.mode_register == 1,
        f"寄存器={ec.mode_register}",
    )
    check(
        "门禁失效时读回仍是残留的手动值",
        (ec.dir / "pwm1_enable").read_text(encoding="utf-8").strip() == "1",
        (ec.dir / "pwm1_enable").read_text(encoding="utf-8"),
    )

    # ---- ①-b 对照组：门禁正常时必须真的写回并且确认 ----
    reset_settings()
    tmp_b = Path(tempfile.mkdtemp(prefix="f1pro-14b-"))
    battery_b, kernel_b = make_battery(tmp_b)
    hwmon_b, ec_b, _ = make_fan(tmp_b, temp_c=60.0, pwm=100)
    plugin_b = Plugin()
    install_fake_sysfs(tmp_b, kernel_b, [battery_b], [], hwmon_b, ec_b)

    ec_b.mode_register = 1
    ec_b.pwm = 120
    ec_b.render()
    handover_b = plugin_b._fan_handover_to_ec()
    check(
        "（对照）门禁正常时交还 EC 成功",
        handover_b is True and ec_b.mode_register == 0,
        f"返回值={handover_b} 寄存器={ec_b.mode_register}",
    )

    # ---- ②-a 真自动：交还必须执行，且报告说清结论 ----
    # 场景：保存模式=自动，EC 本来就真的在自动（寄存器与读回都是自动）。
    # 期望：仍然走一遍交还（幂等写回），报告说明当前处于 EC 自动控制。
    #
    # 注意这里**不**追求"真自动时不提示"：写入前的读回值本身就不可信
    # （读回滞后时会显示成自动），拿它区分"真自动 / 假自动"必然误判。
    # 与其猜，不如统一给出一个准确的结论，让用户知道风扇确实在 EC 手上。
    reset_settings()
    tmp2 = Path(tempfile.mkdtemp(prefix="f1pro-14c-"))
    battery2, kernel2 = make_battery(tmp2)
    hwmon2, ec2, _ = make_fan(tmp2, temp_c=55.0, pwm=110)
    plugin2 = Plugin()
    install_fake_sysfs(tmp2, kernel2, [battery2], [], hwmon2, ec2)
    plugin2._update_setting("fan_mode", "auto")

    check("（真自动）前置：寄存器与读回都是自动", ec2.mode_register == 0, str(ec2.mode_register))
    plugin2._restore_sync()
    check(
        "（真自动）报告给出「已处于 EC 自动控制」这个结论",
        any("已确认处于 EC 自动控制" in line for line in plugin2._restore_report),
        str(plugin2._restore_report),
    )
    check(
        "（真自动）交还动作本身仍然执行过（幂等写回，寄存器保持自动）",
        ec2.mode_register == 0,
        str(ec2.mode_register),
    )

    # ---- ②-b 读回滞后的假自动：必须被纠正，而且要报出来 ----
    # 这是整节的**核心场景**：EC 寄存器还在手动（上次崩了），但 sysfs 读回
    # 文件停在 "2"。旧实现看到读回是自动就直接 return，于是什么都没做，
    # 风扇被永久留在不跟温度走、也没有 85°C 保护的手动状态。
    reset_settings()
    tmp3 = Path(tempfile.mkdtemp(prefix="f1pro-14d-"))
    battery3, kernel3 = make_battery(tmp3)
    hwmon3, ec3, _ = make_fan(tmp3, temp_c=55.0, pwm=120)
    plugin3 = Plugin()
    install_fake_sysfs(tmp3, kernel3, [battery3], [], hwmon3, ec3)
    plugin3._update_setting("fan_mode", "auto")

    # 手工构造不一致：寄存器手动、读回文件却是自动。
    # 不能用 render()，那只会把文件刷成与寄存器一致。
    ec3.mode_register = 1
    ec3.pwm = 120
    (ec3.dir / "pwm1_enable").write_text("2\n", encoding="utf-8")
    (ec3.dir / "pwm1").write_text("120\n", encoding="utf-8")

    check(
        "测试夹具奏效：读回说是自动、寄存器其实还在手动",
        (ec3.dir / "pwm1_enable").read_text(encoding="utf-8").strip() == "2"
        and ec3.mode_register == 1,
        f"读回={(ec3.dir / 'pwm1_enable').read_text(encoding='utf-8').strip()} "
        f"寄存器={ec3.mode_register}",
    )

    plugin3._restore_sync()
    check(
        "读回滞后时启动恢复仍然真的把 EC 写回自动（不凭一次读回早退）",
        ec3.mode_register == 0,
        f"寄存器={ec3.mode_register}",
    )
    check(
        "读回滞后被纠正后报告如实说明风扇已在 EC 手上",
        any("已确认处于 EC 自动控制" in line for line in plugin3._restore_report),
        str(plugin3._restore_report),
    )

    # ---- ②-c 交还失败时，不管原本是不是自动都要如实报错 ----
    # 修掉早退之后，"本来就在自动"这条分支也会走交还；若交还失败却因为
    # 读回是自动而报"已确认处于自动控制"，就是在撒谎。
    reset_settings()
    tmp4 = Path(tempfile.mkdtemp(prefix="f1pro-14e-"))
    battery4, kernel4 = make_battery(tmp4)
    hwmon4, ec4, _ = make_fan(tmp4, temp_c=55.0, pwm=120)
    plugin4 = Plugin()
    install_fake_sysfs(tmp4, kernel4, [battery4], [], hwmon4, ec4)
    plugin4._update_setting("fan_mode", "auto")

    ec4.mode_register = 1
    ec4.pwm = 120
    (ec4.dir / "pwm1_enable").write_text("2\n", encoding="utf-8")  # 读回滞后的假自动

    plugin4._fan_handover_to_ec = lambda: False  # 构造"交还没成功"

    plugin4._restore_sync()
    check(
        "交还失败时如实报告未确认生效（不得因为读回是自动就写成已确认）",
        any("未确认生效" in line for line in plugin4._restore_report),
        str(plugin4._restore_report),
    )
    check(
        "交还失败时不会误报成已交还（措辞与实际情况对应）",
        not any("已确认处于 EC 自动控制" in line for line in plugin4._restore_report),
        str(plugin4._restore_report),
    )

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(tmp_b, ignore_errors=True)
    shutil.rmtree(tmp2, ignore_errors=True)
    shutil.rmtree(tmp3, ignore_errors=True)
    shutil.rmtree(tmp4, ignore_errors=True)


def test_fan_unconfirmed_control_paths():
    """15. 控制权无法确认、PWM 读回与目标不符 —— 两条必须汇报失败的路径。

    这两条都是由离线模拟发现的真缺陷（不是假想的 edge case）：

    ① ``_fan_is_auto()`` 只看 ``== 2``。当 ``pwm1_enable`` **读不回来**
       （读 OSError、内容为空、值非数字 → ``None``）时它返回 ``False``，
       于是"夺回手动控制权"那一步被整段跳过，代码认为仍在手动，接着就去写
       ``pwm1``。可 EC 寄存器里可能根本不是手动模式 —— 真机上这次写入**不生效**，
       但控制线程认为成功了，界面显示手动、温度也在读，风扇却纹丝不动。
       正确做法是把"读不到"和"确认是手动"区分开：读不到时不能当作已确认。

    ② 写 ``pwm1`` 之后只检查"读得到"，**不检查读回值等于目标**。
       目标 115、读回 40 照样返回 40 当成功（离线已复现）。
       死区逻辑依赖"读回值 = 上一次目标"来抑制抖动，读回不可信时
       死区的判断基础也就没了。

    注意这两条与 "写后读回滞后"（``stale_reads``）**不是同一件事**：
    滞后是指读回还是旧值但最终会收敛，这里是**读回本身就不可用/与目标不符**。
    """
    section("15. 控制权无法确认 / PWM 读回与目标不符 必须汇报失败")

    # ---- ① pwm1_enable 读不回来时不得直接写 pwm1 ----
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-15a-"))
    battery, kernel = make_battery(tmp)
    hwmon, ec, temp_dir = make_fan(tmp, temp_c=65.0, pwm=100)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [], hwmon, ec)

    asyncio.run(plugin.set_fan_mode("balanced"))
    plugin._stop_fan_controller()
    check("前置：已进入手动控制", ec.mode_register == 1, str(ec.mode_register))

    # 让 pwm1_enable **持续读失败**。注意不能只清空一次内容：
    # 写入 pwm1_enable 会覆盖文件、读回立刻变正常，那样测的是"暂时读不到"，
    # 不是用户描述的"无法确认手动状态"。真机上读失败通常来自驱动/EC 侧，
    # 不会因为我们写了一次就自愈。
    #
    # **不删文件**：删掉会让 _fan_node 找不到节点、被门禁拦下，
    # 那是另一条路径（第 9 节已覆盖），不是本条要测的情形。
    enable_path = ec.dir / "pwm1_enable"
    enable_path.write_text("\n", encoding="utf-8")
    check("前置：pwm1_enable 确实读回 None", plugin._fan_enable_raw() is None, "未复现读回失败")

    # 把这一层包起来，让读回在这个测试段内**始终**不可用。
    real_read_or_none = plugin._read_or_none

    def stubborn_read(path):
        if Path(path) == enable_path:
            return None
        return real_read_or_none(path)

    plugin._read_or_none = stubborn_read

    ec.pwm = 40
    (ec.dir / "pwm1").write_text("40\n", encoding="utf-8")
    set_temp(temp_dir, 65.0)
    target = plugin._interpolate_curve(plugin._get_fan_curve("balanced"), 65.0)
    check("前置：曲线目标与当前读回差得足够大（死区不会拦下）", target == 138, str(target))

    before_pwm = ec.pwm
    try:
        plugin._fan_step()
        raised = None
    except Exception as exc:  # noqa: BLE001
        raised = f"{type(exc).__name__}: {exc}"
    check(
        "pwm1_enable 读不回来时拒绝写 pwm1（不能把「读不到」当成「已确认手动」）",
        ec.pwm == before_pwm,
        f"写入前={before_pwm} 现在={ec.pwm}（说明仍然写了）",
    )
    check("该情形必须抛出异常，交给控制线程走 fail-safe", raised is not None, str(raised))

    # 正常情形必须保留：读回值可辨且已是手动 → 照常写。
    # 先撤掉上面那层"持续读失败"的覆盖，否则这里测的就不是正常读回了。
    plugin._read_or_none = real_read_or_none
    (ec.dir / "pwm1_enable").write_text("1\n", encoding="utf-8")
    ec.pwm = 40
    (ec.dir / "pwm1").write_text("40\n", encoding="utf-8")
    written = plugin._fan_step()
    check("读回可辨且为手动时仍正常写入 PWM", ec.pwm == 138, str(ec.pwm))
    check("正常写入时返回实际读回值", written == 138, str(written))

    # ---- ② 写入后读回与目标不一致必须算失败 ----
    reset_settings()
    tmp_b = Path(tempfile.mkdtemp(prefix="f1pro-15b-"))
    battery_b, kernel_b = make_battery(tmp_b)
    hwmon_b, ec_b, temp_b = make_fan(tmp_b, temp_c=55.0, pwm=100)
    plugin_b = Plugin()
    install_fake_sysfs(tmp_b, kernel_b, [battery_b], [], hwmon_b, ec_b)

    # 造一条 55°C 恰好给 115 的曲线，把目标钉死在 115。
    asyncio.run(
        plugin_b.set_fan_custom_curve([[40, 115], [50, 115], [60, 115], [70, 200], [80, 255]])
    )
    asyncio.run(plugin_b.set_fan_mode("custom"))
    plugin_b._stop_fan_controller()
    ec_b.pwm = 40
    (ec_b.dir / "pwm1").write_text("40\n", encoding="utf-8")
    set_temp(temp_b, 55.0)
    target_b = plugin_b._interpolate_curve(plugin_b._get_fan_curve("custom"), 55.0)
    check("前置：自定义曲线 55°C 目标为 115", target_b == 115, str(target_b))

    # 让写 pwm1 被**静默丢弃**：EC 侧值不变，读回仍是 40。
    # 这模拟"驱动接受写入但寄存器没变"（EC 忙 / 模式被外部改掉 / 驱动 bug）。
    ec_b.ignored_writes.add("pwm1")
    try:
        result = plugin_b._fan_step()
        raised_b = None
    except Exception as exc:  # noqa: BLE001
        result = None
        raised_b = f"{type(exc).__name__}: {exc}"

    check(
        "读回值与目标不一致时不得当作成功返回",
        result != 40,
        f"返回={result}（目标 {target_b}，读回 {ec_b.pwm} —— 返回读回值即算漏判）",
    )
    check(
        "读回值与目标不一致时必须抛异常",
        raised_b is not None,
        f"异常={raised_b!r}，返回={result!r}",
    )
    check("该情形下 EC 寄存器确实没变（夹具构造正确）", ec_b.pwm == 40, str(ec_b.pwm))

    # 读回一致时仍返回读回值（不能为了修 ② 把正常路径也判成失败）。
    ec_b.ignored_writes.discard("pwm1")
    ok_result = plugin_b._fan_step()
    check("读回一致时写入正常生效", ec_b.pwm == 115, str(ec_b.pwm))
    check("读回一致时返回读回值", ok_result == 115, str(ok_result))

    # 安全保护路径同样要过"读回 == 目标"的确认（不能只在曲线路径上加）。
    ec_b.ignored_writes.add("pwm1")
    set_temp(temp_b, 90.0)
    try:
        plugin_b._fan_step()
        raised_safety = None
    except Exception as exc:  # noqa: BLE001
        raised_safety = f"{type(exc).__name__}: {exc}"
    check(
        "安全保护写入未生效时同样抛异常（不能只守曲线路径）",
        raised_safety is not None,
        f"异常={raised_safety!r}，pwm={ec_b.pwm}",
    )

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(tmp_b, ignore_errors=True)


def test_fan_transition_serialization():
    """16. 风扇转换必须串行化，且不能丢掉还在跑的控制线程。

    两条都由离线并发探针复现（.mut/repro_fan_concurrency.py）：

    ① **没有统一转换锁**。``_fan_lock`` 原先只在 ``_start_fan_controller()`` 里用，
       保护不了"停止线程 → 取控制权 → 写 enable → 写 pwm1 → 验证 → 存设置 → 启线程"
       这整段转换。两个 RPC（各自跑在 ``asyncio.to_thread`` 的工作线程里）并发时
       会互相穿插，实测写入序列变成：

           W pwm1_enable=1   <- op1 取得手动
           W pwm1_enable=2   <- op2 插进来交还 EC
           W pwm1_enable=1   <- op1 又抢回手动
           W pwm1=138        <- op1 写 PWM

       结果 EC 寄存器停在**手动**、而设置文件存的是 ``auto`` —— 界面与硬件不一致，
       而且下次启动会照 ``auto`` 恢复，等于把风扇悄悄留在手动 PWM 上。

    ② **``_stop_fan_controller()`` 会丢掉还没退出的线程**。它 ``join(timeout=4.0)``
       之后**无条件**把 ``_fan_thread`` 清成 None。线程若因为 sysfs 读写阻塞而仍在跑，
       后续 ``_start_fan_controller()`` 看到的是"没有旧线程"，于是**又起一个** ——
       两个控制线程同时写 ``pwm1``。

    这两条都不是"看起来能行"的问题，是真机上会让风扇抽搐的并发缺陷。
    """
    section("16. 风扇转换串行化 / 控制线程生命周期")

    # ---- ① 并发转换必须互斥 ----
    reset_settings()
    tmp = Path(tempfile.mkdtemp(prefix="f1pro-16a-"))
    battery, kernel = make_battery(tmp)
    hwmon, ec, temp_dir = make_fan(tmp, temp_c=65.0, pwm=100)
    plugin = Plugin()
    install_fake_sysfs(tmp, kernel, [battery], [], hwmon, ec)

    # 记下所有 sysfs 写入的先后顺序，这是判断"是否穿插"的唯一可靠依据。
    trace: List[str] = []
    real_write = plugin._write_node

    def traced_write(path, value, attempts=3):
        trace.append(f"{Path(path).name}={value}")
        return real_write(path, value, attempts)

    plugin._write_node = traced_write

    # 在"手动转换的 enable 已写入、pwm1 还没写"这一刻插入第二个操作。
    # 用屏障把这个时刻**确定化**，不依赖线程调度的运气。
    at_gate = threading.Barrier(2, timeout=10)
    state = {"paused": False, "gated": False}
    real_step = plugin._fan_step

    def gated_step():
        # **只拦第一次**：屏障是给 op1 / op2 这两个 RPC 线程做会合的，
        # op1 随后会启动一个真的控制线程，它也会走到这里。
        # 若让它也进屏障，就变成"控制线程等 op2、op2 却在 join 控制线程"
        # 的循环等待（靠 join 超时才能挣脱，一轮要十几秒）。
        # 那不是被测场景，只是夹具的副作用，所以一次性放行。
        if state["gated"]:
            return real_step()
        state["gated"] = True
        state["paused"] = True
        at_gate.wait()  # 等 op2 抢进来
        return real_step()

    plugin._fan_step = gated_step

    results: Dict[str, Any] = {}

    # 判据一（本质判据）：**两个转换的执行区间不得重叠**。
    #
    # 不能拿"线程启动/结束"的时刻当区间——那测的是线程调度，不是互斥：
    # op2 可能在屏障处就被创建，于是区间**看起来**重叠，而实际写入是串行的。
    # 正确做法是在**转换真正持有锁的时刻**采样：谁发现了"另一个转换正在进行"，
    # 就说明两者同时在转换区内。`_fan_transition_active` 就是这个用途，
    # 它在实现里随转换锁一起置位/清除（见 main.py 的 _fan_transition_lock）。
    inside = []
    real_step_probe = plugin._fan_step

    def probe_step():
        # _fan_step 只在转换内部被调用，此刻若标志已置位，
        # 说明我们正处在某个转换之中——记录"同时有几个转换在跑"。
        if plugin._fan_transition_active:
            inside.append(1)
        return real_step_probe()

    plugin._fan_step = probe_step

    def op1():
        results["manual"] = plugin._set_fan_mode_sync("balanced")

    def op2():
        deadline = time.monotonic() + 8
        while not state["paused"] and time.monotonic() < deadline:
            time.sleep(0.005)
        at_gate.wait()
        results["handover"] = plugin._set_fan_mode_sync("auto")

    t1 = threading.Thread(target=op1)
    t2 = threading.Thread(target=op2)
    t1.start()
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)

    plugin._fan_step = real_step
    plugin._write_node = real_write

    # 采样点都落在"某个转换内部"（probe_step 只在 _fan_step 里被调用）。
    # 若互斥正确，每个采样点看到的标志必然为 True —— 不会出现"另一个转换
    # 正在跑时我又进了转换区"。这里断言的是**标志在整个并发过程中被正确置位**，
    # 真正的互斥证据在下面那条：写入序列不得交错。
    check(
        "转换标志确实随转换置位（否则下面那条断言会空转）",
        len(inside) > 0,
        f"采样到 {len(inside)} 次",
    )

    # 判据二（核心）：两个转换的 sysfs 写入必须形成**不相交的两段**。
    #
    # 互斥正确时，"交还 EC"那次 enable=2 要么全在手动转换的写入之前/之后，
    # 绝不会夹在"手动转换的 enable=1"与"手动转换的 pwm1"中间——
    # 因为手动转换的这两次写入是同一段临界区，中途别的转换进不来。
    #
    # 这比"数写入次数"强：次数可能因为重试而天然变化；而"是否夹进"是结构性的。
    # 必须先验证写入确实发生了（否则下面的循环一次都不进，断言空转）。
    seq = list(trace)
    check(
        "前置：确实记录到了写入（否则下面的交错判据会空转）",
        len(seq) >= 3,
        f"写入序列={trace}",
    )

    interleaved = False
    for i, item in enumerate(seq):
        if item != "pwm1_enable=1":
            continue
        # 从这次"取手动"往后找它自己的 pwm1
        for j in range(i + 1, len(seq)):
            if seq[j].startswith("pwm1_enable"):
                # 还没写 pwm1 就又出现了 enable 写入 -> 两段被交错
                interleaved = True
                break
            if seq[j].startswith("pwm1="):
                break  # 正常的 enable -> pwm1
    check(
        "取手动与写 pwm1 之间不得夹进别的转换（互斥的结构性证据）",
        not interleaved,
        f"写入序列={trace}",
    )

    # 判据三：硬件状态与设置文件必须一致（这是**用户可见**的后果）。
    # 注意不能只测一次——并发的结果依赖调度，单次可能碰巧一致，
    # 那样断言就是空转的。这里重复造几轮，任何一轮不一致就失败。
    #
    # 单轮部分起过控制线程，先收掉：它若还在跑会继续写设置文件，
    # 下一轮的 reset_settings() 就会撞上 Windows 的文件占用（PermissionError）。
    plugin._stop_fan_controller()

    mismatch_rounds = []
    for round_index in range(6):
        reset_settings()
        tmp_r = Path(tempfile.mkdtemp(prefix=f"f1pro-16a-r{round_index}-"))
        battery_r, kernel_r = make_battery(tmp_r)
        hwmon_r, ec_r, temp_r = make_fan(tmp_r, temp_c=65.0, pwm=100 + round_index)
        plugin_r = Plugin()
        install_fake_sysfs(tmp_r, kernel_r, [battery_r], [], hwmon_r, ec_r)

        at_gate_r = threading.Barrier(2, timeout=10)
        paused_r = {"v": False, "gated": False}
        real_step_r = plugin_r._fan_step

        def gated_step_r(_p=paused_r, _g=at_gate_r, _s=real_step_r):
            # 与上面同理：只拦第一次（op1 的那一步），
            # op1 起的控制线程必须正常跑，否则会与控制线程的 join 形成循环等待。
            if _p["gated"]:
                return _s()
            _p["gated"] = True
            _p["v"] = True
            _g.wait()
            return _s()

        plugin_r._fan_step = gated_step_r
        r_out: Dict[str, Any] = {}

        def op1_r(_p=plugin_r, _o=r_out):
            _o["manual"] = _p._set_fan_mode_sync("balanced")

        def op2_r(_p=plugin_r, _o=r_out, _s=paused_r, _g=at_gate_r):
            deadline = time.monotonic() + 8
            while not _s["v"] and time.monotonic() < deadline:
                time.sleep(0.005)
            _g.wait()
            _o["handover"] = _p._set_fan_mode_sync("auto")

        ta = threading.Thread(target=op1_r)
        tb = threading.Thread(target=op2_r)
        ta.start()
        tb.start()
        ta.join(timeout=20)
        tb.join(timeout=20)
        plugin_r._fan_step = real_step_r
        # 收尾：停掉这一轮起的控制线程，否则它会继续写设置文件，
        # 下一轮 reset_settings() 会撞上 Windows 的文件占用（PermissionError）。
        plugin_r._stop_fan_controller()

        saved = plugin_r._load_settings().get("fan_mode")
        if ec_r.mode_register == 0 and saved != "auto":
            mismatch_rounds.append(f"第{round_index}轮 EC=自动 但设置={saved!r}")
        if ec_r.mode_register == 1 and saved in (None, "auto"):
            mismatch_rounds.append(f"第{round_index}轮 EC=手动 但设置={saved!r}")
        shutil.rmtree(tmp_r, ignore_errors=True)

    check(
        "重复 6 轮并发后硬件模式始终与设置文件一致",
        not mismatch_rounds,
        str(mismatch_rounds),
    )

    # ---- 单线程下两条路径仍要正常工作（别为了加锁把功能锁死）----
    reset_settings()
    tmp_ok = Path(tempfile.mkdtemp(prefix="f1pro-16a2-"))
    battery_ok, kernel_ok = make_battery(tmp_ok)
    hwmon_ok, ec_ok, temp_ok = make_fan(tmp_ok, temp_c=65.0, pwm=100)
    plugin_ok = Plugin()
    install_fake_sysfs(tmp_ok, kernel_ok, [battery_ok], [], hwmon_ok, ec_ok)

    rpc_manual = plugin_ok._set_fan_mode_sync("balanced")
    plugin_ok._stop_fan_controller()
    check("串行化后切手动仍成功", rpc_manual["ok"] is True, str(rpc_manual)[:160])
    check("串行化后确实进入手动", ec_ok.mode_register == 1, str(ec_ok.mode_register))
    rpc_auto = plugin_ok._set_fan_mode_sync("auto")
    check("串行化后切回自动仍成功", rpc_auto["ok"] is True, str(rpc_auto)[:160])
    check("串行化后确实交还 EC", ec_ok.mode_register == 0, str(ec_ok.mode_register))

    # 重复调用同一模式不得死锁（RLock 可重入是这里的前提）。
    repeat = [plugin_ok._set_fan_mode_sync("auto")["ok"] for _ in range(3)]
    check("同一转换重复执行不会死锁", all(repeat), str(repeat))

    # ---- ② 停止超时后不得丢掉仍存活的线程 ----
    reset_settings()
    tmp_b = Path(tempfile.mkdtemp(prefix="f1pro-16b-"))
    battery_b, kernel_b = make_battery(tmp_b)
    hwmon_b, ec_b, temp_b = make_fan(tmp_b, temp_c=65.0, pwm=100)
    plugin_b = Plugin()
    install_fake_sysfs(tmp_b, kernel_b, [battery_b], [], hwmon_b, ec_b)

    # 造一个"忽略 stop_event、迟迟不退出"的控制线程，
    # 模拟真实硬件上 sysfs 读写阻塞。**不替换被测函数**：线程是真的。
    released = threading.Event()
    entered = threading.Event()

    def stuck_loop():
        entered.set()
        released.wait(timeout=20)  # 故意不看 stop_event

    stuck = threading.Thread(target=stuck_loop, name="StuckFanController", daemon=True)
    stuck.start()
    entered.wait(timeout=5)
    plugin_b._fan_thread = stuck

    # 缩短 join 容忍时间，让探针不必真等 4 秒（只影响这次 join，不改源码常量）。
    real_join = stuck.join

    def quick_join(timeout=None):
        return real_join(timeout=0.3)

    stuck.join = quick_join

    plugin_b._stop_fan_controller()

    check(
        "停止超时后线程仍在跑（前置条件成立，否则下面的断言空转）",
        stuck.is_alive(),
        f"is_alive={stuck.is_alive()}",
    )
    check(
        "停止超时后不得丢掉仍存活的线程引用（否则会重复启动）",
        plugin_b._fan_thread is stuck,
        f"_fan_thread={plugin_b._fan_thread!r}",
    )

    # 此时尝试启动新线程：必须被拒绝，绝不能出现两个控制线程。
    plugin_b._start_fan_controller()
    new_thread = plugin_b._fan_thread
    check(
        "旧线程未退出时拒绝启动新控制线程",
        new_thread is stuck,
        f"启动后 _fan_thread={new_thread!r}（应仍是那个卡住的线程）",
    )
    check(
        "不得出现两个并存且都在跑的控制线程",
        not (stuck.is_alive() and new_thread is not stuck and new_thread.is_alive()),
        f"stuck.alive={stuck.is_alive()} new={new_thread!r}",
    )

    # 旧线程真正退出后，必须能正常启动新的（不能永久锁死）。
    released.set()
    stuck.join(timeout=5)
    check("前置：旧线程已退出", not stuck.is_alive(), f"is_alive={stuck.is_alive()}")
    plugin_b._start_fan_controller()
    started = plugin_b._fan_thread
    check(
        "旧线程退出后可以正常启动新控制线程",
        started is not None and started is not stuck and started.is_alive(),
        f"_fan_thread={started!r}",
    )
    plugin_b._stop_fan_controller()

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(tmp_ok, ignore_errors=True)
    shutil.rmtree(tmp_b, ignore_errors=True)


def test_fan_unload_and_start_failure():
    """17. 卸载要与转换串行化；启动线程失败必须算转换失败。

    两条都由离线探针复现（.mut/repro_fan_unload.py）：

    ① **交还之后，在途的那一步仍会执行**。``_stop_fan_controller()`` 超时返回后，
       一个已经开始的 ``_fan_step()`` 会继续往下走。它读回 ``pwm1_enable``
       发现"不是手动"（因为交还刚把 EC 写回自动），于是走进**夺回控制权**的分支，
       把 ``pwm1_enable`` 又写回 1 —— 探针实测：卸载已经返回、EC 也曾交还自动，
       风扇随后却停在手动 PWM 上（``mode_register=1``）。
       同时 ``_unload()`` 原先不在转换锁里，让这个交错变得更容易发生。

    ② **``_fan_apply_manual()`` 忽略 ``_start_fan_controller()`` 的返回值**。
       ``_start_fan_controller()`` 已经会正确返回 False（旧线程没停干净时拒绝
       启动第二个线程，这是对的），但 ``_fan_apply_manual()`` 照样返回 True。
       于是 RPC 得到 ``ok=True``、设置与 EC 都是手动模式，**却没有任何控制循环**：
       风扇不跟温度走、85°C 保护也失效 —— 比直接交还 EC 危险得多。

    ③ **交还必须如实报告"线程没停干净"**。``_fan_handover_to_ec()`` 的返回值是
       ``confirmed and stopped``：停止超时时仍然写出 ``pwm1_enable = 2``，
       但必须报失败。只测 ``_set_fan_mode_sync`` 的 ok 是**测不到这一条的** ——
       那条走的是 ``_fan_apply_manual`` 返回 False 的分支，与交还的返回值无关。
    """
    section("17. 卸载串行化 / 启动失败必须算转换失败")

    # ---- ① 交还之后，在途的那一步必须整体作废 ----
    #
    # 两个分支走的是**不同的写入点**，必须分别构造：
    #   a) 交还落在"还没确认手动"时 → 不得把 pwm1_enable 抢回手动；
    #   b) 交还落在"已确认手动、还没写 pwm1"时 → 不得再写 pwm1。
    for label, pause_at in (
        ("交还落在未确认手动时", "enable"),
        ("交还落在已确认手动、未写 PWM 时", "temp"),
    ):
        reset_settings()
        tmp = Path(tempfile.mkdtemp(prefix="f1pro-17a-"))
        battery, kernel = make_battery(tmp)
        hwmon, ec, temp_dir = make_fan(tmp, temp_c=65.0, pwm=100)
        plugin = Plugin()
        install_fake_sysfs(tmp, kernel, [battery], [], hwmon, ec)

        plugin._update_setting("fan_mode", "balanced")
        # 先把 EC 置于手动，让步骤走到"已确认手动"的路径上。
        plugin._write_node(plugin._fan_node(plugin.FAN_ENABLE_FILE), "1")

        writes: List[str] = []
        real_write = plugin._write_node

        def traced_write(path, value, attempts=3):
            writes.append(f"{Path(path).name}={value}")
            return real_write(path, value, attempts)

        plugin._write_node = traced_write

        # 在步骤中途"让交还发生"。走**真实的** _stop_fan_controller()：
        # 它才是"取走控制权"的入口（世代推进就发生在它里面），
        # 不直接改内部计数器，避免把被测实现的一部分从测试里拿掉。
        #
        # 只触发一次：夺回控制权的那段代码会**反复**读 pwm1_enable，
        # 若每次都把 EC 重置成自动，就成了"永远确认不了手动"的死循环，
        # 测的也不是我们要测的那一件事了。
        fired = threading.Event()

        def handover_now():
            if fired.is_set():
                return
            fired.set()
            plugin._stop_fan_controller()
            ec.mode_register = 0  # 交还 EC：寄存器回到自动
            ec.pwm = 100
            ec.render()

        if pause_at == "enable":
            real_hook = plugin._fan_enable_raw

            def hook_with_handover():
                handover_now()
                return real_hook()

            plugin._fan_enable_raw = hook_with_handover
            restore = lambda p=plugin, r=real_hook: setattr(p, "_fan_enable_raw", r)  # noqa: E731
        else:
            real_hook = plugin._read_fan_temperature

            def hook_with_handover():  # noqa: F811 - 两个分支互斥，复用名字便于还原
                handover_now()
                return real_hook()

            plugin._read_fan_temperature = hook_with_handover
            restore = lambda p=plugin, r=real_hook: setattr(p, "_read_fan_temperature", r)  # noqa: E731

        # 未修复时这里可能直接抛异常（它真的去写了 PWM，而 EC 已是自动、
        # 写 pwm1 不生效 → 读回不符）。捕获下来交给 check 判定，
        # 让它**干净地报 FAIL**，而不是让整个测试崩掉。
        try:
            result = plugin._fan_step()
        except Exception as exc:  # noqa: BLE001
            result = f"抛异常：{exc}"
        restore()
        plugin._write_node = real_write

        check(
            f"{label}：这一步必须整体作废，不得再写任何节点",
            result is None and writes == [],
            f"result={result!r} 写入={writes}",
        )

    # 反向对照：没有被交还打断时，同样的步骤必须照常写入 ——
    # 否则"作废"会把正常路径也一并拦死，那等于把风扇控制整个关掉。
    reset_settings()
    tmp_ok = Path(tempfile.mkdtemp(prefix="f1pro-17a2-"))
    battery_ok, kernel_ok = make_battery(tmp_ok)
    hwmon_ok, ec_ok, temp_dir_ok = make_fan(tmp_ok, temp_c=65.0, pwm=100)
    plugin_ok = Plugin()
    install_fake_sysfs(tmp_ok, kernel_ok, [battery_ok], [], hwmon_ok, ec_ok)

    plugin_ok._update_setting("fan_mode", "balanced")
    writes_ok: List[str] = []
    real_write_ok = plugin_ok._write_node

    def traced_write_ok(path, value, attempts=3):
        writes_ok.append(f"{Path(path).name}={value}")
        return real_write_ok(path, value, attempts)

    plugin_ok._write_node = traced_write_ok
    result_ok = plugin_ok._fan_step()
    plugin_ok._write_node = real_write_ok

    check(
        "未被交还打断时这一步照常写入（作废守卫不得误伤正常路径）",
        result_ok is not None and any(w.startswith("pwm1=") for w in writes_ok),
        f"result={result_ok!r} 写入={writes_ok}",
    )

    # ---- ② 卸载必须与转换串行化 ----
    #
    # 判据：另一个线程正持有转换锁（= 一次转换正在进行）时，卸载**不得**
    # 抢先去写 EC。旧实现不在锁里，会立刻写完就返回，正是"交错"的来源。
    reset_settings()
    tmp_b = Path(tempfile.mkdtemp(prefix="f1pro-17b-"))
    battery_b, kernel_b = make_battery(tmp_b)
    hwmon_b, ec_b, temp_dir_b = make_fan(tmp_b, temp_c=65.0, pwm=100)
    plugin_b = Plugin()
    install_fake_sysfs(tmp_b, kernel_b, [battery_b], [], hwmon_b, ec_b)

    writes_b: List[str] = []
    real_write_b = plugin_b._write_node

    def traced_write_b(path, value, attempts=3):
        writes_b.append(f"{Path(path).name}={value}")
        return real_write_b(path, value, attempts)

    plugin_b._write_node = traced_write_b
    # 缩短等待，避免测试为了等超时白跑太久（不改被测语义）。
    # 注意这个值同时是"取转换锁的等待上限"，所以要**比下面的采样时刻长**，
    # 否则卸载已经退避完、开始写 EC 了，采不到"它还在等锁"这个状态。
    plugin_b.FAN_STOP_TIMEOUT = 2.0

    lock_taken = threading.Event()
    let_go = threading.Event()

    def holder():
        with plugin_b._fan_transition_lock:
            lock_taken.set()
            let_go.wait(timeout=15)

    holder_thread = threading.Thread(target=holder, daemon=True)
    holder_thread.start()
    lock_taken.wait(timeout=5)
    check("前置：另一个线程已持有转换锁", lock_taken.is_set(), f"taken={lock_taken.is_set()}")

    unload_done = threading.Event()

    def run_unload():
        asyncio.run(plugin_b._unload())
        unload_done.set()

    unload_thread = threading.Thread(target=run_unload, daemon=True)
    unload_thread.start()

    # 锁被占着：卸载此刻必须还在等锁，一个字都不能写。
    # 采样时刻要明显早于上面那个取锁超时（2.0s），否则采到的是"已经退避完"。
    time.sleep(0.5)
    check(
        "转换锁被占用时，卸载不得抢先去写 EC（否则会与转换交错）",
        writes_b == [] and not unload_done.is_set(),
        f"写入={writes_b} 卸载已返回={unload_done.is_set()}",
    )

    let_go.set()
    unload_thread.join(timeout=20)
    holder_thread.join(timeout=5)

    check("锁释放后卸载能正常完成", unload_done.is_set(), f"done={unload_done.is_set()}")
    check(
        "卸载完成后 EC 停在自动",
        ec_b.mode_register == 0,
        f"mode_register={ec_b.mode_register}（0=自动 1=手动）",
    )

    # ---- ③ 真实交错：在途的一步压在锁上，卸载退避后仍须把 EC 交还 ----
    reset_settings()
    tmp_c = Path(tempfile.mkdtemp(prefix="f1pro-17c-"))
    battery_c, kernel_c = make_battery(tmp_c)
    hwmon_c, ec_c, temp_dir_c = make_fan(tmp_c, temp_c=65.0, pwm=100)
    plugin_c = Plugin()
    install_fake_sysfs(tmp_c, kernel_c, [battery_c], [], hwmon_c, ec_c)

    plugin_c._update_setting("fan_mode", "balanced")
    plugin_c._write_node(plugin_c._fan_node(plugin_c.FAN_ENABLE_FILE), "1")
    plugin_c.FAN_STOP_TIMEOUT = 0.5

    step_entered = threading.Event()
    let_step_finish = threading.Event()
    real_enable_raw_c = plugin_c._fan_enable_raw

    def slow_enable_raw():
        step_entered.set()
        let_step_finish.wait(timeout=20)
        return real_enable_raw_c()

    plugin_c._fan_enable_raw = slow_enable_raw

    # 扮演"控制线程的一步"：拿转换锁 → _fan_step()（与 _fan_control_loop 一致）。
    def control_step():
        with plugin_c._fan_transition_lock:
            plugin_c._fan_step()

    step_thread = threading.Thread(target=control_step, daemon=True)
    step_thread.start()
    step_entered.wait(timeout=5)
    check("前置：在途的一步已进入（否则下面的交错不会发生）",
          step_entered.is_set(), f"entered={step_entered.is_set()}")

    writes_c: List[str] = []
    real_write_c = plugin_c._write_node

    def traced_write_c(path, value, attempts=3):
        writes_c.append(f"{Path(path).name}={value}")
        return real_write_c(path, value, attempts)

    plugin_c._write_node = traced_write_c

    asyncio.run(plugin_c._unload())
    step_after_unload = list(writes_c)

    # 卸载已返回：此刻放开那一步，它**不得**再把控制权抢回手动。
    let_step_finish.set()
    step_thread.join(timeout=15)

    plugin_c._fan_enable_raw = real_enable_raw_c
    plugin_c._write_node = real_write_c

    check(
        "卸载返回时 EC 已交还自动",
        ec_c.mode_register == 0,
        f"mode_register={ec_c.mode_register}（写入序列 {step_after_unload}）",
    )
    check(
        "卸载返回后，在途的那一步恢复执行也不得把控制权抢回手动",
        ec_c.mode_register == 0,
        f"mode_register={ec_c.mode_register}（卸载后写入 {writes_c}）",
    )
    check(
        "该步骤不得往 EC 里补写 pwm1_enable（否则等于从自动又切回手动）",
        not any(w == "pwm1_enable=1" for w in writes_c),
        f"写入={writes_c}",
    )

    # ---- ④ 启动线程失败必须算转换失败 ----
    reset_settings()
    tmp_d = Path(tempfile.mkdtemp(prefix="f1pro-17d-"))
    battery_d, kernel_d = make_battery(tmp_d)
    hwmon_d, ec_d, temp_dir_d = make_fan(tmp_d, temp_c=65.0, pwm=100)
    plugin_d = Plugin()
    install_fake_sysfs(tmp_d, kernel_d, [battery_d], [], hwmon_d, ec_d)

    # 造一个"卡住不退出"的旧线程，并让 join 快速超时。
    # stop_event 也要置位 —— 这代表"已经要求它停、它没停"，
    # 也就是用户描述里的"停线程超时"状态（此时 _start_fan_controller 必拒绝）。
    released = threading.Event()
    entered = threading.Event()

    def stuck_loop():
        entered.set()
        released.wait(timeout=20)  # 故意不看 stop_event，模拟阻塞在 I/O 里

    stuck = threading.Thread(target=stuck_loop, name="StuckFanController", daemon=True)
    stuck.start()
    entered.wait(timeout=5)
    plugin_d._fan_thread = stuck
    plugin_d._fan_stop_event.set()

    real_join = stuck.join

    def quick_join(timeout=None):
        return real_join(timeout=0.3)

    stuck.join = quick_join
    plugin_d.FAN_STOP_TIMEOUT = 0.3

    # 前置：这种状态下启动确实会被拒绝（否则下面的断言会空转）。
    start_ok = plugin_d._start_fan_controller()
    check(
        "前置：停线程超时后启动新控制线程被拒绝",
        start_ok is False,
        f"_start_fan_controller()={start_ok!r}",
    )

    result_d = plugin_d._set_fan_mode_sync("balanced")

    stuck.join = real_join

    running = plugin_d._fan_thread
    has_loop = running is not None and running.is_alive() and running is not stuck
    check(
        "不得出现「报告成功但没有可用控制循环」",
        not (bool(result_d.get("ok")) and not has_loop),
        f"ok={result_d.get('ok')!r} 有控制循环={has_loop}",
    )
    check(
        "停线程超时后切手动必须报告失败（不得 ok=True）",
        result_d.get("ok") is False,
        f"返回 ok={result_d.get('ok')!r}（应为 False）",
    )
    check(
        "切手动失败后必须把 EC 交还自动（不能留在无人控制的手动 PWM 上）",
        ec_d.mode_register == 0,
        f"mode_register={ec_d.mode_register}（0=自动 1=手动）",
    )
    check(
        "切手动失败后保存的模式必须回到 auto",
        plugin_d._load_settings().get("fan_mode") == "auto",
        f"fan_mode={plugin_d._load_settings().get('fan_mode')!r}",
    )

    released.set()
    stuck.join(timeout=5)
    plugin_d._fan_thread = None

    # ---- ⑤ 交还必须如实报告"线程没停干净" ----
    #
    # ``_fan_handover_to_ec()`` 的返回值是 ``confirmed and stopped``：停止超时时
    # **仍然写出** pwm1_enable=2（EC 留在手动且无人控制更危险），但必须**如实报失败**，
    # 否则调用方（``_fan_fail_safe`` / ``_unload`` / 启动恢复）会把"线程还在跑"
    # 当成"干净交还"，据此更新设置、界面显示成功。
    #
    # 这条必须**单独构造**：④ 测的是 ``_set_fan_mode_sync`` 的 ok，走的是
    # ``_fan_apply_manual`` 返回 False 的分支，与交还的返回值**无关** ——
    # 把 ``return confirmed and stopped`` 改回 ``return confirmed`` 时 ④ 照样绿
    # （变异验证时实测逃逸过一次，正是这条缺口）。
    reset_settings()
    tmp_e = Path(tempfile.mkdtemp(prefix="f1pro-17e-"))
    battery_e, kernel_e = make_battery(tmp_e)
    hwmon_e, ec_e, temp_dir_e = make_fan(tmp_e, temp_c=65.0, pwm=100)
    plugin_e = Plugin()
    install_fake_sysfs(tmp_e, kernel_e, [battery_e], [], hwmon_e, ec_e)

    # EC 先置于手动，交还才有可观察的效果。
    plugin_e._write_node(plugin_e._fan_node(plugin_e.FAN_ENABLE_FILE), "1")
    check(
        "前置：交还前 EC 处于手动",
        ec_e.mode_register == 1,
        f"mode_register={ec_e.mode_register}",
    )

    released_e = threading.Event()
    entered_e = threading.Event()

    def stuck_loop_e():
        entered_e.set()
        released_e.wait(timeout=20)  # 故意不看 stop_event，模拟阻塞在 I/O 里

    stuck_e = threading.Thread(target=stuck_loop_e, name="StuckFanControllerE", daemon=True)
    stuck_e.start()
    entered_e.wait(timeout=5)
    plugin_e._fan_thread = stuck_e
    plugin_e.FAN_STOP_TIMEOUT = 0.3

    handed_e = plugin_e._fan_handover_to_ec()

    check(
        "线程没停干净时交还仍须把 EC 写回自动（不能因为停不掉就干脆不交还）",
        ec_e.mode_register == 0,
        f"mode_register={ec_e.mode_register}",
    )
    check(
        "线程没停干净时交还必须返回 False（如实报失败，不能假装干净）",
        handed_e is False,
        f"_fan_handover_to_ec()={handed_e!r}",
    )

    released_e.set()
    stuck_e.join(timeout=5)
    plugin_e._fan_thread = None

    # 反向对照：线程能正常停下时交还必须报成功 —— 否则上面那条会被
    # "永远返回 False" 的坏实现骗过去（那种实现下 EC 根本交还不回去）。
    reset_settings()
    tmp_f = Path(tempfile.mkdtemp(prefix="f1pro-17f-"))
    battery_f, kernel_f = make_battery(tmp_f)
    hwmon_f, ec_f, temp_dir_f = make_fan(tmp_f, temp_c=65.0, pwm=100)
    plugin_f = Plugin()
    install_fake_sysfs(tmp_f, kernel_f, [battery_f], [], hwmon_f, ec_f)
    plugin_f._write_node(plugin_f._fan_node(plugin_f.FAN_ENABLE_FILE), "1")

    handed_f = plugin_f._fan_handover_to_ec()
    check(
        "没有滞留线程时交还必须报成功且 EC 回到自动",
        handed_f is True and ec_f.mode_register == 0,
        f"_fan_handover_to_ec()={handed_f!r} mode_register={ec_f.mode_register}",
    )

    for path in (tmp, tmp_ok, tmp_b, tmp_c, tmp_d, tmp_e, tmp_f):
        shutil.rmtree(path, ignore_errors=True)


def test_fan_unload_cancels_rpc_and_curve_start_failure():
    """18. 「取走控制权」必须取消整个 RPC 转换；保存曲线也要检查启线程结果。

    两条都是 v0.6.10 之后由用户复查指出、并先用离线探针复现的
    （.mut/repro_fan_leftovers.py；修好前 probe_f / probe_g 均报"复现"）。

    ① **卸载取锁超时后，持锁的那个 RPC 仍会把控制权抢回手动**。
       世代号只能作废"在途的一步"（``_fan_step``），挡不住 RPC 转换的**后续步骤**：
       ``_fan_apply_manual()`` 在 ``_fan_step()`` 之后还会 ``_start_fan_controller()`` 并
       ``return True``。探针实测：卸载已经返回、当时 EC 是自动，随后那个 RPC
       恢复执行，EC 变回手动（``mode_register=1``）且 RPC 报 ``ok=True``。
       修法：卸载在**取走控制权那一刻**置位 ``_fan_unloading``，此后
       ``_fan_step()`` 不再自动夺回控制权（交给调用方显式重申请）。

    ② **``_set_fan_custom_curve_sync()`` 没检查 ``_start_fan_controller()``**。
       ``_set_fan_mode_sync()`` 那条路径已经修好了（第 17 节 ④），但保存曲线这条
       仍是"调用后直接返回"。探针实测：旧线程没退出时保存曲线报 ``ok=True``，
       却**没有任何控制循环** —— 风扇不跟温度走、85 °C 保护也失效。
    """
    section("18. 卸载取消在途 RPC / 保存曲线必须检查启线程结果")

    # ---- ① 卸载取锁超时后，持锁 RPC 不得把控制权抢回手动 ----
    reset_settings()
    tmp_a = Path(tempfile.mkdtemp(prefix="f1pro-18a-"))
    battery_a, kernel_a = make_battery(tmp_a)
    hwmon_a, ec_a, temp_dir_a = make_fan(tmp_a, temp_c=65.0, pwm=100)
    plugin_a = Plugin()
    install_fake_sysfs(tmp_a, kernel_a, [battery_a], [], hwmon_a, ec_a)
    isolated_settings(plugin_a, tmp_a)

    plugin_a._update_setting("fan_mode", "balanced")
    # 取锁超时设短一点：本测试要的是"卸载取不到锁、走仍尝试交还的分支"。
    plugin_a.FAN_STOP_TIMEOUT = 0.5

    step_entered = threading.Event()
    let_step_go = threading.Event()

    def slow_step():
        # 卡在 _fan_step() 内部：此刻 RPC 正持着转换锁。
        step_entered.set()
        let_step_go.wait(timeout=20)
        return real_step()

    real_step = plugin_a._fan_step
    plugin_a._fan_step = slow_step

    rpc_result: Dict[str, Any] = {}

    def run_rpc():
        rpc_result.update(plugin_a._set_fan_mode_sync("balanced"))

    rpc_thread = threading.Thread(target=run_rpc, daemon=True)
    rpc_thread.start()
    entered = step_entered.wait(timeout=5)
    check(
        "前置：RPC 已持转换锁并卡在 _fan_step 内（否则不会发生交错）",
        entered,
        True,
    )

    # 卸载在锁被占着时会取锁超时 → 走"仍尝试交还 EC"的分支，随后返回。
    asyncio.run(plugin_a._unload())
    check(
        "卸载返回时 EC 已交还自动",
        ec_a.mode_register == 0,
        f"mode_register={ec_a.mode_register}（0=自动 1=手动）",
    )

    # 放开那个 RPC：它**不得**再把控制权抢回手动。
    let_step_go.set()
    rpc_thread.join(timeout=15)
    plugin_a._fan_step = real_step

    check(
        "卸载返回后，在途的 RPC 恢复执行也不得把 EC 切回手动",
        ec_a.mode_register == 0,
        f"mode_register={ec_a.mode_register}（0=自动 1=手动）",
    )
    check(
        "卸载返回后，在途的 RPC 不得报告成功（它并没有把风扇置于可控状态）",
        rpc_result.get("ok") is False,
        f"返回 ok={rpc_result.get('ok')!r}",
    )
    check(
        "卸载返回后，保存的风扇模式不得停在手动模式",
        plugin_a._load_settings().get("fan_mode") != "balanced",
        f"fan_mode={plugin_a._load_settings().get('fan_mode')!r}",
    )

    teardown_plugins(plugin_a)

    # 反向对照：**没有卸载时**，同样的 RPC 必须能正常切到手动 ——
    # 否则上面几条会被"永远拒绝夺回控制权"的坏实现骗过去（风扇再也切不了手动）。
    reset_settings()
    tmp_b2 = Path(tempfile.mkdtemp(prefix="f1pro-18b-"))
    battery_b2, kernel_b2 = make_battery(tmp_b2)
    hwmon_b2, ec_b2, temp_dir_b2 = make_fan(tmp_b2, temp_c=65.0, pwm=100)
    plugin_b2 = Plugin()
    install_fake_sysfs(tmp_b2, kernel_b2, [battery_b2], [], hwmon_b2, ec_b2)
    isolated_settings(plugin_b2, tmp_b2)

    normal = plugin_b2._set_fan_mode_sync("balanced")
    check(
        "反向对照：没有卸载时切手动必须照常成功（不得把夺权一并禁掉）",
        bool(normal.get("ok")) and ec_b2.mode_register == 1,
        f"ok={normal.get('ok')!r} mode_register={ec_b2.mode_register}",
    )
    check(
        "反向对照：切手动后控制线程必须在跑",
        plugin_b2._fan_thread is not None and plugin_b2._fan_thread.is_alive(),
        f"_fan_thread={plugin_b2._fan_thread!r}",
    )
    teardown_plugins(plugin_b2)

    # ---- ①b 直测：**只有** _fan_unloading 能拦住的夺权场景 ----
    # 上面那几条（①）走的是"卸载推进了世代号"的路径 —— 那一路上
    # ``_fan_step_invalidated(epoch)`` **自己就能拦住**夺权，于是卸载标志
    # 在这个场景里其实是第二重保险。只测这一路会让"删掉卸载标志检查"的
    # 变异悄悄逃逸（本轮 M19 就是这么逃的：删掉 if self._fan_unloading 之后
    # 上面三条行为断言全绿，只有日志计数变红 —— 那属于假绿）。
    #
    # 这里直接把场景压到只剩卸载标志这一道拦阻：EC 是自动、``_fan_epoch``
    # 保持当前值（不推进，于是世代号判据为"有效"），只把 ``_fan_unloading``
    # 置位，然后驱动一次 ``_fan_step()``。
    reset_settings()
    tmp_b3 = Path(tempfile.mkdtemp(prefix="f1pro-18b3-"))
    battery_b3, kernel_b3 = make_battery(tmp_b3)
    hwmon_b3, ec_b3, temp_dir_b3 = make_fan(tmp_b3, temp_c=65.0, pwm=100)
    plugin_b3 = Plugin()
    install_fake_sysfs(tmp_b3, kernel_b3, [battery_b3], [], hwmon_b3, ec_b3)
    isolated_settings(plugin_b3, tmp_b3)

    plugin_b3._update_setting("fan_mode", "balanced")
    # EC 交还给自动：此刻 _fan_step 读到"非手动"，正会走夺权分支。
    ec_b3.mode_register = 0
    epoch_before = plugin_b3._fan_epoch
    plugin_b3._fan_unloading = True  # 唯一的一道拦阻

    check(
        "前置：该场景下世代号判据必须为『有效』（否则测不到卸载标志）",
        not plugin_b3._fan_step_invalidated(epoch_before),
        f"epoch 已作废（{epoch_before} != {plugin_b3._fan_epoch}），测不到卸载标志",
    )
    # 注意：这两次调用都要包 try/except —— 夺权**成功**的实现会继续往下走
    # （读温度、算 PWM、写 pwm1），而假 EC 的 pwm1 读回值不一定等于目标，
    # 那一步会抛 RuntimeError。若不让它在这里被咽掉，错误实现会让整节测试
    # **崩在中间**、后面的断言一条不跑 —— 那是"测试崩溃"而不是"干净报 FAIL"，
    # 属于脆写法（本轮注入 M19 时就是这么崩的）。
    step_error: Optional[str] = None
    try:
        plugin_b3._fan_step()
    except Exception as exc:  # noqa: BLE001 - 只需要知道"它继续往下走了"
        step_error = str(exc)
    check(
        "只用卸载标志也必须拦住夺权：_fan_step 不得把 EC 从自动切回手动",
        ec_b3.mode_register == 0,
        f"mode_register={ec_b3.mode_register}（0=自动 1=手动）"
        + (f"；且它继续往下走并抛错：{step_error}" if step_error else ""),
    )
    # 反向对照：清掉卸载标志、其余不变，同样的调用**必须**照常夺权 ——
    # 否则上面那条会被"永远不夺权"的坏实现骗过去。
    plugin_b3._fan_unloading = False
    ec_b3.mode_register = 0
    try:
        plugin_b3._fan_step()
    except Exception:  # noqa: BLE001 - 夺权是否成功只看 mode_register
        pass
    check(
        "反向对照：清掉卸载标志后，同样的 _fan_step 必须照常夺回控制权",
        ec_b3.mode_register == 1,
        f"mode_register={ec_b3.mode_register}（0=自动 1=手动）",
    )
    # 反向对照那一步会真起控制线程（夺权成功 → 继续往下走），必须收干净再走。
    teardown_plugins(plugin_b3)

    # ---- ② 自定义模式下保存曲线必须检查启线程结果 ----
    reset_settings()
    tmp_c2 = Path(tempfile.mkdtemp(prefix="f1pro-18c-"))
    battery_c2, kernel_c2 = make_battery(tmp_c2)
    hwmon_c2, ec_c2, temp_dir_c2 = make_fan(tmp_c2, temp_c=65.0, pwm=100)
    plugin_c2 = Plugin()
    install_fake_sysfs(tmp_c2, kernel_c2, [battery_c2], [], hwmon_c2, ec_c2)
    isolated_settings(plugin_c2, tmp_c2)

    plugin_c2._update_setting("fan_mode", "custom")

    # 造一个"卡住不退出"的旧线程 + stop_event 已置位（= 已要求它停、它没停）。
    released_c2 = threading.Event()
    entered_c2 = threading.Event()

    def stuck_loop_c2():
        entered_c2.set()
        released_c2.wait(timeout=20)

    stuck_c2 = threading.Thread(target=stuck_loop_c2, name="StuckFanControllerC2", daemon=True)
    stuck_c2.start()
    entered_c2.wait(timeout=5)
    plugin_c2._fan_thread = stuck_c2
    plugin_c2._fan_stop_event.set()

    real_join_c2 = stuck_c2.join
    stuck_c2.join = lambda timeout=None: real_join_c2(timeout=0.3)
    plugin_c2.FAN_STOP_TIMEOUT = 0.3

    check(
        "前置：这种状态下启动控制线程确实被拒绝（否则下面的断言会空转）",
        plugin_c2._start_fan_controller() is False,
        "True",
    )

    curve = plugin_c2._get_fan_curve(plugin_c2.FAN_MODE_CUSTOM)
    result_c2 = plugin_c2._set_fan_custom_curve_sync([list(p) for p in curve])
    stuck_c2.join = real_join_c2

    running_c2 = plugin_c2._fan_thread
    has_loop_c2 = (
        running_c2 is not None and running_c2.is_alive() and running_c2 is not stuck_c2
    )
    check(
        "保存曲线时不得出现「报告成功但没有可用控制循环」",
        not (bool(result_c2.get("ok")) and not has_loop_c2),
        f"ok={result_c2.get('ok')!r} 有控制循环={has_loop_c2}",
    )
    check(
        "旧线程没退出时保存曲线必须报失败（不得 ok=True）",
        result_c2.get("ok") is False,
        f"返回 ok={result_c2.get('ok')!r}（应为 False）",
    )
    check(
        "保存曲线失败后必须把 EC 交还自动（不能留在无人控制的手动 PWM 上）",
        ec_c2.mode_register == 0,
        f"mode_register={ec_c2.mode_register}（0=自动 1=手动）",
    )
    check(
        "保存曲线失败后保存的模式必须回到 auto",
        plugin_c2._load_settings().get("fan_mode") == "auto",
        f"fan_mode={plugin_c2._load_settings().get('fan_mode')!r}",
    )

    released_c2.set()
    stuck_c2.join(timeout=5)
    plugin_c2._fan_thread = None

    # 反向对照：控制线程能正常起来时，保存曲线必须照常成功。
    reset_settings()
    tmp_d2 = Path(tempfile.mkdtemp(prefix="f1pro-18d-"))
    battery_d2, kernel_d2 = make_battery(tmp_d2)
    hwmon_d2, ec_d2, temp_dir_d2 = make_fan(tmp_d2, temp_c=65.0, pwm=100)
    plugin_d2 = Plugin()
    install_fake_sysfs(tmp_d2, kernel_d2, [battery_d2], [], hwmon_d2, ec_d2)
    isolated_settings(plugin_d2, tmp_d2)
    plugin_d2._update_setting("fan_mode", "custom")

    curve_d2 = plugin_d2._get_fan_curve(plugin_d2.FAN_MODE_CUSTOM)
    ok_d2 = plugin_d2._set_fan_custom_curve_sync([list(p) for p in curve_d2])
    check(
        "反向对照：控制线程正常时保存曲线必须成功且循环在跑",
        bool(ok_d2.get("ok"))
        and plugin_d2._fan_thread is not None
        and plugin_d2._fan_thread.is_alive(),
        f"ok={ok_d2.get('ok')!r} _fan_thread={plugin_d2._fan_thread!r}",
    )
    # **本场景的控制线程必须在这里收掉**：`_set_fan_custom_curve_sync()` 成功时
    # 会起一个后台线程，而下一个场景第一件事就是 `reset_settings()` 删设置文件。
    # 线程若还活着并在写文件，Windows 下 unlink 会抛
    # `PermissionError: [WinError 32]` —— 而且**是竞态**（要看删文件那一瞬间
    # 线程有没有恰好持着句柄），所以"重跑一次就过"会把它掩盖成"偶发"。
    # 见 `teardown_plugins()` 的说明。
    teardown_plugins(plugin_d2)

    # 只读自定义曲线**不得**启动控制线程（保存曲线才启动）——
    # 否则"读一次曲线"会把风扇从自动拖进手动。
    reset_settings()
    tmp_e2 = Path(tempfile.mkdtemp(prefix="f1pro-18e-"))
    battery_e2, kernel_e2 = make_battery(tmp_e2)
    hwmon_e2, ec_e2, temp_dir_e2 = make_fan(tmp_e2, temp_c=65.0, pwm=100)
    plugin_e2 = Plugin()
    install_fake_sysfs(tmp_e2, kernel_e2, [battery_e2], [], hwmon_e2, ec_e2)
    isolated_settings(plugin_e2, tmp_e2)
    plugin_e2._stop_fan_controller()
    plugin_e2._get_fan_curve(plugin_e2.FAN_MODE_CUSTOM)
    check(
        "只读自定义曲线不得启动控制线程（只有保存才启动）",
        plugin_e2._fan_thread is None,
        f"_fan_thread={plugin_e2._fan_thread!r}",
    )
    teardown_plugins(plugin_e2)

    # 收尾：所有夹具的线程都已确认退出（上面每个场景各自 teardown 过），
    # 这里才敢删临时目录 —— 顺序反了就会在 Windows 上撞文件锁。
    for p in (tmp_a, tmp_b2, tmp_b3, tmp_c2, tmp_d2, tmp_e2):
        shutil.rmtree(p, ignore_errors=True)


def test_log_prefix_branding():
    """17. 日志前缀：改名的收尾 + 风扇日志的分层约定。"""
    section("17. 日志前缀（改名后不得残留旧前缀；风扇日志保持独立分层）")

    source = (PLUGIN_ROOT / "main.py").read_text(encoding="utf-8")

    # 改名时最容易漏的就是这些字符串前缀：它们不参与任何逻辑判断，
    # 跑测试也看不出来，只有翻日志的人才会撞见。所以直接从源码文本扫描。
    # 注意：日志用 logger.xxx(f"...") 和 logger.xxx("...") 两种写法都要覆盖，
    # 故这里扫的是裸前缀 "F1Pro <名字>:"，不限定引号与 f 前缀。
    def occurrences(prefix):
        return [
            (i + 1, line.strip())
            for i, line in enumerate(source.splitlines())
            if prefix in line
        ]

    old_prefixes = ["F1Pro Battery:", "F1 Pro Battery:", "F1Pro 电池:"]
    for old in old_prefixes:
        hits = occurrences(old)
        check(
            f"main.py 中不残留旧日志前缀「{old}」",
            len(hits) == 0,
            " | ".join(f"L{n}: {t[:60]}" for n, t in hits) or f"仍出现 {len(hits)} 次",
        )

    # 新前缀必须在场，且**数量要与预期的日志条数相符**。
    # 这里刻意不用 "> 0"：那样删掉其中一处照样绿（变异验证证明过），
    # 等于给"逐个漏改"留了后门。改成精确计数后，少一处就红。
    #
    # 注意：改日志条数时**必须同步改这里的期望值**，否则会误报。
    EXPECTED_MAIN_PREFIX = 5
    new_hits = occurrences("F1Pro EC Control:")
    check(
        f"main.py 里新日志前缀「F1Pro EC Control:」出现 {EXPECTED_MAIN_PREFIX} 次",
        len(new_hits) == EXPECTED_MAIN_PREFIX,
        f"实际 {len(new_hits)} 次：{new_hits}",
    )

    # ---- 风扇日志的分层约定（用户明确要求保持）----
    # 风扇走独立的 "F1Pro Fan:" 前缀，便于在 ~/homebrew/logs/ 里单独捞一条线。
    # 同样用精确计数，防止有人"顺手统一"掉其中几条。
    # 18 → 21：第 18 节的修复新增三条（卸载期间不夺权 / 卸载放弃起线程 ×2）。
    EXPECTED_FAN_PREFIX = 21
    fan_hits = occurrences("F1Pro Fan:")
    check(
        f"风扇日志保持独立前缀「F1Pro Fan:」共 {EXPECTED_FAN_PREFIX} 处",
        len(fan_hits) == EXPECTED_FAN_PREFIX,
        f"实际 {len(fan_hits)} 处",
    )
    # 结合上面两条：两前缀之和应等于日志总数。任何一处被改成对方的前缀，
    # 单条计数就会失衡 —— 这是分层约定真正被锁住的地方。
    check(
        "主前缀与风扇前缀互不侵占（数量各自独立成立）",
        len(new_hits) == EXPECTED_MAIN_PREFIX and len(fan_hits) == EXPECTED_FAN_PREFIX,
        f"主={len(new_hits)}/{EXPECTED_MAIN_PREFIX} 风扇={len(fan_hits)}/{EXPECTED_FAN_PREFIX}",
    )


def main():
    tests = [
        test_status_and_modes,
        test_write_verification,
        test_threshold_bounds,
        test_restore_on_start,
        test_unsupported_kernel,
        test_battery_selection,
        test_power_sign_and_ac_state,
        test_fan_discovery,
        test_fan_curve_and_control,
        test_fan_restore,
        test_fan_failure_rollback,
        test_fan_safety_paths,
        test_fan_safety_overrides_deadband,
        test_fan_handover_gate_and_no_early_exit,
        test_fan_unconfirmed_control_paths,
        test_fan_transition_serialization,
        test_fan_unload_and_start_failure,
        test_fan_unload_cancels_rpc_and_curve_start_failure,
        test_log_prefix_branding,
    ]
    for test in tests:
        test()
        # 每个场景跑完立刻检查有没有留下活着的控制线程 ——
        # 这比"等删设置文件时撞上 WinError 32"确定得多（那个是竞态）。
        assert_no_leaked_threads(test.__name__)

    print("\n" + "=" * 60)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for name, detail in FAILED:
            print(f"  - {name}: {detail}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
