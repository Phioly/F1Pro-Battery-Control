# F1 Pro Battery Control

SteamOS（Decky Loader）下的 **OneXFly F1 Pro / HX 370** 掌机充电控制插件。

提供：

* 正常充电
* 开机旁路充电
* 始终旁路充电
* 充电上限控制
* 启动时自动恢复
* 内核节点诊断
* 写入后的状态验证

> 当前公开版本：**v0.5.9**

---

## 功能

| 功能      | 写入的内核值                         | 说明                                             |
| ------- | ------------------------------ | ---------------------------------------------- |
| 🔋 正常充电 | `auto`                         | 正常充电，并遵循充电上限                                   |
| ⚡ 开机旁路  | `inhibit-charge-awake`         | 设备唤醒时停止充电，由外部电源直接供电；睡眠 / 关机后根据 F1 Pro 驱动实现恢复充电 |
| 🔌 始终旁路 | `inhibit-charge`               | 运行、睡眠、关机都禁止电池充电                                |
| 🔢 充电上限 | `charge_control_end_threshold` | 50 / 60 / 70 / 80 / 85 / 90 / 95%，另有「不限制」      |
| ♻️ 启动恢复 | —                              | Decky 启动时重新应用上次保存的模式与上限，可关闭                    |
| 🔔 错误提示 | —                              | 操作失败时提示；成功切换不弹通知                               |

### 版本说明

v0.5.9 是在 **v0.5.8 实机验证版本**基础上的文档与诊断修正版，核心后端控制逻辑、界面和构建链保持不变。

插件具有以下安全检查：

* 每次写入后都会重新读取内核节点确认是否真正生效。
* 如果写入没有生效，会明确报告错误，而不会显示为成功。
* 状态每 5 秒自动轮询，界面显示的是当前内核实际状态。
* 不依赖 HHD / Anatase / Loadout。
* 不读写 TDP、风扇、CPU 调度等其它硬件控制节点。
* 可以与 SimpleDeckyTDP 共存。
* 插件需要以 root 权限运行，`plugin.json` 应使用 `flags: ["root"]`。

---

## 前提条件

插件依赖 Linux `power_supply` 接口中的充电控制节点。

在 F1 Pro 上可以检查：

```bash
cat /sys/class/power_supply/BAT0/charge_behaviour
```

正常情况下应该看到类似：

```text
auto inhibit-charge [inhibit-charge-awake]
```

方括号中的值表示当前生效的模式。

同时可以检查充电上限：

```bash
cat /sys/class/power_supply/BAT0/charge_control_end_threshold
```

例如：

```text
95
```

如果 `charge_behaviour` 节点不存在，说明当前内核没有提供插件所需要的充电控制接口。

插件会在界面顶部提示，而不会静默失效。

---

## 语义边界：Linux ABI 与 F1 Pro 的具体行为

这一点很重要。

Linux `power_supply` ABI 定义了 `charge_behaviour` 的基本语义，但**不同硬件平台的具体行为仍由驱动和 EC 实现决定**。

### Linux ABI 的通用定义

| 值                      | ABI 层面的含义     |
| ---------------------- | ------------- |
| `auto`                 | 正常充电          |
| `inhibit-charge-awake` | 设备处于唤醒状态时禁止充电 |
| `inhibit-charge`       | 禁止充电          |

ABI 对 `inhibit-charge-awake` 只规定了：

> 唤醒状态下禁止充电。

它并没有规定设备进入睡眠或关机后一定会发生什么。

因此，睡眠 / 关机后的行为属于具体硬件驱动的实现细节。

### F1 Pro 的具体行为

OneXFly F1 Pro 使用的 OneXPlayer EC 驱动会将：

```text
charge_behaviour
```

映射到 EC 的充电抑制控制，同时单独处理：

```text
charge_control_end_threshold
```

在 F1 Pro 上实际表现为：

* `auto`

  * 唤醒状态正常充电
  * 遵循充电上限

* `inhibit-charge-awake`

  * 唤醒状态停止充电
  * 外部电源直接供给整机
  * 进入睡眠 / 关机后，唤醒状态的充电抑制解除
  * 接电情况下会恢复充电
  * 充电上限仍由 EC threshold 控制

* `inhibit-charge`

  * 所有状态都禁止电池充电
  * 因此充电上限在这个模式下没有实际作用

因此：

> **“开机旁路，睡眠 / 关机后恢复充电并遵循上限”是 F1 Pro 当前 oxpec + EC 实现的具体行为，并不是 Linux `charge_behaviour` ABI 对所有设备的通用保证。**

---

## 安装

### 方式一：Decky 从 ZIP 安装（推荐）

发布 ZIP：

```text
F1-Pro-Battery-Control-v0.5.9.zip
```

在 SteamOS 桌面模式下：

1. 将 ZIP 文件传到掌机。
2. 打开 Decky → 设置（齿轮）。
3. 进入开发者相关设置。
4. 开启 **Developer Mode**。
5. 使用 **Install Plugin from ZIP** 安装 ZIP。
6. 返回 QAM，插件列表中应该出现：

```text
F1 Pro Battery Control
```

> 如果你的 Decky 版本提供的是本地 ZIP 安装入口，请直接选择 ZIP 文件即可。

### 方式二：一键脚本

在 SteamOS 桌面模式下，将仓库或发布文件解压后：

```bash
chmod +x install.sh
./install.sh
```

脚本会将插件复制到：

```text
~/homebrew/plugins/
```

并重启 Decky Plugin Loader。

### 方式三：手动拷贝

```bash
sudo cp -r "F1 Pro Battery Control" ~/homebrew/plugins/
sudo systemctl restart plugin_loader
```

---

## 卸载

### 方式一：通过 Decky Loader 卸载（推荐）

这是最简单、也是推荐的卸载方式。

1. 进入 SteamOS 游戏模式。
2. 打开 **Decky Loader**。
3. 进入 **Decky 设置 / 已安装插件**。
4. 找到 **F1 Pro Battery Control**。
5. 选择 **卸载（Uninstall）**。
6. 根据提示完成卸载。

正常情况下，使用 Decky Loader 自带的卸载功能即可，无需执行任何终端命令。

> 如果卸载后 Decky 中仍显示插件，或者插件无法正常启动，可以重启设备后再次检查。

### 方式二：手动卸载

如果 Decky Loader 无法正常卸载插件，可以在 SteamOS 桌面模式打开 **Konsole**，执行：

```bash
sudo rm -rf ~/homebrew/plugins/"F1 Pro Battery Control"
sudo systemctl restart plugin_loader
```

如需同时删除插件保存的设置文件，可执行：

```bash
rm -rf ~/homebrew/settings/"F1 Pro Battery Control"
```

手动卸载完成后，可以重新启动 Decky Loader，或直接重启设备。

> **注意：** 手动卸载只删除本插件及其设置，不会修改系统的其它充电、TDP、风扇或 CPU 调度设置。

---

## 从源码构建

本仓库使用 npm + 官方 Decky Rollup 构建链。

### 安装依赖

```bash
npm ci
```

### 类型检查

```bash
npm run typecheck
```

### 构建

```bash
npm run build
```

构建产物：

```text
dist/index.js
dist/index.js.map
```

其它可用命令：

```bash
npm run watch
```

用于监听源码变化并自动构建。

---

## 构建与发布

仓库根目录提供：

```text
package.py
```

用于发布前检查并生成可安装的 Decky ZIP。

完整流程：

```bash
npm ci
npm run typecheck
npm run build
python package.py
```

只进行发布前检查：

```bash
python package.py --check
```

`package.py` 会检查：

* `plugin.json` 是否使用正确的 `root` 权限标记
* `package.json` 是否使用 ESM
* `dist/index.js` 是否存在
* bundle 是否为可加载的 ESM
* 发布 ZIP 的目录结构是否正确

生成的 ZIP 会包含 Decky 运行所需的文件，并在 ZIP 内使用：

```text
F1 Pro Battery Control/
```

作为插件顶层目录。

### GitHub Actions

仓库包含 GitHub Actions 工作流。

Push / Pull Request 时会执行：

```text
npm ci
npm run typecheck
npm run build
python package.py --check
```

推送 `v*` 标签时会自动执行构建并创建 GitHub Release，同时上传安装 ZIP。

---

## 使用

打开：

```text
QAM → Decky → F1 Pro Battery Control
```

### 电池状态

界面显示：

* 当前电量
* 当前状态
* 电池侧功率
* 当前充电上限
* 当前充电模式

例如：

```text
充电中 · 45.9 W · 上限 95%
```

### 充电模式

提供三个模式：

```text
正常充电
开机旁路
始终旁路
```

当前生效的模式会显示：

```text
✓
```

### 充电上限

可选择：

```text
50%
60%
70%
80%
85%
90%
95%
不限制
```

选择：

```text
不限制
```

会写入：

```text
100
```

用于解除充电上限。

### 启动恢复

开启后，Decky 启动时会重新应用上一次保存的：

* 充电模式
* 充电上限

可以在插件设置中关闭。

### 诊断

诊断区域可以查看：

* 实际使用的电池节点
* 外接电源节点
* 内核 `status`
* `charge_behaviour`
* `power_now`
* `charge_control_end_threshold`
* 节点是否存在
* 当前检测结果

如果遇到兼容性问题，把诊断区域内容和插件日志一起提供，通常可以快速判断问题是在：

* 内核接口
* 权限
* EC / 驱动
* Decky
* 插件本身

---

## 关于成功提示

插件**不会在成功切换时弹出通知**。

原因是 Decky 的通知除了右下角弹窗，还会进入 Steam 的通知中心。

如果每次切换都发送通知，很快会出现大量：

```text
已切换为……
```

本插件只在操作失败时发送通知。

成功操作后，后端会重新读取内核节点验证结果，因此界面中的模式和上限本身就是反馈。

---

## 状态行

状态行格式：

```text
状态 · 功率 · 上限 N%
```

功率只在电池实际充电或放电时显示。

| 显示              | 出现条件           | 示例                       |
| --------------- | -------------- | ------------------------ |
| `充电中 · 45.9 W`  | 电池正在充电         | `充电中 · 45.9 W · 上限 95%`  |
| `供电中`           | 外接电源带整机，电池不充不放 | `供电中 · 上限 95%`           |
| `放电中 · -37.6 W` | 电池正在放电         | `放电中 · -37.6 W · 上限 95%` |
| `已充满`           | 内核报告电池已充满      | `已充满 · 上限 95%`           |
| `未知`            | 无法读取有效状态       | `未知 · 上限 95%`            |

### 为什么旁路时不显示瓦数？

在旁路状态下：

```text
适配器 → 整机
```

电池本身基本不进行充放电，因此电池侧的 `power_now` 没有可靠的实时意义。

所以插件显示：

```text
供电中
```

而不显示一个可能误导用户的瓦数。

如果需要确认旁路是否真正生效，应观察**充电器 / USB-C 功率计**。

---

## 状态行里的功率

插件读取 Linux：

```text
power_now
```

该值来自电池侧，单位为：

```text
µW
```

插件转换为：

```text
W
```

一般情况下：

* 正值 → 电流进入电池，正在充电
* 负值 → 电流从电池流出，正在放电

部分 ACPI 电池驱动会将电流方向转换为绝对值，因此插件还会结合：

```text
status
```

判断充电 / 放电方向。

### 注意

这个数值：

**不是充电器 / USB-C 适配器的输入功率。**

例如：

```text
充电器端：62.7 W
电池侧：约 46 W
```

两者之间的差值主要来自整机运行功耗以及转换损耗。

因此：

> 判断旁路是否工作，应优先观察充电器端的功率计，而不是插件中的电池侧功率。

---

## 充电上限与各模式的关系

以下结论针对 **F1 Pro 的 oxpec + EC 实现**，不是 Linux ABI 的通用规则。

| 模式                     | 唤醒状态 | 睡眠 / 关机 | 充电上限       |
| ---------------------- | ---- | ------- | ---------- |
| `auto`                 | 正常充电 | 正常充电    | 生效         |
| `inhibit-charge-awake` | 禁止充电 | 恢复充电    | 睡眠 / 关机时生效 |
| `inhibit-charge`       | 禁止充电 | 禁止充电    | 无实际作用      |

因此：

### 正常充电

```text
auto
```

正常充电，并由 EC 根据充电上限停止充电。

### 开机旁路

```text
inhibit-charge-awake
```

唤醒状态：

```text
电源 → 整机
电池 → 不充电
```

进入睡眠 / 关机：

```text
电源 → 整机 + 电池
```

此时 EC 按设定的充电上限控制充电。

### 始终旁路

```text
inhibit-charge
```

所有状态都禁止电池充电。

因此充电上限在此模式下没有实际作用。

---

## 关于 root 权限

本插件需要写入：

```text
/sys/class/power_supply/
```

中的内核控制节点，因此必须以 root 权限运行。

`plugin.json` 应使用：

```json
"flags": ["root"]
```

而不是：

```json
"flags": ["_root"]
```

如果 root 权限没有正确设置，可能出现：

* 插件界面正常打开
* 电池状态正常读取
* 但切换模式失败
* 设置充电上限失败

遇到这种情况首先检查：

```text
plugin.json
```

中的 `flags`。

插件启动时也会记录实际运行 UID，可以通过日志确认是否为：

```text
uid=0
```

---

## 日志位置

### 插件日志

Decky 会保存插件日志：

```bash
ls -lt ~/homebrew/logs/"F1 Pro Battery Control"/
```

查看最近日志：

```bash
tail -n 150 ~/homebrew/logs/"F1 Pro Battery Control"/*.log
```

### Decky Plugin Loader 日志

```bash
journalctl -u plugin_loader -b --no-pager | tail -n 200
```

如果插件无法加载、崩溃或者权限异常，可以同时提供这两处日志。

---

## 故障排查

| 现象                   | 原因 / 处理                                               |
| -------------------- | ----------------------------------------------------- |
| 界面显示「未找到支持充电控制的电池节点」 | 当前内核没有提供 `charge_behaviour`，需要支持相应充电控制接口的内核           |
| 三个模式按钮都是灰的           | 查看诊断区中的 `charge_behaviour` 原始字符串，确认内核支持哪些值            |
| 上限下拉框是灰的             | 当前没有 `charge_control_end_threshold` 节点                |
| 提示没有权限写入电池控制节点       | 检查 `plugin.json` 是否为 `"flags": ["root"]`              |
| 提示内核拒绝写入             | 内核 / EC 不接受该值，错误信息会包含具体 errno                         |
| 改了上限但电量仍然继续增加        | 如果使用的是开机旁路，F1 Pro 在睡眠 / 关机后仍可能恢复充电，这是当前 oxpec 实现的预期行为 |
| 旁路时看不到瓦数             | 预期行为。旁路时电池不进行有效充放电，应查看充电器端功率计                         |
| 插着电、达到上限后显示「供电中」     | 正常。此时适配器带整机，电池不充不放                                    |
| 显示「未充电」              | 罕见兜底状态。检查诊断区中的外接电源状态和电池 `status`                      |
| 显示「已充满」但上限不是 100%    | 某些内核状态可能使用 `Full` 表示已经停止在设定上限，检查诊断区中的 `status`        |

---

## 已知范围

本插件针对：

```text
OneXFly F1 Pro
Ryzen AI 9 HX 370
SteamOS
Decky Loader
```

进行开发和实机验证。

虽然底层使用的是 Linux 标准 `power_supply` 接口，但：

> **不要假设其它 OneXPlayer / OneXFly 型号具有完全相同的 EC 行为。**

尤其是：

```text
inhibit-charge-awake
```

进入睡眠 / 关机后的行为属于硬件驱动实现细节。

如果你在其它型号上测试，欢迎提交：

* 设备型号
* SteamOS / Linux 内核版本
* 诊断信息
* `/sys/class/power_supply/` 相关节点信息
* 插件日志

---

## 与其它工具的关系

本插件只负责：

```text
充电模式
充电上限
```

不会修改：

* TDP
* CPU 调度
* GPU 调度
* 风扇
* 性能模式
* 屏幕刷新率

因此可以与其它性能控制插件配合使用。

例如：

```text
F1 Pro Battery Control
        +
SimpleDeckyTDP
```

两者控制的硬件接口不同。

---

## 开发

源码结构：

```text
src/
└── index.tsx       # Decky 前端

main.py             # Decky 后端

plugin.json         # Decky 插件配置

dist/
└── index.js        # 构建后的前端 bundle
```

构建：

```bash
npm ci
npm run typecheck
npm run build
```

发布前检查：

```bash
python package.py --check
```

生成发布 ZIP：

```bash
python package.py
```

---

## 贡献

欢迎提交：

* Bug report
* 兼容性测试
* SteamOS / Linux 内核差异
* F1 Pro 相关硬件测试
* Pull Request

如果提交兼容性问题，请尽量附带插件的：

```text
诊断信息
+
插件日志
+
Decky Plugin Loader 日志
```

这样可以减少重复排查。

详见：

[`CONTRIBUTING.md`](CONTRIBUTING.md)

---

## 许可

MIT License。

详见 [`LICENSE`](LICENSE)。

---

## 版本

当前公开版本：

**v0.5.9**

v0.5.9 基于 v0.5.8 实机验证版本，主要进行了文档、诊断和发布流程整理。

核心充电控制逻辑保持不变。
