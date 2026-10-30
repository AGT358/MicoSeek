"""
Hábitat y altitud en un punto.
Hábitat: CORINE Land Cover 2018 (local).
Altitud: Open-Meteo elevation (API).
"""
import httpx
from backend.geodatos import obtener_habitat as _obtener_habitat_clc

ELEVATION_API = "https://api.open-meteo.com/v1/elevation"


def _obtener_altitud(lat: float, lng: float) -> float | None:
    """Obtiene la altitud de un punto con la API de Open-Meteo (síncrono)."""
    try:
        r = httpx.get(
            ELEVATION_API,
            params={"latitude": lat, "longitude": lng},
            timeout=10,
        )
        r.raise_for_status()
        elevs = r.json().get("elevation", [])
        return float(elevs[0]) if elevs else None
    except Exception:
        return None


def habitat_en_punto(lat: float, lng: float) -> dict:
    """Devuelve hábitat (CORINE) y altitud (Open-Meteo) para un punto."""
    hab = _obtener_habitat_clc(lat, lng)
    altitud = _obtener_altitud(lat, lng)
    return {
        "habitat": hab["habitat"],
        "clc_code": hab["clc_code"],
        "area_ha": hab.get("area_ha"),
        "altitud": altitud,
    }