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
from backend.orientacion import obtener_orientacion

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
    import time
    t_inicio = time.time()
    print(f"\n[Debug] --- INICIO CONSULTA: {c.especie_principal} ---")

    if c.especie_principal not in ESPECIES:
        return {"error": "Especie no válida"}

    centro = (c.lat, c.lng)
    especie = ESPECIES[c.especie_principal]
    zona_txt = f"({c.lat:.3f}, {c.lng:.3f})"

    # Caché en memoria
    clave_cache = _clave_cache(c.lat, c.lng, c.especie_principal, c.confirmar_temporada)
    cacheado = _leer_cache(clave_cache)
    if cacheado is not None:
        print("[Debug] Devolviendo desde CACHÉ")
        return cacheado

    # Temporada
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

    # CORINE
    print("[Debug] Pasando a CORINE...")
    poligonos = obtener_poligonos(centro[0], centro[1], RADIO_KM)
    print(f"[Debug] CORINE devolvió {len(poligonos)} polígonos")
    
    if not poligonos:
        return {
            "centro": centro, "especie_principal": c.especie_principal,
            "geojson": {"type": "FeatureCollection", "features": []},
            "resumen": f"Para la zona {zona_txt} no se han encontrado polígonos de CORINE.",
        }

    # Filtrar por hábitat
    habitats_especie = set(especie["habitats"])
    poligonos_validos = [p for p in poligonos if p["habitat"] in habitats_especie]
    print(f"[Debug] Polígonos válidos por hábitat: {len(poligonos_validos)}")

    if not poligonos_validos:
        return {
            "centro": centro, "especie_principal": c.especie_principal,
            "geojson": {"type": "FeatureCollection", "features": []},
            "resumen": f"Para la zona {zona_txt} no se han encontrado polígonos con el hábitat adecuado.",
        }

    # Agrupar por celdas de meteo
    celdas = defaultdict(list)
    for p in poligonos_validos:
        celdas[_celda_meteo(p["lat"], p["lng"])].append(p)

    METEO_NEUTRO = {
        "lluvia_60d": 120.0, "lluvia_20d": 40.0, "dias_lluvia_20d": 6,
        "t_min_media": 8.0, "t_max_media": 18.0, "heladas_fuertes": False, "elevation": None,
    }

    # Pedir meteo
    coordenadas = list(celdas.keys())
    try:
        meteo_por_celda = await obtener_meteo_multiple(coordenadas)
    except Exception as e:
        print(f"[Main] Fallo meteo múltiple: {e}")
        meteo_por_celda = {}

    # Primer bucle: puntuar SOLO especie principal
    print("[Debug] Pasando al bucle de scoring...")
    features = []
    meteos_guardados = {}
    alt_lo, alt_hi = especie["altitud"]

    for p in poligonos_validos:
        meteo = meteo_por_celda.get(_celda_meteo(p["lat"], p["lng"]))
        if meteo is None:
            meteo = METEO_NEUTRO

        altitud = meteo.get("elevation")
        altitud_ok = (altitud is None) or (alt_lo <= altitud <= alt_hi)

        res_principal = puntuar_especie(especie, meteo, True, altitud_ok, mes_actual, None, None)
        
        if res_principal["score"] < UMBRAL_FAVORABLE:
            continue

        fid = f"{p['clc_code']}_{p['lat']}_{p['lng']}"
        meteos_guardados[fid] = meteo

        features.append({
            "type": "Feature",
            "geometry": p["geometry"].__geo_interface__,
            "properties": {
                "fid": fid,
                "prob": res_principal["score"],
                "color": etiqueta(res_principal["score"]),
                "habitat": p["habitat"],
                "clc_code": p["clc_code"],
                "motivo": res_principal["motivo"],
                "altitud": altitud,
                "lluvia_60d": meteo["lluvia_60d"],
                "lluvia_20d": meteo["lluvia_20d"],
                "dias_lluvia_20d": meteo["dias_lluvia_20d"],
                "t_min_media": meteo["t_min_media"],
                "heladas_fuertes": meteo["heladas_fuertes"],
                "geologia": None,
                "orientacion": None,
                "top_especies": [],
                "lat": p["lat"],
                "lng": p["lng"],
            },
        })

    # Ordenar y limitar
    features.sort(key=lambda f: f["properties"]["prob"], reverse=True)
    features = features[:MAX_POLIGONOS]
    print(f"[Debug] Polígonos que pasan el filtro: {len(features)}")

    # Segundo bucle: geología, orientación y top-5 EN PARALELO
    print("[Debug] Calculando geología y orientación para los polígonos finales...")
    
    semaforo_geo = asyncio.Semaphore(10)
    semaforo_orient = asyncio.Semaphore(10)

    async def procesar_feature(f):
        p = f["properties"]
        fid = p["fid"]
        meteo = meteos_guardados.get(fid, METEO_NEUTRO)
        lat = p["lat"]
        lng = p["lng"]

        async with semaforo_geo:
            try:
                geologia = await obtener_geologia(lat, lng)
            except Exception:
                geologia = "mixto"
                
        async with semaforo_orient:
            try:
                orientacion = await obtener_orientacion(lat, lng)
            except Exception:
                orientacion = "plano"

        p["geologia"] = geologia
        p["orientacion"] = orientacion

        res_final = puntuar_especie(especie, meteo, True, True, mes_actual, geologia, orientacion)
        p["prob"] = res_final["score"]
        p["color"] = etiqueta(res_final["score"])
        p["motivo"] = res_final["motivo"]

        scores_especies = []
        for eid, e in ESPECIES.items():
            if p["habitat"] not in e.get("habitats", []):
                continue
            meses_e = e.get("meses", [])
            if meses_e and mes_actual not in meses_e:
                continue
            
            alt_lo_e, alt_hi_e = e["altitud"]
            alt_ok_e = (p["altitud"] is None) or (alt_lo_e <= p["altitud"] <= alt_hi_e)
            
            r = puntuar_especie(e, meteo, True, alt_ok_e, mes_actual, geologia, orientacion)
            scores_especies.append({
                "nombre": e["nombre"],
                "score": r["score"],
                "color": etiqueta(r["score"]),
            })
        
        scores_especies.sort(key=lambda x: x["score"], reverse=True)
        p["top_especies"] = scores_especies[:5]
        
        del p["fid"]
        del p["lat"]
        del p["lng"]

        # Lanzamos todas las tareas, pero con un límite global de 4 segundos
    tareas = [procesar_feature(f) for f in features]
    try:
        await asyncio.wait_for(asyncio.gather(*tareas), timeout=2.5)
    except asyncio.TimeoutError:
        print("[Main] Timeout global: algunas geologías/orientaciones no han respondido a tiempo")
    
    # Reordenamos
    features.sort(key=lambda f: f["properties"]["prob"], reverse=True)

    # Resumen
    if features:
        mejor = features[0]["properties"]
        resumen = await generar_resumen_con_llm(
            especie_nombre=especie["nombre"], zona_txt=zona_txt, num_zonas=len(features),
            radio_km=RADIO_KM, mejor_habitat=mejor["habitat"], mejor_color=mejor["color"],
            mejor_prob=mejor["prob"], mejor_motivo=mejor["motivo"],
            fuera_de_temporada=not en_temporada, meses_especie=meses_especie, ia_activa=IA_ACTIVA,
        )
        if resumen is None:
            resumen = generar_resumen_plantilla(
                especie_nombre=especie["nombre"], zona_txt=zona_txt, num_zonas=len(features),
                radio_km=RADIO_KM, mejor_habitat=mejor["habitat"], mejor_color=mejor["color"],
                mejor_prob=mejor["prob"], mejor_motivo=mejor["motivo"],
                fuera_de_temporada=not en_temporada, geologia=mejor.get("geologia"),
            )
    else:
        resumen = f"Para la zona {zona_txt} y la especie {especie['nombre']}: no se han encontrado zonas con condiciones suficientemente favorables."

    otras = _especies_comparten_habitat(c.especie_principal, mes_actual)
    if otras:
        otras_txt = ", ".join(otras[:5])
        if len(otras) > 5:
            otras_txt += f" y {len(otras) - 5} más"
        resumen += f" Otras especies que comparten hábitat y están en temporada: {otras_txt}."

    respuesta = {
        "centro": centro, "especie_principal": c.especie_principal,
        "geojson": {"type": "FeatureCollection", "features": features}, "resumen": resumen,
    }
    _escribir_cache(clave_cache, respuesta)
    print(f"[Timing] Consulta TOTAL tardó: {time.time() - t_inicio:.2f}s")
    print("[Debug] --- FIN CONSULTA ---\n")
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
