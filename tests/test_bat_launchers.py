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
