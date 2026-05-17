"""
Tiny launcher that double-forks a target command so it survives parent exit.

Used by tools/fetcher_cycle.sh on macOS, where `nohup` alone is not enough:
launchd reaps the entire process group of a job when the leader exits.
Double-fork + setsid escapes the original session so the grandchild lives.

Prints the grandchild's PID to stdout and exits 0. Stdin is wired to /dev/null;
stdout/stderr go to the file passed as --log (default: append, created if absent).

Usage:
    python tools/daemonize.py --log data/fetch_images_long.log \
        -- .venv/bin/python tools/fetch_images.py --scope long
"""
from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True, help="Path for the child's stdout+stderr (append).")
    ap.add_argument("--chdir", default=os.getcwd(), help="cd here before exec.")
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="The command to launch (separator '--' optional).")
    args = ap.parse_args()

    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("error: no command given", file=sys.stderr)
        return 2

    # First fork — return PID to caller via stdout right away. We can't print
    # the grandchild's PID directly across the fork boundary, so use a small
    # pipe between the second fork (parent) and us.
    r_fd, w_fd = os.pipe()

    pid1 = os.fork()
    if pid1 != 0:
        # Original process — close the write end, read the grandchild PID, exit.
        os.close(w_fd)
        with os.fdopen(r_fd, "r") as r:
            data = r.read().strip()
        os.waitpid(pid1, 0)
        if data:
            print(data)
        return 0

    # Child #1 — escape the session.
    os.close(r_fd)
    os.setsid()
    pid2 = os.fork()
    if pid2 != 0:
        # Child #1: report the grandchild PID, then exit.
        os.write(w_fd, f"{pid2}\n".encode())
        os.close(w_fd)
        os._exit(0)

    # Child #2 (the grandchild). Replace stdin/stdout/stderr and exec.
    os.close(w_fd)
    try:
        os.chdir(args.chdir)
    except OSError:
        pass
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    logfd = os.open(args.log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    os.dup2(logfd, 1)
    os.dup2(logfd, 2)
    os.close(logfd)
    try:
        os.execvp(cmd[0], cmd)
    except OSError as e:
        os.write(2, f"execvp failed: {e}\n".encode())
        os._exit(127)


if __name__ == "__main__":
    sys.exit(main())
