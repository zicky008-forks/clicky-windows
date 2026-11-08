"""Errors in plain Vietnamese (spec 12, v5-7).

Every error the user sees is one of a few kinds, each with a short title in everyday words,
a hint saying what to do, and two buttons ("Thử lại", "Báo lỗi"). The line "File của chị chưa
bị thay đổi gì" is shown only when the caller knows nothing was changed (``file_unchanged``).

:func:`classify` turns a raw exception or COM / Python error text into a kind. The raw text
never reaches the screen: :func:`describe` logs it and returns only the catalogue words.
"""
from __future__ import annotations

import logging
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

log = logging.getLogger("clicky.errors")

EXCEL_NOT_RESPONDING = "excel_not_responding"
EXCEL_DIALOG = "excel_dialog"
FILE_LOCKED = "file_locked"
OLD_XLS = "old_xls"
OFFLINE = "offline"
TRIAL_USED_UP = "trial_used_up"
GENERIC = "generic"

EYEBROW = "Có trục trặc, em xin lỗi"
UNCHANGED = "File của chị chưa bị thay đổi gì"
RETRY = "Thử lại"
REPORT = "Báo lỗi"
REPORT_NOTE_TITLE = "Nếu chị bấm Báo lỗi"
REPORT_NOTE = "Em gửi nhật ký đã che tên cho đội Clicky. Không gửi file. Chị xem trước được."


@dataclass(frozen=True)
class ErrorText:
    kind: str
    title: str
    hint: str


CATALOGUE = {
    EXCEL_NOT_RESPONDING: ErrorText(
        EXCEL_NOT_RESPONDING, "Excel không trả lời",
        "Chị đợi Excel một chút. Nếu Excel vẫn đứng, chị đóng Excel, mở lại file rồi bấm Thử lại."),
    EXCEL_DIALOG: ErrorText(
        EXCEL_DIALOG, "Excel đang mở hộp thoại",
        "Chị xem Excel có đang hỏi gì không, trả lời hoặc đóng hộp thoại đó, rồi bấm Thử lại."),
    FILE_LOCKED: ErrorText(
        FILE_LOCKED, "File đang được người khác mở",
        "Chị nhờ người đang mở file đóng lại, hoặc lưu một bản riêng, rồi bấm Thử lại."),
    OLD_XLS: ErrorText(
        OLD_XLS, "File .xls cũ",
        "Em chưa đọc được kiểu file cũ này. Chị mở file trong Excel, chọn Lưu thành, chọn kiểu .xlsx, rồi bấm Thử lại."),
    OFFLINE: ErrorText(
        OFFLINE, "Mất mạng",
        "Em vẫn chạy được phần chỉ dùng máy. Khi có mạng lại, chị bấm Thử lại."),
    TRIAL_USED_UP: ErrorText(
        TRIAL_USED_UP, "Hết lượt dùng thử",
        "Chị đã dùng hết lượt dùng thử. Chị nhập mã kích hoạt của công ty để dùng tiếp."),
    GENERIC: ErrorText(
        GENERIC, "Có chỗ em chưa làm được",
        "Chị bấm Thử lại. Nếu vẫn chưa được, chị bấm Báo lỗi để đội Clicky xem giúp."),
}


class TrialUsedUp(Exception):
    """Raised (by the activation / proxy code) when the free trial has no turns left."""


# COM HRESULTs, as pywintypes.com_error reports them (signed 32-bit).
RPC_E_CALL_REJECTED = -2147418111          # 0x80010001 "Call was rejected by callee."
RPC_E_SERVERCALL_RETRYLATER = -2147417846  # 0x8001010A "The message filter indicated that the application is busy."
RPC_S_SERVER_UNAVAILABLE = -2147023174     # 0x800706BA "The RPC server is unavailable."
RPC_E_DISCONNECTED = -2147417848           # 0x80010108 "The object invoked has disconnected from its clients."
CO_E_SERVER_EXEC_FAILURE = -2146959355     # 0x80080005 "Server execution failed"
RPC_E_CALL_FAILED = -2147023170            # 0x800706BE "The remote procedure call failed."

_DIALOG_CODES = {RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER}
_DEAD_CODES = {RPC_S_SERVER_UNAVAILABLE, RPC_E_DISCONNECTED, CO_E_SERVER_EXEC_FAILURE, RPC_E_CALL_FAILED}
_SHARING_WINERRORS = {32, 33}              # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION

_DIALOG_TEXT = re.compile(r"call was rejected by callee|application is busy|retrylater|0x80010001|0x8001010a", re.I)
_DEAD_TEXT = re.compile(r"rpc server is unavailable|disconnected from its clients|server execution failed|"
                        r"remote procedure call failed|excel (is )?not respond|0x800706ba|0x80010108", re.I)
_LOCKED_TEXT = re.compile(r"being used by another|sharing violation|locked for editing|lock violation|"
                          r"\[winerror (32|33)\]|đang được (người khác|sử dụng)", re.I)
_OFFLINE_TEXT = re.compile(r"getaddrinfo|name or service not known|temporary failure in name resolution|"
                           r"network is unreachable|no route to host|all connection attempts failed|"
                           r"connection (refused|reset|aborted)|failed to establish a new connection|"
                           r"\[errno (11001|11004|-2|-3)\]|connecterror|connecttimeout|readtimeout|"
                           r"networkerror|(connect|connection|read|request|handshake|socket) timed out", re.I)
_TRIAL_TEXT = re.compile(r"trial[ _-]?(used[ _-]?up|exhausted)|402 payment required|hết lượt dùng thử", re.I)
_OFFLINE_TYPES = ("ConnectError", "ConnectTimeout", "ReadTimeout", "NetworkError", "TimeoutException",
                  "RemoteProtocolError", "APIConnectionError", "APITimeoutError")


def _hresults(exc: BaseException) -> list:
    """Every HRESULT a COM error carries: its own, and the one inside excepinfo."""
    codes = []
    for value in (getattr(exc, "hresult", None), *(getattr(exc, "args", ()) or ())[:1]):
        if isinstance(value, int):
            codes.append(value)
    args = getattr(exc, "args", ()) or ()
    if len(args) > 2 and isinstance(args[2], (tuple, list)) and len(args[2]) > 5 and isinstance(args[2][5], int):
        codes.append(args[2][5])                        # excepinfo scode
    return codes


def _chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def classify(error: Union[BaseException, str, None], path: Union[str, Path, None] = None) -> str:
    """The catalogue kind for a raw error. ``path`` is the file being worked on, when known."""
    if path and str(path).lower().endswith(".xls"):
        return OLD_XLS
    excs = list(_chain(error)) if isinstance(error, BaseException) else []
    text = " ".join(f"{type(e).__name__}: {e}" for e in excs) if excs else str(error or "")

    if any(isinstance(e, TrialUsedUp) for e in excs) or _TRIAL_TEXT.search(text):
        return TRIAL_USED_UP
    codes = [c for e in excs for c in _hresults(e)]
    if any(c in _DIALOG_CODES for c in codes) or _DIALOG_TEXT.search(text):
        return EXCEL_DIALOG
    if any(c in _DEAD_CODES for c in codes) or _DEAD_TEXT.search(text):
        return EXCEL_NOT_RESPONDING
    if any(getattr(e, "winerror", None) in _SHARING_WINERRORS for e in excs) or _LOCKED_TEXT.search(text):
        return FILE_LOCKED
    if any(isinstance(e, PermissionError) and getattr(e, "filename", None) for e in excs):
        return FILE_LOCKED                             # Windows reports an open workbook as "access denied"
    if path is None and re.search(r"(^|[\s'\"\\/])[^\s'\"\\/]+\.xls(?![a-z0-9])", text, re.I):
        return OLD_XLS
    if any(isinstance(e, TimeoutError) for e in excs) and re.search(r"excel|workbook|\bcom\b", text, re.I):
        return EXCEL_NOT_RESPONDING                    # a COM call that never came back
    if any(isinstance(e, (ConnectionError, socket.gaierror, socket.timeout)) for e in excs) \
            or any(type(e).__name__ in _OFFLINE_TYPES for e in excs) or _OFFLINE_TEXT.search(text):
        return OFFLINE
    return GENERIC


# A sentence the app itself already wrote in Vietnamese (e.g. "câu này cần AI, mà máy chưa bật AI")
# may be shown as the hint of the generic card; English, codes and tracebacks never are.
_VI_LETTERS = re.compile(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹáàãéèíìóòõúùýĐ]", re.I)
_TECHNICAL = re.compile(r"traceback|error|exception|errno|winerror|hresult|0x[0-9a-f]{4,}|-?\d{6,}|https?://|"
                        r"[a-z]:\\|\.py\b|\bnone\b|object|attribute|failed", re.I)


def plain_vietnamese(text: str) -> Optional[str]:
    """``text`` when it is already one short plain Vietnamese sentence, else None."""
    first = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    if not first or len(first) > 240 or not _VI_LETTERS.search(first) or _TECHNICAL.search(first):
        return None
    return first[0].upper() + first[1:]


def describe(error: Union[BaseException, str, None], *, file_unchanged: bool = False,
             path: Union[str, Path, None] = None) -> dict:
    """What the error card shows. The raw error goes to clicky.log, never into the result."""
    kind = classify(error, path)
    traced = isinstance(error, BaseException) and error.__traceback__ is not None
    log.warning("error shown to the user as %s: %r", kind, error, exc_info=error if traced else None)
    entry = CATALOGUE[kind]
    hint = entry.hint
    if kind == GENERIC:
        own = plain_vietnamese(str(error) if error is not None else "")
        if own:
            hint = own
    return {"kind": kind, "eyebrow": EYEBROW, "title": entry.title, "hint": hint,
            "reassurance": UNCHANGED if file_unchanged else "", "buttons": [RETRY, REPORT],
            "report_note_title": REPORT_NOTE_TITLE, "report_note": REPORT_NOTE}


def one_line(error: Union[BaseException, str, None], path: Union[str, Path, None] = None) -> str:
    """Title and hint as one sentence, for places that have room for text only (a check-sheet note)."""
    entry = CATALOGUE[classify(error, path)]
    return f"{entry.title}. {entry.hint}"
