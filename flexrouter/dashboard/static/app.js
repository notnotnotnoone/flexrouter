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
      M.animate(on, { opacity: [0, 1], transform: ["scaleY(.3)", "none"] },
        { duration: 0.14, delay: M.stagger(0.02, { startDelay: 0.18 + i * 0.04 }), ease: EASE });
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
