// Clicky BI task pane: snapshot the workbook with Office.js, run the shared Python
// report engine in the browser with Pyodide, then apply its plan with Office.js.
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/";
const PY_FILES = [
  "engine/__init__.py", "engine/trial_balance.py", "engine/monthly_report.py", "engine/checks.py",
  "engine/questions.py", "engine/report_sheet.py", "engine/build.py",
  "office/__init__.py", "office/report_skill.py", "workbook_plan.py",
];
const $ = (id) => document.getElementById(id);
let pyReady = null;

function status(text, isError = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", isError);
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src; s.onload = resolve; s.onerror = () => reject(new Error("Không tải được " + src));
    document.head.appendChild(s);
  });
}

async function python() {
  if (!pyReady) {
    pyReady = (async () => {
      status("Lần đầu: đang tải bộ tính toán (khoảng 10MB, các lần sau nhanh hơn)…");
      await loadScript(PYODIDE + "pyodide.js");
      const py = await loadPyodide({ indexURL: PYODIDE });
      for (const path of PY_FILES) {
        const res = await fetch("py/" + path, { cache: "no-cache" });
        if (!res.ok) throw new Error("Thiếu file " + path);
        const dir = path.includes("/") ? "/home/pyodide/" + path.split("/")[0] : null;
        if (dir && !py.FS.analyzePath(dir).exists) py.FS.mkdir(dir);
        py.FS.writeFile("/home/pyodide/" + path, await res.text());
      }
      py.runPython("import sys; sys.path.insert(0, '/home/pyodide')");
      return py;
    })();
    pyReady.catch(() => { pyReady = null; });
  }
  return pyReady;
}

async function snapshot() {
  return Excel.run(async (ctx) => {
    const sheets = ctx.workbook.worksheets;
    sheets.load("items/name");
    const active = sheets.getActiveWorksheet();
    active.load("name");
    await ctx.sync();
    const used = sheets.items.map((ws) => {
      const r = ws.getUsedRangeOrNullObject(true);
      r.load("values,rowIndex,columnIndex");
      return r;
    });
    await ctx.sync();
    const out = { sheets: {}, order: [], active: active.name };
    sheets.items.forEach((ws, i) => {
      out.order.push(ws.name);
      const r = used[i];
      if (r.isNullObject) { out.sheets[ws.name] = []; return; }
      // Pad to A1 so row numbers match the sheet, as the desktop COM bridge does.
      const lead = Array(r.columnIndex).fill(null);
      const rows = Array.from({ length: r.rowIndex }, () => []);
      for (const row of r.values) rows.push(lead.concat(row.map((v) => (v === "" ? null : v))));
      out.sheets[ws.name] = rows;
    });
    return out;
  });
}

function colNumber(letters) {
  return [...letters.toUpperCase()].reduce((n, c) => n * 26 + c.charCodeAt(0) - 64, 0);
}

function dims(range) {
  const [a, b = a] = range.split(":");
  const p = (ref) => { const m = /^([A-Z]+)(\d+)$/i.exec(ref); return [colNumber(m[1]), +m[2]]; };
  const [c1, r1] = p(a), [c2, r2] = p(b);
  return [r2 - r1 + 1, c2 - c1 + 1];
}

async function apply(ops) {
  await Excel.run(async (ctx) => {
    const sheets = ctx.workbook.worksheets;
    let last = null;
    for (const op of ops) {
      if (op.op === "add_sheet") sheets.add(op.name);
      else if (op.op === "write" && op.values.length) {
        const range = sheets.getItem(op.sheet).getRange(op.cell)
          .getResizedRange(op.values.length - 1, op.values[0].length - 1);
        range.formulas = op.values;
      } else if (op.op === "format") {
        const [rows, cols] = dims(op.range);
        if (rows > 0 && cols > 0) {
          sheets.getItem(op.sheet).getRange(op.range).numberFormat =
            Array.from({ length: rows }, () => Array(cols).fill(op.format));
        }
      } else if (op.op === "activate") last = op.sheet;
    }
    await ctx.sync();
    if (last) {
      const ws = sheets.getItem(last);
      ws.getRange("A:I").format.autofitColumns();
      ws.activate();
      ws.getRange("A1").select();
      await ctx.sync();
    }
  });
}

function speak(text) {
  if (!$("speak").checked || !window.speechSynthesis) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "vi-VN";
  const voice = speechSynthesis.getVoices().find((v) => v.lang && v.lang.toLowerCase().startsWith("vi"));
  if (voice) u.voice = voice;
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}

async function buildReport() {
  $("report").disabled = true;
  $("summary").style.display = "none";
  try {
    const py = await python();
    status("Đang đọc các sheet…");
    const snap = await snapshot();
    status("Đang tính và kiểm tra…");
    py.globals.set("snapshot_json", JSON.stringify(snap));
    const result = JSON.parse(py.runPython("import workbook_plan; workbook_plan.plan(snapshot_json)"));
    if (result.ops.length) {
      status("Đang ghi các sheet báo cáo…");
      await apply(result.ops);
      status("Xong. Các sheet mới nằm cuối file.");
    } else {
      status("Chưa lập được báo cáo.", true);
    }
    $("summary").textContent = result.summary;
    $("summary").style.display = "block";
    speak(result.summary);
  } catch (err) {
    status("Có lỗi: " + (err && err.message ? err.message : err), true);
  } finally {
    $("report").disabled = false;
  }
}

Office.onReady((info) => {
  if (info.host !== Office.HostType.Excel) { status("Add-in này chạy trong Excel.", true); return; }
  if (!Office.context.requirements.isSetSupported("ExcelApi", "1.7")) {
    status("Excel bản này quá cũ (cần Excel 2019, 2021 hoặc Microsoft 365).", true);
    return;
  }
  $("report").disabled = false;
  $("report").onclick = buildReport;
  status("Sẵn sàng.");
});
