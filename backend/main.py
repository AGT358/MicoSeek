"""
MicoSeek API — Backend FastAPI.

Integra:
- CORINE Land Cover local (hábitat)
- Open-Meteo con caché (lluvia y temperatura)
- Scoring con penalización estacional
- Filtro de temporada con confirmación
- Resumen con plantilla o LLM (OpenAI) activable por admin
"""
import os
import time
import httpx
import json
import asyncio

from collections import defaultdict
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.especies import ESPECIES
from backend.scoring import puntuar_especie, etiqueta
from backend.meteo import obtener_meteo, obtener_meteo_multiple
from backend.geodatos import obtener_poligonos
from backend.ia import generar_resumen_con_llm, generar_resumen_plantilla
from backend.geologia import obtener_geologia

# ---------- Configuración ----------
IA_ACTIVA = os.getenv("IA_ACTIVA", "false").lower() == "true"
ADMIN_KEY = os.getenv("ADMIN_KEY", "micoseek-admin")
MAX_POLIGONOS = 200
UMBRAL_FAVORABLE = 0.40
RADIO_KM = 15.0
CONCURRENCIA_METEO = 1
TRACESTRACK_KEY = os.getenv("TRACESTRACK_KEY", "")

# Caché en memoria de respuestas de /api/consulta
CACHE_CONSULTAS = {}  # clave: (lat_r, lng_r, especie, confirmar), valor: (timestamp, respuesta)
CACHE_TTL_SEGUNDOS = 600  # 10 minutos

# ---------- App ----------
app = FastAPI(title="MicoSeek API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Modelos ----------
class Consulta(BaseModel):
    lat: float
    lng: float
    especie_principal: str
    confirmar_temporada: bool = False


class ConfigIA(BaseModel):
    activa: bool
    admin_key: str


# ---------- Utilidades ----------
def _nombre_mes(m: int) -> str:
    return [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ][m - 1]


def _celda_meteo(lat: float, lng: float, paso: float = 0.05) -> tuple:
    """Redondea lat/lng a una celda de ~5 km para cachear meteo."""
    return (round(lat / paso) * paso, round(lng / paso) * paso)


def _especies_comparten_habitat(especie_principal_id: str, mes_actual: int) -> list[str]:
    """
    Devuelve los nombres comunes de las especies que:
    - Comparten el hábitat principal de la especie consultada.
    - Están en temporada (mes actual en meses).
    - No son la propia especie principal.
    """
    principal = ESPECIES[especie_principal_id]
    if not principal.get("habitats"):
        return []
    habitat_principal = principal["habitats"][0]

    resultado = []
    for eid, e in ESPECIES.items():
        if eid == especie_principal_id:
            continue
        if habitat_principal not in e.get("habitats", []):
            continue
        meses = e.get("meses", [])
        if meses and mes_actual not in meses:
            continue
        resultado.append(e["nombre"])
    return resultado

def _clave_cache(lat: float, lng: float, especie: str, confirmar: bool) -> tuple:
    """Redondea lat/lng a 0.01° (~1 km) para agrupar consultas cercanas."""
    return (round(lat, 2), round(lng, 2), especie, confirmar)


def _leer_cache(clave: tuple):
    entrada = CACHE_CONSULTAS.get(clave)
    if not entrada:
        return None
    ts, respuesta = entrada
    if time.time() - ts > CACHE_TTL_SEGUNDOS:
        del CACHE_CONSULTAS[clave]
        return None
    return respuesta


def _escribir_cache(clave: tuple, respuesta: dict):
    CACHE_CONSULTAS[clave] = (time.time(), respuesta)

# ---------- Endpoints ----------
@app.get("/api/especies")
def listar_especies():
    return [
        {"id": k, "nombre": v["nombre"], "cientifico": v["cientifico"]}
        for k, v in ESPECIES.items()
    ]

@app.get("/api/config/mapas")
def get_config_mapas():
    return {"tracestrack_key": os.getenv("TRACESTRACK_KEY", "")}


@app.get("/api/config/ia")
def get_config_ia(admin_key: str = ""):
    if admin_key != ADMIN_KEY:
        return {"activa": IA_ACTIVA}
    return {"activa": IA_ACTIVA, "tiene_clave": bool(os.getenv("OPENAI_API_KEY"))}


@app.post("/api/config/ia")
def set_config_ia(cfg: ConfigIA):
    if cfg.admin_key != ADMIN_KEY:
        raise HTTPException(403, "No autorizado")
    global IA_ACTIVA
    IA_ACTIVA = cfg.activa
    return {"activa": IA_ACTIVA}

@app.post("/api/config/cache/clear")
def clear_cache(admin_key: str = ""):
    if admin_key != ADMIN_KEY:
        raise HTTPException(403, "No autorizado")
    global CACHE_CONSULTAS
    CACHE_CONSULTAS = {}
    return {"ok": True, "mensaje": "Caché limpiada"}


@app.post("/api/consulta")
async def consulta(c: Consulta):
    if c.especie_principal not in ESPECIES:
        return {"error": "Especie no válida"}

    centro = (c.lat, c.lng)
    especie = ESPECIES[c.especie_principal]
    zona_txt = f"({c.lat:.3f}, {c.lng:.3f})"

    clave_cache = _clave_cache(c.lat, c.lng, c.especie_principal, c.confirmar_temporada)
    cacheado = _leer_cache(clave_cache)
    if cacheado is not None:
        print(f"[Cache] HIT para {clave_cache}")
        return cacheado





    # --- 0. Comprobar temporada ---
    mes_actual = date.today().month
    meses_especie = especie.get("meses", [])
    en_temporada = (not meses_especie) or (mes_actual in meses_especie)

    if not en_temporada and not c.confirmar_temporada:
        return {
            "requiere_confirmacion": True,
            "aviso": (
                f"{especie['nombre']} no está en su temporada habitual "
                f"(meses típicos: {', '.join(_nombre_mes(m) for m in meses_especie)}). "
                f"Estamos en {_nombre_mes(mes_actual)}. "
                f"Los resultados pueden ser poco fiables."
            ),
            "meses_especie": meses_especie,
            "mes_actual": mes_actual,
            "especie_nombre": especie["nombre"],
        }

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
    # --- 4. Pedir meteo real en una sola petición (agrupando coordenadas) ---
    coordenadas = list(celdas.keys())
    try:
        meteo_por_celda = await obtener_meteo_multiple(coordenadas)
    except Exception as e:
        print(f"[Main] Fallo meteo múltiple: {e}")
        meteo_por_celda = {}

    # Fallback neutro para celdas que falten
    METEO_NEUTRO = {
        "lluvia_60d": 120.0,
        "lluvia_20d": 40.0,
        "dias_lluvia_20d": 6,
        "t_min_media": 8.0,
        "t_max_media": 18.0,
        "heladas_fuertes": False,
        "elevation": None,
    }

    # Obtener geología por celda (con caché)
    semaforo_geo = asyncio.Semaphore(5)

    async def geo_celda(clave):
        async with semaforo_geo:
            try:
                return clave, await obtener_geologia(clave[0], clave[1])
            except Exception as e:
                print(f"[Main] Fallo geologia para {clave}: {e}")
                return clave, None

    resultados_geo = await asyncio.gather(*[geo_celda(k) for k in celdas.keys()])
    geologia_por_celda = dict(resultados_geo)

    # --- 5. Puntuar cada polígono ---
    features = []
    alt_lo, alt_hi = especie["altitud"]

    for p in poligonos_validos:
        meteo = meteo_por_celda.get(_celda_meteo(p["lat"], p["lng"]))
        if meteo is None:
            meteo = METEO_NEUTRO

        altitud = meteo.get("elevation")
        altitud_ok = (altitud is None) or (alt_lo <= altitud <= alt_hi)
        
        geologia = geologia_por_celda.get(_celda_meteo(p["lat"], p["lng"]))
        res = puntuar_especie(especie, meteo, True, altitud_ok, mes_actual, geologia)
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
                "lluvia_60d": meteo["lluvia_60d"],
                "lluvia_20d": meteo["lluvia_20d"],
                "dias_lluvia_20d": meteo["dias_lluvia_20d"],
                "t_min_media": meteo["t_min_media"],
                "heladas_fuertes": meteo["heladas_fuertes"],
                "geologia": geologia,
            },
        })

    # --- 6. Ordenar y limitar ---
    
    features.sort(key=lambda f: f["properties"]["prob"], reverse=True)
    features = features[:MAX_POLIGONOS]

    # --- 7. Resumen con LLM o plantilla ---
    if features:
        mejor = features[0]["properties"]
        resumen = await generar_resumen_con_llm(
            especie_nombre=especie["nombre"],
            zona_txt=zona_txt,
            num_zonas=len(features),
            radio_km=RADIO_KM,
            mejor_habitat=mejor["habitat"],
            mejor_color=mejor["color"],
            mejor_prob=mejor["prob"],
            mejor_motivo=mejor["motivo"],
            fuera_de_temporada=not en_temporada,
            meses_especie=meses_especie,
            ia_activa=IA_ACTIVA,
        )
        if resumen is None:
            resumen = generar_resumen_plantilla(
                especie_nombre=especie["nombre"],
                zona_txt=zona_txt,
                num_zonas=len(features),
                radio_km=RADIO_KM,
                mejor_habitat=mejor["habitat"],
                mejor_color=mejor["color"],
                mejor_prob=mejor["prob"],
                mejor_motivo=mejor["motivo"],
                fuera_de_temporada=not en_temporada,
                geologia=mejor.get("geologia"),
            )       
    else:
        resumen = await generar_resumen_con_llm(
            especie_nombre=especie["nombre"],
            zona_txt=zona_txt,
            num_zonas=0,
            radio_km=RADIO_KM,
            mejor_habitat="",
            mejor_color="",
            mejor_prob=0.0,
            mejor_motivo="",
            fuera_de_temporada=not en_temporada,
            meses_especie=meses_especie,
            ia_activa=IA_ACTIVA,            
        )
        if resumen is None:
            resumen = generar_resumen_plantilla(
                especie_nombre=especie["nombre"],
                zona_txt=zona_txt,
                num_zonas=0,
                radio_km=RADIO_KM,
                mejor_habitat="",
                mejor_color="",
                mejor_prob=0.0,
                mejor_motivo="",
                fuera_de_temporada=not en_temporada,
                geologia=None,
            )
       
    # --- 8. Añadir especies que comparten hábitat y están en temporada ---
    otras = _especies_comparten_habitat(c.especie_principal, mes_actual)
    if otras:
        otras_txt = ", ".join(otras[:5])
        if len(otras) > 5:
            otras_txt += f" y {len(otras) - 5} más"
        resumen += f" Otras especies que comparten hábitat y están en temporada: {otras_txt}."

     

    respuesta = {
        "centro": centro,
        "especie_principal": c.especie_principal,
        "geojson": {"type": "FeatureCollection", "features": features},
        "resumen": resumen,
    }
    _escribir_cache(clave_cache, respuesta)
    return respuesta

@app.get("/api/tiles/tracestrack/{z}/{x}/{y}.png")
async def proxy_tracestrack(z: int, x: int, y: int):
    if not TRACESTRACK_KEY:
        raise HTTPException(500, "Tracestrack key no configurada")
    url = f"https://tile.tracestrack.com/topo__/{z}/{x}/{y}@1x.png?key={TRACESTRACK_KEY}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        if r.status_code != 200:
            raise HTTPException(r.status_code, "Error al obtener la tesela")
        return Response(content=r.content, media_type="image/png")


# ---------- Frontend estático ----------
FRONTEND = Path(__file__).parent.parent / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")