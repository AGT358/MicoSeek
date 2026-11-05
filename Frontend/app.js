// ---------- Estado ----------
let especiePrincipal = null;
let mapaFormulario = null;
let marcadorFormulario = null;
let coordsSeleccionadas = null;
let mapa = null;
let capaResultados = null;
let ESPECIES = [];

// ---------- Elementos ----------
const $ = (id) => document.getElementById(id);
const btnConsultar = $("btn-consultar");
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
    const sel = $("selector-especie");
    if (sel) sel.innerHTML = '<option value="">Error al cargar especies</option>';
  }
}

function renderEspecies() {
  const sel = $("selector-especie");
  if (!sel) return;
  sel.innerHTML = '<option value="">— Elegir especie —</option>';
  ESPECIES.forEach(e => {
    const opt = document.createElement("option");
    opt.value = e.id;
    opt.textContent = e.nombre + " (" + e.cientifico + ")";
    if (especiePrincipal === e.id) opt.selected = true;
    sel.appendChild(opt);
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

  setTimeout(() => mapaFormulario.invalidateSize(), 100);
}

function seleccionarPunto(lat, lng, centrar = false) {
  if (!mapaFormulario) inicializarMapaFormulario();
  if (marcadorFormulario) mapaFormulario.removeLayer(marcadorFormulario);
  marcadorFormulario = L.marker([lat, lng], { draggable: true }).addTo(mapaFormulario);
  marcadorFormulario.on("dragend", (ev) => {
    const p = ev.target.getLatLng();
    seleccionarPunto(p.lat, p.lng, false);
  });
  coordsSeleccionadas = { lat: lat, lng: lng };
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
      const lat = pos.coords.latitude;
      const lng = pos.coords.longitude;
      seleccionarPunto(lat, lng, true);
      btn.textContent = "📍 Usar mi ubicación";
      btn.disabled = false;
    },
    (err) => {
      console.error(err);
      alert("No se pudo obtener la ubicación.");
      btn.textContent = "📍 Usar mi ubicación";
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
      const lat = parseFloat(data[0].lat);
      const lon = parseFloat(data[0].lon);
      seleccionarPunto(lat, lon, true);
    } else {
      alert("No se encontró la ubicación.");
    }
  } catch (e) {
    console.error(e);
    alert("Error buscando la ubicación.");
  }
}

// ---------- Eventos del formulario ----------
const selectorEspecie = $("selector-especie");
if (selectorEspecie) {
  selectorEspecie.addEventListener("change", (ev) => {
    especiePrincipal = ev.target.value || null;
    actualizarBoton();
  });
}

const btnMiUbicacion = $("btn-mi-ubicacion");
if (btnMiUbicacion) btnMiUbicacion.addEventListener("click", usarMiUbicacion);

const btnBuscarUbicacion = $("btn-buscar-ubicacion");
if (btnBuscarUbicacion) btnBuscarUbicacion.addEventListener("click", buscarUbicacion);

const inputUbicacion = $("ubicacion");
if (inputUbicacion) {
  inputUbicacion.addEventListener("input", actualizarBoton);
  inputUbicacion.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      buscarUbicacion();
    }
  });
}

function actualizarBoton() {
  const ok = especiePrincipal !== null && coordsSeleccionadas !== null;
  if (btnConsultar) btnConsultar.disabled = !ok;
}

// ---------- Selección por tarjetas de ecosistema ----------
document.querySelectorAll('.tarjeta-ecosistema').forEach(tarjeta => {
  tarjeta.addEventListener('click', () => {
    const especieId = tarjeta.dataset.especie;
    const selector = $("selector-especie");
    if (selector && especieId) {
      selector.value = especieId;
      especiePrincipal = especieId;
      selector.dispatchEvent(new Event("change"));
      document.querySelectorAll('.tarjeta-ecosistema').forEach(t => t.classList.remove('seleccionada'));
      tarjeta.classList.add('seleccionada');
    }
  });
});

// ---------- Consulta al backend ----------
async function consultarBackend(lat, lng, principal, confirmarTemporada) {
  const resp = await fetch("/api/consulta", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      lat: lat,
      lng: lng,
      especie_principal: principal,
      confirmar_temporada: !!confirmarTemporada,
    }),
  });
  if (!resp.ok) {
    let detalle = "HTTP " + resp.status;
    try {
      const err = await resp.json();
      detalle = err.detail || err.error || detalle;
    } catch (_) { }
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

function mostrarModalTemporada(texto) {
  return new Promise((resolve) => {
    const modal = $("modal-temporada");
    const textoEl = $("modal-temporada-texto");
    const btnCancelar = $("btn-cancelar-temporada");
    const btnConfirmar = $("btn-confirmar-temporada");

    textoEl.textContent = texto;
    modal.classList.remove("oculto");

    const cerrar = (confirmado) => {
      modal.classList.add("oculto");
      btnCancelar.removeEventListener("click", onCancelar);
      btnConfirmar.removeEventListener("click", onConfirmar);
      resolve(confirmado);
    };

    const onCancelar = () => cerrar(false);
    const onConfirmar = () => cerrar(true);

    btnCancelar.addEventListener("click", onCancelar);
    btnConfirmar.addEventListener("click", onConfirmar);
  });
}

if (btnConsultar) {
  btnConsultar.addEventListener("click", async () => {
    if (!coordsSeleccionadas) return;
    btnConsultar.disabled = true;
    mostrarCarga("Analizando la zona...");

    try {
      const lat = coordsSeleccionadas.lat;
      const lng = coordsSeleccionadas.lng;
      const principal = especiePrincipal;

      let datos = await consultarBackend(lat, lng, principal, false);

      if (!datos) throw new Error("El servidor no devolvió datos.");
      if (datos.error || datos.detail) throw new Error(datos.error || datos.detail);

      if (datos.requiere_confirmacion) {
        ocultarCarga();
        const continuar = await mostrarModalTemporada(datos.aviso || "Especie fuera de temporada.");
        if (!continuar) {
          actualizarBoton();
          return;
        }
        mostrarCarga("Recalculando con la especie fuera de temporada...");
        datos = await consultarBackend(lat, lng, principal, true);
        if (!datos) throw new Error("El servidor no devolvió datos en el recálculo.");
        if (datos.error || datos.detail) throw new Error(datos.error || datos.detail);
      }

      ocultarCarga();
      mostrarResultados(datos, { lat: lat, lng: lng });
    } catch (err) {
      ocultarCarga();
      console.error("Fallo en consulta:", err);
      alert("No se pudo completar la consulta: " + (err && err.message ? err.message : err));
    } finally {
      btnConsultar.textContent = "Consultar probabilidad";
      actualizarBoton();
    }
  });
}

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

  const tracestrack = L.tileLayer("/api/tiles/tracestrack/{z}/{x}/{y}.png", {
    attribution: "Tracestrack",
    maxZoom: 17,
  });

  const pnoa = L.tileLayer(
    "https://www.ign.es/wmts/pnoa-ma?request=GetTile&service=WMTS&version=1.0.0" +
    "&layer=OI.OrthoimageCoverage&style=default&tilematrixset=GoogleMapsCompatible" +
    "&format=image/jpeg&tilematrix={z}&tilerow={y}&tilecol={x}",
    {
      attribution: "PNOA © IGN",
      maxZoom: 19,
      opacity: 0.7,
    }
  );

  L.control.layers(
    { "Calles (OSM)": osm, "Tracestrack": tracestrack },
    { "Ortofoto (PNOA)": pnoa },
    { position: "topright" }
  ).addTo(mapa);

  capaResultados = L.featureGroup().addTo(mapa);

  const PALETA = {
    verde: { fill: "#4ef108", stroke: "#4ef108" },
    amarillo: { fill: "#FFA500", stroke: "#CC8400" },
    rojo: { fill: "#FF0000", stroke: "#CC0000" },
  };

  if (datos.geojson && datos.geojson.features && datos.geojson.features.length > 0) {
    L.geoJSON(datos.geojson, {
      style: function (feature) {
        const p = PALETA[feature.properties.color] || { fill: "#888", stroke: "#555" };
        return { color: p.stroke, weight: 1.5, fillColor: p.fill, fillOpacity: 0.25 };
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
          'Geología: suelo <strong>' + (p.geologia || "-") + '</strong><br>' +
          (p.altitud ? 'Altitud: <strong>' + Math.round(p.altitud) + ' m</strong><br>' : '') +
          '<em style="color:#666;font-size:0.85em">' + (p.motivo || '') + '</em>' +
          '</div>';
        layer.bindPopup(popupHtml);
        layer.on("mouseover", function () { layer.setStyle({ weight: 3, fillOpacity: 0.4 }); });
        layer.on("mouseout", function () { layer.setStyle({ weight: 1.5, fillOpacity: 0.25 }); });
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

  setTimeout(function () { mapa.invalidateSize(); }, 100);
}

async function cargarConfigIA() {
  const adminKey = new URLSearchParams(window.location.search).get("admin");
  if (!adminKey) return;

  try {
    const resp = await fetch("/api/config/ia?admin_key=" + encodeURIComponent(adminKey));
    const cfg = await resp.json();
    const wrap = $("toggle-ia-wrap");
    const toggle = $("toggle-ia");
    if (wrap && toggle) {
      wrap.classList.remove("oculto");
      toggle.checked = cfg.activa;
      toggle.disabled = !cfg.tiene_clave;
      toggle.addEventListener("change", async (ev) => {
        await fetch("/api/config/ia", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ activa: ev.target.checked, admin_key: adminKey }),
        });
      });
    }
  } catch (e) {
    console.error("No se pudo cargar la config de IA:", e);
  }
}

// En el init:

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
cargarConfigIA();