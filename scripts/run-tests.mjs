#!/usr/bin/env node
/**
 * 测试入口包装。
 *
 * **为什么需要它**：发布 ZIP 里不含 `tests/`（仓库资产，有意不进包），
 * 但 `package.json` 会随包发布、里面带着 `npm test` 等脚本。
 * 于是从 ZIP 解压出来的目录里跑 `npm test`，用户看到的是 Node 的
 * `Error: Cannot find module '.../tests/test_display.mjs'`——
 * 一句纯技术报错，不解释"为什么"和"该怎么办"。
 *
 * 本脚本把那种情况换成一条人话提示，正常环境下则原样转发给真正的测试。
 *
 * 用法（由 package.json 的 scripts 调用）：
 *   node scripts/run-tests.mjs display   # 前端显示规则测试
 *   node scripts/run-tests.mjs backend   # 后端内核交互测试
 *   node scripts/run-tests.mjs all       # 两套一起
 */

import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");

/**
 * 测试套件登记表。
 *
 * `export` 出来是为了**可测试**：测试直接导入它做断言，
 * 不必 spawn 子进程（本机 Windows Git Bash 下 spawn 会 EBUSY，
 * `status` 恒为 `null`，断言会全部落空）。
 */
export const SUITES = {
  display: {
    file: join(root, "tests", "test_display.mjs"),
    cmd: process.execPath,
    args: [join(root, "tests", "test_display.mjs")],
    label: "前端显示规则测试",
  },
  backend: {
    file: join(root, "tests", "test_backend.py"),
    cmd: "python",
    args: [join(root, "tests", "test_backend.py")],
    label: "后端内核交互测试",
  },
};

const REPO_URL = "https://github.com/Phioly/F1Pro-EC-Control";

/** 解析命令行参数为要跑的套件名列表。未知目标返回 `null`。 */
export function resolveTargets(mode) {
  if (mode === "all") return ["display", "backend"];
  if (Object.hasOwn(SUITES, mode)) return [mode];
  return null;
}

/** 生成"测试文件缺失"的人话提示（发布 ZIP 的预期情况）。 */
export function missingHint(keys) {
  const names = [
    ...new Set(keys.map((k) => SUITES[k].file.split(/[\\/]/).slice(-2).join("/"))),
  ];
  return [
    "",
    `找不到测试文件：${names.join("、")}`,
    "",
    "如果你正在 **发布 ZIP 解压出来的目录** 里运行，这是正常的：",
    "  ZIP 只含**运行时需要**的文件（main.py、dist/index.js、install.sh …）。",
    "  `tests/` 与 `assets/` 是**仓库资产**，有意不进包 ——",
    "  掌机安装根本用不到测试，塞进去只会造成困惑。",
    "",
    `  想跑测试请用源码仓库：${REPO_URL}`,
    "",
    "  （从源码仓库运行需要先 `npm install`。）",
    "",
  ].join("\n");
}

/**
 * 判断每个目标套件的可运行状态。
 * 返回 `{ missing: [...keys], runnable: [...keys] }`。
 */
export function inspectTargets(keys) {
  const missing = keys.filter((k) => !existsSync(SUITES[k].file));
  const runnable = keys.filter((k) => existsSync(SUITES[k].file));
  return { missing, runnable };
}

/**
 * CLI 的**决策**部分：给定目标模式与各套件的运行结果，算出退出码与要打印的提示。
 *
 * 抽成纯函数是为了**可测试** —— 退出码这种事必须被断言覆盖，
 * 但在本机（Windows）测试进程里 spawn 子进程会 EBUSY，
 * 没法靠"真跑一遍再读退出码"来验证。把决策分离出来后，
 * 测试可以直接喂各种输入、断言退出码，不必起进程。
 *
 * @param {string} mode 命令行目标（display / backend / all / 其它）
 * @param {Record<string, boolean>} results 每个套件的运行结果（true = 通过）
 * @returns {{ exitCode: number, kind: "unknown"|"missing"|"failed"|"ok" }}
 */
export function decideExit(mode, results = {}) {
  const keys = resolveTargets(mode);
  if (keys === null) return { exitCode: 2, kind: "unknown" };

  const { missing } = inspectTargets(keys);
  if (missing.length > 0) return { exitCode: 1, kind: "missing" };

  const failed = keys.filter((k) => results[k] === false);
  if (failed.length > 0) return { exitCode: 1, kind: "failed" };

  return { exitCode: 0, kind: "ok" };
}

function run(key) {
  const suite = SUITES[key];
  if (!existsSync(suite.file)) {
    return { ok: false, suite };
  }
  console.log(`\n=== ${suite.label} ===\n`);
  const proc = spawnSync(suite.cmd, suite.args, { stdio: "inherit", cwd: root });
  return { ok: proc.status === 0, suite };
}

// ---------------------------------------------------------------------------
// CLI 入口（被 import 时不执行）
// ---------------------------------------------------------------------------
const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];

if (isMain) {
  const mode = process.argv[2] ?? "all";
  const keys = resolveTargets(mode);

  if (keys === null) {
    console.error(`未知的测试目标：${mode}（可选：display / backend / all）`);
    process.exit(2);
  }

  // 先统一检查文件是否齐全：缺任何一个就整体拒绝，不要"跑一半"。
  const { missing } = inspectTargets(keys);
  if (missing.length > 0) {
    console.error(missingHint(missing));
    process.exit(1);
  }

  const results = {};
  for (const key of keys) {
    const { ok, suite } = run(key);
    results[key] = ok;
    if (!ok) console.error(`  ✗ ${suite.label}`);
  }

  const { exitCode, kind } = decideExit(mode, results);
  if (kind === "failed") {
    const failedLabels = keys.filter((k) => results[k] === false).map((k) => SUITES[k].label);
    console.error(`\n以下测试未通过：${failedLabels.join("、")}\n`);
  } else if (kind === "ok") {
    console.log("\n全部测试通过。\n");
  }
  process.exit(exitCode);
}
