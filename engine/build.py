"""One call from two trial balances to the finished report: figures, checks, sheet grids and a spoken summary."""
from dataclasses import dataclass

from engine import checks, monthly_report, report_sheet
from engine.trial_balance import TrialBalance, read_trial_balance

SHEET_NAMES = {"tb_cur": "TB ky nay", "tb_prev": "TB ky truoc", "report": "BC thang", "checks": "Kiem tra"}


@dataclass
class MonthlyReport:
    current: TrialBalance
    previous: TrialBalance
    figures: list
    variances: list
    issues: list
    summary: str
    sheets: dict          # sheet name → grid, in the order to create them


def build(cur_rows, prev_rows, cur_name="Kỳ này", prev_name="Kỳ trước", text_prefix="", names=None) -> MonthlyReport:
    """names maps tb_cur / tb_prev / report / checks to the sheet names the grids will be written to."""
    cur = read_trial_balance(cur_rows, cur_name)
    prev = read_trial_balance(prev_rows, prev_name)
    figures = monthly_report.compute(cur)
    variances = monthly_report.compare(figures, monthly_report.compute(prev))
    issues = checks.check(cur) + [checks.Issue(i.severity, i.code, f"[{prev_name}] {i.message}", 0) for i in checks.check(prev)]
    n = {**SHEET_NAMES, **(names or {})}
    sheets = {
        n["report"]: report_sheet.report_grid(figures, variances, n["tb_cur"], n["tb_prev"],
                                              f"Báo cáo kết quả kinh doanh quản trị: {cur_name} so với {prev_name}"),
        n["checks"]: report_sheet.checks_grid(issues, cur_name),
        n["tb_cur"]: report_sheet.tb_grid(cur, text_prefix),
        n["tb_prev"]: report_sheet.tb_grid(prev, text_prefix),
    }
    return MonthlyReport(cur, prev, figures, variances, issues, monthly_report.summary_vi(variances, issues), sheets)
