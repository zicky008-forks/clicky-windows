// Parse M files given on the command line with Microsoft's own parser (npm @microsoft/powerquery-parser);
// print OK or the first syntax error with its line. Used by tests/test_order_exports.py when the parser is installed.
const PQP = require("@microsoft/powerquery-parser");
const fs = require("fs");
(async () => {
  let bad = 0;
  for (const f of process.argv.slice(2)) {
    const text = fs.readFileSync(f, "utf8");
    const r = await PQP.TaskUtils.tryLexParse(PQP.DefaultSettings, text);
    if (r.resultKind === "Ok" || (r.kind === "Ok")) { console.log("OK", f); continue; }
    bad++;
    const e = r.error || r;
    const inner = e.innerError || e;
    const tok = inner.foundToken || inner.token || (inner.foundToken && inner.foundToken.token);
    const pos = (tok && (tok.token ? tok.token.positionStart : tok.positionStart)) || inner.positionStart;
    console.log("ERR", f, (e.message || "").slice(0, 300));
    if (pos) { const ln = pos.lineNumber; console.log("  at line", ln + 1, ":", text.split("\n")[ln]); }
    else console.log(JSON.stringify(Object.keys(inner)));
  }
  process.exit(bad ? 1 : 0);
})();
