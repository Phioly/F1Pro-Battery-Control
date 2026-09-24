#!/usr/bin/env python3
"""F1Pro EC Control —— 产物校验 + 打包脚本。

为什么要把校验固化在这里：这些检查原先每次都在命令行临时手写，检查项会漂移。
v0.5.1 就漏掉了「main.py 里仍在提示 _root」这一项，结果交付了一个
plugin.json 与 main.py 自相矛盾的包。现在所有前置检查都在这里，跑一次即可。

用法：
    python package.py            # 校验 + 打包
    python package.py --check    # 只校验，不打包

退出码非 0 表示校验未通过，此时不会生成 zip。
"""

from __future__ import annotations

import json
import os
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 本脚本放在插件目录**内部**（GitHub 仓库根就是插件本身），所以插件目录就是 HERE。
# 这样从仓库任意位置调用都能工作：cd <repo> && python package.py
PLUGIN_DIR = HERE

# 打包产物（zip）写到插件目录的上一级，避免污染仓库工作区
# —— 也就是本地的 f1pro-plugin/ 目录，或 GitHub 上的仓库外层。
OUTPUT_DIR = HERE.parent

# 不进入发布包的目录 / 文件
# .mut 是变异验证的工作目录（真机不需要，且里面存着 main.py 的备份副本，
# 混进发布包既臃肿又容易让人误改到旧版本）。
# tests / assets 是「仓库资产」而非运行时所需：
#   tests  —— 源码仓库提供 npm test / python tests/test_backend.py；
#   assets —— plugin.json 的 publish.image 指向 GitHub raw 链接，与包内内容无关。
# 两者都留在仓库里、不进发布包（.gitignore 里有对应注释）。
EXCLUDE_DIRS = {
    "node_modules",
    ".rollup.cache",
    "__pycache__",
    "dist/assets",
    "tests",
    ".mut",
    "assets",
    # 版本库与开发向源码目录：**绝不能进发布包**
    # （.git 尤其危险：一旦入库会把全部对象塞进 ZIP，体积暴涨且泄露历史）
    ".git",
    "scripts",
    "src",
}
EXCLUDE_SUFFIX = (".pyc", ".orig", ".gitignore")
# 单文件排除：本脚本是开发工具（校验 + 打包），掌机用不到，不必进安装包。
EXCLUDE_FILES = {"package.py", "CONTRIBUTING.md"}

# 参与「过时建议」扫描的文本文件（发布包内）
TEXT_FILES = [
    "main.py",
    "README.md",
    "README.en.md",
    "install.sh",
    "plugin.json",
    "package.json",
    "rollup.config.js",
    "tsconfig.json",
    "src/index.tsx",
]

# `_root` 在发布包内只允许作为「被纠正的错误值」出现。
# 同一行必须带上下面的某个纠正词，否则视为仍在向用户推荐 _root。
CORRECTION_MARKERS = ("不是", "不存在", "写成", "至今写着", "不要", "勿", "不生效", "never")

RPC_RE = re.compile(r'callable<\s*\[[^\]]*\]\s*,[^>]*>\s*\(\s*"([A-Za-z_][A-Za-z0-9_]*)"')


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        line = f"  [{mark}] {label}"
        if detail and not ok:
            line += f"\n         -> {detail}"
        print(line)
        if not ok:
            self.failures.append(label)

    def ok(self) -> bool:
        return not self.failures


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    check_only = "--check" in sys.argv

    manifest = json.loads(read_text(PLUGIN_DIR / "plugin.json"))
    pkg = json.loads(read_text(PLUGIN_DIR / "package.json"))
    dist_js = PLUGIN_DIR / "dist" / "index.js"
    main_py = PLUGIN_DIR / "main.py"
    readme = PLUGIN_DIR / "README.md"

    c = Checker()

    # ---------------------------------------------------------- manifest 约定
    print("=== Decky loader 加载前置校验 ===")
    # 这条守的是**安装包**的形态：Decky 按 zip 内的顶层文件夹名建立安装目录、
    # 设置目录与日志目录，而前端 definePlugin({name}) 也必须与之一致。
    # 注意它校验的不是"本脚本所在的文件夹叫什么" —— GitHub 仓库名
    # （F1Pro-EC-Control）与插件名（F1Pro EC Control）本来就不同，
    # 那不影响发布包，因为打包时会把顶层目录写成 name（见下方 zip 打包段）。
    c.check(
        "plugin.json 的 name 可直接作为安装目录名（无路径分隔符/双点）",
        bool(manifest.get("name"))
        and "/" not in str(manifest.get("name"))
        and "\\" not in str(manifest.get("name"))
        and ".." not in str(manifest.get("name")),
        f"name={manifest.get('name')!r}",
    )
    # 前端注册名必须与 plugin.json 的 name 一致 —— 否则 Decky 界面标题与
    # 安装目录/设置目录会对不上（这是改名时最容易漏的一处）。
    tsx_text = read_text(PLUGIN_DIR / "src" / "index.tsx")
    c.check(
        "前端 definePlugin({name}) 与 plugin.json 的 name 一致",
        f'name: "{manifest.get("name")}"' in tsx_text,
        f"期望前端出现 name: \"{manifest.get('name')}\"",
    )
    for key in ("name", "author", "flags"):
        c.check(f"plugin.json 含 {key} 字段（loader 直接下标取值）", key in manifest)
    c.check("api_version >= 1", manifest.get("api_version", 0) >= 1)
    c.check(
        "flags 精确包含 \"root\"（写 sysfs 必需）",
        "root" in manifest.get("flags", []),
        f"flags={manifest.get('flags')!r}",
    )
    c.check(
        "flags 是 [\"root\"] 这个确切集合（不含 _root / hot_reload 等过时值）",
        manifest.get("flags") == ["root"],
        f"flags={manifest.get('flags')!r}",
    )
    c.check("package.json 的 type == module（否则走已废弃的 IIFE 路径，界面空白）",
            pkg.get("type") == "module")
    c.check("package.json 含 version（loader 直接下标取值）", bool(pkg.get("version")))
    c.check("package.json 的 main 指向 dist/index.js", pkg.get("main") == "dist/index.js")

    # ---------------------------------------------------------- 前端构建产物
    print("\n=== 前端产物校验 ===")
    c.check("dist/index.js 存在", dist_js.is_file())
    if dist_js.is_file():
        src = read_text(dist_js)
        c.check("无残留裸 import（外部依赖应已替换为 SP_* / DFL 全局）",
                "from '@decky/ui'" not in src and 'from "@decky/ui"' not in src)
        c.check("结尾导出 default", "as default" in src[-400:], src[-200:])
        c.check("体积合理（>5KB，说明确实打进了代码）", len(src) > 5000, f"{len(src)} 字节")

        # **产物必须比源码新**。曾经出现过改了 src/index.tsx 但忘了重新
        # `npm run build`，于是包里装的是上一版界面 —— 而且很难发现，
        # 因为文件确实存在、体积也正常，只有真机上才看出"改动没生效"。
        src_mtime = (PLUGIN_DIR / "src" / "index.tsx").stat().st_mtime
        c.check(
            "dist/index.js 比 src/index.tsx 新（否则包里是旧的界面）",
            dist_js.stat().st_mtime >= src_mtime,
            f"dist={dist_js.stat().st_mtime:.0f} src={src_mtime:.0f}，请重跑 npm run build",
        )

        # **比对中文文案时要先还原 \\uXXXX 转义**。
        # esbuild 默认把非 ASCII 转成 `\u81EA\u5B9A\u4E49` 形式，
        # 直接 `"自定义" in bundle` 永远是 False —— 会得出"改动没进包"的错误结论
        # （实际进了）。这里统一解码后再匹配，任何前端文案断言都走这个变量。
        bundle_text = re.sub(
            r"\\u([0-9a-fA-F]{4})",
            lambda m: chr(int(m.group(1), 16)),
            src,
        )
        c.check(
            "产物里能解出中文（确认解码生效，避免下面的断言空转）",
            "风扇" in bundle_text or "电池" in bundle_text,
        )
        # 界面里不该再出现网格布局：真机上它导致过"整行超宽"与"按钮重叠"。
        c.check(
            "产物中无网格布局残留（gridTemplateColumns 会导致超宽/重叠）",
            "gridTemplateColumns" not in bundle_text,
        )
    c.check("build 脚本存在", "build" in pkg.get("scripts", {}))

    # ---------------------------------------------------------- 前后端 RPC 对齐
    print("\n=== 前后端 RPC 对齐 ===")
    tsx = tsx_text  # 复用上面读过的内容，避免重复 IO
    py = read_text(main_py)
    rpc_names = sorted(set(RPC_RE.findall(tsx)))
    c.check("前端解析出 RPC 方法名", bool(rpc_names), "未匹配到 callable<...>(...) 写法")
    for name in rpc_names:
        c.check(f"后端实现了 {name}()", re.search(rf"async def {name}\s*\(", py) is not None)

    # ------------------------------------------------- 过时建议扫描（本 bug 的护栏）
    print("\n=== 过时建议扫描（_root）===")
    for rel in TEXT_FILES:
        path = PLUGIN_DIR / rel
        if not path.is_file():
            c.check(f"{rel} 存在", False)
            continue
        for lineno, line in enumerate(read_text(path).splitlines(), 1):
            if "_root" not in line:
                continue
            if any(marker in line for marker in CORRECTION_MARKERS):
                continue
            c.check(
                f"{rel}:{lineno} 提到 _root 但没有同时说明它是错的",
                False,
                line.strip(),
            )
    c.check("安装脚本用的是官方服务名 plugin_loader",
            "systemctl restart plugin_loader" in read_text(PLUGIN_DIR / "install.sh"))
    c.check("README 记录了插件日志位置",
            "homebrew/logs" in read_text(readme))

    # ------------------------------------------------- 双语 README 一致性
    # 互链存在不代表锚点对、也不代表版本号跟着走。这里只守"两份都在、且互链双向"，
    # 锚点由 .mut/check_readme_anchors.py 负责（它会按 GitHub 规则算 slug）。
    print("\n=== 双语 README ===")
    readme_en = PLUGIN_DIR / "README.en.md"
    c.check("README.en.md 存在", readme_en.is_file())
    if readme_en.is_file():
        zh_text = read_text(readme)
        en_text = read_text(readme_en)
        c.check("中文 README 链到英文版", "](README.en.md)" in zh_text)
        c.check("英文 README 链回中文版", "](README.md)" in en_text)
        # 版本号必须两份一致，否则用户按错版本的说明去装包。
        version = str(pkg["version"])
        c.check(
            f"两份 README 都写了当前版本 v{version}",
            f"v{version}" in zh_text and f"v{version}" in en_text,
        )
        # ZIP 名同理：写错版本用户会去找一个不存在的文件。
        for label, text in (("中文", zh_text), ("英文", en_text)):
            c.check(
                f"{label} README 里的 ZIP 名是当前版本",
                f"F1ProECControl-v{version}.zip" in text,
            )

    print()
    if not c.ok():
        print(f"校验未通过：{len(c.failures)} 项失败，已中止打包。")
        for f in c.failures:
            print(f"  - {f}")
        return 1

    print("全部校验通过。")

    if check_only:
        return 0

    # ------------------------------------------------------------------ 打包
    version = pkg["version"]
    zip_name = OUTPUT_DIR / f"F1ProECControl-v{version}.zip"
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PLUGIN_DIR):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
            for name in sorted(files):
                if name.endswith(EXCLUDE_SUFFIX) or name in EXCLUDE_FILES:
                    continue
                if name == ".DS_Store":
                    continue
                full = Path(root) / name
                # 用 PLUGIN_DIR 作基准，保证 zip 内顶层目录就是插件文件夹名
                # （Decky 要求安装包顶层只有插件目录一个）
                zf.write(full, full.relative_to(PLUGIN_DIR.parent).as_posix())

    print(f"\n=== {zip_name.name} ===")
    with zipfile.ZipFile(zip_name) as zf:
        names = zf.namelist()
        for n in names:
            print("  " + n)
        broken = zf.testzip()
        print("\n  zip 完整性:", "通过" if broken is None else f"损坏于 {broken}")
        tops = {n.split("/", 1)[0] for n in names}
        print("  顶层目录:", tops)
    c.check("zip 顶层只有插件文件夹一个", tops == {PLUGIN_DIR.name}, str(tops))
    # **防回归**：发布包里绝不能出现版本库或开发向文件/目录。
    # 踩过一次：`git init` 之后包内混进 83 个 `.git/` 条目，ZIP 从 88 KB 涨到 614 KB
    # 且把完整历史泄露出去。这里对包内每一条路径做独立断言，不靠"排除清单写没写对"。
    forbidden_dirs = (".git", "tests", "src", "scripts", "assets", "node_modules", ".mut", "__pycache__")
    forbidden_files = ("package.py", "CONTRIBUTING.md", ".gitignore")
    bad = []
    for n in names:
        parts = n.split("/")[1:]  # 去掉顶层插件目录
        if any(p in forbidden_dirs for p in parts):
            bad.append(n)
        elif parts and parts[-1] in forbidden_files:
            bad.append(n)
    c.check(
        "发布包内不含 .git / tests / src / scripts / 开发工具",
        not bad,
        f"多出：{bad[:6]}" if bad else "",
    )
    print(f"  大小: {zip_name.stat().st_size} 字节")
    print(f"  版本: {version}")

    return 0 if c.ok() else 1


if __name__ == "__main__":
    raise SystemExit(main())
