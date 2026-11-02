// ---------- Estado ----------
let especiePrincipal = null;
const seleccionadas = new Set();
let mapaFormulario = null;
let marcadorFormulario = null;
let coordsSeleccionadas = null;
let mapa = null;
let capaResultados = null;
let ESPECIES = [];

// ---------- Elementos ----------
const $ = (id) => document.getElementById(id);
const listaEspecies = $("lista-especies");
const buscador = $("buscador-especies");
const btnConsultar = $("btn-consultar");
const acepto = $("acepto-aviso");
const vistaFormulario = $("vista-formulario");
const vistaResultados = $("vista-resultados");

// ---------- Carga de especies ----------
async function cargarEspecies() {
  try {
    const resp = await fetch("/api/especies");
    if (!resp.ok) throw new Error("Error al cargar especies");
    ESPECIES = await resp.json();
    renderEspecies();
  } catch (err) {
    console.error("No se pudieron cargar las especies:", err);
    listaEspecies.innerHTML = '<p style="color:#c62828">No se pudieron cargar las especies.</p>';
  }
}

function renderEspecies(filtro = "") {
  const f = filtro.toLowerCase();
  listaEspecies.innerHTML = "";
  ESPECIES
    .filter(e => e.nombre.toLowerCase().includes(f) || e.cientifico.toLowerCase().includes(f))
    .forEach(e => {
      const div = document.createElement("label");
      div.className = "especie-item" + (especiePrincipal === e.id ? " principal" : "");
      div.innerHTML =
        '<input type="checkbox" data-id="' + e.id + '" ' + (seleccionadas.has(e.id) ? "checked" : "") + ">" +
        e.nombre +
        ' <small>' + e.cientifico + '</small>' +
        (especiePrincipal === e.id ? " ★" : "");
      listaEspecies.appendChild(div);
    });
}

// ---------- Mapa del formulario ----------
function inicializarMapaFormulario() {
  if (mapaFormulario) return;
  mapaFormulario = L.map("mapa-formulario", {
    center: [40.4168, -3.7038],
    zoom: 6,
  });

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "OpenStreetMap",
    maxZoom: 19,
  }).addTo(mapaFormulario);

  mapaFormulario.on("click", (ev) => {
    seleccionarPunto(ev.latlng.lat, ev.latlng.lng, false);
  });
}

function seleccionarPunto(lat, lng, centrar = false) {
  if (!mapaFormulario) inicializarMapaFormulario();
  if (marcadorFormulario) mapaFormulario.removeLayer(marcadorFormulario);
  marcadorFormulario = L.marker([lat, lng], { draggable: true }).addTo(mapaFormulario);
  marcadorFormulario.on("dragend", (ev) => {
    const p = ev.target.getLatLng();
    seleccionarPunto(p.lat, p.lng, false);
  });
  coordsSeleccionadas = { lat, lng };
  const el = $("coords-seleccionadas");
  if (el) el.textContent = "Punto seleccionado: " + lat.toFixed(4) + ", " + lng.toFixed(4);
  if (centrar) mapaFormulario.setView([lat, lng], 12);
  actualizarBoton();
}

function usarMiUbicacion() {
  const btn = $("btn-mi-ubicacion");
  if (!navigator.geolocation) {
    alert("Tu navegador no soporta geolocalización.");
    return;
  }
  btn.textContent = "Obteniendo...";
  btn.disabled = true;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const { latitude, longitude } = pos.coords;
      seleccionarPunto(latitude, longitude, true);
      btn.textContent = "Usar mi ubicación";
      btn.disabled = false;
    },
    (err) => {
      console.error(err);
      alert("No se pudo obtener la ubicación.");
      btn.textContent = "Usar mi ubicación";
      btn.disabled = false;
    }
  );
}

async function buscarUbicacion() {
  const q = $("ubicacion").value.trim();
  if (!q) return;
  try {
    const resp = await fetch(
      "https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=es&q=" + encodeURIComponent(q)
    );
    const data = await resp.json();
    if (data.length > 0) {
      const { lat, lon } = data[0];
      seleccionarPunto(parseFloat(lat), parseFloat(lon), true);
    } else {
      alert("No se encontró la ubicación.");
    }
  } catch (e) {
    console.error(e);
    alert("Error buscando la ubicación.");
  }
}

// ---------- Eventos del formulario ----------
buscador.addEventListener("input", () => renderEspecies(buscador.value));

listaEspecies.addEventListener("change", (ev) => {
  if (ev.target.type !== "checkbox") return;
  const id = ev.target.dataset.id;
  if (ev.target.checked) {
    seleccionadas.add(id);
    if (!especiePrincipal) especiePrincipal = id;
  } else {
    seleccionadas.delete(id);
    if (especiePrincipal === id) {
      especiePrincipal = seleccionadas.size ? [...seleccionadas][0] : null;
    }
  }
  renderEspecies(buscador.value);
  actualizarBoton();
});

acepto.addEventListener("change", actualizarBoton);
$("ubicacion").addEventListener("input", actualizarBoton);
$("ubicacion").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    buscarUbicacion();
  }
});

function actualizarBoton() {
  const ok = acepto.checked && seleccionadas.size > 0 && coordsSeleccionadas !== null;
  btnConsultar.disabled = !ok;
}

// ---------- Consulta al backend ----------
async function consultarBackend(lat, lng, principal) {
  const resp = await fetch("/api/consulta", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      lat: lat,
      lng: lng,
      especie_principal: principal,
    }),
  });
  if (!resp.ok) {
    let detalle = "HTTP " + resp.status;
    try {
      const err = await resp.json();
      detalle = err.detail || err.error || detalle;
    } catch (_) {}
    throw new Error(detalle);
  }
  return await resp.json();
}

function mostrarCarga(texto) {
  const el = $("overlay-texto");
  if (el) el.textContent = texto || "Analizando la zona...";
  const ov = $("overlay-carga");
  if (ov) ov.classList.remove("oculto");
}

function ocultarCarga() {
  const ov = $("overlay-carga");
  if (ov) ov.classList.add("oculto");
}

btnConsultar.addEventListener("click", async () => {
  if (!coordsSeleccionadas) return;
  btnConsultar.disabled = true;
  mostrarCarga("Analizando la zona...");

  try {
    const { lat, lng } = coordsSeleccionadas;
    const datos = await consultarBackend(lat, lng, especiePrincipal);

    if (!datos) throw new Error("El servidor no devolvió datos.");
    if (datos.error || datos.detail) throw new Error(datos.error || datos.detail);

    ocultarCarga();
    mostrarResultados(datos, { lat, lng });
  } catch (err) {
    ocultarCarga();
    console.error("Fallo en consulta:", err);
    alert("No se pudo completar la consulta: " + (err && err.message ? err.message : err));
  } finally {
    btnConsultar.textContent = "Consultar probabilidad";
    actualizarBoton();
  }
});

// ---------- Resultados ----------
function mostrarResultados(datos, punto) {
  vistaFormulario.classList.add("oculto");
  vistaResultados.classList.remove("oculto");

  if (mapa) mapa.remove();
  mapa = L.map("mapa", { zoomControl: true, scrollWheelZoom: true })
    .setView(datos.centro || [punto.lat, punto.lng], 11);

    const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "OpenStreetMap",
    maxZoom: 19,
  }).addTo(mapa);

  const topo = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
    attribution: "OpenTopoMap (CC-BY-SA)",
    maxZoom: 17,
  });

  L.control.layers(
    { "Calles (OSM)": osm, "Topografico": topo },
    null,
    { position: "topright" }
  ).addTo(mapa);

  capaResultados = L.featureGroup().addTo(mapa);

  const PALETA = {
    verde: { fill: "#2e7d32", stroke: "#1b5e20" },
    amarillo: { fill: "#f9a825", stroke: "#f57f17" },
  };

  if (datos.geojson && datos.geojson.features && datos.geojson.features.length > 0) {
    L.geoJSON(datos.geojson, {
      style: function (feature) {
        const p = PALETA[feature.properties.color] || { fill: "#888", stroke: "#555" };
        return { color: p.stroke, weight: 1.5, fillColor: p.fill, fillOpacity: 0.35 };
      },
      onEachFeature: function (feature, layer) {
        const p = feature.properties;
        const pct = Math.round((p.prob || 0) * 100);
        const colorTxt = p.color === "verde" ? "Alta" : "Media";
        const popupHtml =
          '<div style="min-width:180px">' +
            '<strong style="text-transform:capitalize">' + p.habitat + '</strong><br>' +
            '<span style="color:#2e7d32;font-weight:600">' + colorTxt + '</span> - ' + pct + '%<br>' +
            
            '<hr style="margin:6px 0;border:none;border-top:1px solid #ddd">' +
            'Lluvia ultimos 2 meses: <strong>' + (p.lluvia_60d != null ? p.lluvia_60d : "-") + ' mm</strong><br>' +
            'Lluvia ultimos 20 dias: <strong>' + (p.lluvia_20d != null ? p.lluvia_20d : "-") + ' mm</strong><br>' +
            'Dias de lluvia (20d): <strong>' + (p.dias_lluvia_20d != null ? p.dias_lluvia_20d : "-") + '</strong><br>' +
            'Temperatura minima ultimos 20 dias: <strong>' + (p.t_min_media != null ? p.t_min_media : "-") + ' C</strong><br>' +
           
            'Heladas fuertes: <strong>' + (p.heladas_fuertes ? "Si" : "No") + '</strong><br>' +
            (p.altitud ? 'Altitud: <strong>' + Math.round(p.altitud) + ' m</strong><br>' : '') +
            '<em style="color:#666;font-size:0.85em">' + (p.motivo || '') + '</em>' +
          '</div>';
        layer.bindPopup(popupHtml);
        layer.on("mouseover", function () { layer.setStyle({ weight: 3, fillOpacity: 0.5 }); });
        layer.on("mouseout", function () { layer.setStyle({ weight: 1.5, fillOpacity: 0.35 }); });
      },
    }).addTo(capaResultados);

    if (capaResultados && typeof capaResultados.getBounds === "function") {
      const bounds = capaResultados.getBounds();
      if (bounds.isValid()) {
        mapa.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 });
      }
    }
  }

  const resumenEl = $("texto-resumen");
  if (resumenEl) resumenEl.textContent = datos.resumen || "Sin resumen disponible.";

  const lista = $("lista-candidatas");
  lista.innerHTML = "";
  const features = (datos.geojson && datos.geojson.features ? datos.geojson.features : [])
    .slice()
    .sort((a, b) => b.properties.prob - a.properties.prob);

  if (features.length === 0) {
    lista.innerHTML = "<li>No se han encontrado zonas favorables en el radio consultado.</li>";
  } else {
    features.forEach(function (f, i) {
      const p = f.properties;
      const coords = f.geometry.coordinates.flat(2);
      const lngs = coords.filter(function (_, idx) { return idx % 2 === 0; });
      const lats = coords.filter(function (_, idx) { return idx % 2 === 1; });
      const lat = (Math.min.apply(null, lats) + Math.max.apply(null, lats)) / 2;
      const lng = (Math.min.apply(null, lngs) + Math.max.apply(null, lngs)) / 2;

      const etiqueta = p.color === "verde" ? "Alta" : "Media";
      const li = document.createElement("li");
      li.innerHTML =
        "<strong>" + (i + 1) + ". " + p.habitat + "</strong> - " + etiqueta +
        " (" + Math.round(p.prob * 100) + "%)";
      li.style.cursor = "pointer";
      li.addEventListener("click", function () {
        mapa.setView([lat, lng], 14);
      });
      lista.appendChild(li);
    });
  }

  setTimeout(function () { mapa.invalidateSize(); }, 100);
}

// ---------- Nueva consulta ----------
const btnNueva = $("btn-nueva");
if (btnNueva) {
  btnNueva.addEventListener("click", () => {
    vistaResultados.classList.add("oculto");
    vistaFormulario.classList.remove("oculto");
    btnConsultar.textContent = "Consultar probabilidad";
    if (mapaFormulario) setTimeout(() => mapaFormulario.invalidateSize(), 100);
    actualizarBoton();
  });
}

// ---------- Init ----------
cargarEspecies();
inicializarMapaFormulario();
actualizarBoton();