#!/bin/zsh
# macOS double-click entry point. All paths are relative to this launcher, not Terminal's cwd.
SCIENTIST_ROOT="${0:A:h}"
cd "$SCIENTIST_ROOT" || exit 1
if [[ -x "$SCIENTIST_ROOT/.venv/bin/python" ]]; then
  "$SCIENTIST_ROOT/.venv/bin/python" "$SCIENTIST_ROOT/scripts/start_scientist.py" "$@"
elif command -v python3 >/dev/null 2>&1; then
  python3 "$SCIENTIST_ROOT/scripts/start_scientist.py" "$@"
else
  print -u2 "请先安装 Python 3.11+，再根据 docs/AI_Scientist_Software_用户指南.md 初始化环境。"
  exit 1
fi
SCIENTIST_EXIT=$?
if [[ "$SCIENTIST_EXIT" -ne 0 ]]; then
  print ""
  read "?启动失败；请查看上方提示。按回车关闭此窗口。"
fi
exit "$SCIENTIST_EXIT"

