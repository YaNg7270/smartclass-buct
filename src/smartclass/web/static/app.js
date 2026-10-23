/* scls Web 界面 · 原生 JS（无构建链）
 * - 仪表盘：2s 轮询 /api/job 与 /api/status，整体替换日志区
 * - 整课页：Tab 切换
 * - 所有页面：登录失效横幅
 */
"use strict";

// ---- Tab 切换（lecture 页） ---------------------------------------------- //
(function initTabs() {
  const tabs = document.getElementById("tabs");
  if (!tabs) return;
  const show = (id) => {
    document.querySelectorAll(".tab").forEach((el) => { el.hidden = el.id !== id; });
    tabs.querySelectorAll("button").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === id);
    });
  };
  tabs.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-tab]");
    if (btn) show(btn.dataset.tab);
  });
})();

// ---- 登录失效横幅 --------------------------------------------------------- //
let bannerDismissed = false;

function renderStatus(s) {
  const box = document.getElementById("banner");
  if (!box) return;
  if (s && s.login_needed && !bannerDismissed) {
    if (box.querySelector(".login-banner")) return; // 已在展示
    const div = document.createElement("div");
    div.className = "login-banner";
    const detail = s.detail ? `（${s.detail}）` : "";
    div.innerHTML = `<span>⚠️ 登录已失效${detail}。本界面不会弹出登录窗口，请在终端运行 <code>scls login</code> 后重试。</span>`;
    const btn = document.createElement("button");
    btn.textContent = "清除标记";
    btn.addEventListener("click", async () => {
      await fetch("/api/login/clear", { method: "POST" });
      bannerDismissed = false;
      box.innerHTML = "";
    });
    div.appendChild(btn);
    box.innerHTML = "";
    box.appendChild(div);
  } else if (!s || !s.login_needed) {
    bannerDismissed = false;
    box.innerHTML = "";
  }
}

// ---- 任务面板（仪表盘） ---------------------------------------------------- //
const EXIT_TEXT = {
  0: "✅ 成功",
  1: "❌ 失败",
  2: "🔑 需重新登录（请在终端运行 scls login）",
  3: "🔒 数据锁被占（有其他 scls 进程在跑）",
  130: "⏹ 已中断",
};

let lastLogKey = "";

function renderJob(j) {
  const dot = document.getElementById("job-dot");
  if (!dot) return;
  let cls = "idle", title = "无任务";
  if (j.running) { cls = "run"; title = `运行中：${j.verb}`; }
  else if (j.exit_code === 0) { cls = "ok"; title = "上次任务成功"; }
  else if (j.exit_code !== null && j.exit_code !== undefined) {
    cls = j.exit_code === 2 ? "run" : "bad";
    title = `上次任务退出码 ${j.exit_code}`;
  }
  dot.className = `dot ${cls}`;
  dot.title = title;

  const summary = document.getElementById("job-summary");
  if (summary) {
    if (j.running) {
      summary.textContent = `运行中：${j.verb} ${j.args.join(" ")}（自 ${j.started_at}）`;
    } else if (j.verb) {
      const text = EXIT_TEXT[j.exit_code] !== undefined
        ? EXIT_TEXT[j.exit_code]
        : `退出码 ${j.exit_code}`;
      summary.textContent = `上次任务：${j.verb} ${j.args.join(" ")} → ${text}（日志 ${j.log_file}）`;
    } else {
      summary.textContent = "当前无任务。";
    }
  }

  const log = document.getElementById("job-log");
  if (log) {
    const tail = j.log_tail || [];
    // 长任务被 _log_tail 截到 400 行后长度恒定，key 必须带上最后一行才能持续刷新
    const key = `${j.log_file}:${tail.length}:${(tail[tail.length - 1] || "").slice(-80)}`;
    if (key !== lastLogKey) {
      lastLogKey = key;
      const lines = j.log_tail || [];
      let html = "";
      for (const line of lines) {
        const m = line.match(/退出码[:：]?\s*(\d+)/);
        const cls = m ? ` class="xc-${m[1]}"` : "";
        html += `<span${cls}>${escapeHtml(line)}</span>\n`;
      }
      log.innerHTML = html;
      log.scrollTop = log.scrollHeight;
    }
  }

  const submit = document.getElementById("job-submit");
  const cancel = document.getElementById("job-cancel");
  if (submit) submit.disabled = !!j.running;
  if (cancel) cancel.hidden = !j.running;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function poll() {
  try {
    const [jobRes, statusRes] = await Promise.all([
      fetch("/api/job"), fetch("/api/status"),
    ]);
    renderJob(await jobRes.json());
    renderStatus(await statusRes.json());
  } catch { /* 服务器暂不可达时静默 */ }
}
poll();
setInterval(poll, 2000);

// ---- 运行表单 -------------------------------------------------------------- //
(function initForm() {
  const form = document.getElementById("job-form");
  if (!form) return;
  const verb = document.getElementById("f-verb");
  const runOnly = document.getElementById("run-only");
  const resumeOnly = document.getElementById("resume-only");
  const msg = document.getElementById("job-msg");
  const date = document.getElementById("f-date");
  const from = document.getElementById("f-from");
  const to = document.getElementById("f-to");

  const showMsg = (text, cls) => {
    msg.hidden = false;
    msg.className = `msg ${cls}`;
    msg.textContent = text;
  };

  verb.addEventListener("change", () => {
    runOnly.hidden = verb.value !== "run";
    resumeOnly.hidden = verb.value !== "resume";
  });

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    msg.hidden = true;
    const body = { verb: verb.value };
    if (date.value) body.date = date.value;
    if (from.value) body.date_from = from.value;
    if (to.value) body.date_to = to.value;
    const course = document.getElementById("f-course").value.trim();
    if (course) body.course = course;
    const unit = document.getElementById("f-unit").value;
    if (unit) body.unit = unit;
    const tracks = document.getElementById("f-tracks").value.trim();
    if (tracks) body.tracks = tracks;
    const keep = document.getElementById("f-keep").value;
    if (keep) body.keep_video = keep;
    const dir = document.getElementById("f-dir").value.trim();
    if (dir) body.dir = dir;
    for (const [id, key] of [
      ["f-dry", "dry_run"], ["f-force", "force"],
      ["f-noslides", "no_slides"], ["f-nodigest", "no_digest"],
    ]) {
      if (document.getElementById(id).checked) body[key] = true;
    }

    if (body.verb === "resume" && !body.dir) {
      showMsg("resume 需要填写目录。", "err");
      return;
    }
    if (body.verb === "run" && !body.date && !body.date_from && !body.date_to) {
      showMsg("run 需要至少填写日期或起止范围。", "err");
      return;
    }

    try {
      const res = await fetch("/api/job", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data.ok) {
        showMsg(`已启动 ${body.verb} 任务。`, "info");
        lastLogKey = "";
      } else {
        showMsg(data.error || "启动失败。", "err");
      }
    } catch (exc) {
      showMsg(`请求失败：${exc}`, "err");
    }
  });

  document.getElementById("job-cancel").addEventListener("click", async () => {
    try {
      const res = await fetch("/api/job/cancel", { method: "POST" });
      const data = await res.json();
      showMsg(data.ok ? "已发送终止信号。" : (data.error || "取消失败。"),
              data.ok ? "info" : "err");
    } catch (exc) {
      showMsg(`请求失败：${exc}`, "err");
    }
  });
})();
