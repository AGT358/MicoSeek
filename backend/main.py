"""
API de MicoSeek.

Endpoints:
    GET  /api/especies  → catálogo de especies
    POST /api/consulta  → consulta por coordenadas y especie

La consulta:
1. Comprueba temporada de la especie (si no está en temporada, pide confirmación).
2. Obtiene polígonos de CORINE en un radio alrededor del punto.
3. Filtra por hábitat compatible con la especie.
4. Agrupa por celda de ~5 km, pide meteo una vez por celda (en paralelo).
5. Puntúa cada polígono y devuelve GeoJSON con los favorables.
"""
import asyncio
from collections import defaultdict
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.especies import ESPECIES
from backend.scoring import puntuar_especie, etiqueta
from backend.meteo import obtener_meteo
from backend.geodatos import obtener_poligonos
from backend.ia import generar_resumen_con_llm, generar_resumen_plantilla


# ---------- Configuración ----------
MAX_POLIGONOS = 200
UMBRAL_FAVORABLE = 0.40
RADIO_KM = 15.0
CONCURRENCIA_METEO = 1


# ---------- App ----------
app = FastAPI(title="MicoSeek API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # en producción, restringir
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Modelos ----------
class Consulta(BaseModel):
    lat: float
    lng: float
    especie_principal: str
    especies: list[str] = []


# ---------- Utilidades ----------
def _nombre_mes(m: int) -> str:
    return [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ][m - 1]


def _celda_meteo(lat: float, lng: float, paso: float = 0.05) -> tuple:
    """Redondea lat/lng a una celda de ~5 km para cachear meteo."""
    return (round(lat / paso) * paso, round(lng / paso) * paso)


# ---------- Endpoints ----------
@app.get("/api/especies")
def listar_especies():
    return [
        {"id": k, "nombre": v["nombre"], "cientifico": v["cientifico"]}
        for k, v in ESPECIES.items()
    ]


@app.post("/api/consulta")
async def consulta(c: Consulta):
    print(f"[Main] endpoint consulta invocado, especie={c.especie_principal}, lat={c.lat}, lng={c.lng}")
    if c.especie_principal not in ESPECIES:
        return {"error": "Especie no válida"}

    centro = (c.lat, c.lng)
    especie = ESPECIES[c.especie_principal]
    zona_txt = f"({c.lat:.3f}, {c.lng:.3f})"

    # --- 1. Polígonos de CORINE en el radio ---
    poligonos = obtener_poligonos(centro[0], centro[1], RADIO_KM)
    if not poligonos:
        return {
            "centro": centro,
            "especie_principal": c.especie_principal,
            "geojson": {"type": "FeatureCollection", "features": []},
            "resumen": (
                f"Para la zona {zona_txt} y la especie {especie['nombre']}: "
                f"no se han encontrado polígonos de CORINE en un radio de "
                f"{int(RADIO_KM)} km. Recuerda que estos datos son orientativos."
            ),
        }

    # --- 2. Filtrar por hábitat compatible ---
    habitats_especie = set(especie["habitats"])
    poligonos_validos = [p for p in poligonos if p["habitat"] in habitats_especie]

    if not poligonos_validos:
        habitats_txt = ", ".join(especie["habitats"])
        return {
            "centro": centro,
            "especie_principal": c.especie_principal,
            "geojson": {"type": "FeatureCollection", "features": []},
            "resumen": (
                f"Para la zona {zona_txt} y la especie {especie['nombre']}: "
                f"no se han encontrado polígonos con el hábitat adecuado "
                f"({habitats_txt}) en un radio de {int(RADIO_KM)} km. "
                "Recuerda que estos datos son orientativos."
            ),
        }

    # --- 3. Agrupar por celda de meteo (~5 km) ---
    celdas = defaultdict(list)
    for p in poligonos_validos:
        celdas[_celda_meteo(p["lat"], p["lng"])].append(p)

    # --- 4. Pedir meteo real (caché + semáforo a 1 por el free tier) ---
    semaforo = asyncio.Semaphore(1)

    async def meteo_celda(clave):
        async with semaforo:
            try:
                return clave, await obtener_meteo(clave[0], clave[1])
            except Exception as e:
                print(f"[Main] Fallo meteo para {clave}: {e}")
                return clave, None
            
            print(f"[Main] meteo obtenido: {sum(1 for v in meteo_por_celda.values() if v is not None)}/{len(celdas)}") # diagnostico agt
            print(f"[Main] meteo obtenido: {sum(1 for v in meteo_por_celda.values() if v is not None)}/{len(celdas)}")

    resultados_meteo = await asyncio.gather(
        *[meteo_celda(k) for k in celdas.keys()]
    )
    print(f"[Main] celdas a consultar: {len(celdas)}")
    meteo_por_celda = dict(resultados_meteo)

    # Fallback neutro para celdas que fallen (429, timeout, etc.)
    METEO_NEUTRO = {
        "lluvia_60d": 120.0,
        "lluvia_20d": 40.0,
        "dias_lluvia_20d": 6,
        "t_min_media": 8.0,
        "t_max_media": 18.0,
        "heladas_fuertes": False,
        "elevation": None,
    }

    # --- 5. Puntuar cada polígono ---
    features = []
    alt_lo, alt_hi = especie["altitud"]

    for p in poligonos_validos:
        meteo = meteo_por_celda.get(_celda_meteo(p["lat"], p["lng"]))
        if meteo is None:
            meteo = METEO_NEUTRO

        altitud = meteo.get("elevation")
        altitud_ok = (altitud is None) or (alt_lo <= altitud <= alt_hi)

        res = puntuar_especie(especie, meteo, True, altitud_ok)
        if res["score"] < UMBRAL_FAVORABLE:
            continue

        features.append({
            "type": "Feature",
            "geometry": p["geometry"].__geo_interface__,
            "properties": {
                "prob": res["score"],
                "color": etiqueta(res["score"]),
                "habitat": p["habitat"],
                "clc_code": p["clc_code"],
                "motivo": res["motivo"],
                "altitud": altitud,
                "lluvia_20d": meteo["lluvia_20d"],
                "dias_lluvia_20d": meteo["dias_lluvia_20d"],
                "t_min_media": meteo["t_min_media"],
                "heladas_fuertes": meteo["heladas_fuertes"],
            },
        })

    # --- 6. Ordenar y limitar ---
    features.sort(key=lambda f: f["properties"]["prob"], reverse=True)
    features = features[:MAX_POLIGONOS]

    # --- 7. Resumen con plantilla (sin IA) ---
    if features:
        mejor = features[0]["properties"]
        resumen = generar_resumen_plantilla(
            especie_nombre=especie["nombre"],
            zona_txt=zona_txt,
            num_zonas=len(features),
            radio_km=RADIO_KM,
            mejor_habitat=mejor["habitat"],
            mejor_color=mejor["color"],
            mejor_prob=mejor["prob"],
            mejor_motivo=mejor["motivo"],
            fuera_de_temporada=False,
        )
    else:
        resumen = generar_resumen_plantilla(
            especie_nombre=especie["nombre"],
            zona_txt=zona_txt,
            num_zonas=0,
            radio_km=RADIO_KM,
            mejor_habitat="",
            mejor_color="",
            mejor_prob=0.0,
            mejor_motivo="",
            fuera_de_temporada=False,
        )

    return {
        "centro": centro,
        "especie_principal": c.especie_principal,
        "geojson": {"type": "FeatureCollection", "features": features},
        "resumen": resumen,
    }

# ---------- Frontend estático ----------) 
FRONTEND = Path(__file__).parent.parent / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")