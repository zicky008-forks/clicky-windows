"""The one door to the AI: every request passes here, masked, shown, logged, or not sent at all.

GuardedProvider wraps whichever provider is active, so no caller can reach the network around it:
* "Chỉ chạy trên máy" (setting local_only, or CLICKY_LOCAL_ONLY=1) stops every request before
  any byte is sent. The numbers never depend on AI, so month close works the same either way.
* Every request is masked (engine.redact) with the catalogue of names and IDs read from the
  user's own files: the workbook's sheets and the CSV exports in its folder.
* The first request that carries file content (Excel cells, Power Query, an attached file) waits
  for the user to press "Gửi đi" on a card showing exactly the masked lines. After that each
  request is sent and written to gui-di.jsonl in Clicky's data folder, which the user can open.
* The answer comes back with codes; they are turned back into names here, on the machine.
"""
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ai.base_provider import BaseLLMProvider, Message
from engine import redact
from storage import data_dir, set_setting, setting

try:                                    # robot run log; lives on another branch until merged
    import scene_log
except ImportError:
    scene_log = None

log = logging.getLogger(__name__)

LOG_NAME = "gui-di.jsonl"
LOG_ROTATE_BYTES = 5_000_000
LOCAL_ONLY_LABEL = "Chỉ chạy trên máy, không gửi gì"
LOCAL_ONLY_MESSAGE = ("Đang ở chế độ chỉ chạy trên máy, em không gửi gì ra ngoài. Em vẫn làm được: "
                      "\"làm báo cáo tháng từ thư mục này\" và \"kiểm tra power query\".")
DECLINED_MESSAGE = "Em không gửi. Không có gì rời khỏi máy."
APPROVED_KEY = "outgoing_approved"
MAX_FILE_BLOCKS = 12
MAX_SHEETS, MAX_SHEET_ROWS, MAX_CSV_FILES = 12, 3000, 40
CATALOGUE_TTL_S = 300.0


def local_only() -> bool:
    return local_only_forced() or bool(setting("local_only", False))


def local_only_forced() -> bool:
    """CLICKY_LOCAL_ONLY=1 acts as if the switch were on (the robot tests run this way)."""
    return os.getenv("CLICKY_LOCAL_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}


def log_path() -> Path:
    return data_dir() / LOG_NAME


class NotSent(Exception):
    """Nothing left the machine; str() is the Vietnamese sentence to show the user."""


class LocalOnly(NotSent):
    def __init__(self):
        super().__init__(LOCAL_ONLY_MESSAGE)


class Declined(NotSent):
    def __init__(self):
        super().__init__(DECLINED_MESSAGE)


def _record(**fields) -> None:
    if scene_log is not None:
        try:
            scene_log.record("ai_call", **fields)
        except Exception:
            log.debug("scene_log.record failed", exc_info=True)


def stopped_here(chars: int) -> str:
    """A request stopped before it was even built (local-only): report it, return what to tell the user."""
    _record(chars=chars, preview_shown=False, approved=False, local_only=True)
    return LOCAL_ONLY_MESSAGE


# ── Catalogue from the user's files ──────────────────────────────────────────

def _sheet_rows(bridge, name: str) -> list:
    try:
        try:
            rows = bridge.sheet_rows(name, max_rows=MAX_SHEET_ROWS)
        except TypeError:                           # a bridge without the bound (tests' fakes)
            rows = bridge.sheet_rows(name)
    except Exception as exc:
        log.debug("sheet %s unreadable for the catalogue: %s", name, exc)
        return []
    return (rows or [])[:MAX_SHEET_ROWS]


def learn_workbook(catalogue: redact.Catalogue, bridge, sheets: Optional[list] = None) -> int:
    """Names and IDs from the open workbook's sheets and the CSV exports beside it (and one level down).
    sheets: read only these, and skip the folder."""
    learnt = 0
    if sheets is None:
        try:
            names = list(bridge.sheet_names())[:MAX_SHEETS]
        except Exception:
            names = []
    else:
        names = sheets
    for name in names:
        learnt += catalogue.add_rows(_sheet_rows(bridge, name))
    if sheets is not None:
        return learnt
    try:
        folder = bridge.workbook_folder()
    except Exception:
        folder = None
    if folder:
        learnt += learn_folder(catalogue, Path(folder))
    return learnt


def learn_folder(catalogue: redact.Catalogue, folder: Path) -> int:
    from engine.sources import read_csv
    learnt = 0
    try:
        paths = sorted(list(folder.glob("*.csv")) + list(folder.glob("*/*.csv")))[:MAX_CSV_FILES]
    except OSError:
        return 0
    for path in paths:
        try:
            learnt += catalogue.add_rows(read_csv(path)[:MAX_SHEET_ROWS])
        except (OSError, ValueError) as exc:
            log.debug("%s unreadable for the catalogue: %s", path.name, exc)
    return learnt


def learn_text(catalogue: redact.Catalogue, text: str) -> int:
    """An attached CSV or text export: learn from it when its first lines read as a table."""
    import csv
    from engine.sources import sniff_delimiter
    try:
        lines = (text or "").splitlines()[:MAX_SHEET_ROWS]
        return catalogue.add_rows(list(csv.reader(lines, delimiter=sniff_delimiter(text))))
    except (csv.Error, ValueError):
        return 0


# ── One request ──────────────────────────────────────────────────────────────

# Who receives a request and where it goes, said on the card before the first send.
_RECIPIENTS = {
    "ClaudeProvider": ("Anthropic, công ty làm AI Claude", "api.anthropic.com"),
    "OpenAIProvider": ("OpenAI, công ty làm ChatGPT", "api.openai.com"),
    "GeminiProvider": ("Google, AI Gemini", "generativelanguage.googleapis.com"),
    "GitHubCopilotProvider": ("GitHub Copilot của Microsoft", "api.githubcopilot.com"),
    "OllamaProvider": ("AI chạy ngay trên máy chị (Ollama)", "không rời máy chị"),
    "LMStudioProvider": ("AI chạy ngay trên máy chị (LM Studio)", "không rời máy chị"),
}
COMPUTER_USE = ("Anthropic, công ty làm AI Claude", "api.anthropic.com")
_UNKNOWN = ("Nhà cung cấp AI đang chọn trong Cài đặt", "máy chủ của nhà cung cấp đó")


def recipient(provider) -> tuple:
    """(who, where) for the provider behind the door; a custom OpenAI-style server is named by its host."""
    name = type(provider).__name__
    who, where = _RECIPIENTS.get(name, _UNKNOWN)
    if name == "OpenAIProvider" and getattr(provider, "_custom_server", False):
        base = str(getattr(getattr(provider, "_client", None), "base_url", "") or "")
        host = urlparse(base).hostname or "máy chủ đã nhập trong Cài đặt"
        local = host in ("localhost", "127.0.0.1", "::1")
        who = "AI chạy ngay trên máy chị" if local else "Máy chủ AI đã nhập trong Cài đặt"
        where = "không rời máy chị" if local else host
    return who, where


class Send:
    """One request after masking: what goes out, and what the card and the log show."""

    def __init__(self, system: str, user: str, history: list, lines: list, sources: list,
                 counts: dict, images: int):
        self.system, self.user, self.history = system, user, history
        self.lines, self.sources, self.counts, self.images = lines, sources, counts, images
        self.to, self.where = _UNKNOWN

    @property
    def from_files(self) -> bool:
        return bool(self.lines)

    @property
    def chars(self) -> int:
        return len(self.system) + len(self.user) + sum(len(m.content) for m in self.history)

    @property
    def summary(self) -> str:
        return redact.summary(len(self.lines), self.counts, self.images)

    @property
    def what(self) -> str:
        """The third consent line: exactly what goes, and what never goes."""
        parts = [f"{len(self.lines)} dòng số liệu đã che tên", "câu chị hỏi"]
        if self.images:
            parts.append(f"{self.images} ảnh màn hình")
        chars = f"{self.chars:,}".replace(",", ".")
        return ", ".join(parts) + f" ({chars} ký tự). Không gửi file nào."

    def preview(self) -> dict:
        return {"summary": self.summary, "lines": list(self.lines), "images": self.images,
                "to": self.to, "where": self.where, "what": self.what}


class Outgoing:
    """The masking state of this session and the decision for each request."""

    def __init__(self, ask=None):
        self.catalogue = redact.Catalogue()
        self.ask = ask                       # async (preview dict) → bool; None means nobody can approve
        self._blocks: list = []             # (source, text) of file content put into prompts lately
        self._learnt_at: dict = {}

    def file_text(self, source: str, text: str) -> str:
        """Mark text that came from a file; returned unchanged so it can be put in the prompt."""
        if text and len(text.strip()) >= 8:          # a few characters would turn up in any prompt
            self._blocks = [(s, t) for s, t in self._blocks if t != text][-(MAX_FILE_BLOCKS - 1):] + [(source, text)]
        return text

    def learn_workbook(self, bridge, key: str = "", active: str = "") -> None:
        """Read the whole workbook and its folder at most every few minutes (a COM read per sheet);
        the sheet in front is read every time, so a customer typed a moment ago is masked too."""
        now = time.monotonic()
        if key and now - self._learnt_at.get(key, -1e9) < CATALOGUE_TTL_S:
            n = learn_workbook(self.catalogue, bridge, sheets=[active]) if active else 0
            if n:
                log.info("privacy catalogue: %d new names/IDs from sheet %s", n, active)
            return
        self._learnt_at[key] = now
        n = learn_workbook(self.catalogue, bridge)
        log.info("privacy catalogue: %d new names/IDs from %s", n, key or "workbook")

    def prepare(self, user_text: str, system_prompt: str, history: list, images: int = 0) -> Send:
        masker = redact.Masker(self.catalogue)
        lines, sources = [], []

        def mask_part(text: str) -> str:
            # File blocks are masked on their own so the card shows exactly the text that is sent.
            out, rest = [], text or ""
            while True:
                found = [(rest.find(block), source, block) for source, block in self._blocks if block in rest]
                if not found:
                    break
                i, source, block = min(found, key=lambda f: (f[0], -len(f[2])))
                out.append(masker.mask(rest[:i]))
                masked = masker.mask(block)
                out.append(masked)
                lines.extend(line.rstrip() for line in masked.splitlines() if line.strip())
                if source not in sources:
                    sources.append(source)
                rest = rest[i + len(block):]
            out.append(masker.mask(rest))
            return "".join(out)

        system = mask_part(system_prompt)
        user = mask_part(user_text)
        masked_history = [Message(role=m.role, content=mask_part(m.content)) for m in history or []]
        return Send(system, user, masked_history, lines, sources, masker.counts, images)

    def mask(self, text: str) -> str:
        return redact.Masker(self.catalogue).mask(text)

    async def cleared_text(self, text: str, images: int = 0) -> str:
        """For a caller that reaches an AI without a provider (Computer Use pointing): masked, gated, logged."""
        send = self.prepare(text, "", [], images=images)
        send.to, send.where = COMPUTER_USE
        await self.clear(send)
        return send.user

    async def clear(self, send: Send) -> bool:
        """Raise NotSent unless this request may leave; True when the user was shown the card."""
        if local_only():
            _record(chars=send.chars, preview_shown=False, approved=False, local_only=True)
            raise LocalOnly()
        shown, approved = False, True
        # The approval names who receives: a new AI company (Settings changed) asks again.
        if send.from_files and setting(APPROVED_KEY, False) != send.to:
            shown = True
            approved = bool(self.ask) and bool(await self.ask(send.preview()))
            if approved:
                set_setting(APPROVED_KEY, send.to)
        _record(chars=send.chars, preview_shown=shown, approved=approved, local_only=False)
        if not approved:
            write_log(send, sent=False)
            raise Declined()
        write_log(send, sent=True, shown=shown)
        return shown


def write_log(send: Send, sent: bool, shown: bool = False) -> None:
    """One JSON line per request: the exact masked text sent, when, how long, and what was covered."""
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "sent": sent, "card": shown,
             "summary": send.summary, "chars": send.chars, "lines": len(send.lines), "masked": send.counts,
             "images": send.images, "sources": send.sources, "preview": send.lines,
             "to": send.to, "where": send.where}
    if sent:
        entry["text"] = {"system": send.system, "user": send.user,
                         "history": [{"role": m.role, "content": m.content} for m in send.history]}
    path = log_path()
    try:
        if path.exists() and path.stat().st_size > LOG_ROTATE_BYTES:
            os.replace(path, path.with_name(path.stem + ".1" + path.suffix))
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("outgoing log not written: %s", exc)


def read_log(limit: int = 200) -> list:
    """Newest first, for the "Nhật ký gửi đi" window."""
    try:
        lines = log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = []
    for line in reversed(lines[-limit:]):
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


class GuardedProvider(BaseLLMProvider):
    """The active provider behind the door; everything else (close, model lists) passes straight through."""

    def __init__(self, inner: BaseLLMProvider, gate: Outgoing):
        self._inner, self._gate = inner, gate

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def stream_response(self, user_text: str, screenshots_b64: list, history: list,
                              system_prompt: str, model: Optional[str] = None):
        send = self._gate.prepare(user_text, system_prompt, history, images=len(screenshots_b64 or []))
        send.to, send.where = recipient(self._inner)
        await self._gate.clear(send)
        back = redact.Unmasker(self._gate.catalogue)
        async for chunk in self._inner.stream_response(user_text=send.user, screenshots_b64=screenshots_b64,
                                                       history=send.history, system_prompt=send.system,
                                                       model=model):
            out = back.feed(chunk)
            if out:
                yield out
        tail = back.finish()
        if tail:
            yield tail

    async def health_check(self) -> bool:
        return await self._inner.health_check()
