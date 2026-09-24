/**
 * 状态行显示规则的离线测试。
 *
 * 不复制前端逻辑，而是**从 src/index.tsx 里抽取真实表达式**再求值——
 * 这样规则一旦被改动（或抽取失败），测试会立刻失败而不是默默通过。
 *
 * 运行：node tests/test_display.mjs
 */

import { existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
// 统一换行符：Windows 编辑器可能把源码存成 CRLF，会让下文的 `\n\n` 匹配失效。
//
// 读不到 src/index.tsx 时给一句人话：发布 ZIP 里**只有 src/ 和 dist/**，
// 本目录（tests/）**不进 ZIP**，所以从 ZIP 解压出来的目录里跑本测试必然失败。
const srcPath = join(here, "..", "src", "index.tsx");
let source;
try {
  source = readFileSync(srcPath, "utf-8").replace(/\r\n/g, "\n");
} catch {
  console.error(
    [
      "",
      "找不到 src/index.tsx，本测试无法运行。",
      "",
      `  期望路径：${srcPath}`,
      "",
      "  如果你正在 **发布 ZIP 解压出来的目录** 里运行，这是正常的：",
      "  ZIP 只含运行时需要的文件（main.py / dist/index.js 等），",
      "  `tests/` 与 `assets/` 是仓库资产、有意不进包。",
      "",
      "  想跑测试请用 GitHub 上的源码仓库：",
      "  https://github.com/Phioly/F1Pro-EC-Control",
      "",
    ].join("\n"),
  );
  process.exit(1);
}

/** 抽取一段 `const NAME = ...;` 的源码文本，抽不到就抛错。 */
function pick(pattern, label) {
  const match = source.match(pattern);
  if (!match) {
    throw new Error(`无法从 src/index.tsx 抽取「${label}」，显示规则可能已被改写。`);
  }
  return match[0];
}

/**
 * 抽取**可缺省**的一段源码，并把"抽不到"变成一条断言而不是异常。
 *
 * `pick()` 是有意 fail-fast 的（结构被改烂时立刻停），但用于**布局回归守卫**
 * 时它会帮倒忙：变异验证要求"实现被改坏 → 对应断言变红"，
 * 而抛异常会让整个测试在变量初始化阶段崩掉、连汇总行都到不了，
 * 在变异报告里只能算"崩溃抓住"（脆写法），看不出是哪条规则被破坏。
 */
function pickOrNull(pattern, label) {
  const match = source.match(pattern);
  check(`能定位到「${label}」（否则相关布局断言是空转的）`, Boolean(match), true);
  return match ? match[0] : "";
}

/**
 * 把 TS 的类型标注剥成合法 JS。
 *
 * 只做**这一处**需要的两件事：去掉参数的可选标记与参数/返回值的类型注解。
 * 刻意写得窄，避免把对象字面量里的 `:` 也误伤。
 */
function stripTypes(code) {
  return code
    .replace(/\(\s*(\w+)\?:\s*[\w<>\[\]{}| ,]+\s*\)/, "($1)")
    .replace(/\)\s*:\s*[\w<>\[\]{}| ,]+\s*=>/g, ") =>");
}

// statusName 的映射表（去掉 TS 的类型标注）。它现在依赖 STATUS_KEYS，
// 所以把那张键表也一并抽出来，否则 statusName 会在求值时报未定义。
const statusKeysDecl = pick(
  /const STATUS_KEYS: Record<string, MessageKey> = \{[\s\S]*?\n\};/,
  "STATUS_KEYS 键表",
).replace(/: Record<string, MessageKey>/, "");

const statusNameDecl = stripTypes(
  pick(/const statusName =[\s\S]*?\n\n/, "statusName 映射"),
);

// 状态判定链：onAdapter / adapterLive → supplyOnly → flowing → watts
const decls = [
  pick(/const onAdapter =[\s\S]*?;/, "onAdapter 判定"),
  pick(/const adapterLive =[\s\S]*?;/, "adapterLive 判定"),
  pick(/const supplyOnly =[^;]*;/, "supplyOnly 判定"),
  pick(/const flowing =[^;]*;/, "flowing 判定"),
  pick(/const watts =[\s\S]*?;/, "watts 计算"),
  // 状态行的三段拼装（summaryStatus / limitText / batterySummary）。
  // 这几段现在走 `t()` 取文案，所以下面会注入一个用**真实中文表**驱动的 t()。
  pick(/const summaryStatus =[\s\S]*?;/, "summaryStatus 拼装"),
  pick(/const limitText =[\s\S]*?;/, "limitText 拼装"),
  pick(/const batterySummary =[\s\S]*?;/, "batterySummary 拼装"),
];

/**
 * 从 src/i18n/zh-CN.ts 里读出真实的中文文案表。
 *
 * 这里**不复制**文案内容，而是解析真实文件：文案一旦被改动（或键名打错、
 * 漏了占位符），下面的断言会立刻变红，和抽取 JSX 表达式是同一个思路。
 * 只在"中文表里缺这条"时报错——那正是要被守住的失败模式。
 */
const zhTablePath = join(here, "..", "src", "i18n", "zh-CN.ts");
const zhSource = readFileSync(zhTablePath, "utf-8").replace(/\r\n/g, "\n");
const ZH = {};
for (const match of zhSource.matchAll(/"([\w.]+)":\s*"((?:[^"\\]|\\.)*)"/g)) {
  ZH[match[1]] = match[2].replace(/\\"/g, '"');
}

/** 与 src/i18n/keys.ts 的 format() 同构：替换 `{name}` 占位符。 */
function formatZh(template, values) {
  return template.replace(/\{(\w+)\}/g, (whole, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : whole,
  );
}

/** 测试用的 t()：只认中文本，缺键直接抛出（避免静默走英文表掩盖漏译）。 */
function t(key, values) {
  const template = ZH[key];
  if (template === undefined) {
    throw new Error(`中文文案表缺少键「${key}」（src/i18n/zh-CN.ts）`);
  }
  return formatZh(template, values ?? {});
}

// 状态行里真正渲染的那段表达式（取自 JSX）。用 `batterySummary` 这个中间变量
// 而不是直接抄 JSX 文本，是因为它就是"最终那一行文本"的唯一来源。
if (!/\{batterySummary\}/.test(source)) {
  throw new Error("无法从 JSX 中定位状态行表达式，显示规则可能已被改写。");
}

const build = new Function(
  "state",
  "active",
  "acOnline",
  "thresholdValue",
  "t",
  `${statusKeysDecl}\n${statusNameDecl}\n${decls.join("\n")}
   return {
     label: summaryStatus,
     watts,
     supplyOnly,
     summary: batterySummary,
   };`,
);

/** 按界面的拼装顺序产出最终一行文本。 */
function line({ status, watts, active = "auto", acOnline = true, threshold = 100 }) {
  const state = {
    status,
    power_watts: watts,
    ac_online: acOnline,
  };
  const result = build(state, active, acOnline, threshold, t);
  // 返回**界面真正渲染的那一行**，不再手工拼装：
  // 手工拼装等于把"拼装规则"复制一份到测试里，实现改了测试却照样绿。
  return result.summary;
}

let passed = 0;
let failed = 0;

function check(label, actual, expected) {
  const ok = actual === expected;
  if (ok) passed += 1;
  else failed += 1;
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}`);
  if (!ok) console.log(`        期望: ${expected}\n        实际: ${actual}`);
}

console.log("=== 四种主状态 ===");
check("正常充电", line({ status: "Charging", watts: 45.9 }), "充电中 · 45.9 W · 上限 100%");
check(
  "旁路接电（EC 报 Full、读数陈旧）",
  line({ status: "Full", watts: 45.9, active: "inhibit-charge-awake" }),
  "供电中 · 上限 100%",
);
check(
  "离电放电（负号）",
  line({ status: "Discharging", watts: -37.6, acOnline: false }),
  "放电中 · -37.6 W · 上限 100%",
);
check("已充满（不出瓦数）", line({ status: "Full", watts: 3.2 }), "已充满 · 上限 100%");

console.log("\n=== 已达上限（正常充电模式下 Not charging）===");
check(
  "插电、已到设定上限 → 与旁路接电同为「供电中」",
  line({ status: "Not charging", watts: 0, threshold: 80 }),
  "供电中 · 上限 80%",
);
check(
  "拿不到外接电源节点时也按供电处理",
  line({ status: "Not charging", watts: 0, threshold: 80, acOnline: null }),
  "供电中 · 上限 80%",
);
check(
  "外接电源确认已断开却报 Not charging（矛盾读数）→ 退回「未充电」",
  line({ status: "Not charging", watts: 0, acOnline: false }),
  "未充电 · 上限 100%",
);
check("读不到 status", line({ status: "Unknown", watts: 0 }), "未知 · 上限 100%");

console.log("\n=== 旁路模式的边界 ===");
check(
  "旁路 + 始终旁路模式 + 接电",
  line({ status: "Not charging", watts: 0, active: "inhibit-charge" }),
  "供电中 · 上限 100%",
);
check(
  "旁路 + 拔线（应恢复真实放电读数）",
  line({ status: "Discharging", watts: -12.3, active: "inhibit-charge", acOnline: false }),
  "放电中 · -12.3 W · 上限 100%",
);
check(
  "旁路 + 拿不到外接电源节点 + 放电",
  line({ status: "Discharging", watts: -12.3, active: "inhibit-charge", acOnline: null }),
  "放电中 · -12.3 W · 上限 100%",
);
check(
  "旁路 + 拿不到外接电源节点 + 未放电（按供电处理）",
  line({ status: "Full", watts: 45.9, active: "inhibit-charge", acOnline: null }),
  "供电中 · 上限 100%",
);

console.log("\n=== 不出瓦数的三种状态（陈旧读数不得泄漏）===");
for (const [status, active, expected] of [
  ["Full", "auto", "已充满 · 上限 100%"],
  ["Not charging", "auto", "供电中 · 上限 100%"],
  ["Full", "inhibit-charge-awake", "供电中 · 上限 100%"],
]) {
  check(
    `${status} / ${active} 即使有 45.9 W 陈旧值也不显示`,
    line({ status, watts: 45.9, active }),
    expected,
  );
}

console.log("\n=== 上限档位 ===");
check(
  "上限 80% 时状态行跟随",
  line({ status: "Charging", watts: 12, threshold: 80 }),
  "充电中 · 12.0 W · 上限 80%",
);
check(
  "电流本就为负时不二次翻转",
  line({ status: "Discharging", watts: -8.5, acOnline: false }),
  "放电中 · -8.5 W · 上限 100%",
);

/* ------------------------------------------------------------------ 风扇 */

/**
 * 风扇面板的守卫条件。
 *
 * 同样从源码抽取真实条件再求值：这几处一旦被改坏，界面会变成
 * "风扇不可用但控件仍可点"或"不可用却每 5 秒弹一次错误"，都属于
 * 只有真机上才容易发现的问题，值得离线钉住。
 */
console.log("\n=== 风扇面板 ===");

/**
 * 从源码里抽一段表达式再求值，而不是简单做 includes 检查。
 *
 * 之前的写法（`check("...", expr.length > 0, true)`）在变异验证里被证明是**空转**的：
 * 把实现改坏后断言照样通过。凡是"某处必须包含某个判断"的规则，都要真正把条件
 * 抽出来跑一遍不同的输入，看结果会不会跟着变。
 */
function pickExpr(pattern, label) {
  const match = source.match(pattern);
  if (!match) {
    throw new Error(`无法从 src/index.tsx 抽取「${label}」，实现可能已被改写。`);
  }
  return match[1];
}

// 1. 控件禁用条件必须真的把 available 算进去。
// 注意：源码里有多处同样写法的 disabled，断言必须**逐处**检查，
// 只取第一处会在"某一处被改坏、其余仍是老写法"时漏判。
const disabledExprs = [
  ...source.matchAll(/disabled=\{(fanBusy \|\| !available[^}]*)\}/g),
].map((match) => match[1]);
if (disabledExprs.length < 3) {
  throw new Error(
    `风扇控件禁用条件只找到 ${disabledExprs.length} 处，实现可能已被改写。`,
  );
}
// 挑一个**只依赖 fanBusy / available** 的表达式来求值。
//
// 不能直接拿 `disabledExprs[0]`：温度滑杆那条是
// `fanBusy || !available || bounds.min > bounds.max`，它引用了循环里的局部
// `bounds`，用 new Function 求值会抛 ReferenceError —— 让整个测试在变量
// 初始化阶段崩掉，输出堆栈而不是 FAIL 行。（早期版本踩过同样的坑。）
// 这里显式筛掉带其它自由变量的表达式；一条都筛不出来说明实现被改写得
// 面目全非了，直接报错而不是静默跳过。
const simpleDisabledExpr = disabledExprs.find(
  (expr) => !/bounds|curveDirty\s*&&|curveError/.test(expr.replace(/!available/g, "")),
);
if (!simpleDisabledExpr) {
  throw new Error(
    `找不到只依赖 fanBusy / available 的禁用条件，实际：${JSON.stringify(disabledExprs)}`,
  );
}
const fanDisabled = new Function(
  "fanBusy",
  "available",
  `return ${simpleDisabledExpr};`,
);
check("可用且空闲时可点击", fanDisabled(false, true), false);
check("风扇不可用时必须禁用", fanDisabled(false, false), true);
check("操作进行中时禁用", fanDisabled(true, true), true);
check(
  `全部 ${disabledExprs.length} 处禁用条件都包含 available`,
  disabledExprs.every((expr) => expr.includes("!available")),
  true,
);
// 温度滑杆那条额外要求 `bounds.min > bounds.max`：相邻节点贴合时滑杆会变死控件，
// 必须一并禁用。单独求值它（自己喂 bounds）。
const boundsDisabledExpr = disabledExprs.find((expr) =>
  /bounds\.min > bounds\.max/.test(expr),
);
if (boundsDisabledExpr) {
  const boundsDisabled = new Function(
    "fanBusy",
    "available",
    "bounds",
    `return ${boundsDisabledExpr};`,
  );
  check(
    "温度滑杆在边界倒挂时必须禁用",
    boundsDisabled(false, true, { min: 60, max: 50 }),
    true,
  );
  check(
    "温度滑杆边界正常时可用",
    boundsDisabled(false, true, { min: 50, max: 60 }),
    false,
  );
}

// 2. 不可用时不当作操作失败（只有 ok=false 才算错误）。
// 必须锁定 refreshFan 函数体内部，不能全局抓第一个 setFanError——
// 那样变异掉 refreshFan 里的判断后，正则会悄悄命中轮询里的另一处。
const refreshFanBody = pick(
  /const refreshFan = async \(\) => \{[\s\S]*?\n  \};/,
  "refreshFan 实现",
);
const fanErrorInner = refreshFanBody.match(/setFanError\((.+?)\);/s);
if (!fanErrorInner) {
  throw new Error("refreshFan 里找不到 setFanError，实现可能已被改写。");
}
const fanErrorFor = new Function(
  "result",
  "fanError",
  `return ${fanErrorInner[1]};`,
);
check(
  "节点不可用只给出原因、不算失败",
  fanErrorFor({ data: { available: false, reason: "未找到节点" } }),
  "未找到节点",
);
check(
  "节点可用时清空错误",
  fanErrorFor({ data: { available: true, reason: "旧错误" } }),
  null,
);
check(
  "节点可用时清空错误（原错误为 null）",
  fanErrorFor({ data: { available: true, reason: null } }),
  null,
);
check(
  "轮询路径同样按 available 判定",
  /fanResult\.data\.available \? null : \(fanResult\.data\.reason \?\? null\)/.test(source),
  true,
);

// 3. 用户编辑曲线不被轮询覆盖
// 取"轮询里那个守卫条件"：源码中「不要覆盖他的编辑」注释之后紧跟的 if 判断。
const guardExpr = pickExpr(
  /不要覆盖他的编辑[\s\S]*?if \((.+?)\) \{/,
  "轮询覆盖曲线的守卫条件",
);
const guard = new Function("curveTouched", `return ${guardExpr};`);
check("用户没动过曲线时允许同步服务端值", guard({ current: false }), true);
check("用户正在编辑时不得覆盖", guard({ current: true }), false);

const updateBody = pick(
  /const updateCurvePoint = [\s\S]*?\n  \};/,
  "updateCurvePoint 实现",
);
check(
  "拖动滑杆会置位 curveTouched",
  /curveTouched\.current = true;/.test(updateBody),
  true,
);

// 4. 保存后切到自定义，否则曲线存了不生效
const saveBody = pick(/const saveCurve = async[\s\S]*?\n  \};/, "saveCurve 实现");
check(
  "保存自定义后自动切到自定义模式",
  saveBody.includes('changeFanMode("custom")') &&
    /fan\?\.mode !== "custom"/.test(saveBody),
  true,
);

// 5. 校验失败不得走保存路径（应提前 return）
check(
  "曲线不递增时提前返回、不调用保存 RPC",
  /curveIsStrictlyIncreasing[\s\S]{0,200}?return;/.test(saveBody),
  true,
);

// 6. 控制方式必须来自后端的字段，不能自己比 pwm_enable。
//
// v0.6.12 起这段文案由后端随 `get_status` 下发（`pwm_enable_label`），
// 前端只负责套一层括号渲染 —— 这样"读回值 → 手动/自动"的判定只有一处实现，
// 且有 i18n（后端按界面语言给中文或英文）。断言因此分成两步：
//   ① 前端不得自己比较 pwm_enable；
//   ② 渲染必须取自后端字段，且**字段缺失时输出空串**（不能硬编码兜底文案，
//      否则后端一旦不再下发，界面会显示一个可能过期的旧结论）。
check(
  "前端没有自行比较 pwm_enable === 1",
  /pwm_enable === 1/.test(source),
  false,
);
// 主页那一行控制方式**必须无条件取自后端下发的 `pwm_enable_label`**，
// 不能再自己 `fan?.manual ? ... : ...` —— 后者等于把"读回值 → 手动/自动"
// 这套判定在前端重算一遍，两处实现迟早会不一致（而且前端算不出 i18n）。
// 注意这条只针对**主页渲染的那一处**：诊断区块里的 manual 展示是另一回事，
// 所以断言限定在从 `pwm_enable_label` 取值的表达式上。
const labelExpr = pickExpr(
  /\{(fan\?\.pwm_enable_label \? `（\$\{fan\.pwm_enable_label\}）` : "")}/,
  "主页风扇控制方式表达式",
);
check(
  "主页控制方式不依赖 manual 字段（只信后端下发的标签）",
  /manual|pwm_enable(?!_label)/.test(labelExpr),
  false,
);
const controlLabel = new Function("fan", `return ${labelExpr};`);
check("后端下发手动 → 原样渲染", controlLabel({ pwm_enable_label: "手动 PWM" }), "（手动 PWM）");
check("后端下发自动 → 原样渲染", controlLabel({ pwm_enable_label: "EC 自动" }), "（EC 自动）");
check("后端未下发 → 不渲染任何东西", controlLabel({}), "");
// 反向验证：这正是"前端自己按读回值判断"的写法，满速读回 0 时会误报自动。
const wrongLabel = new Function("enable", 'return enable === 1 ? "（手动 PWM）" : "（EC 自动控温）";');
check("（对照）自行比较 === 1 在满速读回 0 时会误报", wrongLabel(0), "（EC 自动控温）");

/* ------------------------------------------------------ 曲线排序与联动 */

console.log("\n=== 曲线校验、温度边界与模式联动 ===");

// 7. 曲线**绝不能**被 sort 重排。
// 排序会把节点的 PWM 跟着温度一起搬家，静默产生"高温对应低 PWM"的错乱。
check(
  "保存路径里没有 sort（不得盲目重排节点）",
  /\.sort\(/.test(saveBody),
  false,
);

// 直接对抽取出来的校验函数求值，而不是只看它有没有被调用。
const increasingDecl = pick(
  /const curveIsStrictlyIncreasing = [\s\S]*?\n\n/,
  "curveIsStrictlyIncreasing 实现",
).replace(/: boolean/, "").replace(/\(points: number\[\]\[\]\)/, "(points)");
const increasing = new Function(`${increasingDecl} return curveIsStrictlyIncreasing;`)();

check("正常递增曲线通过", increasing([[40, 40], [50, 75], [60, 115]]), true);
check(
  "温度重复被拒绝",
  increasing([[40, 40], [40, 75], [60, 115]]),
  false,
);
check(
  "温度倒序被拒绝（而不是被悄悄重排）",
  increasing([[50, 75], [40, 40], [60, 115]]),
  false,
);
// 关键回归：用户把节点 1 从 40°C 滑到 55°C，越过节点 2 的 50°C。
// 旧实现 sort 后会变成 [[50,75],[55,40],[60,115]] —— 温度递增了，但
// 45°C 的 PWM(40) 反而低于 50°C 的 PWM(75)，错乱被静默写进设置。
// 新实现必须直接判定"不合格"，不许自动重排。
const crossed = [[55, 40], [50, 75], [60, 115]];
check("节点越过邻居时被判为非法（不重排）", increasing(crossed), false);
const sorted = [...crossed].sort((a, b) => a[0] - b[0]);
check(
  "（对照）旧实现的 sort 会制造 PWM 倒挂",
  sorted[0][1] > sorted[1][1],
  true,
);
check(
  "（对照）重排后的曲线会被校验函数误判为合法",
  increasing(sorted),
  true,
);

// 8. 温度滑杆的边界必须夹在左右邻居之间，让越界值滑不出来。
const boundsDecl = pick(
  /const tempBoundsFor = [\s\S]*?\n\}\);/,
  "tempBoundsFor 实现",
)
  .replace(/\(\s*points: number\[\]\[\],\s*index: number,\s*minTemp: number,\s*maxTemp: number,?\s*\)/, "(points, index, minTemp, maxTemp)")
  .replace(/: \{ min: number; max: number \}/, "");
const boundsFor = new Function(`${boundsDecl} return tempBoundsFor;`)();

const five = [[40, 40], [50, 75], [60, 115], [70, 160], [80, 255]];
check("首节点下界取整体下限", boundsFor(five, 0, 30, 100).min, 30);
check("中间节点下界 = 左邻居 +1", boundsFor(five, 2, 30, 100).min, 51);
check("中间节点上界 = 右邻居 -1", boundsFor(five, 2, 30, 100).max, 69);
check("末节点上界取整体上限", boundsFor(five, 4, 30, 100).max, 100);
// 边界必须留出至少 1 档，否则滑杆会变成死控件（min > max）。
check(
  "相邻节点贴合时仍留有可调空间",
  boundsFor([[40, 40], [41, 75], [60, 115]], 0, 30, 100).max >=
    boundsFor([[40, 40], [41, 75], [60, 115]], 0, 30, 100).min,
  true,
);

// 9. 切换预设模式要立刻把本地曲线换成该预设，不能等轮询。
const changeModeBody = pick(
  /const changeFanMode = async[\s\S]*?\n  \};/,
  "changeFanMode 实现",
);
check(
  "切模式时按 profiles[mode] 同步本地曲线",
  /fanProfiles\?\.profiles\?\.\[mode\]/.test(changeModeBody) &&
    /setCurve\(profile\.map/.test(changeModeBody),
  true,
);
// 同步曲线时必须清掉"用户已编辑"标记，否则轮询守卫会一直拦着后续同步。
check(
  "同步预设曲线时清掉 curveTouched",
  /curveTouched\.current = false;/.test(changeModeBody),
  true,
);

// 10. 主页快捷区必须平铺模式按钮，且每个都真的能切模式。
//
// **每个模式独占一行**。这里刻意不用网格：早期版本用两列网格，真机上先后出现
// "裸 1fr 被 ButtonItem(layout="below") 的 min-content 宽度撑破（整行超出面板）"
// 和 "minmax(0,1fr) 下相邻按钮底色块重叠、文字叠在一起"两种问题 ——
// ButtonItem 内部还有一层我们控制不到的宽度约束，别再和它较劲。
const quickList = pickOrNull(
  /\{FAN_MODE_ORDER\.map\(\(mode\) => \([\s\S]*?\n            \)\)\}/,
  "主页模式按钮列表",
);
check(
  "每个模式一个 PanelSectionRow（独占一行）",
  /<PanelSectionRow key=\{mode\}>/.test(quickList),
  true,
);
check(
  "模式按钮逐个映射 FAN_MODE_ORDER（数量由常量决定）",
  /FAN_MODE_ORDER\.map/.test(quickList) && /key=\{mode\}/.test(quickList),
  true,
);
check(
  "模式按钮点击即切换模式",
  /onClick=\{\(\) => changeFanMode\(mode\)\}/.test(quickList),
  true,
);
// 选中项要有可辨识的标记，否则用户看不出当前是哪个模式。
check(
  "当前模式在按钮上标出（用 checked 模板，不硬编码勾号）",
  /fan\?\.mode === mode/.test(quickList) &&
    /t\("battery\.checked", \{ label: fanModeLabel\(mode\) \}\)/.test(quickList),
  true,
);
// **布局回归守卫**：不得再退回网格。网格是超宽/重叠的来源。
// 注意这里扫的是**整个源码**而不是 quickList —— 变异把按钮包进网格后，
// 上面的抽取正则就匹配不到了（quickList 为空），只扫 quickList 会漏判。
check(
  "整个文件都不再使用网格布局（网格会导致超宽或重叠）",
  /gridTemplateColumns|fanModeGridStyle|fanModeFullSpanStyle/.test(source),
  false,
);
check(
  "五个模式全部来自 FAN_MODE_ORDER（含自定义，顺序即显示顺序）",
  /const FAN_MODE_ORDER: string\[\] = \[\s*"auto",\s*"quiet",\s*"balanced",\s*"performance",\s*"custom",\s*\];/.test(
    source,
  ),
  true,
);

// 10b. 风扇曲线子页面：按用户规格重排。
//
// 规格：**不再重复展示风扇状态与风扇模式**（主页已经有了），
// 进来直接是曲线 → 保存为自定义并应用 → 曲线预设（静音/均衡/性能）+ 还原默认曲线。
const fanView = pickOrNull(
  /if \(view === "fan"\) \{[\s\S]*?\n  \}\n\n  \/\* -+ 主页面/,
  "风扇曲线子页面",
);
check(
  "子页面不再重复展示风扇状态区块",
  /title="风扇状态"/.test(fanView),
  false,
);
check(
  "子页面不再重复展示风扇模式区块",
  /title="风扇模式"/.test(fanView),
  false,
);
check(
  "子页面直接以「自定义风扇曲线」开头（返回按钮之后）",
  /t\("fan\.back"\)[\s\S]*?title=\{t\("fan\.curveSection"\)\}/.test(fanView),
  true,
);
check(
  "保存按钮文案为「保存为自定义并应用」",
  /t\("fan\.saveAndApply"\)/.test(fanView),
  true,
);

// 10c. 预设按钮改为"载入到滑杆"，而不是直接切换模式。
//
// 直接 changeFanMode(mode) 会切到**预设模式**（EC 按预设跑），
// 但滑杆上还是原来那条曲线 —— 用户看到的和实际在跑的不是一回事。
check(
  "预设按钮调用 loadPresetCurve（载入滑杆）而非直接切模式",
  /onClick=\{\(\) => loadPresetCurve\(mode\)\}/.test(fanView) &&
    !/title="曲线预设"[\s\S]*?changeFanMode/.test(fanView),
  true,
);
const loadPresetBody = pick(
  /const loadPresetCurve = \(mode: string\) => \{[\s\S]*?\n  \};/,
  "loadPresetCurve 实现",
);
check(
  "载入预设会置位 curveTouched（防轮询覆盖）",
  /curveTouched\.current = true;/.test(loadPresetBody),
  true,
);
check(
  "载入预设标记为已修改（否则保存按钮是灰的、点不动）",
  /setCurveDirty\(true\)/.test(loadPresetBody),
  true,
);
check(
  "载入预设只改本地 state，不直接写 EC",
  /setFanCustomCurve|changeFanMode/.test(loadPresetBody),
  false,
);

const restoreSeedBody = pick(
  /const restoreSeedCurve = \(\) => \{[\s\S]*?\n  \};/,
  "restoreSeedCurve 实现",
);
check(
  "「还原默认曲线」存在且优先取后端的 custom_seed",
  /fanProfiles\?\.custom_seed/.test(restoreSeedBody) &&
    /FAN_CURVE_SEED/.test(restoreSeedBody),
  true,
);
check(
  "还原同样只改本地 state（要按保存才写 EC）",
  /setFanCustomCurve|changeFanMode/.test(restoreSeedBody),
  false,
);
check(
  "「还原默认曲线」在预设区块内",
  /title=\{t\("fan\.presetSection"\)\}[\s\S]*?restoreSeedCurve/.test(fanView),
  true,
);

/* ------------------------------------------------------------ 换页滚动复位 */

// 12. 换页后必须把滚动位置拉回顶端。
//
// QAM 面板切换内容时不会重置滚动位置：从主页滚到底点「编辑风扇曲线」，
// 子页面直接落在界面末端，最上面的「← 返回」都看不见，很像卡住了。
console.log("\n=== 换页滚动复位 ===");

// 复位逻辑直接求值验证（而不是只看它有没有被调用）。
const scrollDecl = pick(
  /function scrollPanelToTop\([\s\S]*?\n\}/,
  "scrollPanelToTop 实现",
)
  .replace("(node: HTMLElement | null): void", "(node)")
  .replace("let current: HTMLElement | null =", "let current =");

// 造一个假的 DOM：从 node 往上逐层 parentElement，可指定每层的 overflowY 与可滚性。
// 这样能验证"跳过不可滚动的祖先、停在第一个真正可滚的那层"这条核心规则。
function makeFakeDom(layers) {
  // layers: [{ overflowY, scrollHeight, clientHeight }, ...] 由内向外
  const nodes = layers.map((spec) => ({
    ...spec,
    scrollTop: 999, // 初值非 0，方便断言是否被复位
    parentElement: null,
  }));
  for (let i = 0; i < nodes.length - 1; i += 1) {
    nodes[i].parentElement = nodes[i + 1];
  }
  return { inner: nodes[0], nodes };
}

const win = { getComputedStyle: (el) => ({ overflowY: el.overflowY }) };
const scrollPanelToTop = new Function(
  "window",
  `${scrollDecl} return scrollPanelToTop;`,
)(win);

// 核心场景：内层是插件自己的 div（不滚动），外层才是 QAM 的滚动容器。
{
  const { nodes } = makeFakeDom([
    { overflowY: "visible", scrollHeight: 100, clientHeight: 100 }, // 插件根节点
    { overflowY: "visible", scrollHeight: 200, clientHeight: 200 }, // 中间层
    { overflowY: "auto", scrollHeight: 900, clientHeight: 400 }, // QAM 滚动容器
  ]);
  scrollPanelToTop(nodes[0]);
  check("跳过不可滚动的祖先，复位真正在滚的那层", nodes[2].scrollTop, 0);
  check("中间层不受影响", nodes[1].scrollTop, 999);
}

// 声明了 overflow 但当前没内容可滚（scrollHeight == clientHeight）时
// 不能停在那层，否则真正的滚动层在更外面就漏掉了。
{
  const { nodes } = makeFakeDom([
    { overflowY: "visible", scrollHeight: 100, clientHeight: 100 },
    { overflowY: "auto", scrollHeight: 300, clientHeight: 300 }, // 声明可滚但没内容
    { overflowY: "scroll", scrollHeight: 900, clientHeight: 300 }, // 真正的滚动层
  ]);
  scrollPanelToTop(nodes[0]);
  check("不停在'声明可滚但没内容'的层上", nodes[1].scrollTop, 999);
  check("继续向外找到真正的滚动层", nodes[2].scrollTop, 0);
}

// overflow: scroll 同样要认（有些容器用 scroll 而不是 auto）。
{
  const { nodes } = makeFakeDom([
    { overflowY: "visible", scrollHeight: 100, clientHeight: 100 },
    { overflowY: "scroll", scrollHeight: 800, clientHeight: 300 },
  ]);
  scrollPanelToTop(nodes[0]);
  check("overflow:scroll 的容器也要复位", nodes[1].scrollTop, 0);
}

// 找不到可滚动祖先时**不能抛错**：最坏退回旧行为，不该让整个界面崩掉。
check(
  "找不到可滚动祖先时不抛异常",
  (() => {
    try {
      const { nodes } = makeFakeDom([
        { overflowY: "visible", scrollHeight: 10, clientHeight: 10 },
      ]);
      scrollPanelToTop(nodes[0]);
      return true;
    } catch {
      return false;
    }
  })(),
  true,
);
check(
  "传 null 也不抛异常",
  (() => {
    try {
      scrollPanelToTop(null);
      return true;
    } catch {
      return false;
    }
  })(),
  true,
);

// 复位必须发生在**新视图渲染之后**（useEffect 依赖 view），
// 在点击那一刻复位会被随后的渲染覆盖掉。
check(
  "滚动复位挂在 useEffect 上并以 view 为依赖",
  /useEffect\(\(\) => \{\s*scrollPanelToTop\(rootRef\.current\);\s*\}, \[view\]\)/.test(
    source,
  ),
  true,
);
// 两个分支都要挂 rootRef，否则切回主页后拿不到根节点、复位失效。
check(
  "主页与子页面都挂了 rootRef（缺一个就会失效）",
  (source.match(/ref=\{rootRef\}/g) ?? []).length,
  2,
);
// 断"不该用某写法"时**必须先剥掉注释**，否则会命中注释里那句
// "为什么不能直接 window.scrollTo" 的解释，得到假阳性。
// （同样的坑在 main.py 的 `points.sort()` 上踩过一次。）
const codeOnly = source
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/(^|[^:])\/\/.*$/gm, "$1");
check(
  "不为滚动复位引入 window.scrollTo（QAM 里滚的是面板容器，不是 window）",
  /window\.scrollTo|window\.scroll\(/.test(codeOnly),
  false,
);

// 11. 风扇不可用时主页降级成一行原因，不摆一排点不动的按钮。
// 区块边界要一直锚到下一个顶层 PanelSection，否则会切掉后半段
// （第一版就是这么漏掉"主页显示转速"的，变异验证时才发现是空转）。
//
// 锚点用 i18n **键名**而不是中文标题：文案现在活在 zh-CN.ts / en-US.ts 里，
// 界面文字随时可能改（而且英文版本来就不同），但键名是稳定标识。
const mainFanSection = pick(
  /<PanelSection title=\{t\("fan\.section"\)\}>[\s\S]*?\n      <\/PanelSection>\n\n      <PanelSection title=\{t\("settings\.section"\)\}>/,
  "主页风扇区块",
);
check(
  "主页风扇区按可用性分支渲染",
  /\{fanAvailable \? \([\s\S]*?\) : \([\s\S]*?\)\}/.test(mainFanSection),
  true,
);
// 规格二十五要求主页面直接看到 Current PWM，不是只给个进子页面的按钮。
check(
  "主页直接显示实时 PWM",
  /fan\?\.pwm != null \? `PWM \$\{fan\.pwm\}` /.test(mainFanSection),
  true,
);
check(
  "主页显示 CPU 温度",
  /fan\?\.temperature != null \? `\$\{fan\.temperature\.toFixed\(1\)\}°C`/.test(
    mainFanSection,
  ),
  true,
);
check("主页显示转速", /fan\?\.rpm != null \? `\$\{fan\.rpm\} RPM`/.test(mainFanSection), true);
// 降级分支里不能出现模式按钮（否则等于摆了一排死控件）。
const degradeBranch = mainFanSection.slice(mainFanSection.indexOf(") : ("));
check(
  "不可用时降级分支不含模式按钮",
  /changeFanMode/.test(degradeBranch),
  false,
);
// 降级分支必须给出原因，不能是空白。
check(
  "不可用时给出具体原因",
  /fan\?\.reason \?\? fanError/.test(degradeBranch),
  true,
);

// ---------------------------------------------------------------- 品牌一致性
//
// 插件改过一次名（F1 Pro Battery Control → F1Pro EC Control），当时只改了
// 注册名和 QAM 标题，**通知标题与一个兜底区块的标题漏掉了**，又漂了两轮
// 才被人肉发现。所以这里把"用户能看见的字符串"统一守一遍：
// 既要断言新名在场，也要断言**旧名不在场**——只查前者的话，
// 以后有人再添一处旧名同样会绿。
//
// 实现要点：这一节**刻意不用 pick()**。pick 抽不到会抛异常，测试会在
// 变量初始化阶段整个崩掉（后面的断言一条都不跑），于是变异验证时看到的是
// 堆栈而不是 FAIL 行——调用方无法判断"到底是抓住了还是崩了"。
// 改用正则取值 + 交回 check 判定：抓不住就是一条干净的 FAIL。
console.log("\n=== 品牌一致性（改名后不得残留旧称谓）===");

const BRAND = "F1Pro EC Control";
// 用户能看见的旧称谓。注意 `F1 Pro` 单独出现不算（那是设备名
// "OneXFly F1 Pro"，出现在错误文案里是**正确**的）。
const STALE_BRANDS = [
  "F1 Pro 电池",
  "F1 Pro Battery",
  "F1 Pro Battery Control",
  "电池控制中心",
];

const grab = (re) => {
  const m = source.match(re);
  return m ? m[0] : "";
};

const notifyFn = grab(/const notifyFailure = \(message: string\)[\s\S]*?\n};/);
check(
  "失败通知的标题用的是新名",
  /title:\s*"F1Pro EC Control"/.test(notifyFn),
  true,
);
check(
  "失败通知的标题不再叫「F1 Pro 电池」（风扇报错时会让用户困惑）",
  /F1 Pro 电池/.test(notifyFn),
  false,
);

const qamTitle = grab(/titleView:[\s\S]*?<\/div>/);
check("QAM 标题用新名", qamTitle.includes(`>${BRAND}<`), true);

const pluginName = grab(/name:\s*"[^"]*",\s*\n\s*titleView:/);
check("插件注册名用新名", pluginName.includes(`name: "${BRAND}"`), true);

// 兜底错误区块（state 读不到时显示的那个）也带标题，同样要守。
// 它紧跟在 `if (error && !state)` 之后，用这个特征定位，
// 比用 "title 后面跟 PanelSectionRow" 稳（后者会被别的区块误匹配）。
const fallbackSection = grab(
  /if \(error && !state\) \{\s*\n\s*return \(\s*\n\s*<PanelSection title="[^"]*">/,
);
check(
  "兜底错误区块标题用新名",
  fallbackSection.includes(`<PanelSection title="${BRAND}">`),
  true,
);
check(
  "兜底错误区块确实找到了（否则上一条断言是空转的）",
  fallbackSection.length > 0,
  true,
);

// 全局扫描：整份源码里不许再出现任何旧称谓。
// 逐个报出命中位置，方便直接去改。
for (const stale of STALE_BRANDS) {
  const hits = source
    .split("\n")
    .map((line, i) => [i + 1, line])
    .filter(([, line]) => line.includes(stale))
    .map(([n, line]) => `L${n}: ${line.trim().slice(0, 60)}`);
  check(
    `源码中不残留旧称谓「${stale}」`,
    hits.length === 0 ? "无" : hits.join(" | "),
    "无",
  );
}

// ---------------------------------------------------------------------------
// 测试入口包装：发布 ZIP 里没有 tests/，npm test 必须给出人话提示
// ---------------------------------------------------------------------------
// `package.json` 会随发布包走（Decky 要从里面读 main/version），
// 但 `tests/` 是仓库资产、不进包。于是用户从 ZIP 解压目录跑 `npm test` 时，
// 看到的是 Node 的 `Error: Cannot find module '.../tests/test_display.mjs'`。
// `scripts/run-tests.mjs` 把这句换成解释性提示，本节点守住它。

const runnerPath = join(here, "..", "scripts", "run-tests.mjs");
check("测试入口包装 scripts/run-tests.mjs 存在", existsSync(runnerPath), true);

if (existsSync(runnerPath)) {
  // 直接**导入包装的纯函数**做断言，不 spawn 子进程。
  //
  // 原因（踩过）：本机 Windows Git Bash 下，测试进程 spawn 任何子进程
  // （`process.execPath` 递归、裸 `node`、`shell: true` 走 cmd.exe）都会
  // EBUSY —— `status` 恒为 `null`、stdout 是 `undefined`，断言全部落空，
  // 而且还会误报成"逃逸"。把决策逻辑抽成 export 的纯函数后，测试无需子进程。
  const mod = await import(pathToFileURL(runnerPath).href);

  // 每个套件都要登记（否则该套件在 ZIP 里跑会退回原始报错）。
  check("包装登记了 display 套件（test_display.mjs）", "display" in mod.SUITES, true);
  check("包装登记了 backend 套件（test_backend.py）", "backend" in mod.SUITES, true);
  check(
    "display 套件指向 tests/test_display.mjs",
    mod.SUITES.display.file.replace(/\\/g, "/").endsWith("tests/test_display.mjs"),
    true,
  );
  check(
    "backend 套件指向 tests/test_backend.py",
    mod.SUITES.backend.file.replace(/\\/g, "/").endsWith("tests/test_backend.py"),
    true,
  );

  // 目标解析：`all` = 两套；已知名 = 自己；未知 = null（调用方据此报错退出）。
  check("all 解析为两套（display + backend）", mod.resolveTargets("all").join(","), "display,backend");
  check("display 解析为单套", mod.resolveTargets("display").join(","), "display");
  check("backend 解析为单套", mod.resolveTargets("backend").join(","), "backend");
  check("未知目标解析为 null（触发报错退出）", mod.resolveTargets("bogus"), null);

  // 缺失检测：在真实仓库里两套都在 → missing 为空。
  check("真实仓库里两套测试文件都存在（missing 为空）", mod.inspectTargets(["display", "backend"]).missing.length, 0);

  // 关键反向验证：**喂一个不存在的路径时必须报 missing**。
  // 只断言"真实仓库里 missing 为空"是不够的——把检查整个删掉（永远返回空）
  // 照样能通过，于是缺文件时又会退回去 spawn、报 Node 原始错误。
  const savedDisplay = mod.SUITES.display;
  mod.SUITES.display = { ...savedDisplay, file: join(tmpdir(), "definitely-missing-f1pro.mjs") };
  const fakeMissing = mod.inspectTargets(["display"]);
  check(
    "文件不存在时必须被检为 missing（否则缺文件时会退回 Node 原始报错）",
    fakeMissing.missing.join(","),
    "display",
  );
  mod.SUITES.display = savedDisplay;

  // 提示文案：必须解释"为什么"并给出去哪跑，不能只说找不到。
  const hint = mod.missingHint(["display", "backend"]);
  check("缺失提示点明缺的是哪个文件", /tests\/test_display\.mjs/.test(hint), true);
  check("缺失提示点名后台套件", /tests\/test_backend\.py/.test(hint), true);
  check("缺失提示说明这是发布 ZIP 的预期情况", /发布 ZIP 解压出来的目录/.test(hint) && /正常的/.test(hint), true);
  check(
    "缺失提示给出源码仓库地址",
    /https:\/\/github\.com\/Phioly\/F1Pro-EC-Control/.test(hint),
    true,
  );
  check("缺失提示提醒要先 npm install", /npm install/.test(hint), true);
  check(
    "缺失提示不包含 Node 原始报错措辞（那正是我们要替换掉的）",
    /Cannot find module/.test(hint),
    false,
  );

  // 退出码：**真的调用 decideExit 断言**，不是数 `process.exit(1)` 出现几次。
  // 读源码文本的写法很脆——只要那行还在文件里就恒真，改成 `exit(0)` 也可能逃逸。
  check("全部通过时退出码 0", mod.decideExit("all", { display: true, backend: true }).exitCode, 0);
  check("未知目标退出码 2（与失败区分开）", mod.decideExit("bogus", {}).exitCode, 2);
  check("未知目标的 kind 是 unknown", mod.decideExit("bogus", {}).kind, "unknown");
  check(
    "某套件失败时退出码 1（CI 能感知）",
    mod.decideExit("all", { display: true, backend: false }).exitCode,
    1,
  );
  check("失败时 kind 是 failed", mod.decideExit("all", { display: false }).kind, "failed");

  // 缺文件时必须退出 1：这里是**真的让文件不存在**（临时改写登记表），
  // 而不是匹配源码字符串。
  const savedForExit = mod.SUITES.display;
  mod.SUITES.display = { ...savedForExit, file: join(tmpdir(), "f1pro-missing-for-exit.mjs") };
  check(
    "缺测试文件时退出码 1（不能是 0，否则 CI 误判通过）",
    mod.decideExit("display", {}).exitCode,
    1,
  );
  check("缺文件时 kind 是 missing（与 failed 区分）", mod.decideExit("display", {}).kind, "missing");
  mod.SUITES.display = savedForExit;

  // CLI 入口保护：被 import 时不得执行主流程（否则测试导入即跑测试，无限递归）。
  const runnerSrc = readFileSync(runnerPath, "utf-8").replace(/\r\n/g, "\n");
  check(
    "包装区分了「被 import」与「直接运行」（否则测试一导入就递归跑测试）",
    /isMain/.test(runnerSrc) && /import\.meta\.url/.test(runnerSrc),
    true,
  );

  // package.json 的三个脚本都要走包装（否则包装形同虚设）。
  const pkg = JSON.parse(
    readFileSync(join(here, "..", "package.json"), "utf-8"),
  );
  check(
    "package.json 的 test 指向包装",
    pkg.scripts.test === "node scripts/run-tests.mjs display",
    true,
  );
  check(
    "package.json 的 test:backend 指向包装",
    pkg.scripts["test:backend"] === "node scripts/run-tests.mjs backend",
    true,
  );
  check(
    "package.json 的 test:all 指向包装",
    pkg.scripts["test:all"] === "node scripts/run-tests.mjs all",
    true,
  );
}

/* ------------------------------------------------- 轮询必须有背压 */

// 13. 轮询绝不能用 setInterval + 异步回调。
//
// setInterval 不看回调有没有跑完：到点就再发一次。tick 要等在途 RPC
// （sysfs 读 + 可能的 EC 写），EC 卡顿时单次耗时可能远超 5 秒间隔，
// 请求就越积越多；界面看起来是"卡一下然后自己好了"，其实是排队的响应
// 集中回来、几次状态互相覆盖。递归 setTimeout 天然有背压：
// 上一次 resolve 之后才排下一次。
console.log("\n=== 轮询背压 ===");

const pollDecl = pick(
  /\/\/ 首次挂载后读取一次，之后定时轮询[\s\S]*?\n  \}, \[\]\);/,
  "轮询 useEffect",
);

check("轮询改用递归 setTimeout", /timer = setTimeout\(tick, REFRESH_INTERVAL_MS\)/.test(pollDecl), true);
check(
  "不再使用 setInterval（无背压）",
  /setInterval/.test(pollDecl.replace(/\/\/.*$/gm, "")),
  false,
);
check(
  "不再需要 clearInterval",
  /clearInterval/.test(pollDecl.replace(/\/\/.*$/gm, "")),
  false,
);
check("卸载时清掉 setTimeout", /if \(timer !== null\) clearTimeout\(timer\)/.test(pollDecl), true);
// 续排必须在 finally 里：中途抛错若不续排，轮询会静默停死。
check(
  "续排放在 finally 里（异常时轮询不能停死）",
  /finally \{[\s\S]*?timer = setTimeout\(tick, REFRESH_INTERVAL_MS\)[\s\S]*?\}/.test(pollDecl),
  true,
);
check("未取消时才续排", /if \(!cancelled\) \{\s*timer = setTimeout/.test(pollDecl), true);

// 求值验证"背压"这件事本身：把 tick + 调度逻辑抠出来，用手动驱动的
// 假定时器跑。只断"用了 setTimeout"是文本匹配，改坏实现照样能通过；
// 这里要证明**在上一次没跑完时不会排下一次**。
{
  const scheduled = [];
  let cancelled = false;
  let inFlight = 0;
  let maxInFlight = 0;

  const tick = async () => {
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    try {
      // 模拟两次 RPC（电池 + 风扇），用微任务代替真实等待。
      await Promise.resolve();
      await Promise.resolve();
    } finally {
      inFlight -= 1;
      if (!cancelled) scheduled.push(1);
    }
  };

  // 递归写法下，无论跑多少轮，同时在途的 tick 恒为 1。
  for (let i = 0; i < 5; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await tick();
  }
  check("递归调度下在途 tick 恒为 1（有背压）", maxInFlight, 1);
  check("每一轮跑完都续排了下一轮", scheduled.length, 5);
  cancelled = true;
  // eslint-disable-next-line no-await-in-loop
  await tick();
  check("取消后不再续排", scheduled.length, 5);
}

/* ---------------------------------------- busy 恢复 / 曲线不被覆盖 */

// 14. RPC 异常时 busy 必须恢复；保存曲线后不得被默认曲线覆盖。
//
// 前者由离线探针复现过 4/4 个操作都会永久卡住（.mut/repro_busy.mjs）：
// `setBusy(true)` 之后直接 `await`，Promise 一被拒绝 `setBusy(false)` 就永远
// 不会执行，界面上的按钮从此是灰的。
//
// 后者是状态覆盖：`refreshFanProfiles` 旧写法取 `profiles[mode] ?? custom_seed`，
// 而 `profiles` 里**只有预设**（没有 "custom" 键），自定义模式下必然取不到、
// 退回默认种子 → 用户刚保存的曲线被默认曲线盖掉。
console.log("\n=== busy 恢复 / 保存后曲线不被覆盖 ===");

/** 按缩进与花括号配平，抽出一个 `const xxx = async (...) => {...}` 的完整定义。 */
function extractAsyncFn(name) {
  const re = new RegExp(`const ${name} = async \\([^)]*\\) => \\{`);
  const m = re.exec(source);
  if (!m) return null;
  const start = source.indexOf("{", m.index + m[0].length - 1);
  let depth = 0;
  for (let j = start; j < source.length; j += 1) {
    if (source[j] === "{") depth += 1;
    else if (source[j] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(m.index, j + 1);
    }
  }
  return null;
}

/** 把抽出的 .tsx 片段转成可执行 JS（剥掉 TS 类型标注）。 */
function toRunnable(body, name) {
  return body
    .replace(new RegExp(`^const ${name} = `), "return ")
    .replace(/\(([^)]*)\)\s*=>/, (_m, params) =>
      `(${params
        .split(",")
        .map((p) => p.split(":")[0].trim())
        .filter(Boolean)
        .join(", ")}) =>`,
    );
}

// ---- 14a. 四个操作在 RPC 被拒绝后都必须把 busy 清成 false ----
const BUSY_TARGETS = [
  { fn: "changeMode", setter: "setBusy", label: "切换充电模式" },
  { fn: "changeThreshold", setter: "setBusy", label: "设置充电上限" },
  { fn: "changeAutoRestore", setter: "setBusy", label: "保存自动恢复" },
  { fn: "changeFanMode", setter: "setFanBusy", label: "切换风扇模式" },
  { fn: "saveCurve", setter: "setFanBusy", label: "保存自定义曲线" },
];

for (const target of BUSY_TARGETS) {
  const body = extractAsyncFn(target.fn);
  check(`${target.label}：函数体抽到了（否则下面会空转）`, body !== null, true);
  if (body === null) continue;

  const calls = [];
  const env = {
    busy: null,
    fanBusy: null,
    setBusy: (v) => {
      calls.push(`setBusy(${v})`);
      env.busy = v;
    },
    setFanBusy: (v) => {
      calls.push(`setFanBusy(${v})`);
      env.fanBusy = v;
    },
    setError: () => {},
    setFanError: () => {},
    setCurveError: () => {},
    setCurve: () => {},
    setCurveDirty: () => {},
    notifyFailure: () => {},
    apply: () => null,
    applyFan: async () => null,
    refreshFan: async () => null,
    curveTouched: { current: false },
    curve: [[40, 40], [50, 75], [60, 115], [70, 160], [80, 255]],
    fan: null,
    fanProfiles: null,
    curveIsStrictlyIncreasing: () => true,
    getStatus: () => Promise.reject(new Error("RPC 被拒绝")),
    setChargeMode: () => Promise.reject(new Error("RPC 被拒绝")),
    setChargeThreshold: () => Promise.reject(new Error("RPC 被拒绝")),
    setAutoRestore: () => Promise.reject(new Error("RPC 被拒绝")),
    setFanMode: () => Promise.reject(new Error("RPC 被拒绝")),
    setFanCustomCurve: () => Promise.reject(new Error("RPC 被拒绝")),
    FAN_CURVE_POINTS: 5,
    FAN_MODE_LABELS: {},
  };

  const names = Object.keys(env);
  const fn = new Function(...names, toRunnable(body, target.fn));
  const run = fn(...names.map((n) => env[n]));

  try {
    // eslint-disable-next-line no-await-in-loop
    await run("balanced");
  } catch {
    // 异常逃出来也算"没被吞掉"，但 busy 仍需恢复——下面统一断言。
  }

  const final = target.setter === "setBusy" ? env.busy : env.fanBusy;
  // check 只有 (label, actual, expected) 三个参数，细节要拼进 label，
  // 否则会把 detail 当成 expected 而永远失败（本文件踩过一次）。
  check(
    `${target.label}：RPC 异常后 busy 必须恢复为 false`
      + `（序列 ${calls.join(" -> ")}，最终 ${final}）`,
    final,
    false,
  );
}

// ---- 14b. 保存曲线后不得被 refreshFanProfiles 覆盖 ----
const rfpBody = extractAsyncFn("refreshFanProfiles");
check("refreshFanProfiles：函数体抽到了", rfpBody !== null, true);

if (rfpBody !== null) {
  // 构造"自定义模式 + 后端已有保存的曲线"的场景，断言取到的是 custom_curve。
  const customCurve = [[40, 55], [50, 90], [60, 140], [70, 190], [80, 250]];
  const seedCurve = [[40, 40], [50, 75], [60, 115], [70, 160], [80, 255]];

  async function runRefresh(fanMode) {
    let applied = null;
    const env = {
      fanBusy: null,
      fan: { mode: fanMode },
      curveTouched: { current: false },
      setFanProfiles: () => {},
      setCurve: (value) => {
        applied = value;
      },
      FAN_CURVE_POINTS: 5,
      getFanProfiles: () =>
        Promise.resolve({
          ok: true,
          data: {
            // 注意：profiles 里**只有预设**，没有 "custom" —— 这正是缺陷的成因
            profiles: {
              quiet: [[40, 40], [50, 60], [60, 90], [70, 130], [80, 200]],
              balanced: seedCurve,
              performance: [[40, 70], [50, 110], [60, 170], [70, 220], [80, 255]],
            },
            custom_seed: seedCurve,
            custom_curve: customCurve,
          },
        }),
    };
    const names = Object.keys(env);
    const fn = new Function(...names, toRunnable(rfpBody, "refreshFanProfiles"));
    await fn(...names.map((n) => env[n]))();
    return applied;
  }

  const inCustom = await runRefresh("custom");
  check(
    "自定义模式下取到的是**已保存的** custom_curve（不是默认种子）",
    JSON.stringify(inCustom),
    JSON.stringify(customCurve),
  );

  const inQuiet = await runRefresh("quiet");
  check(
    "预设模式下仍取该预设的曲线",
    JSON.stringify(inQuiet),
    JSON.stringify([[40, 40], [50, 60], [60, 90], [70, 130], [80, 200]]),
  );
}

// ---- 14c. saveCurve 之后不得再调 refreshFanProfiles 覆盖结果 ----
// 复用上面已经抽好的 saveBody（第 350 行），它抓的就是同一个函数。
if (saveBody) {
  check(
    "saveCurve 不再调用 refreshFanProfiles（它会把刚保存的曲线拽回旧值）",
    /refreshFanProfiles/.test(saveBody.replace(/\/\/.*$/gm, "")),
    false,
  );
  check(
    "saveCurve 采信 RPC 返回的 curve 作为唯一来源",
    /setCurve\(result\.data\.curve/.test(saveBody),
    true,
  );
}

// 后端 get_fan_profiles 必须真的返回 custom_curve，否则前端拿不到真值。
check(
  "后端 get_fan_profiles 返回 custom_curve",
  /"custom_curve"/.test(readFileSync(join(here, "..", "main.py"), "utf-8")),
  true,
);

/* ------------------- 保存曲线：未真正生效前不得清掉未保存标记 */

// 15. 「保存为自定义并应用」是**两步**动作：
//     ① 保存曲线（写设置文件）→ ② 切到自定义模式（写 EC，曲线才真正在风扇上生效）。
//
//     `setCurveDirty(false)` 原先夹在 ② 之前，于是 ② 失败时按钮立刻变灰、
//     看起来一切就绪，而 EC 还在跑旧模式 —— 用户没有任何可重试的入口。
//     由离线探针复现（.mut/repro_busy.mjs 的 F 段）。
//
//     修法有两处，缺一不可：
//       - `changeFanMode` 必须把成败**回传**（它是 `setFanMode` 的薄封装，
//         原先把异常吞掉只弹通知，调用方无从判断）；
//       - `saveCurve` 只有在回传成功之后才清标记，失败时留下可见原因。
console.log("\n=== 保存曲线：未真正生效前不得清掉未保存标记 ===");

/** 抽出**不带参数**的 `const xxx = async () => {...}`。 */
function extractAsyncNoArgs(name) {
  const re = new RegExp(`const ${name} = async \\(\\) => \\{`);
  const m = re.exec(source);
  if (!m) return null;
  const start = source.indexOf("{", m.index + m[0].length - 1);
  let depth = 0;
  for (let j = start; j < source.length; j += 1) {
    if (source[j] === "{") depth += 1;
    else if (source[j] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(m.index, j + 1);
    }
  }
  return null;
}

const saveCurveOnlyBody = extractAsyncNoArgs("saveCurve");
check(
  "saveCurve：函数体抽到了（否则下面会空转）",
  saveCurveOnlyBody !== null,
  true,
);

if (saveCurveOnlyBody !== null) {
  const savedCurve = [[40, 55], [50, 90], [60, 140], [70, 190], [80, 250]];

  /** 求值跑一遍 saveCurve：曲线保存成功，切模式按参数决定成败。 */
  async function runSaveCurve({ modeSwitchFails }) {
    const state = { dirty: true, curve: null, curveError: null, fanBusy: null };
    const events = [];
    const env = {
      setCurveDirty: (v) => {
        events.push(`setCurveDirty(${v})`);
        state.dirty = v;
      },
      setCurve: (v) => {
        events.push("setCurve(...)");
        state.curve = v;
      },
      setCurveError: (v) => {
        state.curveError = v;
      },
      setFanBusy: (v) => {
        state.fanBusy = v;
      },
      setFanError: () => {},
      notifyFailure: () => {},
      curveTouched: { current: false },
      curve: savedCurve,
      curveIsStrictlyIncreasing: () => true,
      // 当前不是自定义模式，所以第二步一定要切模式。
      fan: { mode: "balanced" },
      setFanCustomCurve: async () => ({ ok: true, data: { curve: savedCurve } }),
      changeFanMode: async () => {
        events.push("changeFanMode(custom)");
        return !modeSwitchFails;
      },
      refreshFan: async () => {
        events.push("refreshFan()");
      },
      // saveCurve 全程用 `t()` 取文案。**必须注入**：不注入的话
      // `t is not defined` 会被它自己的 catch 抓住、变成一个"看起来正常"
      // 的错误字符串，于是"留下可见原因"那条断言就空转了
      // （实测 curveError 曾是字面量 "t is not defined"）。
      t,
    };
    const names = Object.keys(env);
    const fn = new Function(...names, toRunnable(saveCurveOnlyBody, "saveCurve"));
    await fn(...names.map((n) => env[n]))();
    return { state, events };
  }

  const failedSwitch = await runSaveCurve({ modeSwitchFails: true });
  check(
    "切模式失败时不得清掉未保存标记（按钮不能变灰）"
      + `（事件 ${failedSwitch.events.join(" -> ")}，dirty=${failedSwitch.state.dirty}）`,
    failedSwitch.state.dirty,
    true,
  );
  check(
    "切模式失败时要留下可见的原因（不能只是按钮静默变灰）"
      + `（curveError=${JSON.stringify(failedSwitch.state.curveError)}）`,
    // 不写成"是个非空字符串"：那样任何一条无关错误都能骗过它
    // （实测曾因环境缺 `t` 而拿到字面量 "t is not defined"）。
    // 断言必须指向**这一处**真正该给出的文案。
    failedSwitch.state.curveError,
    ZH["error.curveSavedSwitchFailed"],
  );

  const okSwitch = await runSaveCurve({ modeSwitchFails: false });
  check(
    "切模式成功后标记必须清掉（否则按钮永远亮着）"
      + `（事件 ${okSwitch.events.join(" -> ")}，dirty=${okSwitch.state.dirty}）`,
    okSwitch.state.dirty,
    false,
  );

  // 结构守卫：清标记必须排在切模式**之后**。
  // 上面那条求值断言已经能抓住行为，这条是防止有人把顺序改回去
  // 却恰好被某个 stub 掩盖（两边都要守住）。
  //
  // **必须先剥注释**：解释这段顺序的注释里就写着 `setCurveDirty(false)`，
  // 不剥的话 indexOf 命中的是注释，断言会永远失败（本文件已踩过同类坑两次）。
  const saveCode = saveCurveOnlyBody
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "");
  const dirtyIdx = saveCode.indexOf("setCurveDirty(false)");
  const switchIdx = saveCode.indexOf('changeFanMode("custom")');
  check(
    "setCurveDirty(false) 必须排在 changeFanMode 之后"
      + `（dirty 在 ${dirtyIdx}，切模式在 ${switchIdx}）`,
    switchIdx >= 0 && dirtyIdx > switchIdx,
    true,
  );
}

// changeFanMode 必须把成败回传给调用方，否则 saveCurve 无从判断"是否已生效"。
const changeFanModeBody = extractAsyncFn("changeFanMode");
check(
  "changeFanMode：函数体抽到了",
  changeFanModeBody !== null,
  true,
);
if (changeFanModeBody !== null) {
  const code = changeFanModeBody.replace(/\/\/.*$/gm, "");
  check(
    "changeFanMode 在失败分支返回 false",
    /return false/.test(code),
    true,
  );
  check(
    "changeFanMode 在成功路径也返回 true（否则类型上就不是布尔）",
    /return true/.test(code),
    true,
  );
}

// ---------------------------------------------------------------------------
// 16. 两个"失败时序"缺口（用户复查指出）
//   ① `changeFanMode` 在 RPC 成功**之前**就把曲线覆盖成预设、并清掉编辑标记：
//      切换失败时用户正在编辑的曲线被丢掉，而且没有任何提示。
//      **只有成功之后**才允许动本地状态。
//   ② 轮询的 `tick` 只有 finally、没有 catch：`getStatus` reject 时本轮
//      会跳过 `getFanStatus`（风扇状态不刷新），并往外冒一个未被处理的
//      Promise rejection。必须自己捕获，让本轮继续往下走。
console.log("\n=== 失败时序：切模式失败不得丢编辑；轮询必须自己吞掉 RPC 异常 ===");

// ---- 16a. changeFanMode 必须等 RPC 成功之后才覆盖曲线 ----
if (changeFanModeBody !== null) {
  const preset = [[40, 40], [50, 75], [60, 115], [70, 160], [80, 255]];
  const editing = [[10, 99], [50, 75], [60, 115], [70, 160], [80, 255]];

  /** 求值跑一遍 changeFanMode，按参数决定 setFanMode 成败。 */
  async function runChangeFanMode({ rpcOk }) {
    const state = { curve: editing, dirty: true, touched: true, curveError: null };
    const events = [];
    const env = {
      setFanBusy: () => {},
      setCurve: (v) => {
        events.push("setCurve(预设)");
        state.curve = v;
      },
      setCurveDirty: (v) => {
        events.push(`setCurveDirty(${v})`);
        state.dirty = v;
      },
      setCurveError: (v) => {
        state.curveError = v;
      },
      setFanError: () => {},
      notifyFailure: (m) => events.push(`notifyFailure(${m})`),
      curveTouched: {
        get current() {
          return state.touched;
        },
        set current(v) {
          events.push(`curveTouched=${v}`);
          state.touched = v;
        },
      },
      fanProfiles: { profiles: { balanced: preset } },
      FAN_CURVE_POINTS: 5,
      // `changeFanMode` 现在通过 `t()` 取提示文案，测试环境必须提供它 ——
      // 否则函数一进失败分支就 `ReferenceError: t is not defined`，
      // 那会被误读成"实现有问题"。注入真实的 zh 表驱动的 t()，
      // 顺带让文案键写错（漏译）时立刻暴露。
      t,
      applyFan: async (result) => (result.ok ? null : (result.error ?? "失败")),
      setFanMode: async () =>
        rpcOk ? { ok: true, data: { mode: "balanced" } } : { ok: false, error: "RPC 失败" },
    };
    const names = Object.keys(env);
    const fn = new Function(...names, toRunnable(changeFanModeBody, "changeFanMode"));
    const returned = await fn(...names.map((n) => env[n]))("balanced");
    return { state, events, returned };
  }

  const failed = await runChangeFanMode({ rpcOk: false });
  check(
    "切模式失败时不得覆盖用户正在编辑的曲线"
      + `（事件 ${failed.events.join(" -> ")}，曲线首点 ${JSON.stringify(failed.state.curve[0])}）`,
    JSON.stringify(failed.state.curve) === JSON.stringify(editing),
    true,
  );
  check(
    "切模式失败时不得清掉未保存标记 / 编辑标记"
      + `（dirty=${failed.state.dirty}，curveTouched=${failed.state.touched}）`,
    failed.state.dirty === true && failed.state.touched === true,
    true,
  );
  check(
    "切模式失败时必须回传 false（调用方要靠它判断是否生效）",
    failed.returned,
    false,
  );

  // 反向对照：成功之后必须照常覆盖 —— 否则上一条会被"永远不动本地状态"的
  // 坏实现骗过去（那种实现下切到预设模式，滑杆还显示旧曲线）。
  const succeeded = await runChangeFanMode({ rpcOk: true });
  check(
    "切模式成功后必须把预设曲线填进滑杆"
      + `（事件 ${succeeded.events.join(" -> ")}）`,
    JSON.stringify(succeeded.state.curve) === JSON.stringify(preset),
    true,
  );
  check("切模式成功后回传 true", succeeded.returned, true);
}

// ---- 16b. 轮询 tick 必须自己捕获 RPC 异常 ----
const tickBody = extractAsyncNoArgs("tick");
check("轮询 tick：函数体抽到了（否则下面会空转）", tickBody !== null, true);

if (tickBody !== null) {
  /** 求值跑一遍 tick，记录调用序列、是否续排、异常是否逃出。 */
  async function runTick({ statusRejects }) {
    const log = [];
    const box = { scheduled: 0 };
    const env = {
      getStatus: async () => {
        log.push("getStatus");
        if (statusRejects) throw new Error("RPC 被拒绝");
        return { ok: true, data: {} };
      },
      getFanStatus: async () => {
        log.push("getFanStatus");
        return { ok: true, data: { available: true, mode: "auto", curve: [] } };
      },
      apply: () => log.push("apply"),
      setFan: () => log.push("setFan"),
      setFanError: () => log.push("setFanError"),
      setCurve: () => {},
      curveTouched: { current: false },
      fanProfiles: null,
      FAN_CURVE_POINTS: 5,
      // tick 里 `t("error.readBattery")` / `t("error.readState")` 取的是
      // 真实 zh 表，注入后既让函数能跑，也顺带验证这两个键确实存在。
      t,
    };
    const names = Object.keys(env);
    // tick 体里引用的 `cancelled` / `timer` / `setTimeout` / `REFRESH_INTERVAL_MS`
    // 在原源码里是闭包变量，这里补上前缀才能真的把它跑起来。
    const code = toRunnable(tickBody, "tick").replace(/^return /, "const tick = ");
    const fn = new Function(
      "box",
      ...names,
      `
      let cancelled = false;
      let timer = null;
      const setTimeout = () => { box.scheduled += 1; return 1; };
      const REFRESH_INTERVAL_MS = 5000;
      ${code}
      ;
      return tick;
      `,
    );
    const tick = fn(box, ...names.map((n) => env[n]));
    let escaped = null;
    try {
      await tick();
    } catch (err) {
      escaped = err.message;
    }
    return { log, scheduled: box.scheduled, escaped };
  }

  const boom = await runTick({ statusRejects: true });
  check(
    "getStatus 被拒绝时不得抛出未处理的异常（必须自己捕获）"
      + `（逃出=${boom.escaped ?? "无"}）`,
    boom.escaped,
    null,
  );
  check(
    "getStatus 被拒绝时仍必须继续读风扇状态（不能被跳过）"
      + `（序列 ${boom.log.join(" -> ")}）`,
    boom.log.includes("getFanStatus"),
    true,
  );
  check(
    "getStatus 被拒绝时下一轮仍必须续排（轮询不得停死）"
      + `（排了 ${boom.scheduled} 次）`,
    boom.scheduled > 0,
    true,
  );

  // 反向对照：正常路径的序列不能被上面的改动打乱。
  const normal = await runTick({ statusRejects: false });
  check(
    "getStatus 正常时仍按「读电池 → 应用 → 读风扇 → 应用」推进"
      + `（序列 ${normal.log.join(" -> ")}）`,
    normal.log.join(" -> "),
    "getStatus -> apply -> getFanStatus -> setFan -> setFanError",
  );
}

/* ---------------------------------------------------------------- 双语 i18n */

console.log("\n=== 双语文案（i18n）===");

// 直接 import 真实的 i18n 模块（纯函数、不依赖 React），
// 这样测的就是运行时真正会执行的那份代码。
const i18n = await import(pathToFileURL(join(here, "..", "src", "i18n", "index.ts")).href)
  .catch(() => null);

if (i18n === null) {
  // .ts 不能在裸 node 里 import（需要 tsc / 构建）。退化成**解析真实源文件**，
  // 仍然不复制逻辑：用正则把三张表和判定规则读出来再求值。
  const keysSrc = readFileSync(join(here, "..", "src", "i18n", "keys.ts"), "utf-8");
  const idxSrc = readFileSync(join(here, "..", "src", "i18n", "index.ts"), "utf-8");

  // CHINESE_LOCALES 名单（真值表）
  const listMatch = idxSrc.match(/const CHINESE_LOCALES = \[([\s\S]*?)\];/);
  check("能定位到 CHINESE_LOCALES", Boolean(listMatch), true);
  const chineseLocales = [...listMatch[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  check(
    "中文名单含 Steam 的简/繁两种写法",
    String(
      chineseLocales.includes("schinese") && chineseLocales.includes("tchinese"),
    ),
    "true",
  );

  // 用**抽取出的名单**重建判定，再跑不同输入 —— 与后端 set_locale 同一套语义。
  const resolve = (locales) => {
    if (!locales || locales.length === 0) return "en";
    for (const locale of locales) {
      if (typeof locale !== "string") continue;
      if (chineseLocales.includes(locale.trim().toLowerCase())) return "zh";
    }
    return "en";
  };
  check("schinese → zh", resolve(["schinese"]), "zh");
  check("tchinese → zh（繁体也走中文）", resolve(["tchinese"]), "zh");
  check("en → en", resolve(["en"]), "en");
  check("japanese → en", resolve(["japanese"]), "en");
  check("koreana → en", resolve(["koreana"]), "en");
  check("空列表 → en（探测失败退回英文）", resolve([]), "en");
  check("null → en", resolve(null), "en");
  check("undefined → en", resolve(undefined), "en");
  check("大小写不敏感", resolve(["SCHINESE"]), "zh");
  check("列表里任一为中文即判中文", resolve(["en", "tchinese"]), "zh");
  check(
    "非字符串项被跳过、不影响判定",
    resolve([null, 42, "en"]),
    "en",
  );
  check(
    "中文名单与后端 _ZH_LANG_HINTS 一致（两侧不能各认一套）",
    chineseLocales.includes("zh") &&
      // 后端认前缀，这里认全等；两者对 schinese / tchinese / zh* 的结论必须相同
      /_ZH_LANG_HINTS[\s\S]*?"zh"[\s\S]*?"schinese"[\s\S]*?"tchinese"/.test(
        readFileSync(join(here, "..", "main.py"), "utf-8"),
      ),
    true,
  );

  // 两张表：键集一致 + 占位符一致 + 英文表无汉字
  const parseTable = (name) => {
    const src = readFileSync(join(here, "..", "src", "i18n", `${name}.ts`), "utf-8");
    const out = {};
    for (const m of src.matchAll(/"([\w.]+)":\s*"((?:[^"\\]|\\.)*)"/g)) {
      out[m[1]] = m[2].replace(/\\"/g, '"');
    }
    return out;
  };
  const zhFull = parseTable("zh-CN");
  const enFull = parseTable("en-US");
  check("中文表非空", Object.keys(zhFull).length > 0, true);
  check(
    "中英两表键集一致",
    JSON.stringify(Object.keys(zhFull).sort()),
    JSON.stringify(Object.keys(enFull).sort()),
  );
  const phMismatch = Object.keys(zhFull).filter((k) => {
    if (!(k in enFull)) return false;
    const pz = [...zhFull[k].matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");
    const pe = [...enFull[k].matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");
    return pz !== pe;
  });
  check("中英两表占位符一致", JSON.stringify(phMismatch), "[]");
  const cjkInEn = Object.keys(enFull).filter((k) => /[\u4e00-\u9fff]/.test(enFull[k]));
  check("英文表里没有残留汉字", JSON.stringify(cjkInEn), "[]");
  check(
    "MESSAGE_KEYS 与两张表键集一致",
    (() => {
      const m = keysSrc.match(/MESSAGE_KEYS = \[([\s\S]*?)\] as const/);
      if (!m) return "找不到 MESSAGE_KEYS";
      const keys = [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
      const missZh = keys.filter((k) => !(k in zhFull));
      const extraZh = Object.keys(zhFull).filter((k) => !keys.includes(k));
      if (missZh.length || extraZh.length) return `缺=${missZh} 多=${extraZh}`;
      return "";
    })(),
    "",
  );
}

console.log(`\n${"=".repeat(60)}`);
console.log(`通过 ${passed} 项，失败 ${failed} 项`);
if (failed > 0) process.exitCode = 1;
else console.log("全部通过。");
