// ---------- Estado ----------
let especiePrincipal = null;
let mapaFormulario = null;
let marcadorFormulario = null;
let coordsSeleccionadas = null;
let mapa = null;
let capaResultados = null;
let ESPECIES = [];
let mejorFeature = null; //variable globa



// ---------- Elementos ----------
const $ = (id) => document.getElementById(id);
const btnConsultar = $("btn-consultar");
const vistaFormulario = $("vista-formulario");
const vistaResultados = $("vista-resultados");

// ---------- Funcion MostrarResultados ----------

function mostrarResultados(datos, punto) {
  // 1. Cambiar paneles: ocultar búsqueda, mostrar resultados
  const panelBusqueda = document.getElementById("panel-busqueda");
  if (panelBusqueda) panelBusqueda.classList.add("oculto");
  vistaResultados.classList.remove("oculto");

  // 2. Asegurarse de que el mapa existe
  if (!mapaFormulario) inicializarMapaFormulario();

  // 3. Quitar la capa de resultados y el control anterior (si existen)
  if (capaResultados) {
    mapaFormulario.removeLayer(capaResultados);
    capaResultados = null;
  }
  if (window.controlPoligonos) {
    mapaFormulario.removeControl(window.controlPoligonos);
    window.controlPoligonos = null;
  }

  // 4. Reutilizar el marcador de selección (o crear uno nuevo)
  const centro = datos.centro || [punto.lat, punto.lng];
  if (marcadorFormulario) {
    marcadorFormulario.setLatLng(centro);
    marcadorFormulario.bindPopup("Punto consultado: " + centro[0].toFixed(4) + ", " + centro[1].toFixed(4));
  } else {
    marcadorFormulario = L.marker(centro, { title: "Punto consultado" })
      .addTo(mapaFormulario)
      .bindPopup("Punto consultado: " + centro[0].toFixed(4) + ", " + centro[1].toFixed(4));
  }

  const PALETA = {
    verde: { fill: "#f50ac6", stroke: "#f50ac6" },
    amarillo: { fill: "#FFA500", stroke: "#CC8400" },
    rojo: { fill: "#FF0000", stroke: "#CC0000" },
  };

  const features = datos.geojson?.features || [];
  mejorFeature = features.length > 0 ? features[0] : null;

  const btnMejor = $("btn-mejor-zona");
  if (btnMejor) {
    if (mejorFeature) btnMejor.classList.remove("oculto");
    else btnMejor.classList.add("oculto");
  }

  if (features.length > 0) {
    const capaPoligonos = L.geoJSON(datos.geojson, {
      style: function (feature) {
        const p = PALETA[feature.properties.color] || { fill: "#888", stroke: "#555" };
        return { color: p.stroke, weight: 1.5, fillColor: p.fill, fillOpacity: 0.25 };
      },
      onEachFeature: function (feature, layer) {
        const p = feature.properties;
        const pct = Math.round((p.prob || 0) * 100);
        const colorTxt = p.color === "verde" ? "Alta" : "Media";

        const popupHtml =
          '<div style="min-width:200px">' +
          '<strong style="text-transform:capitalize">' + p.habitat + '</strong><br>' +
          '<span style="color:#2e7d32;font-weight:600">' + colorTxt + '</span> - ' + pct + '%<br>' +
          '<hr style="margin:6px 0;border:none;border-top:1px solid #ddd">' +
          '<strong>Mejores especies:</strong><br>' +
          (p.top_especies && p.top_especies.length > 0
            ? p.top_especies.slice(0, 3).map(e =>
              '• ' + e.nombre + ' (' + Math.round(e.score * 100) + '%)'
            ).join('<br>')
            : '—') +
          '<details style="margin-top:6px">' +
          '<summary style="cursor:pointer;color:#2e7d32;font-size:0.85em">Ver detalle técnico</summary>' +
          '<div style="margin-top:6px;font-size:0.85em">' +
          'Lluvia 2 meses: ' + (p.lluvia_60d != null ? p.lluvia_60d : "-") + ' mm<br>' +
          'Lluvia 20 días: ' + (p.lluvia_20d != null ? p.lluvia_20d : "-") + ' mm<br>' +
          'Días de lluvia (20d): ' + (p.dias_lluvia_20d != null ? p.dias_lluvia_20d : "-") + '<br>' +
          'Tmin 20 días: ' + (p.t_min_media != null ? p.t_min_media : "-") + ' °C<br>' +
          'Heladas: ' + (p.heladas_fuertes ? "Sí" : "No") + '<br>' +
          'Geología: ' + (p.geologia || "-") + '<br>' +
          'Orientación: ' + (p.orientacion || "-") + '<br>' +
          (p.altitud ? 'Altitud: ' + Math.round(p.altitud) + ' m<br>' : '') +
          '<em style="color:#666">' + (p.motivo || '') + '</em>' +
          '</div>' +
          '</details>' +
          '</div>';

        layer.bindPopup(popupHtml);
        layer.on("mouseover", function () { layer.setStyle({ weight: 3, fillOpacity: 0.4 }); });
        layer.on("mouseout", function () { layer.setStyle({ weight: 1.5, fillOpacity: 0.25 }); });
      },
    });

    // Añadir la capa de polígonos al mapa
    capaPoligonos.addTo(mapaFormulario);
    capaResultados = capaPoligonos; // Guardar referencia para limpiarla después

    // Activar el checkbox (por defecto marcado)
    const togglePoligonos = $("toggle-poligonos");
    if (togglePoligonos) {
      togglePoligonos.checked = true;
      // Quitar listener anterior si existe para no duplicar
      togglePoligonos.onchange = (e) => {
        if (e.target.checked) {
          mapaFormulario.addLayer(capaResultados);
        } else {
          mapaFormulario.removeLayer(capaResultados);
        }
      };
    }

    const bounds = capaPoligonos.getBounds();
    if (bounds.isValid()) {
      mapaFormulario.fitBounds(bounds, { padding: [50, 50], maxZoom: 14 });
    }
  }

  const resumenEl = $("texto-resumen");
  if (resumenEl) resumenEl.textContent = datos.resumen || "Sin resumen disponible.";

  setTimeout(function () { mapaFormulario.invalidateSize(); }, 100);
}

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
    zoomControl: false
  });

  // 1. Capa base: OpenStreetMap (siempre funciona)
  const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "OpenStreetMap",
    maxZoom: 19,
  }).addTo(mapaFormulario);

  // 2. Capa Tracestrack (a través de tu backend)
  const tracestrack = L.tileLayer("/api/tiles/tracestrack/{z}/{x}/{y}.png", {
    attribution: "Tracestrack",
    maxZoom: 17,
  });

  // 3. Capa PNOA (Ortofoto del IGN como capa de teselas normal)
  // Usamos el endpoint WMTS del IGN pero con L.tileLayer para que no rompa Leaflet
  const pnoa = L.tileLayer(
    "https://www.ign.es/wmts/pnoa-ma?request=GetTile&service=WMTS&version=1.0.0" +
    "&layer=OI.OrthoimageCoverage&style=default&tilematrixset=GoogleMapsCompatible" +
    "&format=image/jpeg&tilematrix={z}&tilerow={y}&tilecol={x}",
    {
      attribution: "PNOA © IGN",
      maxZoom: 19,
    }
  );

  // 4. Control de capas
  L.control.layers(
    { "Calles (OSM)": osm, "Tracestrack": tracestrack, "Ortofoto (PNOA)": pnoa },
    null,
    { position: 'topleft', collapsed: true }
  ).addTo(mapaFormulario);

  // 5. Control de zoom abajo a la derecha
  L.control.zoom({ position: 'bottomright' }).addTo(mapaFormulario);

  // 6. Evento de clic
  mapaFormulario.on("click", (ev) => {
    seleccionarPunto(ev.latlng.lat, ev.latlng.lng, false);
  });

  // 7. Redibujar
  setTimeout(() => mapaFormulario.invalidateSize(), 200);
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

// Función para seleccionar un punto en el mapa

function seleccionarPunto(lat, lng, centrar = false) {
  if (!mapaFormulario) inicializarMapaFormulario();

  // 1. Quitar el marcador anterior si existe
  if (marcadorFormulario) {
    mapaFormulario.removeLayer(marcadorFormulario);
  }

  // 2. Crear el nuevo marcador
  marcadorFormulario = L.marker([lat, lng], {
    draggable: true,
    title: "Punto seleccionado"
  }).addTo(mapaFormulario);

  // 3. Si arrastras el marcador, actualizamos las coordenadas
  marcadorFormulario.on("dragend", (ev) => {
    const p = ev.target.getLatLng();
    seleccionarPunto(p.lat, p.lng, false);
  });

  // 4. Guardar las coordenadas en la variable global
  coordsSeleccionadas = { lat: lat, lng: lng };

  // 5. Actualizar el texto en el panel
  const el = $("coords-seleccionadas");
  if (el) {
    el.textContent = "Punto seleccionado: " + lat.toFixed(4) + ", " + lng.toFixed(4);
    el.style.color = "#1B4D3E"; // Un toque verde para indicar que ya hay punto
    el.style.fontWeight = "bold";
  }

  // 6. Si el usuario hace clic desde "Mi ubicación" o "Buscar", centramos el mapa
  if (centrar) {
    mapaFormulario.setView([lat, lng], 12);
  }

  // 7. Comprobar si ya podemos activar el botón de consultar
  actualizarBoton();
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
document.querySelectorAll('.btn-eco').forEach(btn => {
  btn.addEventListener('click', () => {
    const especieId = btn.dataset.especie;
    const selector = $("selector-especie");
    if (selector && especieId) {
      selector.value = especieId;
      especiePrincipal = especieId;
      selector.dispatchEvent(new Event("change"));
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


const btnNueva = $("btn-nueva");
if (btnNueva) {
  btnNueva.addEventListener("click", () => {
    // 1. Cambiar paneles
    document.getElementById("panel-busqueda").classList.remove("oculto");
    vistaResultados.classList.add("oculto");

    // 2. Limpiar capa de polígonos y control
    if (capaResultados) {
      mapaFormulario.removeLayer(capaResultados);
      capaResultados = null;
    }
    
    // 3. Quitar el marcador del punto consultado
    if (marcadorFormulario) {
      mapaFormulario.removeLayer(marcadorFormulario);
      marcadorFormulario = null;
    }

    // 4. Restaurar la vista
    mapaFormulario.setView([40.4168, -3.7038], 6);

    btnConsultar.textContent = "Consultar probabilidad";
    actualizarBoton();

    const btnMejor = $("btn-mejor-zona");
    if (btnMejor) btnMejor.classList.add("oculto");

    setTimeout(() => mapaFormulario.invalidateSize(), 100);
  });
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



//Listener para el botón de "Ver la mejor zona en el mapa"
const btnMejorZona = $("btn-mejor-zona");
if (btnMejorZona) {
  btnMejorZona.addEventListener("click", () => {
    console.log("[Debug] Click. mejorFeature:", mejorFeature, "mapaFormulario:", mapaFormulario);
    if (!mejorFeature || !mapaFormulario) return;

    const geom = mejorFeature.geometry;
    console.log("[Debug] geom.type:", geom.type);
    let coords = [];
    if (geom.type === "Polygon") coords = geom.coordinates[0];
    else if (geom.type === "MultiPolygon") coords = geom.coordinates[0][0];
    else return;
    console.log("[Debug] coords length:", coords.length);
    console.log("[Debug] primer coord:", coords[0]);

    const lats = coords.map(c => c[1]);
    const lngs = coords.map(c => c[0]);
    const lat = (Math.min(...lats) + Math.max(...lats)) / 2;
    const lng = (Math.min(...lngs) + Math.max(...lngs)) / 2;
    console.log("[Debug] lat, lng:", lat, lng);

    if (isNaN(lat) || isNaN(lng)) return;
    mapaFormulario.setView([lat, lng], 14);
  });

}

// ---------- Lógica del Bottom Sheet (Móvil) ----------
document.addEventListener("DOMContentLoaded", () => {
  const panel = document.querySelector(".panel-lateral");
  const dragHandle = document.getElementById("drag-handle");
  
  if (!panel || !dragHandle) return;

  let startY = 0;
  let currentY = 0;
  let isDragging = false;
  let panelHeight = 0;

  // Detectar cuando el dedo toca el asa
  dragHandle.addEventListener("touchstart", (e) => {
    if (window.innerWidth > 768) return;
    isDragging = true;
    startY = e.touches[0].clientY;
    panelHeight = panel.offsetHeight; // Guardamos la altura del panel
    panel.style.transition = "none";
  }, { passive: true });

  // Mover el panel mientras se arrastra
  dragHandle.addEventListener("touchmove", (e) => {
    if (!isDragging) return;
    currentY = e.touches[0].clientY;
    const deltaY = currentY - startY;
    
    // Si el panel está oculto, permitimos arrastrar hacia arriba
    const isHidden = panel.classList.contains("oculto-bottom-sheet");
    
    if (isHidden) {
      // Si está oculto, solo permitimos moverlo hacia arriba (delta negativo)
      // Calculamos cuánto ha subido respecto a la posición de "oculto"
      // (el panel oculto está a translateY(100% - 40px))
      const hiddenOffset = panelHeight - 70;
      const newTranslate = Math.max(0, hiddenOffset + deltaY);
      panel.style.transform = `translateY(${newTranslate}px)`;
    } else {
      // Si está abierto, solo permitimos moverlo hacia abajo (delta positivo)
      if (deltaY > 0) {
        panel.style.transform = `translateY(${deltaY}px)`;
      }
    }
  }, { passive: true });

  // Soltar el dedo: decidir si se oculta o se queda abierto
  dragHandle.addEventListener("touchend", (e) => {
    if (!isDragging) return;
    isDragging = false;
    panel.style.transition = "transform 0.3s cubic-bezier(0.25, 1, 0.5, 1)";
    panel.style.transform = "";

    const deltaY = currentY - startY;
    const isHidden = panel.classList.contains("oculto-bottom-sheet");
    
    if (isHidden) {
      // Si estaba oculto y ha arrastrado hacia arriba (delta negativo) más de -80px, abrir
      if (deltaY < -80) {
        panel.classList.remove("oculto-bottom-sheet");
      }
    } else {
      // Si estaba abierto y ha arrastrado hacia abajo más de 80px, ocultar
      if (deltaY > 80) {
        panel.classList.add("oculto-bottom-sheet");
      }
    }
  });

  // Tocar el asa cuando está cerrado, lo vuelve a abrir
  dragHandle.addEventListener("click", () => {
    if (window.innerWidth <= 768) {
      panel.classList.remove("oculto-bottom-sheet");
    }
  });
});

// ---------- Init ----------
cargarEspecies();
inicializarMapaFormulario();
actualizarBoton();
cargarConfigIA();