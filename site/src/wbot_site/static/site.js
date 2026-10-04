// Optional: the site works without this file.
// Remembers the timepoints / all stops choice across pages, and runs the stop filter.
(function () {
  var KEY = "wbot-scope";
  try {
    var saved = localStorage.getItem(KEY);
    var radio = saved && document.getElementById("scope-" + saved);
    if (radio) radio.checked = true;
    document.querySelectorAll('input[name="scope"]').forEach(function (r) {
      r.addEventListener("change", function () {
        try { localStorage.setItem(KEY, r.value); } catch (e) {}
      });
    });
  } catch (e) {}

  var filter = document.getElementById("stop-filter");
  if (filter) {
    var items = document.querySelectorAll("#stop-list li");
    filter.hidden = false;
    filter.querySelector("input").addEventListener("input", function (ev) {
      var q = ev.target.value.trim().toLowerCase();
      items.forEach(function (li) {
        li.hidden = q !== "" && li.textContent.toLowerCase().indexOf(q) === -1;
      });
    });
  }
})();
