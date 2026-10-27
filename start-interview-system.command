#!/bin/zsh
# macOS 双击入口。程序在 Skill 内，档案位置保存在本机私有配置中。
set -u
umask 077

skill_dir="${0:A:h}"
server_script="$skill_dir/scripts/local_answer_server.py"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/interview-system"
config_file="$config_dir/data-root"
data_root=''
check_only=0
provided_root=0

pause_if_interactive() {
  if [[ -t 0 ]]; then
    read -r 'reply?按回车关闭窗口：'
  fi
}

while (( $# > 0 )); do
  case "$1" in
    --root)
      if (( $# < 2 )); then
        print -u2 -- '用法：start-interview-system.command [--root 档案绝对路径] [--check]'
        exit 2
      fi
      data_root="$2"
      provided_root=1
      shift 2
      ;;
    --check)
      check_only=1
      shift
      ;;
    *)
      print -u2 -- "未知参数：$1"
      exit 2
      ;;
  esac
done

if (( ! provided_root )) && [[ -f "$config_file" ]]; then
  IFS= read -r data_root < "$config_file"
fi

if [[ -z "$data_root" || ! -f "$data_root/config.json" || ! -f "$data_root/interview-history.html" ]]; then
  if (( check_only )) || [[ ! -t 0 ]]; then
    print -u2 -- '面试档案路径未配置或档案文件不完整。请传入 --root 档案绝对路径。'
    exit 1
  fi
  print -- '请输入已选定的私有面试档案目录绝对路径：'
  read -r data_root
fi

if [[ "$data_root" != /* || ! -f "$data_root/config.json" || ! -f "$data_root/interview-history.html" ]]; then
  print -u2 -- '档案目录必须是绝对路径，且包含 config.json 和 interview-history.html。'
  pause_if_interactive
  exit 1
fi

if [[ ! -f "$server_script" ]]; then
  print -u2 -- 'Skill 服务程序不存在，请检查安装。'
  pause_if_interactive
  exit 1
fi

if (( provided_root )) || [[ ! -f "$config_file" ]]; then
  mkdir -p -- "$config_dir" || exit 1
  print -r -- "$data_root" > "$config_file" || exit 1
  chmod 600 "$config_file" || exit 1
fi

if (( check_only )); then
  print -- '启动配置可用。'
  exit 0
fi

python_cmd="$skill_dir/.venv/bin/python"
if [[ ! -x "$python_cmd" ]]; then
  python_cmd="$(command -v python3 2>/dev/null)"
fi
if [[ -z "$python_cmd" ]]; then
  print -u2 -- '未找到 Python 3，请先确认 Python 已安装。'
  pause_if_interactive
  exit 1
fi

print -- '正在启动面试系统。请保持此窗口打开；用完按 Ctrl+C 停止。'
"$python_cmd" "$server_script" --root "$data_root" --open-browser
status=$?
print -- "面试系统已停止（退出码 $status）。"
pause_if_interactive
exit "$status"
