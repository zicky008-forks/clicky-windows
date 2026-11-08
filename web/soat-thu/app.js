// Clicky "soát thử": the page around worker.js. One idea per screen, plain Vietnamese, no AI.
"use strict";

// Where the last card sends people. Leave a value empty to hide its button.
const CONFIG = {
  installUrl: "",   // page to download the Clicky desktop app
  contactUrl: "",   // Zalo / Messenger link to book a workshop or ask for help
};

const $ = (id) => document.getElementById(id);
const screens = ["s-welcome", "s-drop", "s-work", "s-card", "s-done", "s-error"];
const files = { cur: null, prev: null };
let worker = null;
let ready = false;
let pending = null;         // a check asked for before the engine finished loading
let cards = [];
let cardAt = 0;
let reportBlob = null;
let lastJob = null;

function show(id) {
  for (const s of screens) $(s).classList.toggle("on", s === id);
  const focus = $(id).querySelector("button.main:not([disabled]), h1, h2");
  if (focus) focus.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

const BOOT_TEXT = {
  1: "Đang chuẩn bị bộ máy soát (lần đầu mất khoảng 10–20 giây)…",
  2: "Sắp xong…",
  3: "Bộ máy soát đã sẵn sàng, chạy ngay trên máy chị.",
};

function startWorker() {
  worker = new Worker("worker.js");
  worker.onmessage = (event) => {
    const msg = event.data;
    if (msg.type === "progress") $("boot-status").textContent = BOOT_TEXT[msg.step] || "";
    if (msg.type === "ready") {
      ready = true;
      $("boot-status").textContent = BOOT_TEXT[3];
      if (pending) { const p = pending; pending = null; run(p); }
    }
    if (msg.type === "boot_error") {
      fail("Trình duyệt này chưa chạy được bộ máy soát. Chị mở link bằng Chrome hoặc Edge bản mới giúp em nhé.");
    }
    if (msg.type === "result") onResult(msg.result, msg.report);
  };
}

function fail(text) {
  $("error-text").textContent = text;
  show("s-error");
}

// ---------- choosing files ----------

function setFile(which, file) {
  files[which] = file;
  const slot = $(which === "cur" ? "slot-cur" : "slot-prev");
  slot.classList.toggle("filled", !!file);
  slot.querySelector(".file").textContent = file ? "✓ " + file.name : "Kéo file vào, hoặc bấm để chọn";
  $("btn-run").disabled = !(files.cur && files.prev);
  $("btn-swap").hidden = !(files.cur && files.prev);
  $("drop-error").hidden = true;
}

// Two files dropped at once: the name that sorts later (T10 after T09, 2026-10 after 2026-09) is this month.
// Chị can swap them with one tap if that guess is wrong.
function placeTwo(list) {
  const [a, b] = [...list].slice(0, 2).sort((x, y) => x.name.localeCompare(y.name, "vi", { numeric: true }));
  setFile("prev", a);
  setFile("cur", b);
}

function accept(file) {
  const ok = /\.(xlsx|xlsm|csv|xls)$/i.test(file.name);
  if (!ok) {
    $("drop-error").textContent = "File “" + file.name + "” không phải file Excel hoặc CSV. Chị chọn file bảng cân đối tài khoản nhé.";
    $("drop-error").hidden = false;
  }
  return ok;
}

let pickingFor = "cur";
function wireSlot(which) {
  const slot = $(which === "cur" ? "slot-cur" : "slot-prev");
  const open = () => { pickingFor = which; $("pick").value = ""; $("pick").click(); };
  slot.addEventListener("click", open);
  slot.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
  slot.addEventListener("dragover", (e) => { e.preventDefault(); slot.classList.add("drag"); });
  slot.addEventListener("dragleave", () => slot.classList.remove("drag"));
  slot.addEventListener("drop", (e) => {
    e.preventDefault();
    slot.classList.remove("drag");
    const list = [...e.dataTransfer.files].filter(accept);
    if (list.length >= 2) placeTwo(list);
    else if (list.length === 1) setFile(which, list[0]);
  });
}

// A drop anywhere else on the drop screen still counts.
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => {
  e.preventDefault();
  if (!$("s-drop").classList.contains("on")) return;
  const list = [...e.dataTransfer.files].filter(accept);
  if (list.length >= 2) placeTwo(list);
  else if (list.length === 1) setFile(files.cur ? "prev" : "cur", list[0]);
});

// ---------- running a check ----------

async function run(job) {
  lastJob = { sample: !!job.sample };
  show("s-work");
  if (!ready) { pending = job; return; }
  worker.postMessage(
    { cmd: "soat", cur: job.cur, curName: job.curName, prev: job.prev, prevName: job.prevName },
    [job.cur, job.prev]
  );
}

async function runFiles() {
  run({
    cur: await files.cur.arrayBuffer(), curName: files.cur.name,
    prev: await files.prev.arrayBuffer(), prevName: files.prev.name,
  });
}

async function runSample() {
  show("s-work");
  try {
    const [cur, prev] = await Promise.all(
      ["mau/TB_T10.xlsx", "mau/TB_T09.xlsx"].map((p) => fetch(p).then((r) => {
        if (!r.ok) throw new Error(p);
        return r.arrayBuffer();
      }))
    );
    run({ cur, curName: "TB_T10.xlsx", prev, prevName: "TB_T09.xlsx", sample: true });
  } catch (e) {
    fail("Chưa tải được file mẫu. Chị kiểm tra mạng rồi thử lại nhé.");
  }
}

function onResult(result, report) {
  const job = lastJob;
  if (!result.ok) { fail(result.error); return; }
  reportBlob = report ? new Blob([report], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }) : null;
  cards = [{ tag: result.issues.length ? ["Kết quả", "red"] : ["Kết quả", "green"], title: result.headline, body: result.reason }];
  if (job && job.sample) cards[0].tag[0] = "Kết quả · file mẫu";
  result.issues.forEach((issue, i) => {
    // "TK 111 Tiền mặt dư Có 195.000.000: tiền … không thể âm" → what was found, then why it matters.
    const cut = issue.text.search(/: |; /);
    const title = cut > 0 ? issue.text.slice(0, cut) : issue.text;
    let body = cut > 0 ? issue.text.slice(cut + 2) : "";
    body = body ? body.charAt(0).toUpperCase() + body.slice(1) + "." : "";
    cards.push({ tag: ["Điểm " + (i + 1) + "/" + result.issues.length, issue.level === "error" ? "red" : ""], title, body });
  });
  const list = $("question-list");
  list.textContent = "";
  for (const q of result.questions) {
    const li = document.createElement("li");
    li.textContent = q;
    list.appendChild(li);
  }
  $("questions").hidden = result.questions.length === 0;
  cardAt = 0;
  showCard();
}

function showCard() {
  const c = cards[cardAt];
  const dots = $("dots");
  dots.textContent = "";
  cards.forEach((_, i) => { const d = document.createElement("i"); if (i === cardAt) d.className = "on"; dots.appendChild(d); });
  dots.hidden = cards.length < 2;
  $("card-tag").textContent = c.tag[0];
  $("card-tag").className = "tag " + (c.tag[1] || "");
  $("card-title").textContent = c.title;
  $("card-title").hidden = !c.title;
  $("card-body").textContent = c.body;
  $("card-body").className = c.title ? "" : "big";
  $("card-body").style.fontSize = c.title ? "" : "22px";
  $("btn-next").textContent = cardAt < cards.length - 1 ? "Tiếp" : "Xem báo cáo";
  show("s-card");
}

function nextCard() {
  if (cardAt < cards.length - 1) { cardAt += 1; showCard(); }
  else show("s-done");
}

function download() {
  if (!reportBlob) return;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(reportBlob);
  a.download = "BaoCao_thang_Clicky.xlsx";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
}

function buildCta() {
  const box = $("cta");
  box.textContent = "";
  const add = (url, text, main) => {
    if (!url) return;
    const a = document.createElement("a");
    a.href = url; a.textContent = text; a.className = "button" + (main ? " main" : "");
    a.target = "_blank"; a.rel = "noopener noreferrer";
    box.appendChild(a);
  };
  add(CONFIG.installUrl, "Tải Clicky cho máy tính", true);
  add(CONFIG.contactUrl, "Nhắn để đặt buổi làm cùng nhóm", !CONFIG.installUrl);
  if (!box.children.length) {
    const p = document.createElement("p");
    p.className = "soft";
    p.textContent = "Chị nhắn lại cho người đã gửi link này để được cài bản đầy đủ nhé.";
    box.appendChild(p);
  }
}

// ---------- wiring ----------

$("btn-sample").addEventListener("click", runSample);
$("btn-own").addEventListener("click", () => show("s-drop"));
$("btn-back").addEventListener("click", () => show("s-welcome"));
$("btn-run").addEventListener("click", runFiles);
$("btn-swap").addEventListener("click", () => { const c = files.cur; setFile("cur", files.prev); setFile("prev", c); });
$("pick").addEventListener("change", (e) => { const f = e.target.files[0]; if (f && accept(f)) setFile(pickingFor, f); });
$("btn-next").addEventListener("click", nextCard);
$("btn-download").addEventListener("click", download);
$("btn-again").addEventListener("click", () => { setFile("cur", null); setFile("prev", null); show("s-drop"); });
$("btn-retry").addEventListener("click", () => show(files.cur && files.prev ? "s-drop" : "s-welcome"));
wireSlot("cur");
wireSlot("prev");
buildCta();
startWorker();
