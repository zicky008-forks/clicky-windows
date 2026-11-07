// Runs Clicky's checking engine (Python) inside the browser with Pyodide.
// Files dropped on the page come here as bytes and never go to any server:
// the only network requests are for this site's own files (pyodide/, app.zip).
/* global importScripts, loadPyodide */
importScripts("pyodide/pyodide.js");

let ready = null;

function say(type, data) {
  self.postMessage(Object.assign({ type }, data || {}));
}

async function boot() {
  say("progress", { step: 1 });
  const py = await loadPyodide({ indexURL: "pyodide/" });
  say("progress", { step: 2 });
  const zip = await (await fetch("app.zip")).arrayBuffer();
  py.unpackArchive(zip, "zip", { extractDir: "/app" });
  await py.runPythonAsync(
    "import sys\nsys.path.insert(0, '/app')\nimport soat_thu\n"
  );
  say("progress", { step: 3 });
  return py;
}

ready = boot().then(
  (py) => {
    say("ready");
    return py;
  },
  (err) => {
    say("boot_error", { message: String(err && err.message ? err.message : err) });
    throw err;
  }
);

self.onmessage = async (event) => {
  const msg = event.data;
  if (msg.cmd !== "soat") return;
  let py;
  try {
    py = await ready;
  } catch (e) {
    return;
  }
  try {
    const out = "/tmp/BaoCao_Clicky.xlsx";
    try { py.FS.unlink(out); } catch (e) { /* first run */ }
    const soat = py.globals.get("soat_thu").soat_json;
    const cur = py.toPy(new Uint8Array(msg.cur));
    const prev = py.toPy(new Uint8Array(msg.prev));
    const text = soat(cur, msg.curName, prev, msg.prevName, out);
    cur.destroy();
    prev.destroy();
    soat.destroy();
    const result = JSON.parse(text);
    let report = null;
    if (result.ok) {
      report = py.FS.readFile(out);
    }
    self.postMessage({ type: "result", result, report }, report ? [report.buffer] : []);
  } catch (e) {
    say("result", {
      result: {
        ok: false,
        error: "Clicky gặp lỗi khi soát file này. Chị thử lại, hoặc gửi file mẫu đã che số cho người hỗ trợ nhé.",
        detail: String(e && e.message ? e.message : e).slice(-400),
      },
    });
  }
};
