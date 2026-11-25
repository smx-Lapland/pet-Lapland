#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "请先按 README 的 macOS 步骤创建 .venv 并安装依赖。" >&2
    exit 1
fi
exec .venv/bin/python -m pet.main "$@"
