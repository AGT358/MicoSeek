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
from datetime import date
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
    confirmar_temporada: bool = False


# ---------- Utilidades ----------
def _nombre_mes(numero: int) -> str:
    meses = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]
    return meses[numero - 1] if 1 <= numero <= 12 else str(numero)


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



# ---------- Endpoints ----------
@app.get("/api/especies")
def listar_especies():
    return [
        {"id": k, "nombre": v["nombre"], "cientifico": v["cientifico"]}
        for k, v in ESPECIES.items()
    ]


@app.post("/api/consulta")
async def consulta(c: Consulta):
    
    if c.especie_principal not in ESPECIES:
        return {"error": "Especie no válida"}

    centro = (c.lat, c.lng)
    especie = ESPECIES[c.especie_principal]
    zona_txt = f"({c.lat:.3f}, {c.lng:.3f})"

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
       
        res = puntuar_especie(especie, meteo, True, altitud_ok, mes_actual)
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

    # Añadir especies que comparten hábitat y están en temporada
    otras = _especies_comparten_habitat(c.especie_principal, mes_actual)
    if otras:
        otras_txt = ", ".join(otras[:5])
        if len(otras) > 5:
            otras_txt += f" y {len(otras) - 5} más"
        resumen += f" Otras especies que comparten hábitat y están en temporada: {otras_txt}."   

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