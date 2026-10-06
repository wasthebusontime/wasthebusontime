// Stop map on /stops/. Optional: without JavaScript the page is the stop list.
// Loads the stop positions, route lines and one preset of counts at a time from
// /stops/data/ (all precomputed by the pipeline), and colors each stop by the share
// of departures on time. Basemap tiles come from our own tile file via pmtiles.
import * as maplibregl from "/static/vendor/maplibre-gl/maplibre-gl.mjs";

const root = document.getElementById("stop-map");
if (root) start(root);

function percent(count, n) {
  return Math.round((count * 100) / n) + "%";
}

function bin(counts, minSample) {
  if (!counts || counts[0] < minSample) return -1;
  const share = counts[2] / counts[0];
  return share >= 0.9 ? 0 : share >= 0.8 ? 1 : share >= 0.7 ? 2 : 3;
}

function css(name) {
  return getComputedStyle(root).getPropertyValue(name).trim();
}

// The basemap follows the page: Protomaps' neutral "white" flavor, or "black" in dark mode.
const darkScheme = window.matchMedia("(prefers-color-scheme: dark)");
let pmtilesReady = false;

function basemapStyle(tiles) {
  const origin = location.origin;
  if (!tiles || !window.pmtiles || !window.basemaps) {
    return { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": css("--map-blank") } }] };
  }
  if (!pmtilesReady) {
    maplibregl.addProtocol("pmtiles", new window.pmtiles.Protocol().tile);
    pmtilesReady = true;
  }
  const flavor = darkScheme.matches ? "black" : "white";
  return {
    version: 8,
    glyphs: origin + "/static/vendor/protomaps/fonts/{fontstack}/{range}.pbf",
    sprite: origin + "/static/vendor/protomaps/sprites/" + flavor,
    sources: {
      protomaps: {
        type: "vector",
        url: "pmtiles://" + new URL(tiles, location.href).href,
        attribution: '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>, <a href="https://protomaps.com">Protomaps</a>',
      },
    },
    layers: window.basemaps.layers("protomaps", window.basemaps.namedFlavor(flavor), { lang: "en" }),
  };
}

function el(tag, text, attrs) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  return node;
}

// Stop names keep the agency's "[sb]"-style codes on screen; screen readers hear the word.
const DIRECTIONS = { nb: "northbound", sb: "southbound", eb: "eastbound", wb: "westbound" };

function stopName(name) {
  const node = el("strong", "", { id: "map-popup-name" });
  name.split(/(\[(?:nb|sb|eb|wb)\])/).forEach((part) => {
    const code = part.match(/^\[(nb|sb|eb|wb)\]$/);
    if (code) {
      node.append(el("span", part, { "aria-hidden": "true" }), el("span", DIRECTIONS[code[1]], { class: "visually-hidden" }));
    } else if (part) {
      node.append(part);
    }
  });
  return node;
}

function popupContent(stop, counts, minSample, scopeLabel) {
  const box = el("div", "", { class: "map-popup" });
  box.append(stopName(stop.name));
  const details = el("div", "", { id: "map-popup-details" });
  box.append(details);
  details.append(el("div", `Stop ${stop.code}, Route${stop.routes.length > 1 ? "s" : ""} ${stop.routes.join(", ")}`));
  if (!counts) {
    details.append(el("div", "Not a timepoint. Choose \"All stops\" to see its numbers."));
  } else if (counts[0] < minSample) {
    details.append(el("div", `Not enough data (${counts[0]} departure${counts[0] === 1 ? "" : "s"}).`));
  } else {
    const [n, early, onTime, late, onTimeAlt] = counts;
    details.append(el("div", `${percent(onTime, n)} on time (0 to 5 min late), ${percent(early, n)} early, ${percent(late, n)} late`));
    details.append(el("div", `${percent(onTimeAlt, n)} on time (1 min early to 5 min late)`));
    details.append(el("div", `${n.toLocaleString("en-US")} departures, ${scopeLabel.toLowerCase()}`));
  }
  const link = el("a", "View stop page", { href: `/stops/${stop.code}/` });
  link.append(el("span", ` for stop ${stop.code}`, { class: "visually-hidden" }));
  box.append(link);
  return box;
}

async function start(root) {
  root.hidden = false;
  const status = document.getElementById("map-status");
  const form = document.getElementById("map-controls");
  const base = root.dataset.base;
  const minSample = Number(root.dataset.minSample);
  const presets = new Map();

  let stops, routes;
  try {
    [stops, routes] = await Promise.all([
      fetch(base + "stops.json").then((r) => r.json()),
      fetch(base + "routes.geojson").then((r) => r.json()),
    ]);
  } catch (e) {
    status.textContent = "The map couldn't load. The list of stops below still works.";
    return;
  }

  try {
    const saved = localStorage.getItem("wbot-scope");
    if (saved) form.scope.value = saved;
  } catch (e) {}

  const bounds = new maplibregl.LngLatBounds();
  stops.forEach((s) => bounds.extend([s.lon, s.lat]));
  const map = new maplibregl.Map({
    container: "map",
    style: basemapStyle(root.dataset.tiles),
    bounds: bounds,
    fitBoundsOptions: { padding: 40 },
    attributionControl: { compact: true },
    cooperativeGestures: true,
  });
  map.getCanvas().setAttribute("aria-describedby", "map-keys");
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }));
  map.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: false } }));

  function load(key) {
    if (!presets.has(key)) presets.set(key, fetch(`${base}map/${key}.json`).then((r) => r.json()));
    return presets.get(key);
  }

  function ticked(name) {
    return [...form.querySelectorAll(`input[name="${name}"]:checked`)].map((box) => box.value);
  }

  function everyBox(name) {
    return form.querySelectorAll(`input[name="${name}"]`).length;
  }

  function nothingTicked() {
    return ["daytype", "band", "route"].some((name) => ticked(name).length === 0);
  }

  // Counts add up, so several days or times of day are the sum of their presets.
  // When every box is ticked, the precomputed "all" preset gives the same numbers in one file.
  let current = [];
  async function loadCounts() {
    const days = ticked("daytype").length === everyBox("daytype") ? ["all"] : ticked("daytype");
    const bands = ticked("band").length === everyBox("band") ? ["all"] : ticked("band");
    const docs = await Promise.all(days.flatMap((d) => bands.map((b) => load(`${form.period.value}/${d}-${b}`))));
    const scope = form.scope.value;
    current = stops.map((_, i) => {
      const parts = docs.map((doc) => doc[scope][i]).filter((c) => c);
      return parts.length ? parts.reduce((sum, c) => sum.map((v, k) => v + c[k])) : null;
    });
    return current;
  }

  // Keeps a panel's "All" box and its summary line in step with the other boxes.
  function summarize(details) {
    const boxes = [...details.querySelectorAll(`input[name="${details.dataset.name}"]`)];
    const on = boxes.filter((box) => box.checked);
    const allBox = details.querySelector(".multi-all");
    allBox.checked = on.length === boxes.length;
    allBox.indeterminate = on.length > 0 && on.length < boxes.length;
    const names = details.dataset.name === "route"
      ? on.map((box) => box.value)
      : on.map((box) => box.parentElement.textContent.trim().replace(/ \(.*\)$/, ""));
    details.querySelector(".multi-value").textContent =
      on.length === boxes.length ? details.dataset.all : on.length === 0 ? "None" : names.join(", ");
  }

  async function stopFeatures() {
    const counts = await loadCounts();
    return {
      type: "FeatureCollection",
      features: stops.map((s, i) => ({
        type: "Feature",
        id: i,
        geometry: { type: "Point", coordinates: [s.lon, s.lat] },
        properties: { i: i, bin: bin(counts[i], minSample), routes: "," + s.routes.join(",") + "," },
      })),
    };
  }

  // Routes narrow which stops show; a stop's numbers still cover every route serving it.
  function routeFilter() {
    const chosen = ticked("route");
    const some = chosen.length > 0 && chosen.length < everyBox("route");
    map.setFilter("stops", some ? ["any", ...chosen.map((r) => ["in", "," + r + ",", ["get", "routes"]])] : null);
    map.setFilter("route-selected", ["in", ["get", "slug"], ["literal", some ? chosen : []]]);
    map.setPaintProperty("routes", "line-opacity", some ? 0.25 : 0.7);
  }

  function fitRoutes() {
    const chosen = ticked("route");
    if (!chosen.length) return;
    const b = new maplibregl.LngLatBounds();
    stops.filter((s) => s.routes.some((r) => chosen.includes(r))).forEach((s) => b.extend([s.lon, s.lat]));
    map.fitBounds(b, { padding: 40, maxZoom: 15 });
  }

  async function refresh() {
    // An open popup would show the previous choice's numbers.
    document.querySelectorAll(".maplibregl-popup").forEach((popup) => popup.remove());
    if (nothingTicked()) {
      map.getSource("stops").setData({ type: "FeatureCollection", features: [] });
      status.textContent = "No stops shown: tick at least one day, one time of day and one route.";
      return;
    }
    try {
      map.getSource("stops").setData(await stopFeatures());
      status.textContent = "";
    } catch (e) {
      status.textContent = "Sorry, the numbers for this choice didn't load. Please try again in a moment.";
    }
  }

  // After any change in a checkbox panel: routes refilter, days and times reload counts.
  function applied(details) {
    summarize(details);
    if (details.dataset.name === "route") {
      routeFilter();
      fitRoutes();
    }
    refresh();
  }

  // The popup is a small dialog named after the stop. Escape closes it, and closing it
  // puts focus back on the map rather than losing it.
  async function openStop(i) {
    const counts = current[i];
    const label = form.scope.selectedOptions[0].textContent;
    // One popup at a time (the click handler gets this from MapLibre, Enter doesn't).
    document.querySelectorAll(".maplibregl-popup").forEach((old) => old.remove());
    const popup = new maplibregl.Popup({ maxWidth: "18rem" })
      .setLngLat([stops[i].lon, stops[i].lat])
      .setDOMContent(popupContent(stops[i], counts, minSample, label))
      .addTo(map);
    const box = popup.getElement();
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-labelledby", "map-popup-name");
    box.setAttribute("aria-describedby", "map-popup-details");
    box.addEventListener("keydown", (e) => {
      if (e.key === "Escape") popup.remove();
    });
    popup.on("close", () => {
      if (!document.activeElement || document.activeElement === document.body || box.contains(document.activeElement)) {
        map.getCanvas().focus();
      }
    });
  }

  // Keyboard selection: Enter on the focused map opens the stop nearest the center cross;
  // Enter again without moving the map opens the next nearest. Messages show on the map
  // itself, where a keyboard user is looking.
  const note = el("p", "", { class: "map-note", role: "status" });
  map.getContainer().append(note);
  let nearby = [];
  let nearbyAt = "";
  let nearbyNext = 0;
  map.on("movestart", () => (note.textContent = ""));
  map.getCanvas().addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      document.querySelectorAll(".maplibregl-popup").forEach((old) => old.remove());
      return;
    }
    if (e.key !== "Enter" || !map.getLayer("stops")) return;
    e.preventDefault();
    const center = map.project(map.getCenter());
    const at = `${map.getCenter().toArray()} ${map.getZoom()}`;
    if (at !== nearbyAt) {
      // Wider than one arrow-key step (100 px), so panning always brings a stop within reach.
      const reach = 60;
      const distance = (f) => {
        const p = map.project(f.geometry.coordinates);
        return (p.x - center.x) ** 2 + (p.y - center.y) ** 2;
      };
      nearby = map.queryRenderedFeatures(
        [[center.x - reach, center.y - reach], [center.x + reach, center.y + reach]], { layers: ["stops"] })
        .sort((a, b) => distance(a) - distance(b));
      nearbyAt = at;
      nearbyNext = 0;
    }
    if (!nearby.length) {
      note.textContent = "No stop near the cross. Move the map or zoom out, then press Enter again.";
      return;
    }
    // Stepping through more than a few stops is slower than zooming in, so say that instead.
    note.textContent = nearby.length > 5 ? "Many stops are near the cross. Zoom in with + to pick one."
      : nearby.length > 1 ? `Stop ${nearbyNext + 1} of ${nearby.length} near the cross. Press Escape, then Enter for the next.` : "";
    openStop(nearby[nearbyNext].properties.i);
    nearbyNext = (nearbyNext + 1) % nearby.length;
  });

  map.on("load", async () => {
    const colors = ["--map-bin-0", "--map-bin-1", "--map-bin-2", "--map-bin-3"].map(css);
    map.addSource("routes", { type: "geojson", data: routes });
    map.addLayer({ id: "routes", type: "line", source: "routes",
      paint: { "line-color": css("--map-route"), "line-width": 2, "line-opacity": 0.7 } });
    map.addLayer({ id: "route-selected", type: "line", source: "routes", filter: ["==", ["get", "slug"], ""],
      paint: { "line-color": css("--map-route-selected"), "line-width": 4 } });
    map.addSource("stops", { type: "geojson", data: await stopFeatures() });
    map.addLayer({
      id: "stops", type: "circle", source: "stops",
      paint: {
        // "Not enough data" is a smaller gray dot, so it can't be mistaken for any bin.
        "circle-radius": ["interpolate", ["linear"], ["zoom"],
          10, ["match", ["get", "bin"], -1, 2.5, 4], 15, ["match", ["get", "bin"], -1, 4.5, 8]],
        "circle-color": ["match", ["get", "bin"], 0, colors[0], 1, colors[1], 2, colors[2], 3, colors[3], css("--map-nodata")],
        "circle-stroke-color": css("--map-stop-stroke"),
        "circle-stroke-width": ["match", ["get", "bin"], -1, 0.5, 1],
      },
    });
    routeFilter();

    // Light or dark mode changed while the page is open: swap the basemap under our layers
    // (keeping their data and filters) and recolor them from the new CSS values.
    darkScheme.addEventListener("change", () => {
      const ours = ["routes", "route-selected", "stops"];
      const bins = ["--map-bin-0", "--map-bin-1", "--map-bin-2", "--map-bin-3"].map(css);
      const colors = {
        routes: { "line-color": css("--map-route") },
        "route-selected": { "line-color": css("--map-route-selected") },
        stops: {
          "circle-color": ["match", ["get", "bin"], 0, bins[0], 1, bins[1], 2, bins[2], 3, bins[3], css("--map-nodata")],
          "circle-stroke-color": css("--map-stop-stroke"),
        },
      };
      map.setStyle(basemapStyle(root.dataset.tiles), {
        transformStyle: (prev, next) => ({
          ...next,
          sources: { ...next.sources, routes: prev.sources.routes, stops: prev.sources.stops },
          layers: [
            ...next.layers,
            ...prev.layers.filter((l) => ours.includes(l.id)).map((l) => ({ ...l, paint: { ...l.paint, ...colors[l.id] } })),
          ],
        }),
      });
    });

    map.on("click", "stops", (e) => openStop(e.features[0].properties.i));
    map.on("mouseenter", "stops", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "stops", () => (map.getCanvas().style.cursor = ""));

    form.addEventListener("change", (e) => {
      const details = e.target.closest("details.multi");
      if (details) {
        // "All" ticks or unticks every box; the other boxes then set "All" (in summarize).
        if (e.target.classList.contains("multi-all")) {
          details.querySelectorAll(`input[name="${details.dataset.name}"]`).forEach((box) => (box.checked = e.target.checked));
        }
        applied(details);
        return;
      }
      if (e.target.name === "scope") {
        try { localStorage.setItem("wbot-scope", form.scope.value); } catch (err) {}
      }
      refresh();
    });

    const wanted = new URLSearchParams(location.search).get("stop");
    const i = stops.findIndex((s) => s.code === wanted);
    if (i >= 0) {
      map.jumpTo({ center: [stops[i].lon, stops[i].lat], zoom: 15 });
      openStop(i);
      root.scrollIntoView();
    }
  });
  form.addEventListener("submit", (e) => e.preventDefault());
  // The checkbox panels behave like dropdowns: one open at a time, closed by a click
  // elsewhere, by focus moving out of them, or by Escape (which returns focus to the panel's button).
  const panels = [...form.querySelectorAll("details.multi")];
  panels.forEach((d) => {
    d.addEventListener("toggle", () => {
      if (d.open) panels.filter((other) => other !== d).forEach((other) => (other.open = false));
    });
    d.addEventListener("focusout", (e) => {
      if (e.relatedTarget && !d.contains(e.relatedTarget)) d.open = false;
    });
    d.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && d.open) {
        d.open = false;
        d.querySelector("summary").focus();
      }
    });
  });
  document.addEventListener("click", (e) => {
    panels.filter((d) => d.open && !d.contains(e.target)).forEach((d) => (d.open = false));
  });
  // Reset ticks every box in its panel again.
  panels.forEach((details) => details.querySelector(".multi-reset").addEventListener("click", () => {
    details.querySelectorAll("input[type=checkbox]").forEach((box) => (box.checked = true));
    if (map.getLayer("stops")) applied(details);
    else summarize(details);
  }));
}
