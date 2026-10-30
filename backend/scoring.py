"""
Motor de puntuación: convierte datos meteorológicos y de hábitat
en una probabilidad de fructificación (0.0 - 1.0) por especie.

Basado en evidencia micológica:
- La lluvia de los últimos 20 días es el desencadenante principal,
  pero importa el número de eventos, no solo el total.
- La temperatura mínima es el factor limitante al final de temporada.
- Las heladas fuertes cortan la fructificación de casi todas las especies,
  con excepciones según tolerancia.
- Pocos días de lluvia penalizan el score aunque el resto de factores sean buenos.
"""


def _score_rango(valor: float, rango: tuple) -> float:
    """
    Devuelve 1.0 si el valor está dentro del rango óptimo.
    Degrada linealmente fuera de él hasta 0 en un 50% de margen.
    """
    lo, hi = rango
    if lo <= valor <= hi:
        return 1.0
    margen = (hi - lo) * 0.5 or 1.0
    if valor < lo:
        return max(0.0, 1.0 - (lo - valor) / margen)
    else:
        return max(0.0, 1.0 - (valor - hi) / margen)


def _score_eventos_lluvia(dias_con_lluvia: int, minimo: int = 3, optimo: int = 8) -> float:
    """
    Puntúa el número de días con lluvia en los últimos 20 días.
    Menos de 'minimo' eventos: 0.0 (sequía).
    Entre minimo y optimo: escala lineal.
    Por encima de optimo: 1.0 (suficiente).
    """
    if dias_con_lluvia <= minimo:
        return 0.0
    if dias_con_lluvia >= optimo:
        return 1.0
    return (dias_con_lluvia - minimo) / (optimo - minimo)


def puntuar_especie(
    especie: dict,
    meteo: dict,
    habitat_ok: bool,
    altitud_ok: bool,
) -> dict:
    """
    Devuelve un dict con el score total y el desglose por factor.
    'meteo' debe traer:
        lluvia_60d (mm), lluvia_20d (mm),
        dias_lluvia_20d (int),
        t_min_media (°C), t_max_media (°C),
        heladas_fuertes (bool)
    """
    desglose = {}

    # 1. Lluvia acumulada 60d (preparación del micelio)
    s_lluvia_60 = _score_rango(meteo["lluvia_60d"], especie["lluvia_60d_optima"])
    desglose["lluvia_60d"] = round(s_lluvia_60, 2)

    # 2. Lluvia acumulada 20d (desencadenante)
    s_lluvia_20 = _score_rango(meteo["lluvia_20d"], especie["lluvia_20d_optima"])
    desglose["lluvia_20d"] = round(s_lluvia_20, 2)

    # 3. Eventos de lluvia en 20d (importa el número de días, no solo el total)
    dias = meteo.get("dias_lluvia_20d", 0)
    s_eventos = _score_eventos_lluvia(dias)
    desglose["dias_lluvia_20d"] = round(s_eventos, 2)

    # 4. Temperatura mínima (limitante al final de temporada)
    s_t_min = _score_rango(meteo["t_min_media"], especie["t_min_optima"])
    desglose["t_min"] = round(s_t_min, 2)

    # 5. Temperatura máxima (menos crítico, pero influye)
    s_t_max = _score_rango(meteo["t_max_media"], especie["t_max_optima"])
    desglose["t_max"] = round(s_t_max, 2)

    # 6. Heladas (castigo proporcional a la tolerancia)
    if meteo.get("heladas_fuertes"):
        if especie["tolera_heladas"]:
            s_helada = 0.5
        else:
            s_helada = 0.0
    else:
        s_helada = 1.0
    desglose["heladas"] = round(s_helada, 2)

    # 7. Hábitat y altitud
    if not habitat_ok:
        return {"score": 0.0, "desglose": desglose, "motivo": "Hábitat no favorable"}

    s_altitud = 1.0 if altitud_ok else 0.0
    desglose["altitud"] = s_altitud

    # Pesos revisados
    pesos = {
        "lluvia_60d": 0.10,
        "lluvia_20d": 0.30,
        "dias_lluvia_20d": 0.20,
        "t_min": 0.15,
        "t_max": 0.05,
        "heladas": 0.15,
        "altitud": 0.05,
    }

    score = (
        pesos["lluvia_60d"] * s_lluvia_60
        + pesos["lluvia_20d"] * s_lluvia_20
        + pesos["dias_lluvia_20d"] * s_eventos
        + pesos["t_min"] * s_t_min
        + pesos["t_max"] * s_t_max
        + pesos["heladas"] * s_helada
        + pesos["altitud"] * s_altitud
    )

    # Penalización multiplicativa si ha habido muy pocos días de lluvia
    # (refleja que sin eventos suficientes la fructificación es improbable)
    if dias < 3:
        score *= 0.6

    return {
        "score": round(score, 3),
        "desglose": desglose,
        "motivo": _motivo(desglose, especie, score),
    }


def _motivo(desglose: dict, especie: dict, score: float) -> str:
    """Genera una frase corta explicando el factor limitante principal."""
    # Si el score es alto, no hace falta señalar un factor limitante
    if score >= 0.65:
        return "Condiciones favorables"
    if desglose["heladas"] == 0.0:
        return "Heladas fuertes recientes incompatibles con esta especie"
    if desglose["lluvia_20d"] < 0.3:
        return "Lluvia de los últimos 20 días insuficiente"
    if desglose["dias_lluvia_20d"] < 0.3:
        return "Pocos días de lluvia en las últimas semanas"
    if desglose["lluvia_60d"] < 0.3:
        return "Déficit de lluvia acumulada en los últimos 2 meses"
    if desglose["t_min"] < 0.3:
        return "Temperaturas mínimas fuera del rango óptimo"
    if desglose["t_max"] < 0.3:
        return "Temperaturas máximas fuera del rango óptimo"
    return "Condiciones moderadas"


def etiqueta(score: float) -> str:
    if score >= 0.65:
        return "verde"
    if score >= 0.40:
        return "amarillo"
    return "rojo"