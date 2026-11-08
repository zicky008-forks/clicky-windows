"""CLAUDE.md rule 8: Clicky calls itself "em" and the user "chị", in one place (engine.address). The lead found 16
sentences in month_close saying "anh" beside others saying "chị"; this keeps any "anh" from coming back."""
import ast
import re
from pathlib import Path

import pytest

from engine import address

ROOT = Path(__file__).resolve().parents[1]
# Every module whose sentences, cards, buttons or Biên bản reasons the month close shows.
USER_TEXT = ["office/month_close.py", "engine/intake.py", "office/pq_skill.py", "office/report_skill.py",
             "engine/audit.py", "engine/visuals.py"]       # engine/address.py is the one place that spells it
WORDS = re.compile(r"(?<!\w)(anh|chị)(?!\w)", re.IGNORECASE)


def _user_strings(path: Path) -> list:
    """(line, text) of every string literal that can reach the user: not a docstring, not a trigger or other regex
    (words the user may say are input), not a log message (English, never shown)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.endswith("TRIGGER") for t in node.targets):
            skip |= {id(n) for n in ast.walk(node.value)}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and node.func.value.id == "log":
            skip |= {id(n) for n in ast.walk(node)}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            if re.search(r"\\b|\\s|\(\?|\[ốo\]", node.value):
                continue                                   # a regular expression
            out.append((node.lineno, node.value))
    return out


@pytest.mark.parametrize("name", USER_TEXT)
def test_no_sentence_says_anh_or_spells_the_address_word_itself(name):
    found = [(line, text) for line, text in _user_strings(ROOT / name) if WORDS.search(text)]
    assert not found, f"{name}: say it with engine.address.you(): {found[:5]}"


def test_the_address_word_is_chi_and_capitalises_at_a_sentence_start():
    assert address.YOU == "chị" and address.you() == "chị" and address.you(True) == "Chị"


def test_sentences_carry_the_word_from_the_one_place(monkeypatch):
    from office import month_close
    monkeypatch.setattr(address, "YOU", "anh")         # a later setting changes every sentence at once
    assert month_close.wrong()[0].text.startswith("Anh nói giúp em")
    monkeypatch.setattr(address, "YOU", "chị")
    assert month_close.wrong()[0].text.startswith("Chị nói giúp em")
