"""Re-check a "<name>_Clicky.html" file from the outside: is every figure Clicky says really in the report?

Reads only the output file (and the original report when given). It trusts nothing the builder kept in memory:
1. the record's seal is recomputed;
2. the sentences in the record are the sentences the page will actually speak;
3. every figure in every sentence is listed, and is found again at the place the record names;
4. with --goc, the original report's SHA-256 matches the record.

The seal is a plain SHA-256: it catches accidental edits, not a deliberate forgery (that needs a signature).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Optional

from present.clicky_html import numbers, page_numbers, parse, seal, target_texts

SCRIPT = r'<script id="{}" type="application/json">(.*?)</script>'


def _embedded(page: str, name: str):
    m = re.search(SCRIPT.format(name), page, re.S)
    return json.loads(m.group(1)) if m else None


def verify(packed: str, original: Optional[str] = None) -> dict:
    page = Path(packed).read_text(encoding="utf-8")
    errors: list = []
    record, data = _embedded(page, "clicky-bien-ban"), _embedded(page, "clicky-data")
    if record is None or data is None:
        return {"ok": False, "errors": ["File này không có biên bản của Clicky."], "tong": {}}
    if seal(record) != record.get("ma_bam"):
        errors.append("Biên bản đã bị sửa sau khi lập (mã băm không khớp).")
    spoken = [s for scene in data.get("scenes", []) for s in scene.get("sentences", [])]
    if spoken != [r["noi"] for r in record.get("cau", [])]:
        errors.append("Lời Clicky sẽ nói khác với lời ghi trong biên bản.")
    if original:
        if hashlib.sha256(Path(original).read_bytes()).hexdigest() != record.get("sha256"):
            errors.append("File gốc đưa vào không phải là báo cáo mà biên bản này được lập từ đó.")
    root = parse(page)
    texts, on_page = target_texts(root), page_numbers(root)
    count = {"cau": 0, "so": 0, "dung_cho_khoanh": 0, "cho_khac_trong_bao_cao": 0, "nguoi_soan_them": 0}
    for row in record.get("cau", []):
        count["cau"] += 1
        listed = [x["so"] for x in row.get("so", [])]
        if numbers(row["noi"]) != listed:
            errors.append(f"Câu «{row['noi'][:70]}» có số chưa được ghi trong biên bản.")
            continue
        for x in row["so"]:
            count["so"] += 1
            tok, where = x["so"], x["nguon"]
            if where == "nguoi_soan":
                count["nguoi_soan_them"] += 1
            elif where == "bao_cao":
                count["cho_khac_trong_bao_cao"] += 1
                if tok not in on_page:
                    errors.append(f"Số {tok} không còn trong báo cáo (câu «{row['noi'][:70]}»).")
            else:
                count["dung_cho_khoanh"] += 1
                if tok not in numbers(texts.get(where, "")):
                    errors.append(f"Số {tok} không có ở chỗ Clicky khoanh ({where}) (câu «{row['noi'][:70]}»).")
    return {"ok": not errors, "errors": errors, "tong": count, "ma_bam": record.get("ma_bam", ""),
            "bao_cao": record.get("bao_cao", ""), "nguoi_chiu_trach_nhiem": record.get("nguoi_chiu_trach_nhiem", "")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kiểm lại: mọi con số Clicky nói có thật sự nằm trong báo cáo không.")
    ap.add_argument("file_clicky", help="file <tên>_Clicky.html")
    ap.add_argument("--goc", help="file báo cáo gốc, để kiểm đúng là lập từ file này")
    a = ap.parse_args(argv)
    rep = verify(a.file_clicky, a.goc)
    t = rep["tong"]
    if rep["ok"]:
        print(f"ĐẠT. {t['so']} con số trong {t['cau']} câu: {t['dung_cho_khoanh']} ở đúng chỗ Clicky khoanh, "
              f"{t['cho_khac_trong_bao_cao']} ở chỗ khác trong báo cáo, {t['nguoi_soan_them']} do "
              f"{rep['nguoi_chiu_trach_nhiem']} bổ sung. Mã biên bản {rep['ma_bam'][:12]}.")
        return 0
    print("KHÔNG ĐẠT:", file=sys.stderr)
    for e in rep["errors"]:
        print(" - " + e, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
