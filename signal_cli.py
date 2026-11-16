# -*- coding: utf-8 -*-
"""状态写入端（生产者）。任何脚本 / Hook / 批处理都可以调它。

用法
----
1) 直接置状态
   python signal_cli.py running "生成 Q3 运营报告" -d "读取数据表"
   python signal_cli.py waiting "批量重命名" -d "等待确认"
   python signal_cli.py done    "生成 Q3 运营报告" -d "用时 42s"
   python signal_cli.py failed  "抓取竞品价格" -d "接口 429"
   python signal_cli.py idle

2) 包一条命令，自动维护开始/完成/失败（推荐）
   python signal_cli.py run "生成 Q3 运营报告" -- python build_report.py

   run 模式会在执行期间每 4 秒续期一次心跳（ttl=12s），
   命令退出码 0 → done，非 0 → failed。
   这样即使脚本卡死，12 秒内也会自动回落为空闲，不会亮假灯。

3) 长任务手动心跳
   python signal_cli.py running "长任务" -t 30     # 每次调用续期 30s
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))

import state as st  # noqa: E402


def cmd_set(args) -> int:
    s = st.write_state(args.state, args.title or "", args.detail or "", args.ttl)
    if not args.quiet:
        print(f"[信号灯] {s.state} · {st.LABELS.get(s.state, '')} {s.title}")
    return 0


def cmd_run(args) -> int:
    if not args.cmd:
        print("用法: signal_cli.py run \"标题\" -- <命令...>", file=sys.stderr)
        return 2

    title = args.title or ""
    ttl = args.ttl if args.ttl is not None else 12.0
    t0 = time.time()
    st.write_state(st.RUNNING, title, "启动中", ttl, started=t0)

    try:
        proc = subprocess.Popen(args.cmd)
    except FileNotFoundError as e:
        st.write_state(st.FAILED, title, f"命令不存在: {e}", 30)
        return 127

    # 心跳续期，避免长任务被 TTL 误判为空闲。
    # started 固定为 t0，否则心跳会把耗时一起重置成 0。
    last_beat = time.time()
    while True:
        rc = proc.poll()
        if rc is not None:
            break
        if time.time() - last_beat >= 4.0:
            st.write_state(st.RUNNING, title, "执行中", ttl, started=t0)
            last_beat = time.time()
        time.sleep(0.25)

    dur = time.time() - t0
    elapsed = f"{dur:.0f}s" if dur < 60 else f"{int(dur // 60)}m{int(dur % 60):02d}s"
    if rc == 0:
        st.write_state(st.DONE, title, f"完成 · 用时 {elapsed}", st.DONE_HOLD)
    else:
        st.write_state(st.FAILED, title, f"退出码 {rc} · 用时 {elapsed}", st.FAILED_HOLD)
    return rc


def cmd_clear(args) -> int:
    st.write_state(st.IDLE, "", "", 0)
    if not args.quiet:
        print("[信号灯] idle")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True, description="AI 工作信号灯 · 状态写入端")
    ap.add_argument("-q", "--quiet", action="store_true", help="不输出提示")

    sub = ap.add_subparsers(dest="mode")

    p_set = sub.add_parser("set", help="设置状态")
    p_set.add_argument("state", choices=list(st.VALID_STATES))
    p_set.add_argument("title", nargs="?", default="")
    p_set.add_argument("-d", "--detail", default="")
    p_set.add_argument("-t", "--ttl", type=float, default=None)

    p_run = sub.add_parser("run", help="包裹命令，自动维护状态")
    p_run.add_argument("title", nargs="?", default="")
    p_run.add_argument("-t", "--ttl", type=float, default=12.0)
    p_run.add_argument("cmd", nargs=argparse.REMAINDER)

    p_clear = sub.add_parser("clear", help="清空为 idle")

    # 便捷写法：signal_cli.py running "标题"  → 等价 set running
    argv = sys.argv[1:]
    if argv and argv[0] in st.VALID_STATES:
        argv = ["set"] + argv
    elif not argv:
        argv = ["-h"]

    args = ap.parse_args(argv)

    if args.mode == "set":
        return cmd_set(args)
    if args.mode == "run":
        if args.cmd and args.cmd[0] == "--":
            args.cmd = args.cmd[1:]
        return cmd_run(args)
    if args.mode == "clear":
        return cmd_clear(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
