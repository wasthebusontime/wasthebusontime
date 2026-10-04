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

function basemapStyle(tiles) {
  const origin = location.origin;
  if (!tiles || !window.pmtiles || !window.basemaps) {
    return { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": css("--map-blank") } }] };
  }
  const protocol = new window.pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  return {
    version: 8,
    glyphs: origin + "/static/vendor/protomaps/fonts/{fontstack}/{range}.pbf",
    sprite: origin + "/static/vendor/protomaps/sprites/grayscale",
    sources: {
      protomaps: {
        type: "vector",
        url: "pmtiles://" + new URL(tiles, location.href).href,
        attribution: '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>, <a href="https://protomaps.com">Protomaps</a>',
      },
    },
    layers: window.basemaps.layers("protomaps", window.basemaps.namedFlavor("grayscale"), { lang: "en" }),
  };
}

function el(tag, text, attrs) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  return node;
}

function popupContent(stop, counts, minSample, scopeLabel) {
  const box = el("div", "", { class: "map-popup" });
  box.append(el("strong", stop.name));
  box.append(el("div", `Stop ${stop.code}, Route${stop.routes.length > 1 ? "s" : ""} ${stop.routes.join(", ")}`));
  if (!counts) {
    box.append(el("div", "Not a timepoint. Choose \"All stops\" to see its numbers."));
  } else if (counts[0] < minSample) {
    box.append(el("div", `Not enough data (${counts[0]} departures).`));
  } else {
    const [n, early, onTime, late, onTimeAlt] = counts;
    box.append(el("div", `${percent(onTime, n)} on time (0 to 5 min late), ${percent(early, n)} early, ${percent(late, n)} late`));
    box.append(el("div", `${percent(onTimeAlt, n)} on time (1 min early to 5 min late)`));
    box.append(el("div", `${n.toLocaleString("en-US")} departures, ${scopeLabel.toLowerCase()}`));
  }
  box.append(el("a", "Stop page", { href: `/stops/${stop.code}/` }));
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

  function summarize(details) {
    const boxes = [...details.querySelectorAll("input")];
    const on = boxes.filter((box) => box.checked);
    const all = on.length === boxes.length || (details.dataset.name === "route" && on.length === 0);
    const names = details.dataset.name === "route"
      ? on.map((box) => box.value)
      : on.map((box) => box.parentElement.textContent.trim().replace(/ \(.*\)$/, ""));
    details.querySelector(".multi-value").textContent = all ? details.dataset.all : names.join(", ");
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
    map.setFilter("stops", chosen.length ? ["any", ...chosen.map((r) => ["in", "," + r + ",", ["get", "routes"]])] : null);
    map.setFilter("route-selected", ["in", ["get", "slug"], ["literal", chosen]]);
    map.setPaintProperty("routes", "line-opacity", chosen.length ? 0.25 : 0.7);
  }

  function fitRoutes() {
    const chosen = ticked("route");
    const b = new maplibregl.LngLatBounds();
    stops.filter((s) => !chosen.length || s.routes.some((r) => chosen.includes(r))).forEach((s) => b.extend([s.lon, s.lat]));
    map.fitBounds(b, { padding: 40, maxZoom: 15 });
  }

  async function refresh() {
    // An open popup would show the previous choice's numbers.
    document.querySelectorAll(".maplibregl-popup").forEach((popup) => popup.remove());
    try {
      map.getSource("stops").setData(await stopFeatures());
      status.textContent = "";
    } catch (e) {
      status.textContent = "Couldn't load the numbers for this choice.";
    }
  }

  async function openStop(i) {
    const counts = current[i];
    const label = form.scope.selectedOptions[0].textContent;
    new maplibregl.Popup({ maxWidth: "18rem" })
      .setLngLat([stops[i].lon, stops[i].lat])
      .setDOMContent(popupContent(stops[i], counts, minSample, label))
      .addTo(map);
  }

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

    map.on("click", "stops", (e) => openStop(e.features[0].properties.i));
    map.on("mouseenter", "stops", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "stops", () => (map.getCanvas().style.cursor = ""));

    form.addEventListener("change", (e) => {
      const details = e.target.closest("details.multi");
      if (details && e.target.name !== "route" && ticked(e.target.name).length === 0) {
        e.target.checked = true; // keep at least one day and one time of day
        status.textContent = "Keep at least one box ticked.";
        return;
      }
      if (details) summarize(details);
      if (e.target.name === "route") {
        routeFilter();
        fitRoutes();
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
  // The checkbox panels behave like dropdowns: one open at a time, closed by a click elsewhere.
  const panels = [...form.querySelectorAll("details.multi")];
  panels.forEach((d) => d.addEventListener("toggle", () => {
    if (d.open) panels.filter((other) => other !== d).forEach((other) => (other.open = false));
  }));
  document.addEventListener("click", (e) => {
    panels.filter((d) => d.open && !d.contains(e.target)).forEach((d) => (d.open = false));
  });
  form.querySelector(".multi-clear").addEventListener("click", () => {
    form.querySelectorAll('input[name="route"]').forEach((box) => (box.checked = false));
    summarize(form.querySelector('details[data-name="route"]'));
    if (map.getLayer("stops")) {
      routeFilter();
      fitRoutes();
    }
  });
}
