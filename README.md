# F1 Pro Battery Control

SteamOS（Decky Loader）下的 OneXFly F1 Pro / HX 370 掌机充电控制插件。

只做两件事：**旁路供电开关** 和 **充电上限**。

---

## 功能

| 功能 | 写入的内核值 | 说明 |
| --- | --- | --- |
| 🔋 正常充电 | `auto` | 正常充电，并遵循充电上限 |
| ⚡ 开机旁路 | `inhibit-charge-awake` | 唤醒时停止充电、由电源直接供电；**进入睡眠 / 关机后解除该抑制，接电照常充电并遵循充电上限**（F1 Pro oxpec 驱动的具体行为，见下） |
| 🔌 始终旁路 | `inhibit-charge` | 运行、睡眠、关机都不充电，适合长期插电 |
| 🔢 充电上限 | `charge_control_end_threshold` | 50 / 60 / 70 / 80 / 85 / 90 / 95 %，另有「不限制」 |
| ♻️ 启动恢复 | — | Decky 启动时重新应用上次保存的模式与上限（可关闭） |
| 🔔 错误提示 | — | 仅操作失败时弹出通知；成功切换静默无提示 |

> 版本：**v0.5.9**（在 v0.5.8 实机验证版本基础上的文档与诊断修正版，
> 后端控制逻辑、界面、构建链均未改动）。

* 每次写入后都会**重新读取内核节点**确认是否真正生效，未生效会明确报错而不是假装成功。
* 状态每 5 秒自动轮询，界面上看到的就是内核里的真实状态。
* 不依赖 HHD / Anatase / Loadout；不读写 TDP、风扇、CPU 调度等任何其它节点，可以与 SimpleDeckyTDP 共存。
* 需要以 root 运行：`plugin.json` 的 `flags` 必须是 `["root"]`（**不是** `_root`），见下方「关于 root 标记」。

## 前提条件

插件依赖标准 Linux `power_supply` 接口：

```bash
cat /sys/class/power_supply/BAT0/charge_behaviour
```

F1 Pro 上正常应输出类似：

```text
auto inhibit-charge [inhibit-charge-awake]
```

方括号内是当前生效值。若这条命令报"没有那个文件"，说明当前内核没有提供充电控制驱动，
插件会在界面顶部直接提示，而不会静默失效。

### 语义边界：内核 ABI 说什么，F1 Pro 具体怎么做

这一段很重要，因为两者**不是一回事**，混在一起会得出错误结论。

**① 内核 ABI 的通用定义**（`Documentation/ABI/testing/sysfs-class-power`）只到这一层：

| 取值 | ABI 上的定义 |
| --- | --- |
| `auto` | 正常充电，**并遵循**充电上限 |
| `inhibit-charge-awake` | **仅设备处于唤醒（awake）状态时**禁止充电 |
| `inhibit-charge` | 任何状态下都禁止充电 |

ABI 对 `inhibit-charge-awake` 只界定了"唤醒时禁止"，**并没有承诺**睡眠 / 关机后会发生什么、
更没有承诺这时候还遵循不遵循充电上限——那是各机型驱动自己的实现细节，不能一概而论。

**② F1 Pro 的实际行为**来自 `drivers/platform/x86/oxpec.c`（OneXPlayer EC 驱动，F1 归入 `oxp_fly`）：
该驱动把 `charge_behaviour` 直接映射到 EC 的充电抑制寄存器，并**单独**处理
`charge_control_end_threshold`。在 F1 Pro 上实测表现为：

> 设备唤醒时暂停充电；进入睡眠 / 关机后解除唤醒状态的充电抑制，
> 因此接电时会恢复充电，充电上限由 EC 的 threshold 设置控制。

所以"开机旁路睡眠后会恢复充电并停在设定上限"是 **oxpec + EC 的具体行为**，
不是 `charge_behaviour` 这个 ABI 的通用承诺。换一台用别的驱动实现同一 ABI 的机器，
睡眠后的行为完全可能不同。

下文「充电上限与各模式的关系」那节给出的是 ② 的寄存器级细节（F1 Pro 特有），
不要把那张表当成 Linux 的通用规则。

## 安装

### 方式一：Decky 从 ZIP 安装（推荐）

1. 把 `F1ProBatteryControl-v0.5.9.zip` 传到掌机。
2. 打开 Decky → 设置（齿轮）→ **开发者** → 打开「Developer Mode」。
3. 在该页面选择 **Install Plugin from ZIP**（或把 zip 的本地路径粘贴到 "Install from URL"），选中 zip。
4. 回到 QAM，插件列表中会出现 **F1 Pro Battery Control**。

### 方式二：一键脚本

在 SteamOS 桌面模式下解压后执行：

```bash
chmod +x install.sh
./install.sh
```

脚本会把插件复制到 `~/homebrew/plugins/` 并重启 Decky。

### 方式三：手动拷贝

```bash
sudo cp -r "F1 Pro Battery Control" ~/homebrew/plugins/
sudo systemctl restart plugin_loader
```

### 卸载

```bash
sudo rm -rf ~/homebrew/plugins/"F1 Pro Battery Control"
sudo systemctl restart plugin_loader
```

设置文件位于 `~/homebrew/settings/F1 Pro Battery Control/f1pro-battery-control.json`，可一并删除。

## 从源码构建

```bash
pnpm i        # 或 npm i
pnpm run build
```

构建产物为 `dist/index.js`。其它可用的脚本：

```bash
npm run watch      # 监听改动
npm run typecheck  # 仅做类型检查
npm test           # 状态行显示规则测试（见下）
```

### 构建与发布

仓库根目录的 `package.py` 用于发布前检查并生成可安装的 Decky ZIP：

```bash
npm ci
npm run typecheck
npm run build

python package.py          # 校验 + 打包
python package.py --check  # 只校验
```

它会检查 `plugin.json` 的 `root` 标记、`package.json` 的 ESM 类型、
`dist/index.js` 是否存在以及是否为可加载的 ESM bundle，并生成带正确顶层插件目录的发布 ZIP。

GitHub Actions 会在 push / pull request 时自动执行类型检查、构建和打包前检查。
推送 `v*` 标签时会自动创建 GitHub Release 并上传安装 ZIP。

> 本仓库当前公开版本以 **v0.5.9** 为基准。v0.5.9 是在 v0.5.8 实机验证版本上的文档与诊断修正版，
> 核心后端控制逻辑、界面和构建链保持不变。

**注意**：Decky 运行的是 `dist/index.js`。如果没有执行过构建（目录里没有 `dist/index.js`），
插件会装上但界面空白——这是 Decky 插件的常见坑。

## 使用

QAM → Decky → F1 Pro Battery Control：

* **电池状态**：容量，以及 `状态 · 功率 · 上限 N%` 状态行（插电且电池不充不放时状态为
  `供电中`），下方是当前生效的充电模式。
* **充电模式**：三个按钮，当前生效的那个前面会显示 ✓。
* **充电上限**：下拉选择。选「不限制」会写入 100% 解除限制。
* **设置**：控制 Decky 启动时是否自动恢复。
* **诊断**：显示实际使用的电池节点、外接电源节点、内核 `status` 原始值、
  `charge_behaviour` 原始字符串、`power_now` 原始值、上限节点是否存在——
  排查内核差异时把这部分内容发出来即可。

### 关于提示：成功时不弹通知

Decky 的 toaster 除了在右下角弹出，还会把通知塞进 Steam 的通知中心。所以只要每次切换都提示，
通知中心里很快就会堆成一长串「已切换为…」。

本插件**只在操作失败时提示**，而且失败提示**故意保留**在通知中心，方便事后回看错误原因
（失败很少发生，不会堆积）。成功切换不弹任何通知——因为每次写入后后端都会重读内核节点，
界面上的模式和上限本身就是内核里的真实状态，已经起到反馈作用了。

### 状态行显示什么

状态行格式为 `状态 · 功率 · 上限 N%`，**功率只在电池确实在充/放电时出现**：

| 显示 | 出现时机 | 示例 |
| --- | --- | --- |
| `充电中 · 45.9 W` | 正在充电 | `充电中 · 45.9 W · 上限 100%` |
| `供电中` | **外接电源在带整机、电池不充不放**：旁路模式（开机旁路 / 始终旁路），或正常充电模式下电池已到设定上限 | `供电中 · 上限 100%` |
| `放电中 · -37.6 W` | 拔掉充电线，或正常充电模式下电池在放电 | `放电中 · -37.6 W · 上限 100%` |
| `已充满` | 充满、电池不再取电 | `已充满 · 上限 100%` |
| `未知` | 读不到内核 `status` | `未知 · 上限 100%` |

`供电中` 把两种来源合并了，因为它们的物理状态完全一样——适配器供整机、电池闲置。
**区别只在"为什么没在充电"**（内核禁止 vs 已到上限），而这个信息就写在其下一行的
「当前模式」里，不需要在状态行重复一遍。所以判据是：
**外接电源在线，且内核没有报充电/放电**。

后三种（`供电中` / `已充满` / `未知`）**不显示瓦数**：这些情况下电池电流≈0，
`power_now` 不再有实时意义，读数往往停留在切换前的旧值，显示出来只会误导。

> 唯一的例外：外接电源**确认已断开**（读到 `online = 0`）却仍报 `Not charging`，
> 这是自相矛盾的读数。此时不硬说成"供电中"，显示 `未充电`——除非遇到硬件异常，
> 这种情况不会出现。

### 状态行里的功率是什么

显示的是内核 `power_now` 的原始读数（单位 µW，插件换算为 W），也就是**电池这一侧**的功率：

* **正值** = 电流流入电池（正在充电），例如 `充电中 · 46.0 W`
* **负值** = 电流流出电池（正在放电），例如 `放电中 · -37.6 W`

> 负号是插件补的：ACPI 电池驱动（`drivers/acpi/battery.c`）会把 `_BST` 的 rate
> 取绝对值（`battery->rate_now = abs((s16)battery->rate_now)`），所以从这个节点读到的
> `power_now` **永远是正数**，方向只能依据 `status` 判断。插件在 `status` 为
> `Discharging` 时补上负号，若内核本来就给了负值则原样保留。

它**不是充电器/适配器的输入功率**。插着电时这个数字通常明显小于充电器端的功率计：
适配器要先供整机运行，剩下的才充进电池。例如实测中充电器端为 62.7 W 时，
电池侧约 46 W —— 差额就是整机功耗。

**要判断旁路是否真的生效，请看充电器端的功率计**（适配器端降到个位数、
≈ 整机空载功耗，就说明旁路成功了），插件里的瓦数在旁路时不显示。

拔掉充电线后旁路自然失去意义，状态行恢复为 `放电中 · -37.6 W` 这样的真实读数。

### 充电上限与各模式的关系（F1 Pro 特有实现，易错）

> 下表和结论都是 **F1 Pro 上 oxpec 驱动的实现**，不是 Linux 通用语义（区别见上文「语义边界」）。

充电模式和充电上限都由 OneXPlayer EC 驱动 `drivers/platform/x86/oxpec.c` 写进 EC 寄存器，
两者是**独立**的，不存在"旁路时上限就失效"这种简单关系：

| EC 寄存器 | `auto` | `inhibit-charge-awake` | `inhibit-charge` |
| --- | --- | --- | --- |
| `0xA4` 充电抑制<br>（bit0 = 唤醒时抑制，bit1 = 关机时也抑制） | `0x00` | `0x01` | `0x03` |
| `0xA3` 充电上限 | 被遵循 | **仅睡眠 / 关机时被遵循** | 永不充电，因此不起作用 |

* **正常充电**（`auto`，抑制位 `0x00`）：`0xA4` 不抑制，充电决定权交给 EC 的上限逻辑，
  充到设定上限后停。
* **开机旁路**（`inhibit-charge-awake`，抑制位 `0x01`）：只置"唤醒时抑制"这一位，
  唤醒期间 EC 不充电；睡眠 / 关机时该位不再生效，EC 按 `0xA3` 的阈值照常充电。
* **始终旁路**（`inhibit-charge`，抑制位 `0x03`）：把"唤醒时抑制"和"关机时抑制"两位都置上，
  EC 在任何状态下都不进入充电流程，因此上限确实没有实际作用。

也就是说，只有「始终旁路」这一种模式下上限才是空转的；正常充电与开机旁路都会被 EC 执行。
插件因此**不**在界面上为上限附加任何说明。

## 关于 root 标记（重要）

`plugin.json` 的 `flags` **必须是 `["root"]`**。

官方模板 `decky-plugin-template` 的 plugin.json 至今写着 `"flags": ["debug", "_root"]`，
而这是一个自 2022 年首次提交起、**四年从未修正**的过时写法：decky-loader 整个仓库
（含全部历史与分支）都不存在 `_root` 这个 flag。真正决定降权的地方是精确字符串匹配：

```python
# backend/decky_loader/plugin/sandboxed_plugin.py
setgid(UserType.EFFECTIVE_USER if "root" in self.flags else UserType.HOST_USER)
setuid(UserType.EFFECTIVE_USER if "root" in self.flags else UserType.HOST_USER)
```

写成 `_root` 不会报任何错，但插件进程会被 setuid 到 `deck`（uid 1000），
对 `/sys/class/power_supply/*` 的所有写入都会 EACCES。症状很有迷惑性：
**界面正常打开、状态也读得到，但每次切换模式/上限都失败**。

真实需要 root 的插件用的都是 `["root"]`（例如 SimpleDeckyTDP、DeckMTP）。
本插件启动时会把实际 uid 写进日志，`uid=0` 才算正确。

## 日志位置

插件日志（Decky 自动清理，只保留最新 5 个按时间命名的 `.log`）：

```bash
ls -lt ~/homebrew/logs/"F1 Pro Battery Control"/
tail -n 150 ~/homebrew/logs/"F1 Pro Battery Control"/*.log
```

Decky 本体日志（加载失败、插件崩溃会出现在这里）：

```bash
journalctl -u plugin_loader -b --no-pager | tail -n 200
```

排查时把这两处的内容一起发出来即可。

## 故障排查

| 现象 | 原因 / 处理 |
| --- | --- |
| 界面显示"未找到支持充电控制的电池节点" | 内核没有 `charge_behaviour`，需要带 oxpec / 充电控制驱动的内核 |
| 三个模式按钮都是灰的 | 看诊断区的 `charge_behaviour` 原始字符串，确认内核支持哪些取值 |
| 上限下拉框是灰的 | 没有 `charge_control_end_threshold` 节点 |
| 提示"没有权限写入电池控制节点" | `plugin.json` 的 `flags` 里没有 `"root"`（写成 `_root` 不生效），或插件不是通过 Decky 加载的。详见「关于 root 标记」 |
| 提示"内核拒绝了写入" | 内核/EC 不接受该值，报错会带上具体 errno |
| 改了上限但电量还在涨 | 开机旁路在睡眠 / 关机时仍会充电（F1 Pro oxpec 行为），属正常；只有「始终旁路」才完全不充电 |
| 旁路接电时看不到瓦数 | 预期行为：`供电中` 不显示数字，请改看充电器端功率计 |
| 插着电、已到上限时显示「供电中」 | 预期行为：适配器在带整机、电池不充不放，与旁路接电是同一种物理状态 |
| 显示「未充电」 | 罕见兜底：外接电源节点报已断开、内核却仍报 `Not charging`。核对诊断区的「外接电源」与 `status` 两行 |
| 显示「已充满」但上限不是 100% | 内核把"已停在上限"报成了 `Full`（而不是 `Not charging`）。看诊断区 `status` 确认 |

## 许可

MIT，见 [LICENSE](LICENSE)。
