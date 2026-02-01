const fitgrabber = {
  // Track all chart elements and their data for synchronized zoom
  _charts: [],
  _points: null,
  _syncing: false,

  plotLayout(title) {
    return {
      title: { text: title, font: { size: 14 } },
      height: 250,
      margin: { t: 40, r: 20, b: 40, l: 50 },
      xaxis: { title: "Time (min)" },
      yaxis: {},
      hovermode: "x unified",
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      dragmode: "zoom",
    };
  },

  timeMinutes(points) {
    if (!points.length) return [];
    const t0 = new Date(points[0].timestamp).getTime();
    return points.map(p => (new Date(p.timestamp).getTime() - t0) / 60000);
  },

  _yRange(values, xRange, x) {
    let vals;
    if (xRange) {
      vals = values.filter((v, i) => v != null && x[i] >= xRange[0] && x[i] <= xRange[1]);
    } else {
      vals = values.filter(v => v != null);
    }
    if (!vals.length) return undefined;
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const pad = Math.max((max - min) * 0.05, max * 0.02, 1);
    return [min - pad, max + pad];
  },

  _syncGen: 0,

  _registerChart(el, x, y, opts) {
    const idx = this._charts.length;
    this._charts.push({ el, x, y, ...opts });

    el.on("plotly_relayout", (ev) => {
      // Ignore events triggered by our own sync relayouts
      if (ev._fitgrabberSync) return;

      let xRange = null;
      let isReset = false;
      if (ev["xaxis.range[0]"] !== undefined) {
        xRange = [ev["xaxis.range[0]"], ev["xaxis.range[1]"]];
      } else if (ev["xaxis.autorange"]) {
        isReset = true;
      } else {
        return; // ignore unrelated relayout events (e.g. pure Y changes)
      }

      const gen = ++this._syncGen;

      for (let i = 0; i < this._charts.length; i++) {
        const chart = this._charts[i];
        const yr = this._yRange(chart.y, xRange, chart.x);
        const yRange = yr
          ? (chart.reverseY ? [yr[1], yr[0]] : yr)
          : undefined;

        if (i === idx) {
          // Originating chart: only update Y axis (X already set by user drag)
          if (yRange) {
            chart.el.layout.yaxis.range = yRange;
            chart.el.layout.yaxis.autorange = false;
            Plotly.redraw(chart.el);
          }
        } else {
          const update = { _fitgrabberSync: true };
          if (isReset) {
            update["xaxis.autorange"] = true;
          } else {
            update["xaxis.range[0]"] = xRange[0];
            update["xaxis.range[1]"] = xRange[1];
          }
          if (yRange) {
            update["yaxis.range"] = yRange;
            update["yaxis.autorange"] = false;
          }
          Plotly.relayout(chart.el, update);
        }
      }

      if (gen === this._syncGen) {
        this._updateMetrics(xRange);
      }
    });
  },

  _updateMetrics(xRange) {
    const pts = this._points;
    if (!pts || !pts.length) return;

    const x = this._timeX;
    let subset;
    if (xRange) {
      subset = pts.filter((_, i) => x[i] >= xRange[0] && x[i] <= xRange[1]);
    } else {
      subset = pts;
    }
    if (!subset.length) return;

    const avg = (field) => {
      const vals = subset.filter(p => p[field] != null).map(p => p[field]);
      return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    };
    const max = (field) => {
      const vals = subset.filter(p => p[field] != null).map(p => p[field]);
      return vals.length ? Math.max(...vals) : null;
    };

    // Distance: difference of distance field across subset
    const distVals = subset.filter(p => p.distance != null).map(p => p.distance);
    let dist = null;
    if (distVals.length >= 2) {
      dist = distVals[distVals.length - 1] - distVals[0];
    } else if (distVals.length === 1) {
      dist = distVals[0];
    }

    // Duration from timestamps
    let dur = null;
    try {
      const t0 = new Date(subset[0].timestamp).getTime();
      const t1 = new Date(subset[subset.length - 1].timestamp).getTime();
      dur = (t1 - t0) / 1000;
    } catch(e) {}

    const avgSpeed = avg("speed");
    const avgHr = avg("heart_rate");
    const maxHr = max("heart_rate");
    const avgCad = avg("cadence");
    const avgPow = avg("power");

    const el = (id) => document.getElementById(id);
    const set = (id, val) => { const e = el(id); if (e) e.textContent = val; };

    set("metric-distance", dist != null ? (dist / 1609.344).toFixed(1) + " mi" : "-");
    set("metric-duration", dur != null ? fitgrabber._fmtDuration(dur) : "-");
    set("metric-pace", avgSpeed && avgSpeed > 0 ? fitgrabber._fmtPace(avgSpeed) : "-");
    set("metric-avg_hr", avgHr != null ? Math.round(avgHr) + " bpm" : "-");
    set("metric-max_hr", maxHr != null ? Math.round(maxHr) + " bpm" : "-");
    set("metric-avg_cadence", avgCad != null ? Math.round(avgCad) + " spm" : "-");
    set("metric-avg_power", avgPow != null ? Math.round(avgPow) + " W" : "-");

    // Calories: estimate proportionally if we have duration ratio
    // (can't compute exactly without full data, so skip or show "-" when zoomed)
    if (xRange) {
      set("metric-calories", "-");
    }
  },

  _fmtDuration(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h) return `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
    return `${m}:${String(s).padStart(2,"0")}`;
  },

  _fmtPace(speedMs) {
    const paceS = 1609.344 / speedMs;
    const m = Math.floor(paceS / 60);
    const s = Math.floor(paceS % 60);
    return `${m}:${String(s).padStart(2,"0")} /mi`;
  },

  renderChart(elementId, x, y, title, yLabel, color) {
    const el = document.getElementById(elementId);
    if (!el || !y.some(v => v !== null)) return;
    const layout = this.plotLayout(title);
    const yr = this._yRange(y, null, x);
    layout.yaxis = { title: yLabel, range: yr, fixedrange: true };
    Plotly.newPlot(el, [{
      x, y, type: "scattergl", mode: "lines",
      line: { color, width: 1.5 },
      hovertemplate: `%{y:.1f} ${yLabel}<extra></extra>`,
    }], layout, { responsive: true, displayModeBar: false, scrollZoom: false });
    this._registerChart(el, x, y, { reverseY: false });
  },

  renderActivityCharts(points) {
    this._charts = [];
    this._points = points;
    const x = this.timeMinutes(points);
    this._timeX = x;

    // Heart Rate
    const hrEl = document.getElementById("chart-hr");
    const hrY = points.map(p => p.heart_rate);
    if (hrEl && hrY.some(v => v != null)) {
      const yr = this._yRange(hrY, null, x);
      const layout = this.plotLayout("Heart Rate");
      layout.yaxis = { title: "bpm", range: yr, fixedrange: true };
      Plotly.newPlot(hrEl, [{
        x, y: hrY, type: "scattergl", mode: "lines",
        line: { color: "#e74c3c", width: 1.5 },
        hovertemplate: "%{y:.0f} bpm<extra></extra>",
      }], layout, { responsive: true, displayModeBar: false, scrollZoom: false });
      this._registerChart(hrEl, x, hrY, { reverseY: false });
    }

    // Pace (inverted: lower = faster at top)
    const pace = points.map(p => p.speed && p.speed > 0 ? (1609.344 / p.speed) / 60 : null);
    const paceEl = document.getElementById("chart-pace");
    if (paceEl && pace.some(v => v !== null)) {
      const yr = this._yRange(pace, null, x);
      const layout = this.plotLayout("Pace");
      layout.yaxis = { title: "min/mi", range: yr ? [yr[1], yr[0]] : undefined, fixedrange: true };
      Plotly.newPlot(paceEl, [{
        x, y: pace, type: "scattergl", mode: "lines",
        line: { color: "#3498db", width: 1.5 },
        hovertemplate: "%{y:.2f} min/mi<extra></extra>",
      }], layout, { responsive: true, displayModeBar: false, scrollZoom: false });
      this._registerChart(paceEl, x, pace, { reverseY: true });
    }

    // Elevation
    const elev = points.map(p => p.altitude);
    const elevEl = document.getElementById("chart-elevation");
    if (elevEl && elev.some(v => v != null)) {
      const yr = this._yRange(elev, null, x);
      const layout = this.plotLayout("Elevation");
      layout.yaxis = { title: "m", range: yr, fixedrange: true };
      Plotly.newPlot(elevEl, [{
        x, y: elev, type: "scattergl", mode: "lines",
        line: { color: "#2ecc71", width: 1.5 },
        hovertemplate: "%{y:.1f} m<extra></extra>",
      }], layout, { responsive: true, displayModeBar: false, scrollZoom: false });
      this._registerChart(elevEl, x, elev, { reverseY: false });
    }

    // Power
    const pow = points.map(p => p.power);
    const powEl = document.getElementById("chart-power");
    if (powEl && pow.some(v => v != null)) {
      const yr = this._yRange(pow, null, x);
      const layout = this.plotLayout("Power");
      layout.yaxis = { title: "W", range: yr, fixedrange: true };
      Plotly.newPlot(powEl, [{
        x, y: pow, type: "scattergl", mode: "lines",
        line: { color: "#9b59b6", width: 1.5 },
        hovertemplate: "%{y:.1f} W<extra></extra>",
      }], layout, { responsive: true, displayModeBar: false, scrollZoom: false });
      this._registerChart(powEl, x, pow, { reverseY: false });
    }

    // Cadence
    const cad = points.map(p => p.cadence);
    const cadEl = document.getElementById("chart-cadence");
    if (cadEl && cad.some(v => v != null)) {
      const yr = this._yRange(cad, null, x);
      const layout = this.plotLayout("Cadence");
      layout.yaxis = { title: "spm", range: yr, fixedrange: true };
      Plotly.newPlot(cadEl, [{
        x, y: cad, type: "scattergl", mode: "lines",
        line: { color: "#e67e22", width: 1.5 },
        hovertemplate: "%{y:.1f} spm<extra></extra>",
      }], layout, { responsive: true, displayModeBar: false, scrollZoom: false });
      this._registerChart(cadEl, x, cad, { reverseY: false });
    }
  },

  renderMap(points) {
    const el = document.getElementById("map");
    if (!el) return;
    const coords = points.filter(p => p.latitude && p.longitude).map(p => [p.latitude, p.longitude]);
    if (!coords.length) return;
    const map = L.map("map");
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxZoom: 18,
    }).addTo(map);
    const line = L.polyline(coords, { color: "#3498db", weight: 3 }).addTo(map);
    map.fitBounds(line.getBounds(), { padding: [20, 20] });
    L.circleMarker(coords[0], { radius: 6, color: "#2ecc71", fillOpacity: 1 }).addTo(map).bindPopup("Start");
    L.circleMarker(coords[coords.length - 1], { radius: 6, color: "#e74c3c", fillOpacity: 1 }).addTo(map).bindPopup("Finish");
  },
};

// Dark mode toggle
(function() {
  const saved = localStorage.getItem("theme");
  if (saved) document.documentElement.setAttribute("data-bs-theme", saved);

  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("theme-toggle");
    if (!btn) return;
    const icon = btn.querySelector("i");
    const update = () => {
      const dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
      icon.className = dark ? "bi bi-sun-fill" : "bi bi-moon-fill";
    };
    update();
    btn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-bs-theme");
      const next = current === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-bs-theme", next);
      localStorage.setItem("theme", next);
      update();
    });
  });
})();

// Keyboard navigation on activity detail pages (j/k for prev/next)
(function() {
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    // Activity list: j/k to navigate rows
    const rows = document.querySelectorAll("table.table-hover tbody tr[onclick]");
    if (rows.length > 0) {
      const focused = document.querySelector("tr.table-active");
      let idx = focused ? Array.from(rows).indexOf(focused) : -1;
      if (e.key === "j") {
        idx = Math.min(idx + 1, rows.length - 1);
      } else if (e.key === "k") {
        idx = Math.max(idx - 1, 0);
      } else if (e.key === "Enter" && focused) {
        focused.click();
        return;
      } else {
        return;
      }
      rows.forEach(r => r.classList.remove("table-active"));
      rows[idx].classList.add("table-active");
      rows[idx].scrollIntoView({ block: "nearest" });
    }
  });
})();
