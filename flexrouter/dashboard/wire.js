/* Live update for the Overview, and nothing else.

   Ruling R3 says no JavaScript for anything load-bearing, and this keeps
   to that: the page is already complete and correct when it arrives, every
   control on it is a real link or a real form, and the time range lives in
   the URL. If this file fails to load, is blocked, or throws, the Overview
   still works - it just stops updating itself.

   What it does: re-fetch the same URL with `fragment=1`, which returns the
   inside of `#ov` built by the same server code that built the page, and
   swap it in. There is no second rendering path and no JSON contract, so
   the live view cannot disagree with the served one.

   No framework, no build step, no dependency. */

(function () {
  "use strict";

  var root = document.getElementById("ov");
  if (!root || !window.fetch) return;

  var seconds = parseInt(root.getAttribute("data-poll"), 10);
  if (!seconds || seconds < 2) seconds = 10;

  var timer = null;
  var inFlight = false;
  var failures = 0;

  /* What the reader is in the middle of doing, so a swap does not
     interrupt it. Open disclosures are keyed by their summary text rather
     than by index: the panels can reorder between polls (providers are
     sorted by traffic), and an index would reopen the wrong one. */
  function openDetails() {
    var open = [];
    root.querySelectorAll("details[open] > summary").forEach(function (s) {
      open.push(s.textContent);
    });
    return open;
  }

  function restoreDetails(open) {
    if (!open.length) return;
    root.querySelectorAll("details > summary").forEach(function (s) {
      if (open.indexOf(s.textContent) !== -1) s.parentNode.open = true;
    });
  }

  function url() {
    var range = root.getAttribute("data-range") || "24h";
    return "/?range=" + encodeURIComponent(range) + "&fragment=1";
  }

  function tick() {
    /* A hidden tab is not being read. Polling it burns the machine's
       battery and the provider's log for nobody. */
    if (inFlight || document.hidden) return schedule();
    inFlight = true;

    fetch(url(), { headers: { "X-Requested-With": "fragment" } })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.text();
      })
      .then(function (html) {
        /* Never swap while the reader is typing or has focus inside the
           block - replacing the DOM under a focused element loses both the
           caret and the keyboard. */
        if (root.contains(document.activeElement) &&
            document.activeElement !== document.body) {
          return;
        }
        var open = openDetails();
        root.innerHTML = html;
        restoreDetails(open);
        failures = 0;
        root.removeAttribute("data-stale");
      })
      .catch(function () {
        /* The service may simply have been restarted. Back off rather than
           hammering it, and after a few misses say so instead of quietly
           showing figures that are no longer true. */
        failures += 1;
        if (failures >= 3) root.setAttribute("data-stale", "");
      })
      .then(function () {
        inFlight = false;
        schedule();
      });
  }

  function schedule() {
    clearTimeout(timer);
    var wait = seconds * 1000 * (failures ? Math.min(failures, 6) : 1);
    timer = setTimeout(tick, wait);
  }

  document.addEventListener("visibilitychange", function () {
    /* Coming back to the tab should show something current immediately,
       not after waiting out the rest of an interval. */
    if (!document.hidden) {
      clearTimeout(timer);
      timer = setTimeout(tick, 150);
    }
  });

  schedule();
})();
