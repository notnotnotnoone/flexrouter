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

  /* ── the showcase's parts (.scratch/polish/showcase/port.js) ─ */
  /* Movement here uses the browser's own element.animate(), so it runs
     with or without Motion. */

  var MOVE = "cubic-bezier(.25, 1, .5, 1)";
  var ENTER = "cubic-bezier(.22, 1, .36, 1)";
  var flex = window.flex = window.flex || {};

  function restart(el, cls) {
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
  }

  function shake(el) {
    if (motion() !== "full") return;
    restart(el, "is-shake");
    setTimeout(function () { el.classList.remove("is-shake"); }, 340);
  }

  /* Move the other rows into their new places after `change` adds or
     removes one (FLIP): they slide, they don't jump. */
  function flip(container, change) {
    var rows = container.querySelectorAll("[data-row]"), was = new Map();
    each(rows, function (r) { was.set(r, r.getBoundingClientRect().top); });
    change();
    if (motion() !== "full") return;
    was.forEach(function (top, r) {
      if (!r.isConnected) return;
      var dy = top - r.getBoundingClientRect().top;
      if (dy) r.animate([{ transform: "translateY(" + dy + "px)" }, { transform: "none" }],
        { duration: 240, easing: MOVE });
    });
  }
  flex.flip = flip;

  /* ── toasts ──────────────────────────────────────────────── */
  /* flex.toast(message, kind, {action: "Undo", onAction: fn, ms}) - kind
     is ok | warn | bad. The thin bar shows how long it stays; hovering or
     focusing it holds it there. At most three at a time. */

  function toast(message, kind, opts) {
    opts = opts || {};
    var box = document.getElementById("toasts");
    if (!box || !message) return null;
    kind = kind || "ok";
    var ms = opts.ms || (opts.action ? 6000 : kind === "bad" ? 7000 : 4200);
    var t = document.createElement("div");
    t.className = "toast " + kind;
    t.setAttribute("role", kind === "bad" ? "alert" : "status");
    var msg = document.createElement("span");
    msg.className = "toast-msg";
    msg.textContent = message;
    t.appendChild(msg);
    if (opts.action) {
      var act = document.createElement("button");
      act.type = "button";
      act.className = "toast-act";
      act.textContent = opts.action;
      act.addEventListener("click", function () { close(); if (opts.onAction) opts.onAction(); });
      t.appendChild(act);
    }
    var x = document.createElement("button");
    x.type = "button";
    x.className = "toast-x";
    x.setAttribute("aria-label", "Dismiss");
    x.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-x"/></svg>';
    x.addEventListener("click", function () { close(); });
    t.appendChild(x);
    var bar = document.createElement("i");
    bar.className = "toast-timer";
    t.appendChild(bar);
    box.appendChild(t);

    var m = motion();
    if (m !== "off") {
      t.animate(m === "full"
        ? [{ opacity: 0, transform: "translateY(10px) scale(.97)" }, { opacity: 1, transform: "none" }]
        : [{ opacity: 0 }, { opacity: 1 }], { duration: 240, easing: ENTER });
    }
    var timerAnim = m !== "off"
      ? bar.animate([{ transform: "scaleX(1)" }, { transform: "scaleX(0)" }], { duration: ms, easing: "linear", fill: "forwards" })
      : null;
    if (!timerAnim) bar.style.display = "none";

    var left = ms, started = Date.now(), timer = setTimeout(close, ms), closed = false;
    function hold() {
      if (closed || !timer) return;
      clearTimeout(timer);
      timer = null;
      left -= Date.now() - started;
      if (timerAnim) timerAnim.pause();
    }
    function resume() {
      if (closed || timer) return;
      started = Date.now();
      timer = setTimeout(close, Math.max(left, 800));
      if (timerAnim) timerAnim.play();
    }
    t.addEventListener("mouseenter", hold);
    t.addEventListener("mouseleave", resume);
    t.addEventListener("focusin", hold);
    t.addEventListener("focusout", resume);

    function close() {
      if (closed) return;
      closed = true;
      clearTimeout(timer);
      if (motion() === "off") { t.remove(); return; }
      t.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateX(16px)" }],
        { duration: 160, easing: ENTER, fill: "forwards" }).finished.then(function () { t.remove(); });
    }

    var live = box.querySelectorAll(".toast");
    if (live.length > 3 && live[0].__close) live[0].__close();
    t.__close = close;
    return { close: close };
  }
  flex.toast = toast;

  document.addEventListener("toast", function (e) {
    var d = e.detail || {};
    toast(d.message, d.kind);
  });

  /* ── buttons: working → done / failed ────────────────────── */
  /* Labels come from data-working / data-done / data-failed on the button;
     opts.label overrides one call. The reason for a failure goes in a
     .btn-note right after the button. */

  var GLYPH = { working: "loader", done: "check", failed: "x" };

  function labelEl(btn) { return btn.querySelector(":scope > span:not(.btn-note)"); }

  function glyphEl(btn) {
    var g = btn.querySelector(":scope > svg.icon");
    if (!g) {
      g = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      g.setAttribute("class", "icon btn-glyph is-added");
      g.setAttribute("aria-hidden", "true");
      g.innerHTML = '<use href=""/>';
      btn.insertBefore(g, btn.firstChild);
    }
    g.classList.add("btn-glyph");
    return g;
  }

  function noteEl(btn, make) {
    var n = btn.nextElementSibling;
    if (n && n.classList.contains("btn-note")) return n;
    if (!make) return null;
    n = document.createElement("span");
    n.className = "btn-note";
    n.setAttribute("role", "status");
    btn.insertAdjacentElement("afterend", n);
    return n;
  }

  function press(btn, state, opts) {
    opts = opts || {};
    var label = labelEl(btn);
    if (!btn.hasAttribute("data-label") && label) btn.setAttribute("data-label", label.textContent);
    clearTimeout(btn.__hold);

    if (state === "idle") {
      btn.removeAttribute("data-state");
      btn.removeAttribute("aria-busy");
      btn.style.minWidth = "";
      if (label) label.textContent = btn.getAttribute("data-label");
      var g = btn.querySelector(":scope > svg.btn-glyph");
      if (g && g.classList.contains("is-added")) g.remove();
      else if (g && btn.__icon) g.querySelector("use").setAttribute("href", btn.__icon);
      if (opts.clearNote !== false) { var n0 = noteEl(btn); if (n0) n0.textContent = ""; }
      return;
    }

    if (!btn.getAttribute("data-state")) btn.style.minWidth = btn.offsetWidth + "px";
    var glyph = glyphEl(btn), use = glyph.querySelector("use");
    if (!btn.__icon) btn.__icon = use.getAttribute("href");
    use.setAttribute("href", "#i-" + GLYPH[state]);
    btn.setAttribute("data-state", state);
    if (label) {
      label.textContent = opts.label || btn.getAttribute("data-" + state) ||
        (state === "done" ? "Done" : btn.getAttribute("data-label"));
    }

    var note = noteEl(btn, !!opts.note);
    if (state === "working") {
      btn.setAttribute("aria-busy", "true");
      if (note && !opts.note) note.textContent = "";
    } else {
      btn.removeAttribute("aria-busy");
    }
    if (opts.note && note) {
      note.textContent = opts.note;
      note.classList.toggle("ok", state === "done");
      note.classList.toggle("wait", state === "working");
      if (motion() === "full") restart(note, "is-in");
    }
    if (state === "failed") shake(btn);
    if (state === "done" && !opts.stay) {
      btn.__hold = setTimeout(function () { press(btn, "idle", { clearNote: false }); }, opts.hold || 1600);
    }
  }
  flex.press = press;

  /* Run the server work behind a button. `work` returns a promise that
     resolves to {label, note} (both optional) or rejects with an Error
     whose message is the reason, in plain words. */
  flex.run = function (btn, work, opts) {
    opts = opts || {};
    if (btn.getAttribute("data-state") === "working") return Promise.resolve(null);
    press(btn, "working", { label: opts.working });
    return Promise.resolve().then(work).then(function (r) {
      r = r || {};
      press(btn, "done", { label: r.label, note: r.note, stay: opts.stay, hold: opts.hold });
      return r;
    }, function (err) {
      press(btn, "failed", { label: opts.failed, note: err && err.message });
      return null;
    });
  };

  /* ── writes: every form that POSTs ───────────────────────── */
  /* Forms are boosted by htmx. While the server works, the button says so
     (a form's data-busy sentence goes beside it). A write that failed -
     the redirect carries ok=0 and the reason - stays on this page: the
     button shows the reason and what was typed is kept. A write that
     worked swaps the page in as before, and its toast offers Undo when
     the server left an undo token. */

  var pendingUndo = null;

  function writeForm(detail) {
    var elt = detail && detail.requestConfig ? detail.requestConfig.elt : detail && detail.elt;
    if (!elt || elt.tagName !== "FORM" || (elt.getAttribute("method") || "").toLowerCase() !== "post") return null;
    return elt;
  }

  document.addEventListener("htmx:beforeRequest", function (e) {
    var form = writeForm(e.detail);
    if (!form) return;
    var ev = e.detail.requestConfig && e.detail.requestConfig.triggeringEvent;
    var btn = (ev && ev.submitter) || form.querySelector("button[type=submit], button:not([type])");
    form.__btn = btn || null;
    if (btn) press(btn, "working", { note: form.getAttribute("data-busy") || undefined });
  });

  function failWrite(form, reason) {
    var btn = form.__btn;
    form.__btn = null;
    if (btn) press(btn, "failed", { note: reason });
    else toast(reason, "bad");
  }

  document.addEventListener("htmx:beforeSwap", function (e) {
    var form = writeForm(e.detail);
    if (!form || !form.__btn) return;
    var xhr = e.detail.xhr, url = null;
    try { url = new URL(xhr.responseURL); } catch (err) { url = null; }
    if (xhr.status >= 400 || (url && url.searchParams.get("ok") === "0")) {
      e.detail.shouldSwap = false;
      failWrite(form, (url && url.searchParams.get("message")) ||
        "flexrouter answered " + xhr.status + ". Nothing was saved.");
      return;
    }
    pendingUndo = url && url.searchParams.get("undo");
    press(form.__btn, "done");
    form.__btn = null;
  });

  document.addEventListener("htmx:sendError", function (e) {
    var form = writeForm(e.detail);
    if (form && form.__btn) failWrite(form, "Couldn't reach flexrouter. Is it still running?");
  });

  function offerUndo(token) {
    return {
      action: "Undo",
      onAction: function () {
        fetch("/undo/" + encodeURIComponent(token), { method: "POST" })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (!d.ok) { toast(d.message || "Too late to undo that", "bad"); return; }
            go({ href: location.pathname, toast: d.message || "Put back" });
          }, function () { toast("Couldn't reach flexrouter to undo", "bad"); });
      }
    };
  }

  /* ── toggle ──────────────────────────────────────────────── */
  /* Flips at once. Whoever saves it listens for "flex:switch" and calls
     detail.done(true) or detail.done(false, reason); a failure flips it
     back. Nobody listening means it just flips. "Working" only shows if
     the save takes longer than 150 ms, so fast saves never flicker. A
     switch with data-post saves itself: it POSTs value=true|false there. */

  document.addEventListener("click", function (e) {
    var sw = e.target.closest && e.target.closest(".switch");
    if (!sw || sw.disabled || sw.getAttribute("data-state") === "working") return;
    var on = sw.getAttribute("aria-checked") !== "true";
    sw.setAttribute("aria-checked", on ? "true" : "false");
    sw.removeAttribute("data-state");
    var slow = setTimeout(function () { sw.setAttribute("data-state", "working"); }, 150);
    var settled = false;
    function done(ok, reason) {
      if (settled) return;
      settled = true;
      clearTimeout(slow);
      sw.removeAttribute("data-state");
      if (ok) return;
      sw.setAttribute("aria-checked", on ? "false" : "true");
      sw.setAttribute("data-state", "failed");
      shake(sw);
      toast(reason || "Not saved", "bad");
      setTimeout(function () { sw.removeAttribute("data-state"); }, 1600);
    }
    var ev = new CustomEvent("flex:switch", { bubbles: true, cancelable: true, detail: { on: on, done: done } });
    sw.dispatchEvent(ev);
    if (!ev.defaultPrevented) done(true);
  });

  document.addEventListener("flex:switch", function (e) {
    var sw = e.target, url = sw.getAttribute && sw.getAttribute("data-post");
    if (!url) return;
    e.preventDefault();
    var body = new URLSearchParams();
    body.set("value", e.detail.on ? "true" : "false");
    fetch(url, { method: "POST", body: body }).then(function (r) {
      var u = null;
      try { u = new URL(r.url); } catch (err) { u = null; }
      if (!r.ok || (u && u.searchParams.get("ok") === "0")) {
        e.detail.done(false, (u && u.searchParams.get("message")) || "Not saved");
      } else {
        e.detail.done(true);
      }
    }, function () { e.detail.done(false, "Couldn't reach flexrouter. Not saved."); });
  });

  /* ── copy ────────────────────────────────────────────────── */
  /* data-copy="#selector" copies that element's text; data-copy-text
     copies the attribute itself. The button says "Copied" in place. */

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (ok, fail) {
      var t = document.createElement("textarea");
      t.value = text;
      t.setAttribute("readonly", "");
      t.style.position = "fixed";
      t.style.opacity = "0";
      document.body.appendChild(t);
      t.select();
      var worked = false;
      try { worked = document.execCommand("copy"); } catch (err) { worked = false; }
      t.remove();
      if (worked) ok(); else fail(new Error("The browser blocked copying. Select the text instead."));
    });
  }
  flex.copyText = copyText;

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-copy], [data-copy-text]");
    if (!btn || btn.disabled) return;
    var text = btn.getAttribute("data-copy-text");
    if (text === null) {
      var src = document.querySelector(btn.getAttribute("data-copy"));
      text = src ? src.textContent.trim() : "";
    }
    copyText(text).then(function () {
      if (btn.classList.contains("btn")) press(btn, "done", { label: btn.getAttribute("data-done") || "Copied", hold: 1400 });
      else toast("Copied", "ok");
    }, function (err) {
      if (btn.classList.contains("btn")) press(btn, "failed", { note: err.message });
      else toast(err.message, "bad");
    });
  });

  /* ── statuses ────────────────────────────────────────────── */

  var GLYPHS = { ready: "●", busy: "◐", struggling: "◆", needs: "▲", off: "○" };
  var WORDS = { ready: "Ready", busy: "Busy", struggling: "Struggling", needs: "Needs you", off: "Off" };
  flex.GLYPHS = GLYPHS;

  /* Turn a .pill (or a .st-row's dot) into another status, with a pop. */
  function setStatus(el, status) {
    el.setAttribute("data-status", status);
    var dot = el.querySelector(":scope > .pill-dot");
    if (dot) dot.textContent = GLYPHS[status];
    var word = el.querySelector(":scope > .pill-word");
    if (word) word.textContent = WORDS[status];
    if (status !== "busy") {
      var cd = el.querySelector(":scope > .countdown");
      if (cd) cd.remove();
    }
    if (el.classList.contains("pill") && motion() === "full") restart(el, "is-pop");
  }
  flex.setStatus = setStatus;

  /* ── countdowns ──────────────────────────────────────────── */
  /* Server renders "Resets in 2h 13m" with the target time attached; this
     keeps it true between refreshes. At zero a Busy pill turns Ready by
     itself and tells the page ("flex:back"); anything else says so and
     stops. */

  function duration(s) {
    var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600),
        m = Math.floor(s % 3600 / 60), sec = s % 60;
    if (d) return d + "d " + h + "h";
    if (h) return h + "h " + m + "m";
    if (m) return m + "m " + (sec < 10 ? "0" : "") + sec + "s";
    return sec + "s";
  }
  flex.duration = duration;

  function tick() {
    var now = Date.now() / 1000;
    each(document.querySelectorAll("[data-countdown]"), function (el) {
      var left = Math.max(0, Math.round(parseFloat(el.getAttribute("data-countdown")) - now));
      if (left > 0) {
        el.textContent = (el.getAttribute("data-prefix") || "Resets in") + " " + duration(left);
        return;
      }
      if (el.classList.contains("is-done")) return;
      el.classList.add("is-done");
      var pill = el.closest(".pill[data-status='busy'], .st-row[data-status='busy']");
      if (pill) {
        pill.dispatchEvent(new CustomEvent("flex:back", { bubbles: true }));
        if (pill.classList.contains("pill")) setStatus(pill, "ready");
        return;
      }
      el.textContent = "Back now";
      el.classList.add("status-ok");
    });
  }
  setInterval(tick, 1000);
  flex.tick = tick;

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
    if (!boosted(e.detail) || !e.detail.shouldSwap) return;
    html.classList.remove("entered");   /* the next page gets its own entrance */
    var mk = document.querySelector(".nav-marker");
    lastMarker = mk ? mk.style.transform : null;
  });

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

  /* ── Add models with AI: real IDs only (§6, §18) ─────────── */
  /* Every checked row's model must be on its provider's real list (sent as
     #known-ids) before Apply works; [use] swaps in a suggested ID. A
     provider missing from the list was never checked and gets the benefit
     of the doubt, same as the server. */

  function checkIds(form) {
    var known = {};
    try { known = JSON.parse((document.getElementById("known-ids") || {}).textContent || "{}"); }
    catch (e) { /* no list: nothing to check */ }
    var bad = 0;
    form.querySelectorAll("[data-bad-row]").forEach(function (note) {
      var i = note.getAttribute("data-bad-row");
      var model = form.querySelector('[name="model:' + i + '"]');
      var provider = form.querySelector('[name="provider:' + i + '"]');
      var apply = form.querySelector('[name="apply:' + i + '"]');
      var ids = known[provider && provider.value.trim()];
      var ok = !ids || ids.indexOf(model.value.trim()) !== -1;
      note.hidden = ok;
      if (!ok && apply && apply.checked) bad++;
    });
    var btn = document.getElementById("apply-rows"), why = document.getElementById("apply-block");
    if (btn) btn.disabled = bad > 0;
    if (why) why.textContent = bad ? " Fix " + bad + " ID" + (bad === 1 ? "" : "s") + " first, or uncheck " + (bad === 1 ? "it" : "them") + "." : "";
  }

  document.addEventListener("click", function (e) {
    var use = e.target.closest && e.target.closest("[data-use-id]");
    if (!use) return;
    var form = use.closest("form");
    var model = form.querySelector('[name="model:' + use.getAttribute("data-row") + '"]');
    if (model) model.value = use.getAttribute("data-use-id");
    checkIds(form);
  });
  document.addEventListener("input", function (e) {
    var form = e.target.closest && e.target.closest("form[data-id-check]");
    if (form) checkIds(form);
  });
  document.addEventListener("change", function (e) {
    var form = e.target.closest && e.target.closest("form[data-id-check]");
    if (form) checkIds(form);
  });
  function bootIdCheck() {
    document.querySelectorAll("form[data-id-check]").forEach(checkIds);
  }

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

  /* Markdown for replies (§7, US-45): a small, safe subset. Everything is
     escaped first, so a model can only ever produce the tags made here. */
  function md(src) {
    var esc = function (t) { return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); };
    var inline = function (t) {
      return t.replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    };
    var out = [], list = null, para = [];
    var flushPara = function () { if (para.length) { out.push("<p>" + inline(para.join("<br>")) + "</p>"); para = []; } };
    var flushList = function () { if (list) { out.push("</" + list + ">"); list = null; } };
    var blocks = src.split(/```/);
    blocks.forEach(function (block, i) {
      if (i % 2) { flushPara(); flushList(); out.push("<pre><code>" + esc(block.replace(/^[\w-]*\n/, "")) + "</code></pre>"); return; }
      esc(block).split("\n").forEach(function (line) {
        var h = /^(#{1,4})\s+(.*)$/.exec(line), ul = /^\s*[-*]\s+(.*)$/.exec(line), ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
        if (h) { flushPara(); flushList(); out.push("<h" + (h[1].length + 2) + ">" + inline(h[2]) + "</h" + (h[1].length + 2) + ">"); }
        else if (ul || ol) {
          flushPara();
          var kind = ul ? "ul" : "ol";
          if (list !== kind) { flushList(); out.push("<" + kind + ">"); list = kind; }
          out.push("<li>" + inline((ul || ol)[1]) + "</li>");
        }
        else if (!line.trim()) { flushPara(); flushList(); }
        else { flushList(); para.push(line); }
      });
      flushPara(); flushList();
    });
    return out.join("");
  }

  /* The collapsible Thinking section above a reply, made on the first
     reasoning chunk (§7). */
  function thinking(body) {
    var fold = body.parentNode.querySelector(".fold");
    if (!fold) {
      fold = document.createElement("details");
      fold.className = "fold";
      fold.innerHTML = '<summary>Thinking <span class="n"></span></summary><div class="fold-body rq-think"></div>';
      body.parentNode.insertBefore(fold, body);
    }
    return fold;
  }

  function bubble(role, text) {
    var log = document.getElementById("pg-log");
    var empty = log.querySelector(".empty");
    if (empty) empty.remove();
    var b = document.createElement("div");
    b.className = "pg-msg pg-" + role;
    var who = document.createElement("span");
    who.className = "pg-who";
    who.textContent = role === "user" ? "you" : "waiting for a model";
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
    s.href = "/requests/" + encodeURIComponent(info.id);
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
    var who = target.parentNode.querySelector(".pg-who");
    if (who && !target.closest(".pg-compare-card"))
      who.textContent = info.answered_by || "nothing answered";
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
    var answer = "", reasoning = "", cards = null;
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
            var think = (c && c.delta && c.delta.reasoning_content) || "";
            if (card) {
              card.text += delta;
              card.body.classList.remove("shimmer");
              card.body.innerHTML = md(card.text);
            } else if (out) {
              // The reply is labelled with the model answering it, never
              // "flexrouter" (US-44): every chunk names it (ADR 0018).
              if (payload.flexrouter && payload.flexrouter.model)
                out.parentNode.querySelector(".pg-who").textContent = payload.flexrouter.model;
              if (think) {
                reasoning += think;
                var fold = thinking(out);
                fold.querySelector(".rq-think").textContent = reasoning;
                fold.querySelector(".n").textContent = reasoning.length.toLocaleString() + " characters";
              }
              answer += delta;
              if (answer) { out.classList.remove("shimmer"); out.innerHTML = md(answer); }
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

  /* ── drag a model onto a bucket ──────────────────────────── */
  /* Pointer events, so it works with a finger too. Three ways in, one
     outcome:
       drag it          - the card follows the pointer; drop on a bucket
       tap, then tap    - tap a card to pick it up, tap a bucket to drop
       keyboard         - Enter on a card, Tab to a bucket, Enter
     Dropping fires "flex:drop" on the bucket; the listener below does the
     POST and calls detail.done(true) or detail.done(false, reason). */

  var drag = null, picked = null, justDragged = false;

  function zones() { return document.querySelectorAll("[data-dropzone]"); }

  function pick(card) {
    unpick();
    picked = card;
    card.setAttribute("aria-pressed", "true");
    each(zones(), function (z) { z.classList.add("drop-ready"); z.setAttribute("tabindex", "0"); });
  }
  function unpick() {
    if (picked) picked.setAttribute("aria-pressed", "false");
    picked = null;
    each(zones(), function (z) { z.classList.remove("drop-ready", "drag-over"); z.removeAttribute("tabindex"); });
  }

  function dropOn(zone, card) {
    var bucket = zone.getAttribute("data-dropzone");
    var id = card.getAttribute("data-provider") + "/" + card.getAttribute("data-model");
    if (zone.querySelector('[data-row="' + id + '"]')) {
      shake(zone);
      toast(id + " is already in " + bucket, "warn");
      return;
    }
    zone.dispatchEvent(new CustomEvent("flex:drop", { bubbles: true, detail: {
      bucket: bucket, id: id, card: card,
      done: function (ok, reason) {
        if (ok) return;
        shake(zone);
        toast("Couldn't add " + id + " to " + bucket + ": " + (reason || "the server said no"), "bad");
      }
    } }));
  }

  document.addEventListener("pointerdown", function (e) {
    var card = e.target.closest && e.target.closest(".model-card");
    if (!card || e.button !== 0) return;
    drag = { card: card, x: e.clientX, y: e.clientY, id: e.pointerId, ghost: null, zone: null };
  });

  document.addEventListener("pointermove", function (e) {
    if (!drag || e.pointerId !== drag.id) return;
    var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!drag.ghost) {
      if (Math.abs(dx) + Math.abs(dy) < 6) return;
      unpick();
      var r = drag.card.getBoundingClientRect(), g = drag.card.cloneNode(true);
      g.classList.add("drag-ghost");
      g.removeAttribute("tabindex");
      g.setAttribute("aria-hidden", "true");
      g.style.left = r.left + "px";
      g.style.top = r.top + "px";
      g.style.width = r.width + "px";
      document.body.appendChild(g);
      drag.ghost = g;
      drag.card.classList.add("is-dragging");
      each(zones(), function (z) { z.classList.add("drop-ready"); });
    }
    var tilt = motion() === "full" ? " rotate(-1.5deg) scale(1.03)" : "";
    drag.ghost.style.transform = "translate(" + dx + "px, " + dy + "px)" + tilt;
    var under = document.elementFromPoint(e.clientX, e.clientY);
    var zone = under && under.closest("[data-dropzone]");
    if (zone !== drag.zone) {
      if (drag.zone) drag.zone.classList.remove("drag-over");
      if (zone) zone.classList.add("drag-over");
      drag.zone = zone;
    }
  });

  function endDrag(e, cancelled) {
    if (!drag || e.pointerId !== drag.id) return;
    var d = drag;
    drag = null;
    if (!d.ghost) return;             /* a tap: the click handler picks it up */
    justDragged = true;
    setTimeout(function () { justDragged = false; }, 0);
    d.card.classList.remove("is-dragging");
    each(zones(), function (z) { z.classList.remove("drop-ready", "drag-over"); });
    var m = motion(), g = d.ghost;
    if (d.zone && !cancelled) {
      dropOn(d.zone, d.card);
      if (m === "off") g.remove();
      else g.animate([{ opacity: 1 }, { opacity: 0, transform: g.style.transform + " scale(.9)" }],
        { duration: 140, easing: ENTER, fill: "forwards" }).finished.then(function () { g.remove(); });
    } else if (m === "off") {
      g.remove();
    } else {
      g.animate([{ transform: g.style.transform }, { transform: "none" }],
        { duration: 240, easing: MOVE, fill: "forwards" }).finished.then(function () { g.remove(); });
    }
  }
  document.addEventListener("pointerup", function (e) { endDrag(e, false); });
  document.addEventListener("pointercancel", function (e) { endDrag(e, true); });

  document.addEventListener("click", function (e) {
    if (justDragged) return;
    var card = e.target.closest && e.target.closest(".model-card");
    if (card) { if (picked === card) unpick(); else pick(card); return; }
    var zone = picked && e.target.closest && e.target.closest("[data-dropzone]");
    if (zone) { var c = picked; unpick(); dropOn(zone, c); return; }
    if (picked) unpick();
  });

  document.addEventListener("keydown", function (e) {
    var card = e.target.closest && e.target.closest(".model-card");
    if (card && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      if (picked === card) { unpick(); return; }
      pick(card);
      var first = document.querySelector("[data-dropzone]");
      if (first) first.focus();
      return;
    }
    var zone = picked && e.target.closest && e.target.closest("[data-dropzone]");
    if (zone && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      var c = picked;
      unpick();
      dropOn(zone, c);
      c.focus();
      return;
    }
    if (picked && e.key === "Escape") { var back = picked; unpick(); back.focus(); }
  });

  /* The drop's server side: everything the card needs is already in its
     own data-* attributes (facts.models() carried them from the model's
     live config), so a drop replays the same POST the manual "add a
     model" form makes - nothing is looked up again, no new endpoint. */
  document.addEventListener("flex:drop", function (e) {
    var d = e.detail, card = d.card;
    var quotas = {};
    try { quotas = JSON.parse(card.getAttribute("data-quotas") || "{}"); } catch (err) { quotas = {}; }
    var body = new URLSearchParams();
    body.set("provider", card.getAttribute("data-provider"));
    body.set("model", card.getAttribute("data-model"));
    body.set("score", card.getAttribute("data-score") || "");
    body.set("rpm", card.getAttribute("data-rpm") || "");
    body.set("tpm", card.getAttribute("data-tpm") || "");
    if (card.getAttribute("data-context-window")) body.set("context_window", card.getAttribute("data-context-window"));
    if (card.getAttribute("data-tokens-per-second")) body.set("tokens_per_second", card.getAttribute("data-tokens-per-second"));
    if (card.getAttribute("data-vision") === "1") body.set("vision", "on");
    Object.keys(quotas).forEach(function (k) { body.set(k, quotas[k]); });
    fetch("/buckets/" + encodeURIComponent(d.bucket) + "/models", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString()
    }).then(function (r) {
      var u = null;
      try { u = new URL(r.url); } catch (err) { u = null; }
      if (!r.ok || (u && u.searchParams.get("ok") === "0")) {
        d.done(false, u && u.searchParams.get("message"));
        return;
      }
      d.done(true);
      go({ href: location.pathname, toast: "Added " + d.id + " to " + d.bucket });
    }, function () { d.done(false, "couldn't reach flexrouter"); });
  });

  /* ── the status list ─────────────────────────────────────── */

  /* Open a row's detail from anywhere on the row; the chevron button is
     the keyboard way in (a row can't be a button: it holds one). */
  document.addEventListener("click", function (e) {
    var row = e.target.closest && e.target.closest(".st-row");
    if (!row) return;
    var chevron = row.querySelector(".st-open");
    if (!chevron || (e.target.closest(".btn, a, input, select") && !e.target.closest(".st-open"))) return;
    var detail = document.getElementById(chevron.getAttribute("aria-controls"));
    if (!detail) return;
    var opening = detail.hidden;
    detail.hidden = !opening;
    row.classList.toggle("is-open", opening);
    chevron.setAttribute("aria-expanded", opening ? "true" : "false");
  });

  /* The summary strip filters: press "2 need you" to see only those. */
  document.addEventListener("click", function (e) {
    var chip = e.target.closest && e.target.closest(".st-count");
    if (!chip) return;
    var list = chip.closest(".st");
    var on = chip.getAttribute("aria-pressed") !== "true";
    each(list.querySelectorAll(".st-count"), function (c) { c.setAttribute("aria-pressed", "false"); });
    chip.setAttribute("aria-pressed", on ? "true" : "false");
    var want = on ? chip.getAttribute("data-status") : null;
    each(list.querySelectorAll(".st-group"), function (g) {
      /* Ready and Off stay folded away unless asked for (data-quiet). */
      g.hidden = want ? g.getAttribute("data-group") !== want : g.hasAttribute("data-quiet");
    });
  });

  function bump(list, status, by) {
    var chip = list.querySelector('.st-count[data-status="' + status + '"]');
    if (!chip) return;
    var b = chip.querySelector("b"), n = Math.max(0, parseInt(b.textContent, 10) + by);
    b.textContent = n;
    b.setAttribute("data-value", n);
    if (motion() !== "off") restart(chip, by > 0 ? "flash-up" : "flash-down");
  }

  /* Good news flies to where it's counted: a small green square goes from
     the row to the "ready" chip, which pops as the number goes up. */
  function fly(from, to, then) {
    var a = from.getBoundingClientRect(), b = to.getBoundingClientRect();
    var dot = document.createElement("span");
    dot.className = "fly-dot";
    dot.style.left = (a.left + a.width / 2 - 4) + "px";
    dot.style.top = (a.top + a.height / 2 - 4) + "px";
    document.body.appendChild(dot);
    var dx = b.left + 18 - (a.left + a.width / 2), dy = b.top + b.height / 2 - (a.top + a.height / 2);
    var anim = dot.animate([
      { transform: "translate(0, 0) scale(1)" },
      { transform: "translate(" + dx * 0.45 + "px, " + (dy * 0.45 - 48) + "px) scale(1.3)", offset: 0.5 },
      { transform: "translate(" + dx + "px, " + dy + "px) scale(.6)" }
    ], { duration: 560, easing: "cubic-bezier(.45, 0, .25, 1)" });
    var landed = false;
    function land() { if (landed) return; landed = true; dot.remove(); then(); }
    anim.finished.then(land);
    setTimeout(land, 700);   /* a paused tab never finishes animations */
  }

  /* The last problem in a group is gone: say so, and let it glow once. */
  function cleared(group) {
    var empty = group.querySelector(".st-empty");
    if (!empty) return;
    empty.hidden = false;
    if (group.getAttribute("data-group") !== "needs") return;
    empty.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-check"/></svg><span>' +
      empty.textContent + "</span>";
    restart(empty, "is-clear");
  }

  /* A row's model changed status (a fix worked, a countdown ran out): the
     glyph turns, the row leaves its group, the rows below slide up, and
     the counts follow. A live refresh does the same through morphing;
     this runs for the click that caused it. */
  flex.settle = function (row, status) {
    var list = row.closest(".st"), group = row.closest(".st-group");
    var from = row.getAttribute("data-status");
    var dot = row.querySelector(":scope > .pill-dot");
    var chip = list.querySelector('.st-count[data-status="' + status + '"]');
    var detail = document.getElementById(row.querySelector(".st-open").getAttribute("aria-controls"));
    var full = motion() === "full", good = status === "ready";
    if (full && good) {
      restart(dot, "is-morph");
      setTimeout(function () { setStatus(row, status); }, 150);
    } else {
      setStatus(row, status);
    }
    setTimeout(function () {
      bump(list, from, -1);
      if (full && good && chip) {
        fly(dot, chip, function () { bump(list, status, 1); restart(chip, "is-pop"); });
      } else {
        bump(list, status, 1);
      }
      row.classList.add("is-leaving");
      setTimeout(function () {
        flip(group, function () {
          row.remove();
          if (detail) detail.remove();
        });
        if (!group.querySelector(".st-row")) cleared(group);
      }, full ? 190 : 0);
    }, full ? 650 : 500);
  };

  /* ── test all ────────────────────────────────────────────── */
  /* flex.testAll(button, list, test) - `test(row)` returns a promise that
     resolves to a short result ("0.4s") or rejects with the reason. Two
     run at once; each row and each meter cell says how it went. */

  flex.testAll = function (btn, list, test, opts) {
    opts = opts || {};
    var rows = Array.prototype.slice.call(list.querySelectorAll(".test-row"));
    var meter = document.getElementById(btn.getAttribute("data-meter"));
    var cells = meter ? meter.querySelectorAll("i") : [];
    var total = rows.length, finished = 0, failed = 0, next = 0;
    each(rows, function (r) {
      r.setAttribute("data-state", "waiting");
      r.querySelector(".test-mark use").setAttribute("href", "#i-dot");
      r.querySelector(".test-out").textContent = "waiting";
    });
    each(cells, function (c) { c.className = ""; });
    press(btn, "working", { label: "Testing 0 of " + total });

    return new Promise(function (resolve) {
      if (!total) { press(btn, "idle"); resolve({ total: 0, failed: 0 }); return; }
      function start() {
        if (next >= total) return;
        var i = next++, r = rows[i];
        r.setAttribute("data-state", "working");
        r.querySelector(".test-mark use").setAttribute("href", "#i-loader");
        r.querySelector(".test-out").textContent = "saying hi…";
        if (cells[i]) cells[i].className = "run";
        Promise.resolve().then(function () { return test(r); }).then(function (out) {
          r.setAttribute("data-state", "done");
          r.querySelector(".test-mark use").setAttribute("href", "#i-check");
          r.querySelector(".test-out").textContent = out || "works";
          if (cells[i]) cells[i].className = "on";
        }, function (err) {
          failed += 1;
          r.setAttribute("data-state", "failed");
          r.querySelector(".test-mark use").setAttribute("href", "#i-x");
          r.querySelector(".test-out").textContent = err.message;
          if (cells[i]) cells[i].className = "bad";
        }).then(function () {
          finished += 1;
          var label = labelEl(btn);
          if (label && finished < total) label.textContent = "Testing " + finished + " of " + total;
          if (finished === total) {
            if (failed) {
              press(btn, "failed", { label: (total - failed) + " of " + total + " work",
                note: failed === 1 ? "1 didn't answer. See why below." : failed + " didn't answer. See why below." });
            } else {
              /* Everything answered: the meter lights up in a wave, then
                 the button pops its news. */
              var wave = motion() === "full" && meter;
              if (wave) {
                each(cells, function (c, n) { c.style.setProperty("--i", n); });
                restart(meter, "is-wave");
              }
              setTimeout(function () {
                press(btn, "done", { label: "All " + total + " work", stay: opts.stay });
                if (wave) restart(btn, "is-pop");
              }, wave ? cells.length * 40 + 260 : 0);
            }
            resolve({ total: total, failed: failed });
          } else {
            start();
          }
        });
      }
      start();
      start();
    });
  };

  /* ── quickstart ──────────────────────────────────────────── */
  /* A step ticks itself when the thing it asks for has happened; the
     card never writes anything (ADR 0022). */

  flex.tickStep = function (step) {
    var card = step.closest(".qs");
    if (step.getAttribute("data-step") === "done") return;
    step.setAttribute("data-step", "done");
    step.querySelector(".qs-mark").innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-check"/></svg>';
    if (motion() === "full") restart(step, "is-ticked");
    var steps = card.querySelectorAll(".qs-step"), done = card.querySelectorAll('.qs-step[data-step="done"]').length;
    var nextStep = card.querySelector('.qs-step:not([data-step="done"])');
    each(steps, function (s) {
      if (s.getAttribute("data-step") !== "done") s.setAttribute("data-step", s === nextStep ? "now" : "todo");
      var mk = s.querySelector(".qs-mark");
      if (s.getAttribute("data-step") === "now") mk.textContent = "▶";
      else if (s.getAttribute("data-step") === "todo") mk.textContent = s.getAttribute("data-n");
    });
    each(card.querySelectorAll(".qs-bar i"), function (c, i) { c.classList.toggle("on", i < done); });
    var sub = card.querySelector(".box-sub");
    if (sub) sub.textContent = done + " of " + steps.length + " done";
    if (done === steps.length) {
      var line = card.querySelector(".qs-done-line");
      if (line) line.hidden = false;
      card.classList.add("is-complete");
      /* Once, ever: a green sweep across the card and the ticks rippling
         in order. */
      if (motion() === "full") {
        each(card.querySelectorAll(".qs-mark"), function (mk, n) { mk.style.setProperty("--i", n); });
        restart(card, "is-celebrating");
        var sweep = document.createElement("span");
        sweep.className = "qs-sweep";
        sweep.setAttribute("aria-hidden", "true");
        sweep.innerHTML = "<i></i>";
        card.appendChild(sweep);
        setTimeout(function () { sweep.remove(); card.classList.remove("is-celebrating"); }, 1400);
      }
    }
  };

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
        if (item.toast) toast(item.toast, "ok");
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

  var GO = { o: "/", p: "/providers", m: "/models_catalog", b: "/buckets", r: "/requests",
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

  /* ── boot ────────────────────────────────────────────────── */

  function boot(root, navigated) {
    each(document.querySelectorAll(".toast-seed"), function (s) {
      var kind = s.getAttribute("data-kind");
      toast(s.textContent, kind, kind === "ok" && pendingUndo ? offerUndo(pendingUndo) : undefined);
      s.remove();
    });
    pendingUndo = null;
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
    bootIdCheck();
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
