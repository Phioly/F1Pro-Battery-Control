#!/usr/bin/env bash
# 把本目录作为 Decky 插件安装到 ~/homebrew/plugins/ 并重启 Decky。
set -euo pipefail

PLUGIN_NAME="F1 Pro Battery Control"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_ROOT="${DECKY_PLUGINS_DIR:-$HOME/homebrew/plugins}"
DEST_DIR="$DEST_ROOT/$PLUGIN_NAME"

if [ ! -f "$SRC_DIR/dist/index.js" ]; then
  echo "错误：找不到 dist/index.js。"
  echo "请先在本目录运行 npm install && npm run build，然后再执行本脚本。"
  exit 1
fi

echo "源目录   : $SRC_DIR"
echo "目标目录 : $DEST_DIR"
echo

sudo mkdir -p "$DEST_ROOT"
# 只复制运行所需的文件，避免把 node_modules / src 带进插件目录。
sudo rm -rf "$DEST_DIR"
sudo mkdir -p "$DEST_DIR"
sudo cp "$SRC_DIR/main.py" "$DEST_DIR/"
sudo cp "$SRC_DIR/plugin.json" "$DEST_DIR/"
sudo cp "$SRC_DIR/package.json" "$DEST_DIR/"
sudo cp "$SRC_DIR/README.md" "$DEST_DIR/"
sudo cp "$SRC_DIR/LICENSE" "$DEST_DIR/"
sudo cp -r "$SRC_DIR/dist" "$DEST_DIR/dist"

echo "已复制，正在重启 Decky…"
sudo systemctl restart plugin_loader

echo "完成。回到游戏模式，在 QAM 的 Decky 中找到「$PLUGIN_NAME」。"
