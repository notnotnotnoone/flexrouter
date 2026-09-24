/* The dashboard's behaviour. Loaded (deferred) after htmx, idiomorph and
   Motion; every page is complete HTML before this runs.

   Animation triggers, from the design spec §4:
     first paint  - once per page load or page navigation
     on change    - after a live refresh, only elements whose value changed
     continuous   - CSS only (live dot, shimmer)
     interaction  - CSS, plus the menu marker below
   A live refresh on its own never replays anything. */
(function () {
  "use strict";

  var html = document.documentElement;
  html.classList.add("js");
  var M = window.Motion || null;
  var EASE = [0.22, 1, 0.36, 1];

  function motion() {
    var pref = html.getAttribute("data-motion") || "full";
    if (pref === "full" && window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return "reduced";
    }
    return pref;
  }

  function each(list, fn) { Array.prototype.forEach.call(list, fn); }

  /* ── first paint ──────────────────────────────────────────── */

  function reveal(els) { each(els, function (el) { el.style.opacity = 1; }); }

  /* CSS hides [data-enter] blocks until <html> has the "entered" class.
     The class goes on as soon as the entrance animation has taken over
     their opacity - never later - so a live refresh that morphs these
     elements (and drops Motion's inline styles) cannot hide them again. */
  function enter(root) {
    var blocks = root.querySelectorAll("[data-enter]");
    if (motion() !== "full" || !M) { reveal(blocks); html.classList.add("entered"); return; }

    M.animate(blocks, { opacity: [0, 1], transform: ["translateY(6px)", "none"] },
      { duration: 0.24, delay: M.stagger(0.045), ease: EASE });
    html.classList.add("entered");

    each(root.querySelectorAll(".meter[data-value]"), function (m, i) {
      var on = m.querySelectorAll("i.on");
      if (!on.length) return;
      // An explicit scaleY(1), not "none": Motion interpolates toward "none"
      // as scaleY(0) and leaves every lit cell flattened to nothing.
      // The start is capped so a page with hundreds of meters (the Error
      // brain's probability tables) does not keep the last ones dark for
      // tens of seconds.
      M.animate(on, { opacity: [0, 1], transform: ["scaleY(.3)", "scaleY(1)"] },
        { duration: 0.14, delay: M.stagger(0.02, { startDelay: 0.18 + Math.min(i, 24) * 0.04 }), ease: EASE });
    });

    each(root.querySelectorAll(".strip"), function (s, i) {
      M.animate(s.children, { opacity: [0, 1] },
        { duration: 0.12, delay: M.stagger(0.012, { startDelay: 0.25 + i * 0.06 }) });
    });

    each(root.querySelectorAll(".stat-value"), countUp);

    each(root.querySelectorAll(".chart"), function (svg) {
      M.animate(svg, { clipPath: ["inset(0 100% 0 0)", "inset(0 0% 0 0)"] },
        { duration: 0.7, delay: 0.2, ease: EASE });
    });
  }

  /* "1,284", "98.6%", "820 ms", "$0.12" - count the number, keep the rest. */
  function countUp(el) {
    var raw = el.getAttribute("data-value") || el.textContent;
    var m = /^([^\d-]*)(-?[\d,]*\.?\d+)(.*)$/.exec(raw);
    if (!m || !M) return;
    var target = parseFloat(m[2].replace(/,/g, ""));
    if (!isFinite(target) || target === 0) return;
    var decimals = (m[2].split(".")[1] || "").length;
    var grouped = m[2].indexOf(",") !== -1 || target >= 1000;
    M.animate(function (p) {
      var v = target * p;
      el.textContent = m[1] + v.toLocaleString("en-US", {
        minimumFractionDigits: decimals, maximumFractionDigits: decimals,
        useGrouping: grouped
      }) + m[3];
    }, { duration: 0.7, ease: EASE }).finished.then(function () { el.textContent = raw; });
  }

  /* ── on change, after a live refresh ─────────────────────── */

  var before = null;

  function keyOf(el) { return el.getAttribute("data-stat") || el.getAttribute("data-row"); }

  function valueOf(el) {
    var v = el.hasAttribute("data-value") ? el : el.querySelector("[data-value]");
    return v ? v.getAttribute("data-value") : el.textContent;
  }

  function snapshot(root) {
    var s = {};
    each(root.querySelectorAll("[data-stat],[data-row]"), function (el) { s[keyOf(el)] = valueOf(el); });
    return s;
  }

  function num(v) {
    var n = parseFloat(String(v).replace(/[^\d.-]/g, ""));
    return isNaN(n) ? null : n;
  }

  function isPoll(detail) {
    var elt = detail && detail.requestConfig && detail.requestConfig.elt;
    return !!(elt && elt.hasAttribute && elt.hasAttribute("data-live"));
  }

  document.addEventListener("htmx:beforeSwap", function (e) {
    if (isPoll(e.detail)) before = snapshot(e.detail.target);
  });

  document.addEventListener("htmx:afterSettle", function (e) {
    if (!isPoll(e.detail)) return;
    var root = e.detail.target;
    failures = 0;
    root.removeAttribute("data-stale");
    if (!before || motion() === "off") { before = null; return; }
    each(root.querySelectorAll("[data-stat],[data-row]"), function (el) {
      var k = keyOf(el), now = valueOf(el);
      if (!(k in before)) {
        if (el.hasAttribute("data-row")) flash(el, "is-new");
        return;
      }
      if (before[k] === now) return;
      var a = num(before[k]), b = num(now);
      flash(el, a !== null && b !== null && b < a ? "flash-down" : "flash-up");
    });
    before = null;
  });

  function flash(el, cls) {
    el.classList.remove("flash-up", "flash-down", "is-new");
    void el.offsetWidth; /* restart the animation */
    el.classList.add(cls);
  }

  /* ── stale marking for anything that polls ───────────────── */

  var failures = 0;
  function onFail(e) {
    if (!isPoll(e.detail)) return;
    failures += 1;
    if (failures >= 3 && e.detail.target) e.detail.target.setAttribute("data-stale", "");
  }
  document.addEventListener("htmx:responseError", onFail);
  document.addEventListener("htmx:sendError", onFail);

  /* ── toasts ──────────────────────────────────────────────── */

  function toast(message, kind) {
    var box = document.getElementById("toasts");
    if (!box || !message) return;
    var t = document.createElement("div");
    t.className = "toast " + (kind || "ok");
    t.setAttribute("role", kind === "bad" ? "alert" : "status");
    t.textContent = message;
    box.appendChild(t);
    var animate = M && motion() !== "off";
    if (animate) {
      M.animate(t, { opacity: [0, 1], transform: ["translateX(24px)", "none"] },
        { duration: 0.28, ease: EASE });
    }
    setTimeout(function () {
      if (!animate) { t.remove(); return; }
      M.animate(t, { opacity: 0, transform: "translateX(24px)" }, { duration: 0.2 })
        .finished.then(function () { t.remove(); });
    }, kind === "bad" ? 7000 : 4200);
  }

  document.addEventListener("toast", function (e) {
    var d = e.detail || {};
    toast(d.message, d.kind);
  });
  window.flex = { toast: toast };

  /* ── the menu marker ─────────────────────────────────────── */

  function placeMarker(instant) {
    var here = document.querySelector(".nav a.nav-link[aria-current]");
    var mk = document.querySelector(".nav-marker");
    if (!here || !mk) return;
    if (instant) mk.style.transition = "none";
    mk.style.transform = "translateY(" + here.offsetTop + "px)";
    mk.style.height = here.offsetHeight + "px";
    if (instant) { void mk.offsetWidth; mk.style.transition = ""; }
  }

  /* hx-boost swaps the whole body, which would drop the marker back to
     where the new page renders it. Remember where it was so it can slide. */
  var lastMarker = null;
  document.addEventListener("htmx:beforeSwap", function (e) {
    if (!boosted(e.detail)) return;
    html.classList.remove("entered");   /* the next page gets its own entrance */
    var mk = document.querySelector(".nav-marker");
    lastMarker = mk ? mk.style.transform : null;
  });

  /* ── countdowns ──────────────────────────────────────────── */
  /* Server renders "Resets in 2h 13m" with the target time attached; this
     keeps it true between refreshes. At zero it says so and stops. */

  function duration(s) {
    var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600),
        m = Math.floor(s % 3600 / 60), sec = s % 60;
    if (d) return d + "d " + h + "h";
    if (h) return h + "h " + m + "m";
    if (m) return m + "m " + (sec < 10 ? "0" : "") + sec + "s";
    return sec + "s";
  }

  setInterval(function () {
    var now = Date.now() / 1000;
    each(document.querySelectorAll("[data-countdown]"), function (el) {
      var left = Math.max(0, Math.round(parseFloat(el.getAttribute("data-countdown")) - now));
      if (left === 0) {
        if (!el.classList.contains("is-done")) {
          el.textContent = "Back now";
          el.classList.add("is-done", "status-ok");
        }
        return;
      }
      el.textContent = (el.getAttribute("data-prefix") || "Resets in") + " " + duration(left);
    });
  }, 1000);

  /* ── filter-as-you-type (Settings) ───────────────────────── */

  document.addEventListener("input", function (e) {
    var box = e.target;
    var sel = box.getAttribute && box.getAttribute("data-filter");
    if (!sel) return;
    var q = box.value.trim().toLowerCase();
    each(document.querySelectorAll(sel), function (row) {
      row.hidden = !!q && (row.getAttribute("data-search") || row.textContent).toLowerCase().indexOf(q) === -1;
    });
    /* open "Advanced" when it holds a match, so a search never looks empty */
    each(document.querySelectorAll("details.set-advanced"), function (d) {
      if (q && d.querySelector(sel + ":not([hidden])")) d.open = true;
    });
  });

  /* ── filter by attribute (e.g. the Models provider picker) ── */

  document.addEventListener("change", function (e) {
    var sel = e.target;
    var attr = sel.getAttribute && sel.getAttribute("data-filter-attr");
    if (!attr) return;
    var v = sel.value;
    each(document.querySelectorAll(sel.getAttribute("data-filter-target")), function (row) {
      row.hidden = !!v && row.getAttribute("data-" + attr) !== v;
      var detail = document.getElementById(row.getAttribute("data-toggle-row"));
      if (detail && row.hidden) detail.hidden = true;
    });
  });

  /* ── sortable tables ─────────────────────────────────────── */
  /* Click a header with data-sort=KEY; rows sort by their data-KEY. A row's
     detail row (data-toggle-row) travels with it. */

  document.addEventListener("click", function (e) {
    var th = e.target.closest && e.target.closest("th[data-sort]");
    if (!th) return;
    var table = th.closest("table"), key = th.getAttribute("data-sort");
    var dir = th.getAttribute("aria-sort") === "descending" ? "ascending" : "descending";
    each(table.querySelectorAll("th[data-sort]"), function (h) { h.removeAttribute("aria-sort"); });
    th.setAttribute("aria-sort", dir);
    var tbody = table.tBodies[0];
    var rows = Array.prototype.filter.call(tbody.rows, function (r) { return r.hasAttribute("data-" + key); });
    rows.sort(function (a, b) {
      var x = a.getAttribute("data-" + key), y = b.getAttribute("data-" + key);
      var nx = parseFloat(x), ny = parseFloat(y);
      var c = !isNaN(nx) && !isNaN(ny) ? nx - ny : x.localeCompare(y);
      return dir === "ascending" ? c : -c;
    });
    rows.forEach(function (r) {
      tbody.appendChild(r);
      var d = document.getElementById(r.getAttribute("data-toggle-row"));
      if (d) tbody.appendChild(d);
    });
  });

  /* ── expanding detail rows (Models) ──────────────────────── */

  document.addEventListener("click", function (e) {
    var row = e.target.closest && e.target.closest("tr[data-toggle-row]");
    if (!row || e.target.closest("a, input, select, textarea, form")) return;
    var detail = document.getElementById(row.getAttribute("data-toggle-row"));
    if (!detail) return;
    var opening = detail.hidden;
    detail.hidden = !opening;
    row.classList.toggle("is-open", opening);
    if (opening && M && motion() !== "off") {
      M.animate(detail.querySelector(".m-detail-inner"),
        { opacity: [0, 1], transform: ["translateY(-6px)", "none"] }, { duration: 0.2, ease: EASE });
    }
  });

  /* ── busy forms ──────────────────────────────────────────── */
  /* A form marked data-busy says what it is doing while the server works
     (testing a key can take a few seconds) instead of looking frozen. */

  document.addEventListener("submit", function (e) {
    var form = e.target, msg = form.getAttribute && form.getAttribute("data-busy");
    if (!msg) return;
    var btn = form.querySelector("button[type=submit]");
    if (!btn) return;
    btn.classList.add("shimmer");
    btn.textContent = msg;
    setTimeout(function () { btn.disabled = true; }, 0);   /* after the submit is sent */
  });

  /* ── copy buttons ────────────────────────────────────────── */

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-copy]");
    if (!btn) return;
    var src = document.querySelector(btn.getAttribute("data-copy"));
    if (!src || !navigator.clipboard) return;
    navigator.clipboard.writeText(src.textContent.trim()).then(function () {
      toast("Copied", "ok");
    });
  });

  /* ── section list follows the scroll (Settings) ──────────── */

  function watchToc() {
    var links = document.querySelectorAll(".toc-link");
    if (!links.length || !window.IntersectionObserver) return;
    var byId = {};
    each(links, function (a) { byId[a.getAttribute("href").slice(1)] = a; });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting || !byId[en.target.id]) return;
        each(links, function (a) { a.classList.remove("is-here"); });
        byId[en.target.id].classList.add("is-here");
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    Object.keys(byId).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) io.observe(el);
    });
  }

  /* ── the Playground ──────────────────────────────────────── */
  /* The conversation lives only in this page; the settings column is
     remembered per browser. Replies stream in as the model writes them. */

  var convo = [];

  function remember(el) {
    try {
      var saved = localStorage.getItem("pg:" + el.id);
      if (saved !== null) el.value = saved;
      el.addEventListener("change", function () { localStorage.setItem("pg:" + el.id, el.value); });
    } catch (err) { /* storage blocked: settings just aren't remembered */ }
  }

  function bubble(role, text) {
    var log = document.getElementById("pg-log");
    var empty = log.querySelector(".empty");
    if (empty) empty.remove();
    var b = document.createElement("div");
    b.className = "pg-msg pg-" + role;
    var who = document.createElement("span");
    who.className = "pg-who";
    who.textContent = role === "user" ? "you" : "flexrouter";
    var body = document.createElement("div");
    body.className = "pg-text";
    body.textContent = text;
    b.appendChild(who);
    b.appendChild(body);
    log.appendChild(b);
    if (M && motion() !== "off") M.animate(b, { opacity: [0, 1], transform: ["translateY(6px)", "none"] }, { duration: 0.2 });
    log.scrollTop = log.scrollHeight;
    return body;
  }

  function strip(target, info) {
    var s = document.createElement("a");
    s.className = "pg-strip outcome-" + (info.outcome || "ok");
    s.href = "/requests?id=" + encodeURIComponent(info.id);
    s.setAttribute("hx-get", "/requests/" + encodeURIComponent(info.id) + "/journey");
    s.setAttribute("hx-target", "#sheet-root");
    s.setAttribute("hx-swap", "innerHTML");
    s.textContent = [
      (info.outcome === "failover" ? "FAILOVER · " : info.outcome === "failed" ? "FAILED · " : "") +
        (info.answered_by || "nothing answered"),
      (info.tokens_in || 0) + " in / " + (info.tokens_out || 0) + " out",
      (info.ms || 0).toLocaleString() + " ms"
    ].join("  ·  ");
    target.parentNode.appendChild(s);
    if (window.htmx) htmx.process(s);
  }

  /* One card per model in a Compare turn; keyed by the `model` field every
     chunk for that model carries, so late-arriving chunks find their card. */
  function compareGrid(targets) {
    var log = document.getElementById("pg-log");
    var empty = log.querySelector(".empty");
    if (empty) empty.remove();
    var wrap = document.createElement("div");
    wrap.className = "pg-msg pg-assistant pg-compare-msg";
    var who = document.createElement("span");
    who.className = "pg-who";
    who.textContent = "flexrouter · comparing " + targets.length + " models";
    wrap.appendChild(who);
    var grid = document.createElement("div");
    grid.className = "pg-compare-grid";
    var cards = {};
    targets.forEach(function (t) {
      var card = document.createElement("div");
      card.className = "pg-compare-card";
      var head = document.createElement("div");
      head.className = "pg-compare-head";
      head.textContent = t;
      var body = document.createElement("div");
      body.className = "pg-text shimmer";
      card.appendChild(head);
      card.appendChild(body);
      grid.appendChild(card);
      cards[t] = { body: body, text: "" };
    });
    wrap.appendChild(grid);
    log.appendChild(wrap);
    log.scrollTop = log.scrollHeight;
    return cards;
  }

  async function send() {
    var input = document.getElementById("pg-input"), btn = document.getElementById("pg-send");
    var text = input.value.trim();
    if (!text || btn.disabled) return;
    var targetValue = document.getElementById("pg-target").value;
    var compare = targetValue.indexOf("compare:") === 0;
    input.value = "";
    convo.push({ role: "user", content: text });
    bubble("user", text);
    var out = compare ? null : bubble("assistant", "");
    if (out) out.classList.add("shimmer");
    btn.disabled = true;
    var sys = document.getElementById("pg-system").value.trim();
    var msgs = (sys ? [{ role: "system", content: sys }] : []).concat(convo);
    var answer = "", cards = null;
    try {
      var r = await fetch("/playground/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: targetValue,
                               temperature: document.getElementById("pg-temp").value,
                               max_tokens: document.getElementById("pg-max").value,
                               top_p: document.getElementById("pg-top-p").value,
                               frequency_penalty: document.getElementById("pg-freq").value,
                               presence_penalty: document.getElementById("pg-pres").value,
                               stop: document.getElementById("pg-stop").value,
                               messages: msgs })
      });
      if (!r.ok) {
        var err = await r.json().catch(function () { return {}; });
        throw new Error(err.error || ("HTTP " + r.status));
      }
      var reader = r.body.getReader(), dec = new TextDecoder(), buf = "", evName = "";
      for (;;) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buf += dec.decode(chunk.value, { stream: true });
        var parts = buf.split("\n\n");
        buf = parts.pop();
        parts.forEach(function (block) {
          evName = "";
          block.split("\n").forEach(function (line) {
            if (line.indexOf("event: ") === 0) evName = line.slice(7).trim();
            if (line.indexOf("data: ") !== 0) return;
            var data = line.slice(6);
            if (data === "[DONE]") return;
            var payload;
            try { payload = JSON.parse(data); } catch (e2) { return; }
            if (evName === "targets") { cards = compareGrid(payload.targets || []); return; }
            if (evName === "flexrouter") {
              if (payload.target && cards && cards[payload.target]) strip(cards[payload.target].body, payload);
              else if (out) strip(out, payload);
              return;
            }
            var card = cards && payload.model ? cards[payload.model] : null;
            if (compare && !card) return;
            if (payload.error) {
              var msg = "[" + payload.error.message + "]";
              if (card) { card.text += (card.text ? "\n\n" : "") + msg; }
              else { answer += (answer ? "\n\n" : "") + msg; }
            }
            var c = payload.choices && payload.choices[0];
            var delta = (c && c.delta && c.delta.content) || "";
            if (card) {
              card.text += delta;
              card.body.classList.remove("shimmer");
              card.body.textContent = card.text;
            } else {
              answer += delta;
              if (out) { out.classList.remove("shimmer"); out.textContent = answer; }
            }
            document.getElementById("pg-log").scrollTop = 1e9;
          });
        });
      }
      if (!compare) convo.push({ role: "assistant", content: answer });
    } catch (e) {
      if (out) {
        out.textContent = "Not sent: " + e.message;
        out.parentNode.classList.add("pg-error");
      } else if (cards) {
        Object.keys(cards).forEach(function (t) {
          cards[t].body.classList.remove("shimmer");
          if (!cards[t].text) cards[t].body.textContent = "Not sent: " + e.message;
        });
      }
      convo.pop();
    } finally {
      if (out) out.classList.remove("shimmer");
      if (cards) Object.keys(cards).forEach(function (t) { cards[t].body.classList.remove("shimmer"); });
      btn.disabled = false;
      input.focus();
    }
  }

  function bootPlayground() {
    if (!document.getElementById("pg-composer")) return;
    each(document.querySelectorAll("[data-remember]"), remember);
    [["pg-temp", "pg-temp-out"], ["pg-top-p", "pg-top-p-out"],
     ["pg-freq", "pg-freq-out"], ["pg-pres", "pg-pres-out"]].forEach(function (pair) {
      var range = document.getElementById(pair[0]), out = document.getElementById(pair[1]);
      if (!range || !out) return;
      out.textContent = range.value;
      range.addEventListener("input", function () { out.textContent = range.value; });
    });
  }

  document.addEventListener("submit", function (e) {
    if (e.target.id === "pg-composer") { e.preventDefault(); send(); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.target.id === "pg-input" && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  document.addEventListener("click", function (e) {
    if (e.target.id !== "pg-clear") return;
    convo = [];
    var log = document.getElementById("pg-log");
    log.innerHTML = '<div class="empty"><p>Cleared. Nothing was saved.</p></div>';
  });

  /* ── Buckets: Try it ─────────────────────────────────────── */
  /* One real, one-token request through the bucket; the result names who
     answered and links to the request's journey. */

  document.addEventListener("click", async function (e) {
    var btn = e.target.closest && e.target.closest("[data-try]");
    if (!btn || btn.disabled) return;
    var bucket = btn.getAttribute("data-try");
    var out = document.getElementById("try-" + bucket);
    btn.disabled = true;
    out.className = "try-result shimmer";
    out.textContent = "Sending one small request through " + bucket + "...";
    var info = null, error = "";
    try {
      var r = await fetch("/playground/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: bucket, max_tokens: 1,
                               messages: [{ role: "user", content: "Reply with OK." }] })
      });
      var text = await r.text();
      var m = /event: flexrouter\ndata: (.*)\n/.exec(text);
      if (m) info = JSON.parse(m[1]);
      if (!r.ok) error = "HTTP " + r.status;
    } catch (err) { error = err.message; }
    out.className = "try-result";
    out.textContent = "";
    var word = document.createElement("span");
    if (info) {
      word.className = "outcome outcome-" + info.outcome;
      word.textContent = info.outcome.toUpperCase();
      out.appendChild(word);
      var what = document.createElement("span");
      what.textContent = (info.answered_by ? "answered by " + info.answered_by : "nothing answered") +
        " in " + info.ms.toLocaleString() + " ms";
      out.appendChild(what);
      var link = document.createElement("a");
      link.href = "/requests?id=" + encodeURIComponent(info.id);
      link.setAttribute("hx-get", "/requests/" + encodeURIComponent(info.id) + "/journey");
      link.setAttribute("hx-target", "#sheet-root");
      link.textContent = "See the path it took";
      out.appendChild(link);
      if (window.htmx) htmx.process(out);
    } else {
      out.textContent = "Not sent: " + (error || "no answer");
    }
    btn.disabled = false;
  });

  /* ── Buckets: drag a model card onto a bucket to add it there ──── */
  /* Everything the card needs is already in its own data-* attributes
     (facts.models() carried them from the model's live config), so a drop
     just replays the same POST the manual "add a model" form makes -
     nothing is looked up again, and there is no new endpoint. */

  var draggedCard = null;

  document.addEventListener("dragstart", function (e) {
    var card = e.target.closest && e.target.closest(".model-card");
    if (!card) return;
    draggedCard = card;
    card.classList.add("is-dragging");
    e.dataTransfer.effectAllowed = "copy";
    e.dataTransfer.setData("text/plain", card.getAttribute("data-provider") + "/" +
      card.getAttribute("data-model"));
  });

  document.addEventListener("dragend", function (e) {
    var card = e.target.closest && e.target.closest(".model-card");
    if (card) card.classList.remove("is-dragging");
    draggedCard = null;
  });

  document.addEventListener("dragover", function (e) {
    var zone = e.target.closest && e.target.closest("[data-dropzone]");
    if (!zone || !draggedCard) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    zone.classList.add("drag-over");
  });

  document.addEventListener("dragleave", function (e) {
    var zone = e.target.closest && e.target.closest("[data-dropzone]");
    if (zone && !zone.contains(e.relatedTarget)) zone.classList.remove("drag-over");
  });

  document.addEventListener("drop", async function (e) {
    var zone = e.target.closest && e.target.closest("[data-dropzone]");
    if (!zone || !draggedCard) return;
    e.preventDefault();
    zone.classList.remove("drag-over");
    var card = draggedCard;
    var bucket = zone.getAttribute("data-dropzone");
    var provider = card.getAttribute("data-provider");
    var model = card.getAttribute("data-model");
    var already = zone.querySelector('[data-row="' + provider + "/" + model + '"]');
    if (already) {
      window.flex.toast(provider + "/" + model + " is already in " + bucket, "bad");
      return;
    }
    var quotas = {};
    try { quotas = JSON.parse(card.getAttribute("data-quotas") || "{}"); } catch (err) { quotas = {}; }
    var body = new URLSearchParams();
    body.set("provider", provider);
    body.set("model", model);
    body.set("score", card.getAttribute("data-score") || "");
    body.set("rpm", card.getAttribute("data-rpm") || "");
    body.set("tpm", card.getAttribute("data-tpm") || "");
    if (card.getAttribute("data-context-window")) {
      body.set("context_window", card.getAttribute("data-context-window"));
    }
    if (card.getAttribute("data-tokens-per-second")) {
      body.set("tokens_per_second", card.getAttribute("data-tokens-per-second"));
    }
    if (card.getAttribute("data-vision") === "1") body.set("vision", "on");
    Object.keys(quotas).forEach(function (k) { body.set(k, quotas[k]); });
    try {
      var r = await fetch("/buckets/" + encodeURIComponent(bucket) + "/models", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });
      if (r.ok || r.redirected) {
        window.location.reload();
      } else {
        window.flex.toast("Could not add " + provider + "/" + model + " to " + bucket, "bad");
      }
    } catch (err) {
      window.flex.toast("Could not add " + provider + "/" + model + " to " + bucket, "bad");
    }
  });

  /* ── Ctrl+K command bar ──────────────────────────────────── */

  var index = null, hits = [], active = 0;

  function score(label, q) {
    /* In-order letters, rewarding a match at a word start or run. */
    label = label.toLowerCase();
    var i = 0, s = 0, run = 0;
    for (var j = 0; j < q.length; j++) {
      var at = label.indexOf(q[j], i);
      if (at === -1) return -1;
      run = at === i ? run + 1 : 0;
      s += 1 + run * 2 + (at === 0 || /[\s/_-]/.test(label[at - 1]) ? 3 : 0);
      i = at + 1;
    }
    return s - label.length * 0.01;
  }

  function render() {
    var box = document.getElementById("palette"), list = box.querySelector(".palette-list");
    var q = box.querySelector(".palette-input").value.trim().toLowerCase();
    hits = (index || []).map(function (it) {
      return { it: it, s: q ? Math.max(score(it.label, q), it.hint ? score(it.hint, q) - 1 : -1) : 0 };
    }).filter(function (h) { return h.s >= 0; })
      .sort(function (a, b) { return b.s - a.s; }).slice(0, 12);
    active = Math.min(active, Math.max(hits.length - 1, 0));
    list.innerHTML = "";
    if (!hits.length) {
      var none = document.createElement("li");
      none.className = "palette-none";
      none.textContent = index ? "Nothing matches." : "Loading...";
      list.appendChild(none);
      return;
    }
    hits.forEach(function (h, n) {
      var li = document.createElement("li");
      li.className = "palette-item" + (n === active ? " is-active" : "");
      li.setAttribute("role", "option");
      var kind = document.createElement("span");
      kind.className = "palette-kind";
      kind.textContent = h.it.kind;
      var label = document.createElement("span");
      label.className = "palette-label";
      label.textContent = h.it.label;
      li.appendChild(kind);
      li.appendChild(label);
      if (h.it.hint) {
        var hint = document.createElement("span");
        hint.className = "palette-hint";
        hint.textContent = h.it.hint;
        li.appendChild(hint);
      }
      li.addEventListener("mousemove", function () { if (active !== n) { active = n; render(); } });
      li.addEventListener("click", function () { go(h.it); });
      list.appendChild(li);
    });
  }

  function openDialog(id) {
    var box = document.getElementById(id);
    if (!box) return;
    box.hidden = false;
    if (M && motion() !== "off") {
      M.animate(box.querySelector(".palette-box"),
        { opacity: [0, 1], transform: ["translateY(-8px) scale(.98)", "translateY(0px) scale(1)"] },
        { duration: 0.18, ease: EASE });
    }
  }

  function openPalette() {
    var box = document.getElementById("palette");
    if (!box) return;
    openDialog("palette");
    var input = box.querySelector(".palette-input");
    input.value = "";
    active = 0;
    input.focus();
    render();
    if (!index) {
      fetch("/palette.json").then(function (r) { return r.json(); })
        .then(function (data) { index = data; render(); });
    }
  }

  function closeDialogs() {
    var open = false;
    each(document.querySelectorAll(".palette:not([hidden])"), function (b) { b.hidden = true; open = true; });
    return open;
  }

  function go(item) {
    closeDialogs();
    if (item.download) { location.href = item.href; return; }
    if (window.htmx && item.href.indexOf("#") === -1) {
      htmx.ajax("GET", item.href, { target: "body", swap: "innerHTML" }).then(function () {
        history.pushState({}, "", item.href);
        boot(document, true);
      });
    } else {
      location.href = item.href;
    }
  }

  document.addEventListener("input", function (e) {
    if (e.target.classList && e.target.classList.contains("palette-input")) { active = 0; render(); }
  });
  document.addEventListener("click", function (e) {
    if (e.target.classList && e.target.classList.contains("palette")) closeDialogs();
  });

  /* ── keyboard shortcuts ──────────────────────────────────── */

  var GO = { o: "/", p: "/providers", m: "/models", b: "/buckets", r: "/requests",
             a: "/allowance", s: "/settings", l: "/playground" };
  var pendingG = 0;

  function typing(el) {
    return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" ||
                  el.tagName === "SELECT" || el.isContentEditable);
  }

  document.addEventListener("keydown", function (e) {
    var paletteOpen = !document.getElementById("palette") ? false : !document.getElementById("palette").hidden;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (paletteOpen) closeDialogs(); else openPalette();
      return;
    }
    if (paletteOpen) {
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, hits.length - 1); render(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
      else if (e.key === "Enter" && hits[active]) { e.preventDefault(); go(hits[active].it); }
      else if (e.key === "Escape") { e.preventDefault(); closeDialogs(); }
      return;
    }
    if (e.key === "Escape" && closeDialogs()) { e.preventDefault(); return; }
    if (typing(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "?") { e.preventDefault(); openDialog("shortcuts"); return; }
    if (e.key === "/") {
      var s = document.querySelector("[data-page-search]");
      if (s) { e.preventDefault(); s.focus(); }
      return;
    }
    var k = e.key.toLowerCase();
    if (k === "g") { pendingG = Date.now(); return; }
    if (pendingG && Date.now() - pendingG < 1200 && GO[k]) {
      pendingG = 0;
      go({ href: GO[k] });
    }
  });

  /* ── the side panel ──────────────────────────────────────── */

  function sheetOpened(root) {
    var sheet = root.querySelector(".sheet");
    if (!sheet) return;
    var close = sheet.querySelector(".sheet-close");
    if (close) close.focus({ preventScroll: true });
    if (!M || motion() === "off") return;
    M.animate(sheet, { transform: ["translateX(40px)", "none"], opacity: [0, 1] },
      { type: "spring", stiffness: 420, damping: 38 });
    var back = root.querySelector(".sheet-backdrop");
    if (back) M.animate(back, { opacity: [0, 1] }, { duration: 0.2 });
    if (motion() === "full") {
      M.animate(sheet.querySelectorAll(".jstep"),
        { opacity: [0, 1], transform: ["translateY(6px)", "none"] },
        { duration: 0.22, delay: M.stagger(0.06, { startDelay: 0.12 }), ease: EASE });
    }
  }

  /* Closing is a real link (it works without JavaScript); with it, the
     panel slides away and the address goes back without a reload. */
  function closeSheet(href) {
    var root = document.getElementById("sheet-root");
    if (!root || !root.firstChild) return;
    if (href) history.pushState({}, "", href);
    var sheet = root.querySelector(".sheet"), back = root.querySelector(".sheet-backdrop");
    if (!M || motion() === "off" || !sheet) { root.innerHTML = ""; return; }
    M.animate(sheet, { transform: "translateX(40px)", opacity: 0 }, { duration: 0.18 });
    (back ? M.animate(back, { opacity: 0 }, { duration: 0.18 }).finished : Promise.resolve())
      .then(function () { root.innerHTML = ""; });
  }

  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("[data-sheet-close]");
    if (!a) return;
    e.preventDefault();
    closeSheet(a.getAttribute("href"));
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var close = document.querySelector("#sheet-root [data-sheet-close]");
    if (close) { e.preventDefault(); closeSheet(close.getAttribute("href")); }
  });
  document.addEventListener("htmx:afterSwap", function (e) {
    if (e.detail.target && e.detail.target.id === "sheet-root") sheetOpened(e.detail.target);
  });

  /* A log row opens its panel from anywhere on the row, not only the link. */
  document.addEventListener("click", function (e) {
    var row = e.target.closest && e.target.closest("tr.req-row");
    if (!row || e.target.closest("a, button, input, select")) return;
    var link = row.querySelector(".row-link");
    if (link) link.click();
  });

  /* ── tooltips ────────────────────────────────────────────── */
  /* title="" would show the OS bubble. Move the text into data-tip on the
     way in, show our own box, and never put the title back. */

  var tip = null;
  function showTip(el) {
    var text = el.getAttribute("data-tip");
    if (el.hasAttribute("title")) {
      text = el.getAttribute("title");
      el.setAttribute("data-tip", text);
      if (!el.hasAttribute("aria-label") && !el.textContent.trim()) el.setAttribute("aria-label", text);
      el.removeAttribute("title");
    }
    if (!text) return;
    if (!tip) { tip = document.createElement("div"); tip.className = "tip"; tip.setAttribute("role", "tooltip"); document.body.appendChild(tip); }
    tip.textContent = text;
    tip.hidden = false;
    var r = el.getBoundingClientRect(), t = tip.getBoundingClientRect();
    var left = Math.max(8, Math.min(r.left + r.width / 2 - t.width / 2, innerWidth - t.width - 8));
    var top = r.top - t.height - 8;
    if (top < 8) top = r.bottom + 8;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  function hideTip() { if (tip) tip.hidden = true; }
  function tipTarget(e) { return e.target.closest && e.target.closest("[title], [data-tip]"); }
  document.addEventListener("mouseover", function (e) { var el = tipTarget(e); if (el) showTip(el); });
  document.addEventListener("mouseout", function (e) { if (tipTarget(e)) hideTip(); });
  document.addEventListener("focusin", function (e) { var el = tipTarget(e); if (el) showTip(el); });
  document.addEventListener("focusout", hideTip);
  document.addEventListener("scroll", hideTip, true);

  /* ── confirm ─────────────────────────────────────────────── */
  /* form[data-confirm] asks first in a themed box, not window.confirm(). */

  document.addEventListener("submit", function (e) {
    var form = e.target;
    var question = form.getAttribute && form.getAttribute("data-confirm");
    if (!question || form.__confirmed) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    var box = document.getElementById("confirm");
    if (!box) {
      box = document.createElement("div");
      box.className = "palette";
      box.id = "confirm";
      box.setAttribute("role", "alertdialog");
      box.setAttribute("aria-modal", "true");
      box.innerHTML = '<div class="palette-box confirm-box"><p></p><div class="confirm-actions">'
        + '<button type="button" data-no>Cancel</button>'
        + '<button type="button" class="danger" data-yes>Yes, do it</button></div></div>';
      box.addEventListener("click", function (ev) { if (ev.target === box || ev.target.closest("[data-no]")) closeDialogs(); });
      document.body.appendChild(box);
    }
    box.querySelector("p").textContent = question;
    box.querySelector("[data-yes]").onclick = function () {
      closeDialogs();
      form.__confirmed = true;
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
      form.__confirmed = false;
    };
    openDialog("confirm");
    box.querySelector("[data-no]").focus();
  }, true);

  /* ── boot ────────────────────────────────────────────────── */

  function boot(root, navigated) {
    each(document.querySelectorAll(".toast-seed"), function (s) {
      toast(s.textContent, s.getAttribute("data-kind"));
      s.remove();
    });
    var mk = document.querySelector(".nav-marker");
    if (navigated && mk && lastMarker) {
      mk.style.transition = "none";
      mk.style.transform = lastMarker;
      void mk.offsetWidth;
      mk.style.transition = "";
      requestAnimationFrame(function () { placeMarker(false); });
    } else {
      placeMarker(true);
    }
    enter(root);
    watchToc();
    bootPlayground();
    var sr = document.getElementById("sheet-root");
    if (sr && sr.firstChild) sheetOpened(sr);
  }

  function boosted(detail) {
    return !!(detail && (detail.boosted || (detail.requestConfig && detail.requestConfig.boosted)));
  }
  document.addEventListener("htmx:afterSettle", function (e) {
    if (boosted(e.detail)) boot(document, true);   /* a page navigation, not a poll */
  });

  if (window.htmx) {
    htmx.config.globalViewTransitions = false;
    htmx.config.scrollIntoViewOnBoost = false;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { boot(document, false); });
  } else {
    boot(document, false);
  }
})();
