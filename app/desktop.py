# -*- coding: utf-8 -*-
"""Read-only desktop task adapter. UI calls run in a disposable child process.

Only titles, task indicators and the latest user question are retained. Assistant
responses, credentials and raw UI snapshots are never logged or persisted.
"""
from __future__ import annotations
import atexit
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
import state as st

class DesktopWatcher:
    def __init__(self, start=True):
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        self.probe = base / "assets" / "ChatGPTProbe.exe"
        self._queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._process = None
        self._received = time.monotonic()
        self._active = {}
        self._prompts = {}
        self._slots = {}
        self._misses = {}
        self._last = {}
        if start and sys.platform == "win32" and self.probe.is_file():
            threading.Thread(target=self._worker, daemon=True, name="desktop-task-reader").start()
            atexit.register(self.close)

    def close(self):
        self._stop.set()
        p = self._process
        if p is not None:
            try:
                p.kill()
            except OSError:
                pass

    def _worker(self):
        while not self._stop.is_set():
            p = None
            try:
                p = subprocess.Popen(
                    [str(self.probe)], stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self._process = p
                self._received = time.monotonic()
                for line in p.stdout:
                    if self._stop.is_set():
                        break
                    try:
                        snapshot = json.loads(line)
                        self._received = time.monotonic()
                        try:
                            self._queue.get_nowait()
                        except queue.Empty:
                            pass
                        self._queue.put_nowait(snapshot)
                    except (ValueError, queue.Full):
                        continue
            except OSError:
                pass
            finally:
                if p is not None:
                    try:
                        p.kill()
                    except OSError:
                        pass
                    if p.stdout:
                        p.stdout.close()
                    try:
                        p.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                self._process = None
            self._stop.wait(3)

    @staticmethod
    def _key(row):
        return (str(row.get("source", "")), str(row.get("window", "")), str(row.get("title", "")).strip())

    def _sid(self, key):
        if key not in self._slots:
            # Reuse the legacy Codex app slot to avoid displaying the same task twice.
            if key[0] == "codex" and not any(k[0] == "codex" for k in self._active):
                self._slots[key] = "app"
            else:
                self._slots[key] = "desktop-" + hashlib.sha256("\0".join(key).encode()).hexdigest()[:16]
        return self._slots[key]

    def _apply(self, snapshot, now=None):
        now = time.time() if now is None else now
        if not isinstance(snapshot, dict) or not snapshot.get("ok"):
            return False
        current = {}
        for row in snapshot.get("current", []):
            if isinstance(row, dict) and row.get("source") in ("chatgpt", "codex"):
                key = self._key(row)
                if not key[2]:
                    continue
                current[key] = row
                # A new prompt replaces the previous prompt, including when unreadable.
                if row.get("state") == st.RUNNING:
                    self._prompts[key] = str(row.get("prompt") or "")[:8192]
        observed = {}
        for scope in snapshot.get("scopes", []):
            if isinstance(scope, dict):
                for title in scope.get("titles", []):
                    observed[(str(scope.get("source", "")), str(scope.get("window", "")), str(title))] = True
        running = {}
        for row in snapshot.get("tasks", []):
            if isinstance(row, dict) and row.get("source") in ("chatgpt", "codex") and row.get("state") in (st.RUNNING, st.WAITING, st.FAILED):
                key = self._key(row)
                if key[2]:
                    running[key] = row
        for key, row in current.items():
            observed[key] = True
            if row.get("state") == st.RUNNING:
                running.setdefault(key, row)
        changed = False
        for key, row in running.items():
            prompt = self._prompts.get(key, "")
            previous = self._active.get(key)
            # Changed user question means a new turn even if the running indicator never blinked.
            started = previous["started"] if previous and previous["prompt"] == prompt else now
            sid = self._sid(key)
            task = dict(source=key[0], sid=sid, title=key[2], state=row["state"],
                        prompt=prompt, started=started, observed=now)
            self._active[key] = task
            self._misses.pop(key, None)
            self._emit(task)
            changed = True
        for key in list(self._active):
            if key in running:
                continue
            task = self._active[key]
            # Absence is only a completion signal when this exact chat is still observable.
            if key in observed:
                self._misses[key] = self._misses.get(key, 0) + 1
                if self._misses[key] >= 2:
                    st.write_slot(task["source"], task["sid"], st.DONE, task["title"],
                                  task["prompt"] or "本轮已结束", ttl=12, started=task["started"])
                    del self._active[key]
                    self._misses.pop(key, None)
                    self._prompts.pop(key, None)
                    changed = True
            elif now - task["observed"] > 30:
                # A hidden/collapsed chat is unknown, never falsely marked completed.
                st.write_slot(task["source"], task["sid"], st.IDLE)
                del self._active[key]
                self._prompts.pop(key, None)
                changed = True
        return changed

    @staticmethod
    def _emit(task):
        st.write_slot(task["source"], task["sid"], task["state"], task["title"],
                      task["prompt"] or "当前问题待读取：打开该对话后自动补充",
                      ttl=30, started=task["started"])

    def poll(self):
        # The worker can block on an unresponsive UI provider; never block Qt.
        if self._process is not None and time.monotonic() - self._received > 20:
            try:
                self._process.kill()
            except OSError:
                pass
        changed = False
        try:
            while True:
                changed = self._apply(self._queue.get_nowait()) or changed
        except queue.Empty:
            pass
        # Re-emit only recent confirmed observations. A failed probe cannot keep a false lamp alive.
        now = time.time()
        for task in self._active.values():
            if now - task["observed"] <= 10:
                self._emit(task)
        return changed
