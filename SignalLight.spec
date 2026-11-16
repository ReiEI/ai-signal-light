# -*- mode: python ; coding: utf-8 -*-
"""Build the UI and its read-only Windows task probe from source."""
from pathlib import Path
import os
import subprocess

root = Path(SPECPATH)
probe_source = root / "assets" / "ChatGPTProbe.cs"
probe = root / "assets" / "ChatGPTProbe.exe"
if not probe.is_file() or probe.stat().st_mtime < probe_source.stat().st_mtime:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    compiler = windows / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    refs = []
    for assembly in ("UIAutomationClient", "UIAutomationTypes", "WindowsBase"):
        refs.append(next((windows / "Microsoft.NET/assembly/GAC_MSIL" / assembly).glob("*/*.dll")))
    subprocess.run([str(compiler), "/nologo", "/target:exe", "/platform:x64",
                    "/out:" + str(probe)] + ["/reference:" + str(p) for p in refs] +
                   ["/reference:System.Web.Extensions.dll", str(probe_source)], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

a = Analysis(
    ['app/main.py'], pathex=['app'], binaries=[],
    datas=[('assets/signallight.ico', 'assets')],
    hiddenimports=['state','theme','metrics','codex','desktop','cooling','cooling_ui','ecfan','powerplan','textio'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
# Normally use PyInstaller's dependency collection. Hosts with a custom Python
# launcher may optionally provide a verified native runtime snapshot.
override = os.environ.get("AISIGNAL_NATIVE_RUNTIME")
if override:
    runtime = Path(override).resolve()
    if not runtime.is_dir():
        raise RuntimeError("AISIGNAL_NATIVE_RUNTIME must point to a verified native-runtime directory.")
    native = [(str(p.relative_to(runtime)), str(p), "BINARY")
              for p in sorted(runtime.rglob("*"))
              if p.is_file() and p.suffix.lower() in (".dll", ".pyd")]
    existing = {Path(d).name.lower() for d, s, k in native}
    native += [(d, s, k) for d, s, k in a.binaries
               if Path(d).name.lower() not in existing and Path(d).name.lower() == "_queue.pyd"]
    a.binaries = native
a.binaries.append(("assets/ChatGPTProbe.exe", str(probe), "BINARY"))
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='SignalLight',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          upx_exclude=[], runtime_tmpdir=None, console=False,
          disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None,
          icon=['assets/signallight.ico'])
