"""One call from two trial balances to the finished report: figures, checks, sheet grids and a spoken summary."""
from dataclasses import dataclass

from engine import checks, comparable, sources, monthly_report, questions, report_sheet
from engine.trial_balance import TrialBalance, read_trial_balance

SHEET_NAMES = {"tb_cur": "TB ky nay", "tb_prev": "TB ky truoc", "report": "BC thang",
               "questions": "Cau hoi xac minh", "checks": "Kiem tra"}


@dataclass
class MonthlyReport:
    current: TrialBalance
    previous: TrialBalance
    figures: list
    variances: list
    issues: list
    questions: list
    summary: str
    sheets: dict          # sheet name → grid, in the order to create them
    gate: comparable.Gate = None    # the two periods compare like for like, or what stops it (F5, F8)


def build(cur_rows, prev_rows, cur_name="Kỳ này", prev_name="Kỳ trước", text_prefix="", names=None,
          mapping=None, mapping_file="") -> MonthlyReport:
    """names maps tb_cur / tb_prev / report / checks to the sheet names the grids will be written to.
    mapping: {old account: new account} from the folder's account-mapping table, if it has one."""
    cur = read_trial_balance(cur_rows, cur_name)
    prev = read_trial_balance(prev_rows, prev_name)
    gate = comparable.gate(prev_rows, prev, cur_rows, cur, mapping, mapping_file,
                           comparable.is_new_year(sources.period_of(prev_rows), sources.period_of(cur_rows)))
    figures = monthly_report.compute(cur)
    # The prior period under this period's account codes, so a renumbered account is compared with itself.
    variances = monthly_report.compare(figures, monthly_report.compute(comparable.remap(prev, mapping or {})))
    pat = next(f.value for f in figures if f.key == "pat")
    # Missing recurring entries first: they change the profit story more than anything else found.
    issues = (checks.check_against_previous(cur, prev) + checks.check(cur) + checks.check_profit_tie(cur, pat)
              + [checks.Issue(i.severity, i.code, f"[{prev_name}] {i.message}", 0) for i in checks.check(prev)]
              + comparable.issues(gate, cur))
    preliminary = any(i.code == "not_closed" and not i.message.startswith("[") for i in issues)
    # A change that is not like for like gets no question about its cause either: the question would explain it.
    agenda = [] if not gate.same_basis else questions.build_questions([v for v in variances if not gate.uses_blocked(v)])
    n = {**SHEET_NAMES, **(names or {})}
    report = report_sheet.report_grid(figures, variances, n["tb_cur"], n["tb_prev"],
                                      ("SỐ SƠ BỘ, CHƯA KHOÁ SỔ. " if preliminary else "")
                                      + ("KỲ TRƯỚC KHÁC CƠ SỞ LẬP, CHƯA SO SÁNH ĐƯỢC. " if not gate.same_basis else "")
                                      + f"Báo cáo kết quả kinh doanh quản trị: {cur_name} so với {prev_name}")
    overstated = sources.returns_in_revenue(cur)
    if overstated > 0:
        # The corrected profit lives in the file, under the reported one, for anyone who opens it later.
        report += report_sheet.real_profit_rows(len(report), n["tb_cur"], overstated)
    sheets = {
        n["report"]: report,
        n["questions"]: questions.questions_grid(agenda),
        n["checks"]: report_sheet.checks_grid(issues, cur_name),
        n["tb_cur"]: report_sheet.tb_grid(cur, text_prefix),
        n["tb_prev"]: report_sheet.tb_grid(prev, text_prefix),
    }
    summary = monthly_report.summary_vi(variances, issues, len(agenda), gate)
    return MonthlyReport(cur, prev, figures, variances, issues, agenda, summary, sheets, gate)
