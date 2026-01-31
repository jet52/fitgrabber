const fitgrabber = {
  plotLayout(title) {
    return {
      title: { text: title, font: { size: 14 } },
      height: 250,
      margin: { t: 40, r: 20, b: 40, l: 50 },
      xaxis: { title: "Time (min)" },
      hovermode: "x unified",
    };
  },

  timeMinutes(points) {
    if (!points.length) return [];
    const t0 = new Date(points[0].timestamp).getTime();
    return points.map(p => (new Date(p.timestamp).getTime() - t0) / 60000);
  },

  renderChart(elementId, x, y, title, yLabel, color) {
    const el = document.getElementById(elementId);
    if (!el || !y.some(v => v !== null)) return;
    const layout = this.plotLayout(title);
    layout.yaxis = { title: yLabel };
    Plotly.newPlot(el, [{
      x, y, type: "scattergl", mode: "lines",
      line: { color, width: 1.5 },
      hovertemplate: `%{y:.1f} ${yLabel}<extra></extra>`,
    }], layout, { responsive: true, displayModeBar: false });
  },

  renderActivityCharts(points) {
    const x = this.timeMinutes(points);
    this.renderChart("chart-hr", x, points.map(p => p.heart_rate), "Heart Rate", "bpm", "#e74c3c");
    // Convert m/s to min/mi for pace (invert, so lower = faster shown at top)
    const pace = points.map(p => p.speed && p.speed > 0 ? (1609.344 / p.speed) / 60 : null);
    const paceEl = document.getElementById("chart-pace");
    if (paceEl && pace.some(v => v !== null)) {
      const layout = this.plotLayout("Pace");
      layout.yaxis = { title: "min/mi", autorange: "reversed" };
      Plotly.newPlot(paceEl, [{
        x, y: pace, type: "scattergl", mode: "lines",
        line: { color: "#3498db", width: 1.5 },
        hovertemplate: "%{y:.2f} min/mi<extra></extra>",
      }], layout, { responsive: true, displayModeBar: false });
    }
    this.renderChart("chart-elevation", x, points.map(p => p.altitude), "Elevation", "m", "#2ecc71");
    this.renderChart("chart-power", x, points.map(p => p.power), "Power", "W", "#9b59b6");
    this.renderChart("chart-cadence", x, points.map(p => p.cadence), "Cadence", "spm", "#e67e22");
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
