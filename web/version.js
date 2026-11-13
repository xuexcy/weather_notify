/* 版本徽标：主页与设置页顶部共用。
 *
 * 数据来自 GET /api/version：
 *   {mode, current, latest, state, url, release_url, assets[], checked_text, error}
 *   state: latest | outdated | unknown | off | unchecked
 *
 * 视觉上分主次：已是最新只留一个小圆点（不该抢注意力），有新版本才展开成
 * 醒目的胶囊并给「下载」入口，检测失败是暗红小字而不是整条红带——内网设备
 * 常年连不上 GitHub，大红条看久了就没人当真了。
 */
(function () {
  "use strict";

  var chip = document.getElementById("verChip");
  if (!chip) return;

  var bar = chip.parentNode;
  var numEl = document.getElementById("verNum");
  var stateEl = document.getElementById("verState");
  var refreshBtn = document.getElementById("verRefresh");

  var STATE_TEXT = {
    latest: "已是最新",
    outdated: "有新版本",
    unknown: "无法检测新版本",
    off: "检查已关闭",
    unchecked: "未检查"
  };

  var busy = false;
  var queued = false;
  var retried = false;
  var last = null;
  var dlBtn = null;
  var panel = null;

  function esc(text) {
    return String(text === undefined || text === null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtSize(bytes) {
    var n = Number(bytes) || 0;
    if (!n) return "";
    if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
    return Math.max(1, Math.round(n / 1024)) + " KB";
  }

  /* ---------- 下载面板：有新版本时才出现 ---------- */
  function ensurePanel() {
    if (panel) return panel;
    panel = document.createElement("div");
    panel.className = "ver-panel";
    panel.hidden = true;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "新版本下载");
    bar.appendChild(panel);
    return panel;
  }

  function ensureDlBtn() {
    if (dlBtn) return dlBtn;
    dlBtn = document.createElement("button");
    dlBtn.type = "button";
    dlBtn.className = "link-btn ver-dl";
    dlBtn.id = "verDl";
    dlBtn.hidden = true;
    dlBtn.textContent = "下载";
    dlBtn.setAttribute("aria-expanded", "false");
    chip.insertAdjacentElement("afterend", dlBtn);
    dlBtn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      togglePanel();
    });
    return dlBtn;
  }

  function panelHtml(data) {
    var latest = data && data.latest ? "v" + data.latest : "新版本";
    var notes = data && data.release_url
      ? '<a class="ver-notes" href="' + esc(data.release_url) +
        '" target="_blank" rel="noopener">查看 ' + esc(latest) + " 的更新说明 →</a>"
      : "";
    var assets = (data && data.assets) || [];
    var list = assets.map(function (a) {
      var meta = [a.arch === "arm" ? "ARM64" : "x86_64", fmtSize(a.size)]
        .filter(Boolean).join(" · ");
      return '<li><a href="' + esc(a.url) + '" target="_blank" rel="noopener">' +
        esc(a.name) + "</a><span>" + esc(meta) + "</span></li>";
    }).join("");
    var body = assets.length
      ? '<ul class="ver-assets">' + list + "</ul>"
      : '<p class="ver-panel-empty">这个 release 没传 fpk 附件，去 releases 页面看看</p>';
    return '<div class="ver-panel-head">' + esc(latest) + " 已发布</div>" +
      notes +
      '<p class="ver-panel-tip">fnOS 用户：下载对应架构的 fpk，在应用中心手动安装即可升级，配置会保留。</p>' +
      body;
  }

  function hidePanel() {
    if (!panel || panel.hidden) return;
    panel.hidden = true;
    if (dlBtn) dlBtn.setAttribute("aria-expanded", "false");
  }

  function togglePanel() {
    var box = ensurePanel();
    if (!box.hidden) {
      hidePanel();
      return;
    }
    box.innerHTML = panelHtml(last);
    box.hidden = false;
    if (dlBtn) dlBtn.setAttribute("aria-expanded", "true");
  }

  document.addEventListener("click", function (e) {
    if (!panel || panel.hidden) return;
    if (panel.contains(e.target) || e.target === dlBtn) return;
    hidePanel();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") hidePanel();
  });

  /* ---------- 渲染 ---------- */
  function paint(data, failedText) {
    var current = (data && data.current) || "";
    var latest = (data && data.latest) || "";
    var state = failedText ? "unknown" : ((data && data.state) || "unknown");
    last = data || null;

    if (data && data.url) chip.href = data.url;
    numEl.textContent = current ? "v" + current : "v—";

    var text = failedText || STATE_TEXT[state] || STATE_TEXT.unknown;
    if (!failedText && state === "outdated" && latest) {
      text = "有新版本 v" + latest;
    }
    stateEl.textContent = text;
    chip.className = "ver-chip is-" + state;

    var dl = ensureDlBtn();
    dl.hidden = !(state === "outdated");
    if (dl.hidden) hidePanel();

    if (refreshBtn) {
      var off = state === "off";
      refreshBtn.hidden = off;
      refreshBtn.disabled = busy || off;
      refreshBtn.title = off ? "检查更新已关闭：设置页「服务 → 检查更新」可以打开"
                             : "忽略缓存，立刻查一次 GitHub";
    }

    var tips = [];
    if (current) tips.push("当前版本 v" + current);
    if (latest) tips.push("GitHub 上最新 v" + latest);
    if (data && data.checked_text) {
      tips.push("上次检查 " + data.checked_text + (data.stale ? "（旧结果，可点「重新检查」）" : ""));
    }
    if (state === "off") tips.push("已关闭自动检查，不再向 GitHub 发请求");
    if (data && data.error) tips.push("说明：" + data.error);
    if (failedText) tips.push("说明：" + failedText);
    tips.push("点击打开 releases 页面");
    chip.title = tips.join("\n");
  }

  function load(force) {
    // 正在查的时候又来了 ver:reload（比如刚把「检查更新」切成关闭）：
    // 记一笔，等这次查完再重跑，不然新设置要等下次刷新页面才生效
    if (busy) { queued = true; return Promise.resolve(); }
    busy = true;
    if (refreshBtn) refreshBtn.disabled = true;
    chip.className = "ver-chip is-checking";
    stateEl.textContent = force ? "检查中…"
      : (numEl.textContent === "v—" ? "检查中…" : stateEl.textContent);

    var url = "/api/version" + (force ? "?refresh=1" : "");
    return fetch(url, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        paint(data, null);
        // 首次打开时服务端刚起后台刷新：等它一秒再取一次
        if (!retried && data && data.stale && !data.latest) {
          retried = true;
          setTimeout(function () { load(false); }, 2500);
        }
      })
      .catch(function (err) {
        paint(null, "检测失败：" + (err && err.message ? err.message : "接口不可用"));
      })
      .then(function () {
        busy = false;
        // 关掉检查时按钮是隐藏的，别再顺手把它 enable 回来
        if (refreshBtn) refreshBtn.disabled = !!(last && last.state === "off");
        if (queued) { queued = false; load(false); }
      });
  }

  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () { load(true); });
  }
  // 设置页改完「检查更新」会派发这个事件，让徽标按新设置重画
  document.addEventListener("ver:reload", function () { load(false); });

  load(false);
})();
