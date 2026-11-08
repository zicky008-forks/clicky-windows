"""'Hôm nay làm gì': the month's FP&A calendar from the company's lich_thang.yaml (engine.company_config), one step
per screen: what runs today and what it checks, what is due within two days, else the next step. Nothing is read
from the customer's files; the calendar is the user's own configuration on the PC.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import scene_log
from office import month_close as mc
from office.month_close import Beat

# Only words that ask for the calendar: "hôm nay có gì", "việc hôm nay" are everyday chat.
TRIGGER = r"h[ôo]m\s*nay\s*l[àa]m\s*g[ìi]|l[ịi]ch\s*(th[áa]ng|fp\s*&?\s*a|ch[ốo]t\s*s[ổo])"


def beats(on: Optional[dt.date] = None) -> list:
    from engine import company_config
    company = company_config.find()
    on = on or dt.date.today()
    if company is None or not company.steps:
        scene_log.record("lich_thang", steps=0)
        return [Beat("Em chưa có lịch tháng của chị. Chị chép thư mục cấu hình (có file lich_thang.yaml) vào thư mục "
                     "cau-hinh của Clicky, rồi hỏi lại em.", mark="warn")]
    said = company_config.today(company, on)
    scene_log.record("lich_thang", steps=len(company.steps), said=len(said), problems=len(company.problems))
    out = [Beat(s, mark="warn" if ("hạn" in s and "rơi vào" in s) else "") for s in said]
    out += [Beat(p, mark="warn") for p in company.problems]
    return out


async def handle(manager, transcript: str) -> str:
    await mc.play(manager, beats())
    return ""


SKILLS = [
    {"name": "Lịch FP&A tháng", "trigger": TRIGGER, "handler": handle,
     "description": "Hôm nay làm bước nào của chu trình tháng, kiểm gì, hạn nào sắp tới (từ lich_thang.yaml của chị)."},
]
