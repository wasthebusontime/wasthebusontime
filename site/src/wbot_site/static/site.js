// Optional: the site works without this file.
// Remembers the timepoints / all stops choice across pages, runs the stop filter, and
// lets keyboards scroll tables that are wider than the screen.
(function () {
  var KEY = "wbot-scope";
  try {
    var saved = localStorage.getItem(KEY);
    var radio = saved && document.getElementById("scope-" + saved);
    if (radio) radio.checked = true;
  } catch (e) {}
  // The toggle also changes the numbers above it, so say so to screen readers.
  var scopeStatus = document.getElementById("scope-status");
  document.querySelectorAll('input[name="scope"]').forEach(function (r) {
    r.addEventListener("change", function () {
      try { localStorage.setItem(KEY, r.value); } catch (e) {}
      scrollable();
      if (scopeStatus) {
        var label = document.querySelector('label[for="' + r.id + '"]').textContent;
        scopeStatus.textContent = "Showing " + label.toLowerCase() + ". The numbers above and below changed.";
      }
    });
  });

  var filter = document.getElementById("stop-filter");
  if (filter) {
    var items = document.querySelectorAll("#stop-list li");
    var count = document.getElementById("stop-filter-count");
    var timer;
    filter.hidden = false;
    filter.querySelector("input").addEventListener("input", function (ev) {
      var q = ev.target.value.trim().toLowerCase();
      var shown = 0;
      items.forEach(function (li) {
        li.hidden = q !== "" && li.textContent.toLowerCase().indexOf(q) === -1;
        if (!li.hidden) shown++;
      });
      // Waits for a pause in typing, so screen readers announce one count, not every keystroke.
      clearTimeout(timer);
      timer = setTimeout(function () {
        count.textContent = q === "" ? "" : shown === 0 ? "No stops match." : shown === 1 ? "1 stop matches." : shown + " stops match.";
      }, 500);
    });
  }

  // A table wider than the screen scrolls sideways; make its box focusable, with the
  // table's caption as its name, so it can be scrolled with the arrow keys.
  var wraps = document.querySelectorAll(".table-wrap");
  function scrollable() {
    wraps.forEach(function (w) {
      var caption = w.querySelector("caption");
      if (w.scrollWidth > w.clientWidth + 1) {
        w.setAttribute("tabindex", "0");
        w.setAttribute("role", "region");
        if (caption) w.setAttribute("aria-label", caption.textContent.trim());
      } else {
        w.removeAttribute("tabindex");
        w.removeAttribute("role");
        w.removeAttribute("aria-label");
      }
    });
  }
  if (wraps.length) {
    scrollable();
    window.addEventListener("resize", scrollable);
    // Tables inside "Show table" only get a width once opened.
    document.querySelectorAll("details").forEach(function (d) { d.addEventListener("toggle", scrollable); });
  }
})();
