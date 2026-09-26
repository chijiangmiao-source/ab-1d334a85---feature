"use strict";

// ---------------------------------------------------------------------------
// 状态与 DOM 构建
// ---------------------------------------------------------------------------

const sides = ["A", "B"];

function $(id) { return document.getElementById(id); }

function state(side) {
  return {
    n: parseInt($(`n-${side}`).value, 10),
    safe: $(`safe-${side}`).value,
    initial: $(`initial-${side}`).value,
    symbols: $(`symbols-${side}`).value,
  };
}

function parseSymbols(text) {
  return text.split(",").map((s) => s.trim()).filter((s) => s.length > 0);
}

function buildCommands(side) {
  const n = parseInt($(`n-${side}`).value, 10);
  const symbols = parseSymbols($(`symbols-${side}`).value);
  const container = $(`commands-${side}`);
  const old = readMatrices(side);
  container.innerHTML = "";

  if (!(n >= 1)) return;

  for (const symbol of symbols) {
    const block = document.createElement("div");
    block.className = "command-block";
    const h = document.createElement("h3");
    h.textContent = `命令 ${JSON.stringify(symbol)}`;
    block.appendChild(h);

    const table = document.createElement("table");
    table.className = "matrix";

    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.appendChild(Object.assign(document.createElement("th"), { textContent: "源状态 \\ 条目" }));
    for (let c = 0; c < n; c++) {
      const th = document.createElement("th");
      th.textContent = `#${c}`;
      headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    for (let r = 0; r < n; r++) {
      const tr = document.createElement("tr");
      const rowLabel = document.createElement("th");
      rowLabel.textContent = `状态 ${r}`;
      tr.appendChild(rowLabel);

      for (let c = 0; c < n; c++) {
        const td = document.createElement("td");

        const target = document.createElement("select");
        target.dataset.path = `${side}.commands.${symbol}.rows.${r}.${c}.target`;
        for (let j = 0; j < n; j++) {
          const opt = document.createElement("option");
          opt.value = String(j);
          opt.textContent = `→ ${j}`;
          if (j === c) opt.selected = true;
          target.appendChild(opt);
        }

        const prob = document.createElement("input");
        prob.type = "text";
        prob.placeholder = "p/q";
        prob.dataset.path = `${side}.commands.${symbol}.rows.${r}.${c}.prob`;
        // 默认单位矩阵，方便录入
        prob.value = r === c ? "1" : "0";

        const prev = old[symbol] && old[symbol][r] && old[symbol][r][c];
        if (prev) {
          if ([...target.options].some((o) => o.value === String(prev.target))) {
            target.value = String(prev.target);
          }
          prob.value = prev.prob;
        }

        target.addEventListener("input", markDirty);
        prob.addEventListener("input", markDirty);
        td.appendChild(target);
        td.appendChild(document.createTextNode(" "));
        td.appendChild(prob);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    block.appendChild(table);
    container.appendChild(block);
  }
}

function readMatrices(side) {
  const out = {};
  const container = $(`commands-${side}`);
  const blocks = container.querySelectorAll(".command-block");
  for (const block of blocks) {
    const symbol = JSON.parse(block.querySelector("h3").textContent.replace("命令 ", ""));
    const rows = [];
    const trs = block.querySelectorAll("tbody tr");
    trs.forEach((tr) => {
      const row = [];
      tr.querySelectorAll("td").forEach((td) => {
        row.push({
          target: parseInt(td.querySelector("select").value, 10),
          prob: td.querySelector("input").value.trim(),
        });
      });
      rows.push(row);
    });
    out[symbol] = rows;
  }
  return out;
}

function collectPayload(side) {
  const n = parseInt($(`n-${side}`).value, 10);
  const initial = $(`initial-${side}`).value.split(/[\s,]+/).filter(Boolean);
  const safe = $(`safe-${side}`).value.split(/[\s,]+/).filter(Boolean).map(Number);
  const matrices = readMatrices(side);
  return {
    n,
    initial,
    safe,
    commands: Object.keys(matrices).map((symbol) => ({ symbol, rows: matrices[symbol] })),
  };
}

// ---------------------------------------------------------------------------
// 草稿修改即清除旧结论（“不显示旧结论”）
// ---------------------------------------------------------------------------

let dirty = false;
function markDirty() {
  dirty = true;
  $("result").hidden = true;
  clearHighlights();
}

for (const side of sides) {
  $(`n-${side}`).addEventListener("input", () => { markDirty(); buildCommands(side); });
  $(`symbols-${side}`).addEventListener("input", () => { markDirty(); buildCommands(side); });
  $(`safe-${side}`).addEventListener("input", markDirty);
  $(`initial-${side}`).addEventListener("input", markDirty);
}

// ---------------------------------------------------------------------------
// 错误定位
// ---------------------------------------------------------------------------

function clearHighlights() {
  document.querySelectorAll("input.invalid, select.invalid").forEach((el) => {
    el.classList.remove("invalid");
  });
}

function locToSelector(loc) {
  // loc 形如 ["A", "commands", 0, "rows", 1, 2, "prob"]
  // 或 ["A","initial",0] / ["A","safe",0] / ["A","n"] / ["commands"]
  if (!Array.isArray(loc) || loc.length === 0) return null;
  const side = loc[0];
  if (side !== "A" && side !== "B") return null;

  if (loc[1] === "initial" && loc.length >= 3) return `${side}.initial.${loc[2]}`;
  if (loc[1] === "safe" && loc.length >= 3) return `${side}.safe.${loc[2]}`;
  if (loc[1] === "n") return `n-${side}`;

  if (loc[1] === "commands") {
    // [side, "commands", index, ...]
    const rest = loc.slice(2);
    if (rest.length === 0) return null;
    const container = $(`commands-${side}`);
    const blocks = container.querySelectorAll(".command-block");
    const block = blocks[rest[0]];
    if (!block) return null;
    if (rest.length === 1) return null; // 高亮整个命令块（用其首个输入代替）
    // ["rows", r, c, field?]
    if (rest[1] === "rows" && rest.length >= 3) {
      const r = rest[2];
      const tr = block.querySelectorAll("tbody tr")[r];
      if (!tr) return null;
      if (rest.length === 3) {
        const first = tr.querySelector("input");
        return first ? first.dataset.path : null;
      }
      const c = rest[3];
      const td = tr.querySelectorAll("td")[c];
      if (!td) return null;
      const field = rest[4] === "target" ? "select" : "input";
      const el = td.querySelector(field);
      return el ? el.dataset.path : null;
    }
    if (rest[1] === "symbol") {
      const el = block.querySelector("input, select");
      return el ? el.dataset.path : null;
    }
  }
  return null;
}

function showErrors(errors) {
  clearHighlights();
  const box = $("errors");
  box.innerHTML = "";
  const h = document.createElement("h2");
  h.textContent = `复核未通过：发现 ${errors.length} 处录入问题（已在表单中定位）`;
  box.appendChild(h);
  const ul = document.createElement("ul");
  for (const err of errors) {
    const li = document.createElement("li");
    li.className = "error-item";
    const loc = document.createElement("span");
    loc.className = "error-loc";
    loc.textContent = err.loc && err.loc.length ? `[${err.loc.join(" › ")}] ` : "";
    li.appendChild(loc);
    li.appendChild(document.createTextNode(err.msg));
    ul.appendChild(li);

    const path = locToSelector(err.loc);
    if (path) {
      const el = document.querySelector(`[data-path="${CSS.escape(path)}"]`);
      if (el) el.classList.add("invalid");
    }
    if (err.loc && (err.loc[0] === "A" || err.loc[0] === "B")) {
      const field = err.loc[1];
      if (field === "initial") $(`initial-${err.loc[0]}`).classList.add("invalid");
      if (field === "safe") $(`safe-${err.loc[0]}`).classList.add("invalid");
      if (field === "n") $(`n-${err.loc[0]}`).classList.add("invalid");
    }
  }
  box.appendChild(ul);
  box.hidden = false;
}

// ---------------------------------------------------------------------------
// 结果渲染
// ---------------------------------------------------------------------------

function fracText(f) { return f.text; }

function renderEquivalent() {
  const box = $("result");
  box.className = "result verdict-equivalent";
  box.innerHTML = "";
  const h = document.createElement("h2");
  h.textContent = "✓ 两份规程对所有有限命令串精确等价";
  box.appendChild(h);
  const p = document.createElement("p");
  p.textContent =
    "经任意精度有理数线性子空间判定（覆盖全部有限命令串，非长度上限内抽样）：" +
    "现网规程 A 与候选规程 B 在每个有限命令串下的最终进入安全态概率完全相同。";
  box.appendChild(p);
  box.hidden = false;
}

function renderTraceTable(trace, nStates) {
  const table = document.createElement("table");
  table.className = "step-table";
  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  const headers = ["命令串后", ...Array.from({ length: nStates }, (_, i) => `状态 ${i}`), "安全概率"];
  for (const text of headers) {
    const th = document.createElement("th");
    th.textContent = text;
    hr.appendChild(th);
  }
  thead.appendChild(hr);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  trace.steps.forEach((step, idx) => {
    const tr = document.createElement("tr");
    const td0 = document.createElement("td");
    td0.style.textAlign = "center";
    td0.textContent = idx === 0 ? "ε（空串）" : `第 ${idx} 步`;
    tr.appendChild(td0);
    for (const v of step.distribution) {
      const td = document.createElement("td");
      td.textContent = fracText(v);
      tr.appendChild(td);
    }
    const tdSafe = document.createElement("td");
    tdSafe.textContent = fracText(step.safeProb);
    tdSafe.style.fontWeight = "600";
    tr.appendChild(tdSafe);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  return table;
}

function renderCounterexample(result) {
  const box = $("result");
  box.className = "result verdict-diff";
  box.innerHTML = "";

  const h = document.createElement("h2");
  h.textContent = "✗ 两份规程不等价";
  box.appendChild(h);

  const meta = document.createElement("p");
  const word = result.word.length ? result.word.join("") : "ε（空串）";
  meta.innerHTML =
    `最短反例命令串：<span class="word-badge">${word.replace(/</g, "&lt;")}</span>` +
    `（长度 ${result.wordLength}，同长度内 ASCII 字典序最小）`;
  box.appendChild(meta);

  const traceWrap = document.createElement("div");
  traceWrap.className = "trace";
  for (const side of sides) {
    const div = document.createElement("div");
    const h3 = document.createElement("h3");
    h3.textContent = `规程 ${side}：最终安全概率 ${fracText(result[side].final)}`;
    div.appendChild(h3);
    div.appendChild(renderTraceTable(result[side], result[side].steps[0].distribution.length));
    traceWrap.appendChild(div);
  }
  box.appendChild(traceWrap);

  const diff = document.createElement("p");
  diff.className = "diff-line";
  diff.textContent = `最终概率差 P_A(安全) − P_B(安全) = ${fracText(result.difference)}`;
  box.appendChild(diff);

  box.hidden = false;
}

// ---------------------------------------------------------------------------
// 提交
// ---------------------------------------------------------------------------

$("btn-review").addEventListener("click", async () => {
  clearHighlights();
  $("errors").hidden = true;
  $("result").hidden = true;
  dirty = false;

  let payload;
  try {
    payload = { A: collectPayload("A"), B: collectPayload("B") };
  } catch (e) {
    showErrors([{ loc: [], msg: `表单读取失败：${e.message}` }]);
    return;
  }

  try {
    const resp = await fetch("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) {
      showErrors(data.errors || [{ loc: [], msg: "服务端返回未知错误" }]);
      return;
    }
    if (data.result.equivalent) renderEquivalent();
    else renderCounterexample(data.result);
  } catch (e) {
    showErrors([{ loc: [], msg: `API 请求失败：${e.message}` }]);
  }
});

// ---------------------------------------------------------------------------
// 示例
// ---------------------------------------------------------------------------

function fillForm(side, { n, initial, safe, symbols, matrices }) {
  $(`n-${side}`).value = String(n);
  $(`initial-${side}`).value = initial;
  $(`safe-${side}`).value = safe;
  $(`symbols-${side}`).value = symbols;
  buildCommands(side);
  const container = $(`commands-${side}`);
  container.querySelectorAll(".command-block").forEach((block) => {
    const symbol = JSON.parse(block.querySelector("h3").textContent.replace("命令 ", ""));
    block.querySelectorAll("tbody tr").forEach((tr, r) => {
      tr.querySelectorAll("td").forEach((td, c) => {
        td.querySelector("select").value = String(c);
        td.querySelector("input").value = matrices[symbol][r][c];
      });
    });
  });
  markDirty();
}

$("btn-example").addEventListener("click", () => {
  // 差异只在长度 2 的 "aa" 暴露
  fillForm("A", {
    n: 3,
    initial: "1 0 0",
    safe: "2",
    symbols: "a",
    matrices: {
      a: [["0", "1", "0"], ["0", "1", "0"], ["0", "0", "1"]],
    },
  });
  fillForm("B", {
    n: 3,
    initial: "1 0 0",
    safe: "2",
    symbols: "a",
    matrices: {
      a: [["0", "1", "0"], ["0", "0", "1"], ["0", "0", "1"]],
    },
  });
});

$("btn-equivalent-example").addEventListener("click", () => {
  const m = [["1/2", "1/2"], ["1/3", "2/3"]];
  for (const side of sides) {
    fillForm(side, {
      n: 2,
      initial: "1 0",
      safe: "1",
      symbols: "x",
      matrices: { x: m },
    });
  }
});

// 初始化
for (const side of sides) buildCommands(side);
