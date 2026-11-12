"""The double-click launchers for the HTML-report kit parse in cmd.exe."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BATS = ["Gan_Clicky_vao_bao_cao.bat", "Ghi_cau_treo.bat", "Nghe_va_chon_giong.bat", "tim_python.bat"]


def test_echo_inside_a_block_never_closes_it():
    # cmd ends an "if (...)" block at the first bare ")", even inside an echo line
    for name in BATS:
        depth = 0
        for n, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if depth and s.lower().startswith("echo") and re.search(r"(?<!\^)\)", s):
                raise AssertionError(f"{name}:{n} has a bare ')' inside a block: {s}")
            depth += s.endswith("(") - s.startswith(")")


def test_launchers_use_windows_line_ends():
    for name in BATS:
        data = (ROOT / name).read_bytes()
        assert data.count(b"\r\n") == data.count(b"\n"), name


def test_launchers_never_call_the_store_python_shortcut():
    # "python" on a fresh Windows is a Microsoft Store shortcut that only prints "Python was not found":
    # every launcher finds a real one first (tim_python.bat) and runs it as %PY%
    for name in BATS[:3]:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "call tim_python.bat" in text.split("%PY%")[0], name
        bare = [l for l in text.splitlines() if re.search(r"(^|[\s(])python(\.exe)?\s+(-m|tools)", l.strip())]
        assert not bare, (name, bare)
    finder = (ROOT / "tim_python.bat").read_text(encoding="utf-8")
    assert "Programs\\Python" in finder and "InstallAllUsers=0" in finder and "choice" in finder


def test_dropped_paths_are_always_quoted():
    # a folder like "C:\Bao cao Q3 & Q4" breaks any unquoted %1 or %~1: cmd reads "&" as a second command
    for name in BATS[:2]:
        text = (ROOT / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.lower().startswith("rem "):
                continue
            for m in re.finditer(r"%~?[a-z]*[1-9]", line):
                assert line[:m.start()].count('"') % 2 == 1, (name, line)      # inside a pair of quotes


def test_report_launcher_flow():
    text = (ROOT / "Gan_Clicky_vao_bao_cao.bat").read_text(encoding="utf-8")
    # the real Documents folder, also when OneDrive backs it up
    assert "GetFolderPath('MyDocuments')" in text
    # MP3 without ffmpeg, and a failed pip stops with a Vietnamese message instead of a traceback later
    lines = [l.strip() for l in text.splitlines()]
    pip = next(k for k, l in enumerate(lines) if "pip install" in l)
    assert "lameenc" in lines[pip] and lines[pip + 1] == "if errorlevel 1 goto :loi_pip"
    assert "KHÔNG ĐẠT: chưa cài được các thư viện" in text
    # the whole flow (owner, build, re-check, look, open) runs in Python, with the full command line that
    # Explorer used, read with delayed expansion so an unquoted "P&L_T10.html" never runs as a command
    assert 'set "CLICKY_CMDLINE=!cmdcmdline!"' in text and "EnableDelayedExpansion" in text
    assert text.index("EnableDelayedExpansion") < text.index("!cmdcmdline!") < text.index("DisableDelayedExpansion")
    run = next(l for l in lines if "present.clicky_html" in l)
    assert "--tu-bat" in run and '"%~1"' in run
    assert lines[lines.index(run) + 1].startswith("if errorlevel 1")


def test_memory_launcher_uses_the_real_documents_folder():
    text = (ROOT / "Ghi_cau_treo.bat").read_text(encoding="utf-8")
    assert "GetFolderPath('MyDocuments')" in text and "%USERPROFILE%\\Documents\\Clicky" not in text
