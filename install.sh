#!/usr/bin/env bash
# 把本目录作为 Decky 插件安装到 ~/homebrew/plugins/ 并重启 Decky。
set -euo pipefail

PLUGIN_NAME="F1Pro EC Control"
# 改名前的旧插件目录名。改名后 Decky 会把新旧两份当成**两个不同的插件**：
# QAM 里会同时出现两个条目，而且都会去抢同一套 sysfs 节点（同一个 EC），
# 两个控制线程互相覆盖 PWM。所以安装时必须清掉旧的。
LEGACY_PLUGIN_NAMES=("F1 Pro Battery Control")
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_ROOT="${DECKY_PLUGINS_DIR:-$HOME/homebrew/plugins}"
DEST_DIR="$DEST_ROOT/$PLUGIN_NAME"

# 插件目录应该归**当前登录用户**所有，不是 root。
#
# 原因：下面用 `sudo mkdir` + `sudo cp` 写入，不加处理的话文件属主会变成
# root:root。虽然插件自身以 root 运行（plugin.json 的 flags），但 Decky
# 还要以普通用户身份去**列目录、读资源、做删除/更新**，root 属主会让这些
# 操作受限。
#
# 取属主的三级回退（顺序很重要，越靠前越准）：
#   ① SUDO_USER —— `sudo ./install.sh` 时由 sudo 设置为调用者，最可靠；
#   ② stat $SRC_DIR 的属主 —— 源码目录的属主就是"这个项目属于谁"。
#      直接用 $USER 是不行的：脚本若以 root 身份运行（su / systemd），
#      $USER 也会是 root，chown 就成了空操作，而且**静默失效**；
#   ③ 最后才退回 ${USER:-root}。
if [ -n "${SUDO_USER:-}" ]; then
  TARGET_OWNER="$SUDO_USER"
elif OWNER_FROM_SRC="$(stat -c '%U' "$SRC_DIR" 2>/dev/null)" && [ -n "$OWNER_FROM_SRC" ]; then
  TARGET_OWNER="$OWNER_FROM_SRC"
else
  TARGET_OWNER="${USER:-root}"
fi
TARGET_GROUP="$(id -gn "$TARGET_OWNER" 2>/dev/null || echo "$TARGET_OWNER")"

if [ ! -f "$SRC_DIR/dist/index.js" ]; then
  echo "错误：找不到 dist/index.js。"
  echo "请先在本目录运行 npm install && npm run build，然后再执行本脚本。"
  exit 1
fi

echo "源目录   : $SRC_DIR"
echo "目标目录 : $DEST_DIR"
echo "归属用户 : $TARGET_OWNER:$TARGET_GROUP"
echo

sudo mkdir -p "$DEST_ROOT"

for legacy in "${LEGACY_PLUGIN_NAMES[@]}"; do
  legacy_dir="$DEST_ROOT/$legacy"
  if [ -d "$legacy_dir" ]; then
    echo "清理旧插件目录：$legacy_dir"
    sudo rm -rf "$legacy_dir"
  fi
done

# 只复制运行所需的文件，避免把 node_modules / src 带进插件目录。
sudo rm -rf "$DEST_DIR"
sudo mkdir -p "$DEST_DIR"
sudo cp "$SRC_DIR/main.py" "$DEST_DIR/"
sudo cp "$SRC_DIR/plugin.json" "$DEST_DIR/"
sudo cp "$SRC_DIR/package.json" "$DEST_DIR/"
sudo cp "$SRC_DIR/README.md" "$DEST_DIR/"
sudo cp "$SRC_DIR/LICENSE" "$DEST_DIR/"
sudo cp -r "$SRC_DIR/dist" "$DEST_DIR/dist"

# 把上面那些 sudo 写出来的文件交还给登录用户（必须在重启 Decky 之前，
# 这样 loader 首次扫描时看到的就是正确的属主）。
sudo chown -R "$TARGET_OWNER:$TARGET_GROUP" "$DEST_DIR"

echo "已复制，正在重启 Decky…"
sudo systemctl restart plugin_loader

echo "完成。回到游戏模式，在 QAM 的 Decky 中找到「$PLUGIN_NAME」。"
