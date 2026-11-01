"""
Obtención de datos meteorológicos desde Open-Meteo (gratuito, sin API key).
Incluye caché en disco (24 h) y reintentos ante 429.
Documentación: https://open-meteo.com/en/docs
"""
import json
import time
import asyncio
from pathlib import Path
from datetime import date, timedelta

import httpx

API_URL = "https://archive-api.open-meteo.com/v1/archive"

CACHE_DIR = Path(__file__).parent / "cache_meteo"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL_SEGUNDOS = 24 * 3600  # 24 horas


def _clave_cache(lat: float, lng: float) -> Path:
    """Fichero de caché por celda de 0.05° (~5 km)."""
    paso = 0.05
    lat_r = round(lat / paso) * paso
    lng_r = round(lng / paso) * paso
    nombre = f"{lat_r:+.3f}_{lng_r:+.3f}.json".replace("+", "p").replace("-", "m")
    return CACHE_DIR / nombre


def _leer_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data.get("_ts", 0) > CACHE_TTL_SEGUNDOS:
            return None
        return data
    except Exception:
        return None


def _escribir_cache(path: Path, datos: dict) -> None:
    try:
        datos["_ts"] = time.time()
        path.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[Meteo] No se pudo escribir caché: {e}")

async def obtener_meteo_multiple(coordenadas: list[tuple[float, float]]) -> dict:
    """
    Obtiene meteo para varias coordenadas en una sola petición a Open-Meteo.
    Devuelve un dict {(lat, lng): meteo_dict}.
    """
    if not coordenadas:
        return {}

    # Filtrar las que ya están en caché
    resultados = {}
    pendientes = []
    for lat, lng in coordenadas:
        cache_path = _clave_cache(lat, lng)
        cacheado = _leer_cache(cache_path)
        if cacheado is not None:
            print(f"[Meteo] cache HIT {cache_path.name}")
            resultados[(lat, lng)] = {k: v for k, v in cacheado.items() if not k.startswith("_")}
        else:
            pendientes.append((lat, lng))

    if not pendientes:
        return resultados

    # Construir listas de coordenadas para una sola petición
    lats = ",".join(str(c[0]) for c in pendientes)
    lngs = ",".join(str(c[1]) for c in pendientes)

    hoy = date.today()
    inicio_60d = hoy - timedelta(days=60)

    params = {
        "latitude": lats,
        "longitude": lngs,
        "start_date": inicio_60d.isoformat(),
        "end_date": hoy.isoformat(),
        "daily": "precipitation_sum,temperature_2m_min,temperature_2m_max",
        "timezone": "Europe/Madrid",
    }

    data = None
    async with httpx.AsyncClient(timeout=30) as client:
        for intento in range(3):
            try:
                r = await client.get(API_URL, params=params)
                if r.status_code == 429:
                    espera = 30 * (intento + 1)
                    print(f"[Meteo] 429 en petición múltiple, esperando {espera}s (intento {intento+1}/3)")
                    await asyncio.sleep(espera)
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except httpx.HTTPStatusError as e:
                print(f"[Meteo] HTTP error {e.response.status_code} en petición múltiple")
                if intento == 2:
                    raise
                await asyncio.sleep(5)
            except Exception as e:
                print(f"[Meteo] Error de red: {e}")
                if intento == 2:
                    raise
                await asyncio.sleep(5)

    if data is None:
        raise RuntimeError("No se pudo obtener meteo para la petición múltiple")

    # Si es una sola ubicación, Open-Meteo devuelve un dict (no una lista)
    # Normalizar a lista
    if isinstance(data, dict):
        data = [data]

    for i, (lat, lng) in enumerate(pendientes):
        if i >= len(data):
            break
        bloque = data[i]

        diario = bloque["daily"]
        fechas = diario["time"]
        precip = diario["precipitation_sum"]
        tmin = diario["temperature_2m_min"]
        tmax = diario["temperature_2m_max"]

        lluvia_60d = sum(p or 0 for p in precip)
        corte_20d = (hoy - timedelta(days=20)).isoformat()
        lluvia_20d = sum((p or 0) for f, p in zip(fechas, precip) if f >= corte_20d)

        tmin_vals = [t for t in tmin if t is not None]
        tmax_vals = [t for t in tmax if t is not None]

        t_min_media = sum(tmin_vals) / len(tmin_vals) if tmin_vals else 10.0
        t_max_media = sum(tmax_vals) / len(tmax_vals) if tmax_vals else 20.0

        heladas_fuertes = any(
            t is not None and t <= -3
            for f, t in zip(fechas, tmin) if f >= corte_20d
        )

        dias_lluvia_20d = sum(
            1 for f, p in zip(fechas, precip)
            if f >= corte_20d and p is not None and p >= 1.0
        )

        resultado = {
            "lluvia_60d": round(lluvia_60d, 1),
            "lluvia_20d": round(lluvia_20d, 1),
            "dias_lluvia_20d": dias_lluvia_20d,
            "t_min_media": round(t_min_media, 1),
            "t_max_media": round(t_max_media, 1),
            "heladas_fuertes": heladas_fuertes,
            "elevation": bloque.get("elevation"),
        }

        _escribir_cache(_clave_cache(lat, lng), resultado)
        resultados[(lat, lng)] = resultado

    return resultados


async def obtener_meteo(lat: float, lng: float) -> dict:
    # 1. Intentar caché
    cache_path = _clave_cache(lat, lng)
    cacheado = _leer_cache(cache_path)
    if cacheado is not None:
        print(f"[Meteo] cache HIT {cache_path.name}")
        return {k: v for k, v in cacheado.items() if not k.startswith("_")}

    # 2. Llamar a Open-Meteo con reintento ante 429
    hoy = date.today()
    inicio_60d = hoy - timedelta(days=60)

    params = {
        "latitude": lat,
        "longitude": lng,
        "start_date": inicio_60d.isoformat(),
        "end_date": hoy.isoformat(),
        "daily": "precipitation_sum,temperature_2m_min,temperature_2m_max",
        "timezone": "Europe/Madrid",
    }

    data = None
    async with httpx.AsyncClient(timeout=25) as client:
        for intento in range(3):
            try:
                r = await client.get(API_URL, params=params)
                if r.status_code == 429:
                    espera = 30 * (intento + 1)  # 30s, 60s, 90s
                    print(f"[Meteo] 429 en {cache_path.name}, esperando {espera}s (intento {intento+1}/3)")
                    await asyncio.sleep(espera)
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except httpx.HTTPStatusError as e:
                print(f"[Meteo] HTTP error {e.response.status_code} en {cache_path.name}")
                if intento == 2:
                    raise
                await asyncio.sleep(5)
            except Exception as e:
                print(f"[Meteo] Error de red: {e}")
                if intento == 2:
                    raise
                await asyncio.sleep(5)

    if data is None:
        raise RuntimeError(f"No se pudo obtener meteo para ({lat:.3f},{lng:.3f})")

    # 3. Procesar
    diario = data["daily"]
    fechas = diario["time"]
    precip = diario["precipitation_sum"]
    tmin = diario["temperature_2m_min"]
    tmax = diario["temperature_2m_max"]

    lluvia_60d = sum(p or 0 for p in precip)
    corte_20d = (hoy - timedelta(days=20)).isoformat()
    lluvia_20d = sum((p or 0) for f, p in zip(fechas, precip) if f >= corte_20d)

    dias_lluvia_20d = sum(
        1 for f, p in zip(fechas, precip)
        if f >= corte_20d and p is not None and p >= 1.0
    )

    tmin_vals = [t for t in tmin if t is not None]
    tmax_vals = [t for t in tmax if t is not None]

    t_min_media = sum(tmin_vals) / len(tmin_vals) if tmin_vals else 10.0
    t_max_media = sum(tmax_vals) / len(tmax_vals) if tmax_vals else 20.0

    heladas_fuertes = any(
        t is not None and t <= -3
        for f, t in zip(fechas, tmin) if f >= corte_20d
    )

    resultado = {
        "lluvia_60d": round(lluvia_60d, 1),
        "lluvia_20d": round(lluvia_20d, 1),
        "dias_lluvia_20d": dias_lluvia_20d,
        "t_min_media": round(t_min_media, 1),
        "t_max_media": round(t_max_media, 1),
        "heladas_fuertes": heladas_fuertes,
        "elevation": data.get("elevation"),
    }

    _escribir_cache(cache_path, resultado)
    return resultado