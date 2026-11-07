"""The writers of the Doi chieu and Bien ban sheets as they were before they sent everything in one go (0dfa2aa),
call by call. tests/test_sheet_batch.py runs them in month_close's namespace and checks the batched writers leave
every cell with the same value and format. Not imported: read and run by that test only."""
# flake8: noqa


def _write_check_sheet(bridge, loaded: Optional[Loaded], truth, ledger: dict,
                       details: Optional[list] = None, gap_state: Optional[str] = None) -> Optional[str]:
    """The reconciliation as a small, formatted table (rows 3-7) and the reasons below it (row 9 on).

    details: rows of [what, documents, revenue, '', explanation]; the chat only carries one sentence per
    idea, so this is where the full story lives, next to the numbers it explains.
    """
    names = bridge.sheet_names()
    sheet = CHECK_SHEET if CHECK_SHEET in names else bridge.add_sheet(CHECK_SHEET)
    if not sheet:
        return None
    if loaded and "amount" in loaded.columns:
        a, c = _col(loaded.columns["amount"]), _col(loaded.columns.get("cost", loaded.columns["amount"]))
        q = _q(loaded.sheet)
        excel_row = ["Power Query trong Excel", f"=COUNT({q}!{a}:{a})", f"=SUM({q}!{a}:{a})",
                     f"=SUM({q}!{c}:{c})" if "cost" in loaded.columns else "", "Công thức sống, đọc từ bảng query"]
    else:
        excel_row = ["Power Query trong Excel", "", "", "", "Chưa đọc được bảng query"]
    dups = ", ".join(d for d, _, _ in truth.duplicates[:5])
    grid = [
        ["Đối chiếu doanh thu tháng: Power Query, file gốc và sổ cái", "", "", "", ""],
        ["Số liệu file gốc và sổ cái do Clicky đọc bằng code; dòng Excel là công thức", "", "", "", ""],
        ["Nguồn", "Số dòng", "Doanh thu (đ)", "Giá vốn (đ)", "Ghi chú"],
        excel_row,
        ["File gốc (đọc bằng code)", truth.unique_rows, truth.amount, truth.cost,
         f"Đã bỏ {len(truth.duplicates)} chứng từ lặp: {dups}" if truth.duplicates else "Không có chứng từ lặp"],
        [f"Sổ cái {ledger.get('name', '')}", "", ledger.get("revenue", ""), ledger.get("cost", ""),
         (f"Có TK {ledger.get('rev_acct', '511')} − Nợ TK 521 (file gốc có phiếu trả hàng) / Nợ TK 632" if ledger.get("net")
          else f"Phát sinh Có TK {ledger.get('rev_acct', '511')} / Nợ TK 632") if ledger else "Chưa thấy file cân đối tài khoản"],
        ["Chênh lệch Excel − sổ cái", "", "=C4-C6" if ledger and loaded else "",
         "=D4-D6" if ledger and loaded and "cost" in loaded.columns else "",
         "Phải bằng 0" if loaded else "Chưa đối chiếu được: chưa đọc được bảng query"],
    ]
    if details:
        grid += [["", "", "", "", ""], ["Chi tiết kiểm tra", "Chứng từ", "Doanh thu (đ)", "", "Giải thích"]]
        grid += [list(r) + [""] * (5 - len(r)) for r in details]
    _try(bridge, "clear", f"{_q(sheet)}!A1:F60")
    bridge.write(f"{_q(sheet)}!A1", grid)
    bridge.format_numbers(f"{_q(sheet)}!B4:D7", ZERO_SHOWN)
    _style_check_sheet(bridge, sheet, len(details or []), gap_state if loaded else None)
    return sheet


def _style_check_sheet(bridge, sheet: str, n_details: int, gap_state: Optional[str]) -> None:
    s = _q(sheet)
    _try(bridge, "layout_sheet", sheet, _CHECK_COLUMNS, gridlines=False)
    _try(bridge, "style", f"{s}!A1", bold=True, size=15, color=_INK)
    _try(bridge, "style", f"{s}!A2", italic=True, size=9, color=_MUTED)
    _try(bridge, "style", f"{s}!A3:E3", bold=True, color="#FFFFFF", fill="#1E3A5F", height=24)
    _try(bridge, "style", f"{s}!B3:D3", halign="right")
    _try(bridge, "style", f"{s}!A4:E6", border_bottom=_LINE, height=21)
    _try(bridge, "style", f"{s}!E4:E7", color="#475569")
    fill, ink = _GAP_STYLE.get(gap_state, _GAP_STYLE[None])
    _try(bridge, "style", f"{s}!A7:E7", bold=True, fill=fill, height=26)
    _try(bridge, "style", f"{s}!A7:D7", color=ink)
    if n_details:
        top, last = DETAIL_TOP, DETAIL_TOP + n_details
        _try(bridge, "style", f"{s}!A{top}:E{top}", bold=True, color=_INK, border_bottom="#94A3B8", height=24)
        _try(bridge, "style", f"{s}!B{top}:D{top}", halign="right")
        _try(bridge, "style", f"{s}!A{top + 1}:E{last}", border_bottom=_LINE, wrap=True)
        _try(bridge, "style", f"{s}!A{top + 1}:A{last}", bold=True, color=_INK)
        _try(bridge, "style", f"{s}!E{top + 1}:E{last}", color="#334155")
        bridge.format_numbers(f"{s}!B{top + 1}:C{last}")


def _write_audit(bridge) -> Optional[str]:
    """Rewrite the whole Biên bản sheet from the log (it is small), then put the status on the report too."""
    if _AUDIT is None:
        return None
    names = bridge.sheet_names()
    sheet = audit.SHEET if audit.SHEET in names else bridge.add_sheet(audit.SHEET)
    if not sheet:
        return None
    rows, at = audit.grid(_AUDIT)
    s = _q(sheet)
    _try(bridge, "clear", f"{s}!A1:I200")
    bridge.write(f"{s}!A1", rows)
    first, last = at["changes"]
    bridge.format_numbers(f"{s}!E{first}:F{last}")
    bridge.format_numbers(f"{s}!C{at['dropped'] + 1}:C{at['coverage'] - 2}")
    bridge.format_numbers(f"{s}!B{at['coverage'] + 1}:D{at['last']}")
    _try(bridge, "layout_sheet", sheet, _AUDIT_COLUMNS, gridlines=False)
    _try(bridge, "style", f"{s}!A1", bold=True, size=15, color=_INK)
    _try(bridge, "style", f"{s}!A2", italic=True, size=9, color=_MUTED)
    for row in (4, at["dropped"], at["coverage"]):
        _try(bridge, "style", f"{s}!A{row}:I{row}", bold=True, color="#FFFFFF", fill="#1E3A5F", height=22)
    _try(bridge, "style", f"{s}!A{first}:I{last}", border_bottom=_LINE, wrap=True)
    _try(bridge, "style", f"{s}!F{first}:F{last}", bold=True, color=_FINAL_INK)
    fill, ink = (_FINAL_FILL, _FINAL_INK) if _AUDIT.status == audit.FINAL else (_DRAFT_FILL, _DRAFT_INK)
    _try(bridge, "style", f"{s}!A{at['status']}", bold=True, color=_INK)
    _try(bridge, "style", f"{s}!B{at['status']}", bold=True, color=ink, fill=fill)
    _show_status_on_report(bridge, _AUDIT.status)
    scene_log.record("status", label=_AUDIT.status)
    return sheet


def _show_status_on_report(bridge, status: str) -> None:
    from engine.build import SHEET_NAMES
    report = next((n for n in bridge.sheet_names() if n.startswith(SHEET_NAMES["report"])), None)
    if not report:
        return
    fill, ink = (_FINAL_FILL, _FINAL_INK) if status == audit.FINAL else (_DRAFT_FILL, _DRAFT_INK)
    bridge.write(f"{_q(report)}!I1", [[status]])
    _try(bridge, "style", f"{_q(report)}!I1", bold=True, color=ink, fill=fill, halign="center")
