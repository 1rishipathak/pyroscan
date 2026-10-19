// the PyroScan dashboard. season data is static json in data/<season>/, the live panel uses the api.
const SEASON = new URLSearchParams(location.search).get("season") || "2025";
const D = `data/${SEASON}`;
const fmt = (n, d = 0) => (n == null || Number.isNaN(n) ? "–" : Number(n).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d }));
const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const COL = {
  detected: css("--detected"), undetected: css("--undetected"), detection: css("--detection"),
  ink: css("--ink"), ink2: css("--ink-2"), muted: css("--muted"), line: css("--line"), line2: css("--line-2"), surface: css("--surface"),
};
const dateLong = (iso) => new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
const dateShort = (iso) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

async function j(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

let S = {};
const SEASONS = [...document.querySelectorAll(".season-tab")].map((a) => a.dataset.s);
const LIVE_SEASON = document.querySelector(".season-tab .live-dot")?.closest(".season-tab")?.dataset.s;
const INSAT_EVERY = 30;  // minutes between insat-3dr scans (every_min in insat.json is how often we read one)

async function init() {
  const [summary, timeline, overlay, scars, firms, hours, districts, hotspots, punjab, ...insats] = await Promise.all([
    j(`${D}/summary.json`), j(`${D}/timeline.json`), j(`${D}/overlay.json`), j(`${D}/scars.geojson`),
    j(`${D}/firms.json`), j(`${D}/hours.json`), j("data/districts.geojson"), j(`${D}/hotspots.json`).catch(() => null),
    j("data/punjab.geojson").catch(() => null),
    ...SEASONS.map((s) => j(`data/${s}/insat.json`).catch(() => null)),
  ]);
  const insatAll = Object.fromEntries(SEASONS.map((s, i) => [s, insats[i]]));
  const insat = insatAll[SEASON] || null;
  S = { summary, timeline, overlay, scars, firms, hours, districts, hotspots, punjab, insat, insatAll };
  document.querySelectorAll(".insat-only").forEach((el) => (el.hidden = !insat));
  document.querySelectorAll(".insat-map").forEach((el) => (el.hidden = !(insat && insat.rows.length)));
  header();
  kpis();
  stats("study_area");
  map();
  charts();
  teams();
  response();
  provenance();
  live();
}

// header, status bar and key finding
function header() {
  document.querySelectorAll(".season-tab").forEach((a) => a.classList.toggle("on", a.dataset.s === SEASON));
  const tick = () => { document.getElementById("utc").textContent = `UTC ${new Date().toISOString().slice(11, 19)}`; };
  tick(); setInterval(tick, 1000);

  const s = S.summary, sa = s.study_area, ov = S.overlay;
  const official = s.detections === "official";
  const b = s.official_bulletin || {};
  const items = [
    `<span><b>Study area</b> Sangrur + Barnala · 2 of Punjab's 23 districts</span>`,
    `<span><b>Season</b> Kharif ${SEASON} · ${dateShort(ov.scenes[0])} – ${dateLong(ov.scenes[ov.n - 1])}${s.live ? " (in progress)" : ""}</span>`,
    `<span><b>Imagery</b> ${ov.n} Sentinel-2 L2A passes, tile 43REP</span>`,
    b.date ? `<span><b>Official count</b> CREAMS bulletin ${dateLong(b.date)} · read via <span class="via">${s.official_via === "tinyfish" ? "TinyFish" : "PDF"}</span></span>` : "",
    `<span><b>Matching</b> ${official ? "official GPS events (CREAMS)" : "VIIRS S-NPP, NOAA-20, MODIS (NASA FIRMS)"}</span>`,
    `<span><b>Model</b> U-Net v2 · flip TTA · p ≥ 0.7</span>`,
    S.insat ? `<span><b>Geostationary</b> ISRO INSAT-3DR fire product, a scan every ${INSAT_EVERY} min (MOSDAC)</span>` : "",
  ];
  document.getElementById("statusbar").innerHTML = items.join("");

  const by = s.burnt_by_year || {}, y = s.years || {};
  let tag, text;
  if (SEASON === "2021") {
    tag = "Control year";
    text = `In Kharif 2021, before burning moved past the satellite overpasses, <strong>${pct(sa.unseen_share)} of burnt cropland</strong> went undetected by the official-count satellites. The same method finds ${pct((by["2025"] || {}).unseen_share)} in 2025.`
      + (S.insat ? ` ISRO's INSAT-3DR fire product flagged just ${fmt(S.insat.punjab.pixels)} fire pixels over all of Punjab that season.` : "");
  } else if (s.live) {
    tag = "Live season";
    text = `<strong>${fmt(sa.burnt_ha)} ha burnt</strong> so far this season in Sangrur and Barnala, against <strong>${fmt(sa.official_events)} fire events</strong> in the official count (CREAMS bulletin ${dateLong(b.date)}).`
      + (S.insat && !S.insat.study.pixels ? ` ISRO's INSAT-3DR, scanning every ${INSAT_EVERY} minutes, has not flagged a single fire pixel there.` : "");
  } else {
    tag = "Key finding";
    let tail = "";
    if (y["2021"] && by["2021"] && by[SEASON]) {
      const c0 = y["2021"].Sangrur + y["2021"].Barnala;
      tail = ` Since 2021 the official count fell ${Math.round((1 - sa.official_events / c0) * 100)}%; measured burnt area fell ${Math.round((1 - by[SEASON].burnt_ha / by["2021"].burnt_ha) * 100)}%.`;
    }
    const geo = S.insat && !S.insat.study.pixels
      ? ` ISRO's INSAT-3DR, which scans every ${INSAT_EVERY} minutes, did not flag a single fire pixel in either district all season.` : "";
    text = `<strong>${pct(sa.unseen_share)} of burnt cropland</strong> in Sangrur and Barnala (Kharif ${SEASON}) had no detection from the satellites behind India's official farm-fire count.${tail}${geo}`;
  }
  document.getElementById("finding").innerHTML = `<span class="tag">${tag}</span><p>${text}</p>`;
  document.getElementById("export").href = `/api/export/${SEASON}/unseen.csv`;
}

function kpis() {
  const s = S.summary, sa = s.study_area, b = s.official_bulletin || {};
  const by = s.burnt_by_year || {}, y = s.years || {};
  const official = s.detections === "official";
  let ratioSub = "";
  if (SEASON !== "2021" && by["2021"] && y["2021"]) ratioSub = `2021: ${fmt(by["2021"].burnt_ha / (y["2021"].Sangrur + y["2021"].Barnala))} ha per event`;
  else if (SEASON === "2021" && by["2025"]) ratioSub = "2025: see the 2025 season";
  const sm = s.smoke ? s.smoke.study_area.pm25_unseen_t : null;
  const h = S.hotspots;
  const tiles = [
    { label: "Official fire events", value: fmt(sa.official_events), sub: b.date ? `CREAMS, ${dateShort(b.date)} · via ${s.official_via === "tinyfish" ? "TinyFish" : "PDF"}` : "CREAMS, ICAR-IARI", subClass: "via" },
    { label: "Burnt cropland area", value: fmt(sa.burnt_ha), unit: "ha", sub: sa.rule_burnt_ha != null ? `Strict-rule floor ${fmt(sa.rule_burnt_ha)} ha` : "Sentinel-2, U-Net v2" },
    { label: "Undetected burnt area", sw: "undetected", value: pct(sa.unseen_share), sub: `${fmt(sa.unseen_ha)} ha · ${official ? "not in official GPS list" : "no VIIRS / MODIS match"}` },
    { label: "Burnt area per official event", value: fmt(sa.ha_per_official_event), unit: "ha", sub: ratioSub || "Sentinel-2 area ÷ official count" },
    { label: "PM2.5 from undetected burns", value: sm ? fmt(sm.mid) : "–", unit: "t", sub: sm ? `Range ${fmt(sm.low)}–${fmt(sm.high)} t (estimate)` : "" },
    { label: "Villages with no detection", value: h ? fmt(h.zero_detection_villages_50ha) : "–", sub: h ? `≥ 50 ha burnt · of ${fmt(h.villages_with_burns)} with burns` : "" },
  ];
  document.getElementById("kpis").innerHTML = tiles.map((t) => `<div class="kpi">
      <div class="k-label">${t.sw ? `<i class="sw sw-${t.sw}"></i>` : ""}${t.label}</div>
      <div class="k-value">${t.value}${t.unit ? `<span class="k-unit">${t.unit}</span>` : ""}</div>
      <div class="k-sub ${t.subClass || ""}">${t.sub}</div></div>`).join("");
}

function stats(key) {
  const s = key === "study_area" ? S.summary.study_area : S.summary.districts[key];
  const official = S.summary.detections === "official";
  const rows = [
    ["Official fire events", fmt(s.official_events)],
    [official ? "Official events with GPS" : "Satellite detections (4 satellites)", fmt(s.firms_detections)],
    ["Burnt cropland", `${fmt(s.burnt_ha)} ha`],
    ["Burnt, undetected", `${fmt(s.unseen_ha)} ha`],
    ["Undetected share", pct(s.unseen_share)],
    ["Burn scars (≥ 0.4 ha)", fmt(s.scars)],
    ["Undetected scars", fmt(s.unseen_scars)],
    ["Burnt area per official event", `${fmt(s.ha_per_official_event, 1)} ha`],
    ["Detections on a burn scar", pct(s.firms_matched_share)],
  ];
  if (s.rule_burnt_ha != null) rows.push(["Strict-rule floor (no U-Net)", `${fmt(s.rule_burnt_ha)} ha`]);
  if (s.unseen_share_vs_official_list != null) rows.push(["Not in official GPS list", pct(s.unseen_share_vs_official_list)]);
  document.getElementById("dstats").innerHTML = rows.map(([a, b]) => `<tr><td>${a}</td><td>${b}</td></tr>`).join("");
}

document.querySelectorAll(".seg-btn").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".seg-btn").forEach((x) => x.classList.toggle("on", x === b));
    stats(b.dataset.d);
    if (window._fit) window._fit(b.dataset.d);
  })
);

// map

// small inset showing where the two districts sit in punjab
function locator(m) {
  if (!S.punjab) return;
  const W = 116, pad = 4;
  const rings = (g) => (g.type === "Polygon" ? [g.coordinates] : g.coordinates).flat();
  const pts = S.punjab.features.flatMap((f) => rings(f.geometry)).flat();
  const x0 = Math.min(...pts.map((p) => p[0])), x1 = Math.max(...pts.map((p) => p[0]));
  const y0 = Math.min(...pts.map((p) => p[1])), y1 = Math.max(...pts.map((p) => p[1]));
  const k = Math.cos(((y0 + y1) / 2) * Math.PI / 180);  // equirectangular, true to scale at mid-latitude
  const s = (W - 2 * pad) / Math.max((x1 - x0) * k, y1 - y0);
  const H = Math.round((y1 - y0) * s + 2 * pad);
  const xy = ([lon, lat]) => `${(pad + (lon - x0) * k * s).toFixed(1)},${(pad + (y1 - lat) * s).toFixed(1)}`;
  const d = (g) => rings(g).map((r) => `M${r.map(xy).join("L")}Z`).join("");
  const svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Sangrur and Barnala within Punjab">
    ${S.punjab.features.map((f) => `<path class="loc-state" d="${d(f.geometry)}"/>`).join("")}
    ${S.districts.features.map((f) => `<path class="loc-study" d="${d(f.geometry)}"/>`).join("")}</svg>`;
  const ctl = L.control({ position: "bottomleft" });
  ctl.onAdd = () => {
    const el = L.DomUtil.create("div", "locator");
    el.innerHTML = `<div class="lg-title">Punjab</div>${svg}<div class="loc-cap"><i class="loc-key"></i>Study area: 2 of 23 districts</div>`;
    L.DomEvent.disableClickPropagation(el);
    return el;
  };
  ctl.addTo(m);
}

function map() {
  const m = L.map("map", { zoomControl: true, attributionControl: true, preferCanvas: true });
  window._map = m;
  const sat = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 18, attribution: "Imagery © Esri",
  });
  const dark = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 18, attribution: "© OpenStreetMap, © CARTO",
  });
  const mapEl = document.getElementById("map");
  sat.addTo(m);
  mapEl.classList.add("sat-dim");

  const dl = L.geoJSON(S.districts, {
    style: { color: "#ffffff", weight: 1.4, opacity: 0.9, fill: false },
    onEachFeature: (f, l) => l.bindTooltip(f.properties.district, { sticky: true }),
  }).addTo(m);
  const byName = {};
  dl.eachLayer((l) => (byName[l.feature.properties.district] = l));
  window._fit = (k) => m.fitBounds(k === "study_area" ? dl.getBounds() : byName[k].getBounds(), { padding: [20, 20] });
  window._fit("study_area");
  locator(m);

  // outlines and scar rings: white over satellite imagery, ink over the light basemap
  let ringColor = "#ffffff";
  document.getElementById("t-sat").addEventListener("change", (e) => {
    if (e.target.checked) { m.removeLayer(dark); sat.addTo(m); mapEl.classList.add("sat-dim"); ringColor = "#ffffff"; }
    else { m.removeLayer(sat); dark.addTo(m); mapEl.classList.remove("sat-dim"); ringColor = COL.ink; }
    sat.bringToBack(); dark.bringToBack();
    dl.setStyle({ color: ringColor });
    const now = S.overlay.scenes[+document.getElementById("slider").value - 1];
    drawScars(now);
    drawInsat(now);
  });

  const ov = S.overlay;
  const img = L.imageOverlay(`${D}/overlay_${ov.n}.png`, ov.bounds, { opacity: 1, interactive: false }).addTo(m);
  for (let k = 1; k <= ov.n; k++) { const i = new Image(); i.src = `${D}/overlay_${k}.png`; }  // preload: no flicker

  // satellite fire detections
  const fl = L.layerGroup().addTo(m);
  const fpts = S.firms.rows.map(([lon, lat, t, si, mt]) => ({ t, src: S.firms.sources[si], m: !!mt, ll: [lat, lon] }));
  function drawFirms(until) {
    fl.clearLayers();
    if (!document.getElementById("t-firms").checked) return;
    const z = m.getZoom();
    const r = z <= 10 ? 1.4 : z <= 11 ? 2.2 : z <= 12 ? 3 : 4;
    for (const p of fpts) {
      if (p.t.slice(0, 10) > until) continue;
      L.circleMarker(p.ll, { radius: r, color: COL.surface, weight: z >= 12 ? 1.5 : 0.6, fillColor: COL.detection, fillOpacity: 0.95, bubblingMouseEvents: false })
        .bindTooltip(`${p.src}<br>${p.t} IST`).addTo(fl);
    }
  }

  // largest scars, clickable
  const sl = L.layerGroup().addTo(m);
  function drawScars(until) {
    sl.clearLayers();
    if (!document.getElementById("t-scars").checked || m.getZoom() < 10) return;
    for (const f of S.scars.features) {
      const p = f.properties;
      if (p.post > until) continue;
      const ll = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
      L.circleMarker(ll, { radius: 8, color: ringColor, weight: 1.5, opacity: 0.95, fill: true, fillOpacity: 0.01, bubblingMouseEvents: false })
        .on("click", () => inspect(p, ll, m)).addTo(sl);
    }
  }

  // insat-3dr fire pixels, drawn as ~4 km squares (the real pixel size)
  const il = L.layerGroup().addTo(m);
  const ipts = S.insat ? S.insat.rows.map(([lon, lat, t, bt]) => ({ lon, lat, t, bt })) : [];
  function drawInsat(until) {
    il.clearLayers();
    if (!ipts.length || !document.getElementById("t-insat").checked) return;
    for (const p of ipts) {
      if (p.t.slice(0, 10) > until) continue;
      L.rectangle([[p.lat - 0.021, p.lon - 0.021], [p.lat + 0.021, p.lon + 0.021]],
        { color: ringColor, weight: 1.2, opacity: 0.9, fill: false, bubblingMouseEvents: false })
        .bindTooltip(`ISRO INSAT-3DR fire pixel<br>${p.t} IST${p.bt ? ` · ${p.bt} K at 3.9 µm` : ""}`).addTo(il);
    }
  }

  // 2 km hotspot grid, darker = more undetected burns
  let gridLayer = null;
  document.getElementById("t-grid").addEventListener("change", async (e) => {
    if (!e.target.checked) { if (gridLayer) m.removeLayer(gridLayer); return; }
    if (!gridLayer) {
      const g = await j(`${D}/grid.geojson`).catch(() => null);
      if (!g) return;
      const max = Math.max(...g.features.map((f) => f.properties.u));
      gridLayer = L.geoJSON(g, {
        style: (f) => ({ color: COL.undetected, weight: 0.4, opacity: 0.5, fillColor: COL.undetected, fillOpacity: Math.min(0.85, 0.06 + 0.8 * Math.sqrt(f.properties.u / max)) }),
        onEachFeature: (f, l) => l.bindTooltip(`Near ${f.properties.v}<br>${fmt(f.properties.u)} ha undetected of ${fmt(f.properties.b)} ha burnt<br>${f.properties.d} satellite detections`),
      });
    }
    gridLayer.addTo(m);
  });
  window._flyTo = (lat, lon, html) => { m.flyTo([lat, lon], 13, { duration: 0.8 }); L.popup().setLatLng([lat, lon]).setContent(html).openOn(m); };

  // click anywhere: did this field burn? (needs the api server)
  m.on("click", async (e) => {
    try {
      const r = await j(`/api/check?lat=${e.latlng.lat.toFixed(6)}&lon=${e.latlng.lng.toFixed(6)}&season=${SEASON}`);
      if (!r.inside) return;
      const html = r.burnt
        ? `<b>Burnt</b> between ${r.burnt_after} and ${r.burnt_by}<br><span class="badge"><i class="sw sw-${r.detected_by_official_satellites ? "detected" : "undetected"}"></i>${r.detected_by_official_satellites ? "Detected by an official-count satellite" : "No satellite detection"}</span>`
        : `<b>No burn detected</b><br>${r.clear_looks} clear Sentinel-2 looks this season`;
      L.popup().setLatLng(e.latlng).setContent(html).openOn(m);
    } catch { /* static copy: no api */ }
  });

  const slider = document.getElementById("slider");
  slider.max = ov.n;
  slider.value = ov.n;
  function setK(k) {
    img.setUrl(`${D}/overlay_${k}.png`);
    const d = ov.scenes[k - 1];
    document.getElementById("tb-date").textContent = dateLong(d);
    document.getElementById("tb-sub").textContent = `Sentinel-2 pass ${k} of ${ov.n} · new burns shown at full opacity`;
    drawFirms(d);
    drawScars(d);
    drawInsat(d);
  }
  slider.addEventListener("input", () => setK(+slider.value));
  m.on("zoomend", () => { const d = ov.scenes[+slider.value - 1]; drawFirms(d); drawScars(d); });
  document.getElementById("t-firms").addEventListener("change", () => setK(+slider.value));
  document.getElementById("t-scars").addEventListener("change", () => setK(+slider.value));
  document.getElementById("t-insat").addEventListener("change", () => setK(+slider.value));
  const icon = document.getElementById("play-icon");
  const PLAY = "M4 2.5v11l9-5.5z", PAUSE = "M4 2.5h3v11H4zM9 2.5h3v11H9z";
  let timer = null;
  const stop = () => { clearInterval(timer); timer = null; icon.setAttribute("d", PLAY); };
  document.getElementById("play").addEventListener("click", () => {
    if (timer) { stop(); return; }
    icon.setAttribute("d", PAUSE);
    if (+slider.value >= ov.n) slider.value = 1;
    timer = setInterval(() => {
      const k = +slider.value + 1;
      if (k > ov.n) { stop(); return; }
      slider.value = k; setK(k);
    }, 650);
  });
  setK(ov.n);
}

function inspect(p, ll, m) {
  const badge = p.seen
    ? `<span class="badge"><i class="sw sw-detected"></i>Detected · ${p.by.replaceAll(",", ", ")}</span>`
    : `<span class="badge"><i class="sw sw-undetected"></i>Undetected</span>`;
  const chips = p.chips
    ? `<div class="chips2"><figure><img src="${D}/img/chip_${p.id}_before.jpg" alt="Before burning"><figcaption>Before · ${p.pre}</figcaption></figure>
       <figure><img src="${D}/img/chip_${p.id}_after.jpg" alt="After burning"><figcaption>After · ${p.post}</figcaption></figure></div>`
    : "";
  document.getElementById("inspect").innerHTML = `<h4>${fmt(p.ha, 1)} ha · ${p.district}</h4>${badge}${chips}
    <p class="hint">Burnt between the clear passes of ${p.pre} and ${p.post}. ${p.seen ? `${p.n} detection(s) matched.` :
    "No VIIRS or MODIS detection fell on it in that window."} Chips: 1.2 km, Sentinel-2 true colour, 10 m.</p>`;
  m.panTo(ll);
}

// charts
const Y_AXIS_W = 48;  // fixed y-axis width so small multiples share one x-axis
const fixW = { afterFit: (sc) => { sc.width = Y_AXIS_W; } };

// labels each line at its last point
const endLabels = {
  id: "endLabels",
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    ctx.save();
    ctx.font = "500 12px 'Noto Sans', sans-serif";
    ctx.fillStyle = COL.ink2;
    ctx.textBaseline = "middle";
    chart.data.datasets.forEach((ds, i) => {
      if (!ds.endLabel || chart.getDatasetMeta(i).hidden) return;
      for (let k = ds.data.length - 1; k >= 0; k--) {
        if (ds.data[k] != null) { const pt = chart.getDatasetMeta(i).data[k]; ctx.fillText(ds.endLabel, pt.x + 10, pt.y); break; }
      }
    });
    ctx.restore();
  },
};

// a labelled solid reference line (e.g. aqi 'very poor')
const refLine = {
  id: "refLine",
  afterDatasetsDraw(chart, args, o) {
    if (!o || o.value == null) return;
    const { ctx, chartArea: a, scales: { y } } = chart;
    const py = y.getPixelForValue(o.value);
    ctx.save();
    ctx.strokeStyle = COL.line2; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke();
    ctx.font = "11px 'Noto Sans', sans-serif"; ctx.fillStyle = COL.muted; ctx.textAlign = "right"; ctx.textBaseline = "bottom";
    ctx.fillText(o.label, a.right - 4, py - 2);
    ctx.restore();
  },
};

// shaded x-range with a label (fig. 3)
const band = {
  id: "band",
  beforeDatasetsDraw(chart, args, o) {
    if (!o || o.from == null) return;
    const { ctx, chartArea: a, scales: { x } } = chart;
    const half = x.width / chart.data.labels.length / 2;
    const x0 = x.getPixelForValue(o.from) - half;
    const x1 = x.getPixelForValue(o.to) + half;
    ctx.save();
    ctx.fillStyle = "rgba(32, 33, 36, 0.05)";
    ctx.fillRect(x0, a.top, x1 - x0, a.bottom - a.top);
    ctx.font = "11px 'Noto Sans', sans-serif"; ctx.fillStyle = COL.muted; ctx.textAlign = "center"; ctx.textBaseline = "top";
    ctx.fillText(o.label, (x0 + x1) / 2, a.top + 4);
    ctx.restore();
  },
};

function charts() {
  Chart.defaults.color = COL.muted;
  Chart.defaults.font.family = "'Noto Sans', system-ui, sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.borderColor = COL.line;
  const tip = { backgroundColor: "#ffffff", borderColor: COL.line2, borderWidth: 1, titleColor: COL.ink, bodyColor: COL.ink2, padding: 8, cornerRadius: 4, boxPadding: 4 };
  const tickMono = { font: { family: "'Noto Sans Mono', monospace", size: 10.5 } };

  // fig. 1: three small multiples on one shared time axis
  const t = S.timeline;
  const labels = t.map((r) => r.date);
  const xAxis = (show) => ({
    ticks: { display: show, maxRotation: 0, autoSkip: true, maxTicksLimit: 10, callback: (v, i) => dateShort(labels[i]), ...tickMono },
    grid: { display: false }, border: { color: COL.line2 },
  });
  const yAxis = (extra = {}) => ({ beginAtZero: true, grid: { color: COL.line }, border: { display: false }, ticks: { maxTicksLimit: 4, ...tickMono }, ...fixW, ...extra });
  const common = { maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
    plugins: { legend: { display: false }, tooltip: { ...tip, callbacks: { title: (c) => dateLong(labels[c[0].dataIndex]) } } } };

  new Chart(document.getElementById("ch-burn"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Burnt, undetected (ha)", data: t.map((r) => r.unseen_ha), backgroundColor: COL.undetected, borderColor: COL.surface,
          borderWidth: { top: 2 }, borderSkipped: false, maxBarThickness: 12, stack: "s" },
        { label: "Burnt, detected (ha)", data: t.map((r) => Math.max(0, +(r.burnt_ha - r.unseen_ha).toFixed(1))), backgroundColor: COL.detected,
          borderColor: COL.surface, borderWidth: { top: 2 }, borderRadius: { topLeft: 3, topRight: 3 }, borderSkipped: false, maxBarThickness: 12, stack: "s" },
      ],
    },
    options: { ...common, scales: { x: { ...xAxis(false), stacked: true }, y: { ...yAxis(), stacked: true } },
      plugins: { ...common.plugins, tooltip: { ...common.plugins.tooltip, callbacks: { ...common.plugins.tooltip.callbacks, label: (c) => ` ${c.dataset.label}: ${fmt(c.raw)}` } } } },
  });
  new Chart(document.getElementById("ch-det"), {
    type: "bar",
    data: { labels, datasets: [{ label: "Satellite fire detections", data: t.map((r) => r.firms), backgroundColor: COL.detection,
      borderRadius: { topLeft: 3, topRight: 3 }, borderSkipped: "bottom", maxBarThickness: 12 }] },
    options: { ...common, scales: { x: xAxis(false), y: yAxis() } },
  });
  new Chart(document.getElementById("ch-aqi"), {
    type: "line",
    data: { labels, datasets: [{ label: "Delhi AQI", data: t.map((r) => r.aqi_delhi), borderColor: COL.ink2, borderWidth: 2, pointRadius: 0,
      pointHoverRadius: 4, pointHoverBackgroundColor: COL.ink, tension: 0.2, spanGaps: true }] },
    options: { ...common, scales: { x: xAxis(true), y: yAxis({ max: 500, ticks: { stepSize: 250, ...tickMono } }) },
      plugins: { ...common.plugins, refLine: { value: 300, label: "Very poor (301+)" } } },
    plugins: [refLine],
  });
  const hasOfficial = t.some((r) => r.official_punjab != null);
  document.getElementById("tv-daily").innerHTML = `<thead><tr><th>Date</th><th class="n">Burnt (ha)</th><th class="n">Undetected (ha)</th>
    <th class="n">Detections</th>${hasOfficial ? '<th class="n">Official, Punjab</th>' : ""}<th class="n">Delhi AQI</th></tr></thead><tbody>` +
    t.map((r) => `<tr><td class="mono">${r.date}</td><td class="n">${fmt(r.burnt_ha, 1)}</td><td class="n">${fmt(r.unseen_ha, 1)}</td><td class="n">${fmt(r.firms)}</td>
      ${hasOfficial ? `<td class="n">${fmt(r.official_punjab)}</td>` : ""}<td class="n">${fmt(r.aqi_delhi)}</td></tr>`).join("") + "</tbody>";

  // fig. 2: official count vs measured burnt area, indexed to 2021 = 100 (one axis)
  const y = S.summary.years || {}, by = S.summary.burnt_by_year || {};
  const yrs = Object.keys(y).sort();
  const cnt = (k) => y[k].Sangrur + y[k].Barnala;
  const chg = (now, then) => { const d = Math.round((now / then - 1) * 100); return `${d < 0 ? "−" : "+"}${Math.abs(d)}%`; };  // one rounding for labels and caption
  const base = y["2021"] ? cnt("2021") : null;
  const hasBurnt = by["2021"] && Object.keys(by).length > 1;
  const offIdx = yrs.map((k) => (base ? +(cnt(k) / base * 100).toFixed(1) : null));
  const burntIdx = yrs.map((k) => (hasBurnt && by[k] ? +(by[k].burnt_ha / by["2021"].burnt_ha * 100).toFixed(1) : null));
  const last = yrs[yrs.length - 1];
  const lastBurnt = [...yrs].reverse().find((k) => hasBurnt && by[k]);
  const ds = [{ label: "Official fire events (index)", data: offIdx, borderColor: COL.detected, backgroundColor: COL.detected, borderWidth: 2,
    pointRadius: 4, pointHoverRadius: 6, pointBorderColor: COL.surface, pointBorderWidth: 2,
    endLabel: base ? `Official count ${chg(cnt(last), base)}` : null }];
  if (hasBurnt) ds.push({ label: "Burnt cropland (index)", data: burntIdx, borderColor: COL.ink2, backgroundColor: COL.ink2, borderWidth: 2,
    pointRadius: 4, pointHoverRadius: 6, pointBorderColor: COL.surface, pointBorderWidth: 2, spanGaps: true,
    endLabel: `Burnt area ${chg(by[lastBurnt].burnt_ha, by["2021"].burnt_ha)}` });
  else document.querySelector("#ch-years").closest(".panel").querySelector(".key-ink").parentElement.style.display = "none";
  new Chart(document.getElementById("ch-years"), {
    type: "line",
    data: { labels: yrs, datasets: ds },
    options: { maintainAspectRatio: false, animation: false, layout: { padding: { right: 128, top: 8 } }, interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false }, tooltip: { ...tip, callbacks: { label: (c) => ` ${c.dataset.label}: ${fmt(c.raw)}` } } },
      scales: { x: { grid: { display: false }, border: { color: COL.line2 }, ticks: tickMono },
        y: { beginAtZero: true, grid: { color: COL.line }, border: { display: false }, ticks: tickMono,
          title: { display: true, text: "Index (2021 = 100)", color: COL.muted, font: { size: 11 } } } } },
    plugins: [endLabels],
  });
  if (S.summary.live) {
    document.querySelector("#ch-years").closest(".panel").querySelector(".panel-note").textContent =
      `Official counts indexed to 2021 = 100, same window each year: 15 Sep – ${dateShort(S.summary.official_date || last)}.`;
  }
  if (base && last) {
    document.getElementById("years-note").textContent = hasBurnt
      ? `Official count 2021 → ${lastBurnt}: ${fmt(base)} → ${fmt(cnt(lastBurnt))} events (${chg(cnt(lastBurnt), base)}). Burnt cropland, same method: ${fmt(by["2021"].burnt_ha)} → ${fmt(by[lastBurnt].burnt_ha)} ha (${chg(by[lastBurnt].burnt_ha, by["2021"].burnt_ha)}).`
      : `Official count, same window: 2021 ${fmt(base)} → ${last} ${fmt(cnt(last))} events.`;
  }
  document.getElementById("tv-years").innerHTML = `<thead><tr><th>Year</th><th class="n">Official events</th><th class="n">Index</th>
    <th class="n">Burnt (ha)</th><th class="n">Index</th></tr></thead><tbody>` +
    yrs.map((k, i) => `<tr><td class="mono">${k}</td><td class="n">${fmt(cnt(k))}</td><td class="n">${fmt(offIdx[i], 1)}</td>
      <td class="n">${by[k] ? fmt(by[k].burnt_ha) : "–"}</td><td class="n">${burntIdx[i] != null ? fmt(burntIdx[i], 1) : "–"}</td></tr>`).join("") + "</tbody>";

  // fig. 3: when the official-count satellites detect fires (a), and what insat-3dr flagged (b)
  const h = S.hours, I = S.insat;
  const hrs = h.hours.map((x) => String(x).padStart(2, "0"));
  const BLIND = I ? I.blind_from : 16;
  const hourX = (show) => ({ grid: { display: false }, border: { color: COL.line2 },
    ticks: { display: show, ...tickMono, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
    title: { display: show, text: "Hour of day (IST)", color: COL.muted } });
  const hourY = { beginAtZero: true, grid: { color: COL.line }, border: { display: false }, ticks: { maxTicksLimit: 4, precision: 0, ...tickMono }, ...fixW };
  const hourChart = (id, data, color, unit, bandLabel, showX) => new Chart(document.getElementById(id), {
    type: "bar",
    data: { labels: hrs, datasets: [{ label: unit, data, backgroundColor: color, borderRadius: { topLeft: 2, topRight: 2 }, borderSkipped: "bottom", maxBarThickness: 18 }] },
    options: { maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, band: { from: String(BLIND), to: "23", label: bandLabel },
        tooltip: { ...tip, callbacks: { title: (c) => `${c[0].label}:00–${c[0].label}:59 IST`, label: (c) => ` ${fmt(c.raw)} ${c.dataset.label}` } } },
      scales: { x: hourX(showX), y: hourY } },
    plugins: [band],
  });
  hourChart("ch-hours", h.detections, COL.detection, "detections", "no polar-orbiting pass", true);
  // b: insat-3dr's fire pixels for every season, next to the official count
  if (I) {
    const rows = SEASONS.filter((s) => S.insatAll[s]);
    document.getElementById("tv-insat").innerHTML = `<thead><tr><th>Season</th><th class="n">Scans read</th><th class="n">INSAT fire pixels, Punjab</th>
      <th class="n">…in Sangrur + Barnala</th><th class="n">Official fire events, Punjab</th></tr></thead><tbody>` +
      rows.map((s) => { const x = S.insatAll[s]; return `<tr class="${s === SEASON ? "cur" : ""}"><td class="mono">${s}${s === LIVE_SEASON ? ", to date" : ""}</td>
        <td class="n">${fmt(x.scans)}${x.every_min > 30 ? "*" : ""}</td><td class="n strong">${fmt(x.punjab.pixels)}</td><td class="n">${fmt(x.study.pixels)}</td>
        <td class="n">${fmt(x.punjab_official)}</td></tr>`; }).join("") + "</tbody>";
  }

  const tot = h.detections.reduce((a, b) => a + b, 0);
  const polarLate = h.detections.slice(BLIND).reduce((a, b) => a + b, 0);
  let note = tot ? `The official-count satellites made ${fmt(tot)} detections here; ${polarLate ? fmt(polarLate) : "none"} after ${BLIND}:00 IST.` : "";
  if (I) {
    note += ` ISRO's INSAT-3DR scans every ${INSAT_EVERY} minutes, so it could watch those hours, but its published fire product flagged only ${fmt(I.punjab.pixels)} fire `
      + `pixel${I.punjab.pixels === 1 ? "" : "s"} over all of Punjab this season and ${I.study.pixels ? fmt(I.study.pixels) : "none"} in Sangrur or Barnala (B).`;
    const y21 = S.insatAll["2021"];
    if (y21 && SEASON !== "2021") note += ` Even in 2021, when the official count logged ${fmt(y21.punjab_official)} events in Punjab, it flagged ${fmt(y21.punjab.pixels)}.`;
    note += " The product was built for forest fires, and a burning field fills a sliver of its 4 km (about 1,600 ha) pixel.";
    if (Object.values(S.insatAll).some((x) => x && x.every_min > 30)) note += " *One scan an hour.";
  }
  document.getElementById("hours-note").textContent = note;
  document.getElementById("tv-hours").innerHTML = `<thead><tr><th>Hour (IST)</th><th class="n">VIIRS / MODIS detections</th>${I ? `<th class="n">INSAT-3DR fire pixels, Punjab</th>` : ""}</tr></thead><tbody>` +
    h.hours.map((x, i) => `<tr><td class="mono">${hrs[i]}:00</td><td class="n">${fmt(h.detections[i])}</td>${I ? `<td class="n">${fmt(I.punjab.by_hour[i])}</td>` : ""}</tr>`).join("") + "</tbody>";
}

// sources table
function provenance() {
  const p = [...(S.summary.provenance || [])];
  if (S.insat) p.push({ numbers: "INSAT-3DR fire pixels (Fig. 3, map)", source: `MOSDAC ${S.insat.dataset} (SAC/ISRO)`, url: "https://www.mosdac.gov.in/",
    via: `MOSDAC download API: ${fmt(S.insat.scans)} scans, ${S.insat.first.slice(0, 10)} to ${S.insat.last.slice(0, 10)}` });
  const via = (v) => (v || "").replace(/tinyfish[a-z ()0-9,-]*/gi, (m) => `<span class="tf">${m.replace(/tinyfish/i, "TinyFish")}</span>`);
  document.getElementById("prov").innerHTML = `<thead><tr><th>Figures</th><th>Source document</th><th>Retrieved with</th></tr></thead><tbody>` +
    p.map((r) => `<tr><td>${r.numbers[0].toUpperCase() + r.numbers.slice(1)}</td>
      <td>${r.url ? `<a href="${r.url}" target="_blank" rel="noopener">${r.source} ↗</a>` : r.source}</td>
      <td>${via(r.via)}${r.at ? `<br><span class="mono" style="color:var(--muted);font-size:11px">${r.at.replace("T", " ").slice(0, 16)} UTC</span>` : ""}</td></tr>`).join("") + "</tbody>";
}

// priority villages
function teams() {
  const h = S.hotspots;
  const sec = document.getElementById("teams");
  if (!h || !h.villages || !h.villages.length) { sec.style.display = "none"; return; }
  document.getElementById("t-csv").href = `${D}/hotspots.csv`;
  document.getElementById("t-zero").textContent = h.zero_detection_villages_50ha
    ? `${h.zero_detection_villages_50ha} villages had 50 ha or more of burnt cropland and no satellite detection all season.` : "";
  const rows = h.villages.slice(0, 15).map((v, i) => `<tr class="click" data-i="${i}">
      <td class="n">${v.rank}</td><td class="strong">${v.village}</td><td>${v.district}</td>
      <td class="n">${fmt(v.unseen_ha)}</td><td class="n">${fmt(v.burnt_ha)}</td><td class="n">${pct(v.unseen_share)}</td><td class="n">${fmt(v.detections)}</td></tr>`).join("");
  document.getElementById("t-table").innerHTML = `<thead><tr><th class="n">Rank</th><th>Village</th><th>District</th><th class="n">Undetected (ha)</th>
      <th class="n">Burnt (ha)</th><th class="n">Undetected share</th><th class="n">Satellite detections</th></tr></thead><tbody>${rows}</tbody>`;
  document.querySelectorAll("#t-table tbody tr").forEach((tr) => tr.addEventListener("click", () => {
    const v = h.villages[+tr.dataset.i];
    document.querySelector(".map-row").scrollIntoView({ behavior: "smooth" });
    window._flyTo(v.lat, v.lon, `<b>${v.village}</b>, ${v.district}<br>${fmt(v.unseen_ha)} ha burnt, undetected<br>${fmt(v.detections)} satellite detections this season`);
  }));
}

// enforcement news (tinyfish search + web agent)
const rs = (n) => (n >= 1e7 ? `₹${fmt(n / 1e7, 2)} cr` : n >= 1e5 ? `₹${fmt(n / 1e5, 2)} lakh` : `₹${fmt(n)}`);

async function response() {
  let n;
  try { n = await j(`${D}/news.json`); } catch { n = null; }
  const items = (n && n.items) || [];
  if (!items.length) { document.getElementById("response").style.display = "none"; return; }
  const fact = (v, label, money) => (v != null && v >= 0 ? `<div class="fact"><div class="fv">${money ? rs(v) : fmt(v)}</div><div class="fl">${label}</div></div>` : "");
  document.getElementById("resp-grid").innerHTML = items.filter((x) => x.facts).map((x) => {
    const f = x.facts;
    return `<div class="rcard"><a href="${x.url}" target="_blank" rel="noopener">${x.title}</a>
      <div class="meta">${x.source || ""} · extracted by TinyFish Web Agent in ${x.facts_steps || "?"} steps</div>
      <div class="facts">${fact(f.firs_registered, "FIRs")}${fact(f.red_entries, "Red entries")}${fact(f.fines_rs, "Fines", true)}${fact(f.officials_acted_against, "Officials served notice")}</div>
      <p>${f.summary || ""}</p>
      <div class="meta">${(f.districts || []).join(", ")}</div></div>`;
  }).join("") || `<p class="hint" style="padding:0 16px">No enforcement stories extracted for this season.</p>`;
  const rest = items.filter((x) => !x.facts);
  document.getElementById("resp-more").textContent = `${rest.length} further stories found by TinyFish Search`;
  document.getElementById("resp-list").innerHTML = rest.map((x) => `<div class="news-item"><a href="${x.url}" target="_blank" rel="noopener">${x.title}</a>
    <div class="meta">${(x.tags || []).map((t) => `<span class="ntag">${t}</span>`).join("")}${x.source || ""}</div></div>`).join("");
}

// live feeds (tinyfish)
const AQI_COL = { Good: "#55a84f", Satisfactory: "#a3c853", Moderate: "#fff833", Poor: "#f29c33", "Very Poor": "#e93f33", Severe: "#af2d24" };

function renderLive(d) {
  const st = document.getElementById("tf-status");
  st.className = `status ${d.tinyfish_key ? "ok" : "off"}`;
  st.innerHTML = `<i></i>${d.tinyfish_key ? "TinyFish connected" : "TinyFish key not set"}`;
  const via = (v) => `<div class="via-line">Retrieved via <b>${v === "tinyfish" ? "TinyFish Fetch" : v || "–"}</b></div>`;
  const c = d.creams || {};
  const yr = c.date ? String(new Date(c.date).getFullYear()) : null;
  document.querySelector("#l-creams .lbody").innerHTML = c.error ? `<p class="hint">${c.error}</p>` : c.date ? `
    <div><span class="big-num">${fmt(c.day_punjab)}</span><span class="big-unit">fire events, Punjab, ${dateLong(c.date)}</span></div>
    <table class="kv" style="width:100%;margin:10px 0 0">
      <tr><td>Punjab, season to date</td><td>${fmt(c.season_punjab)}</td></tr>
      <tr><td>Haryana, season to date</td><td>${fmt(c.season_haryana)}</td></tr>
      <tr><td>Sangrur, season to date</td><td>${fmt(c.district_cumulative?.Sangrur?.[yr])}</td></tr>
      <tr><td>Barnala, season to date</td><td>${fmt(c.district_cumulative?.Barnala?.[yr])}</td></tr>
      <tr><td>Punjab events listed with GPS</td><td>${fmt((c.punjab_events || []).length)}</td></tr></table>
    ${via(c.via)}<div class="via-line"><a href="${c.bulletin?.url}" target="_blank" rel="noopener">Open bulletin (PDF) ↗</a></div>` : `<p class="hint">Use refresh to retrieve today's bulletin.</p>`;

  const a = d.aqi || {};
  document.querySelector("#l-aqi .lbody").innerHTML = a.error ? `<p class="hint">${a.error}</p>` : a.aqi ? `
    <div><span class="big-num">${a.aqi}</span><span class="aqi-cat"><i style="background:${AQI_COL[a.category] || "#888"}"></i>${a.category}</span></div>
    <div class="lsub">Delhi, 24 h average at 16:00 IST, ${dateLong(a.date)}</div>
    <table class="kv" style="width:100%;margin:10px 0 0"><tr><td>Prominent pollutant</td><td>${a.pollutant}</td></tr>
      <tr><td>Stations reporting</td><td>${a.stations}</td></tr></table>
    ${via(a.via)}` : `<p class="hint">Use refresh to retrieve today's bulletin.</p>`;

  const n = d.news || [];
  document.querySelector("#l-news .lbody").innerHTML = n.length ? n.map((x) => `
    <div class="news-item"><a href="${x.url}" target="_blank" rel="noopener">${x.title}</a>
    <div class="meta">${(x.tags || []).map((t) => `<span class="ntag">${t}</span>`).join("")}${x.source || ""}${x.date ? " · " + String(x.date).slice(0, 10) : ""}</div></div>`).join("")
    : `<p class="hint">${d.tinyfish_key === false ? "Add TINYFISH_API_KEY to .env to search the news." : "Use refresh to search this week's news."}</p>`;
}

async function renderLog() {
  try {
    const rows = await j("/api/tinyfish/log?n=30");
    document.querySelector("#l-log .lbody").innerHTML = rows.length ? rows.map((r) => `
      <div class="log-row"><span class="api">${r.api.toUpperCase()}</span> ${r.ok ? "" : "<span class='bad'>FAILED</span>"} ${fmt(r.ms)} ms · ${r.t.slice(11, 19)} UTC<br>${r.what}</div>`).join("")
      : `<p class="hint">No calls yet.</p>`;
  } catch { document.querySelector("#l-log .lbody").innerHTML = `<p class="hint">Start the API server to see calls.</p>`; }
}

function renderInsatLive(I) {
  const el = document.querySelector("#l-insat .lbody");
  if (!I) { el.innerHTML = `<p class="hint">Run <code>scripts/12_insat_fires.py</code> to fetch ISRO's INSAT fire product.</p>`; return; }
  const day = I.last.slice(0, 10);
  const d = I.daily[day] || { punjab: 0, study: 0 };
  el.innerHTML = `<div><span class="big-num">${fmt(d.punjab)}</span><span class="big-unit">fire-pixel detections, Punjab, ${dateLong(day)}</span></div>
    <table class="kv" style="width:100%;margin:10px 0 0">
      <tr><td>Sangrur + Barnala, same day</td><td>${fmt(d.study)}</td></tr>
      <tr><td>Punjab, season to date</td><td>${fmt(I.punjab.pixels)}</td></tr>
      <tr><td>…of which after ${I.blind_from}:00 IST</td><td>${fmt(I.punjab.blind)} (${pct(I.punjab.blind_share)})</td></tr>
      <tr><td>Latest scan</td><td>${I.last.slice(11)} IST</td></tr></table>
    <div class="via-line">Retrieved via <b>MOSDAC download API</b> · ${I.dataset}, ${fmt(I.scans)} scans this season</div>`;
}

async function live() {
  renderInsatLive(S.insatAll[LIVE_SEASON] || null);
  try { renderLive(await j("/api/live")); }
  catch {
    renderLive({ tinyfish_key: false });
    document.querySelector("#l-creams .lbody").innerHTML = `<p class="hint">Static copy: run <code>uvicorn api.main:app</code> for live feeds.</p>`;
  }
  renderLog();
  const btn = document.getElementById("refresh");
  btn.addEventListener("click", async () => {
    btn.disabled = true; btn.textContent = "Retrieving bulletins…";
    try {
      const r = await fetch("/api/live/refresh", { method: "POST" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || r.status);
      renderLive(d);
    } catch (e) { alert(`Refresh failed: ${e.message}`); }
    btn.disabled = false; btn.textContent = "Refresh via TinyFish";
    renderLog();
  });
}

init().catch((e) => {
  console.error(e);
  document.getElementById("finding").innerHTML = `<span class="tag">Error</span><p>Could not load season data: ${e.message}</p>`;
});
