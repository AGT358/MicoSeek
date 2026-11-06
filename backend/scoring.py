"""
Motor de puntuación con penalización estacional.
"""

def _score_rango(valor: float, rango: tuple) -> float:
    lo, hi = rango
    if lo <= valor <= hi:
        return 1.0
    margen = (hi - lo) * 0.5 or 1.0
    if valor < lo:
        return max(0.0, 1.0 - (lo - valor) / margen)
    else:
        return max(0.0, 1.0 - (valor - hi) / margen)


def _score_eventos_lluvia(dias: int, minimo: int = 3, optimo: int = 8) -> float:
    if dias <= minimo:
        return 0.0
    if dias >= optimo:
        return 1.0
    return (dias - minimo) / (optimo - minimo)


def _factor_temporada(especie: dict, mes_actual: int) -> float:
    """
    Factor multiplicativo según el momento de la temporada.
    - Plena: 1.0
    - Inicio: 0.9
    - Final: 0.7 si termófila, 0.9 si tardía
    """
    temporada = especie.get("temporada", {})
    if not temporada:
        return 1.0
    if mes_actual in temporada.get("plena", []):
        return 1.0
    if mes_actual in temporada.get("inicio", []):
        return 0.9
    if mes_actual in temporada.get("final", []):
        if especie.get("termofila", False):
            return 0.7
        return 0.9
    return 1.0


def puntuar_especie(
    especie: dict,
    meteo: dict,
    habitat_ok: bool,
    altitud_ok: bool,
    mes_actual: int | None = None,
    geologia: str | None = None, # nuevo parámetro para geología
    orientacion: str | None = None, # nuevo parámetro para orientación
) -> dict:
    desglose = {}
 
    s_lluvia_60 = _score_rango(meteo["lluvia_60d"], especie["lluvia_60d_optima"])
    desglose["lluvia_60d"] = round(s_lluvia_60, 2)

    s_lluvia_20 = _score_rango(meteo["lluvia_20d"], especie["lluvia_20d_optima"])
    desglose["lluvia_20d"] = round(s_lluvia_20, 2)

    dias = meteo.get("dias_lluvia_20d", 0)
    s_eventos = _score_eventos_lluvia(dias)
    desglose["dias_lluvia_20d"] = round(s_eventos, 2)

    s_t_min = _score_rango(meteo["t_min_media"], especie["t_min_optima"])
    desglose["t_min"] = round(s_t_min, 2)

    s_t_max = _score_rango(meteo["t_max_media"], especie["t_max_optima"])
    desglose["t_max"] = round(s_t_max, 2)

    if meteo.get("heladas_fuertes"):
        s_helada = 0.5 if especie["tolera_heladas"] else 0.0
    else:
        s_helada = 1.0
    desglose["heladas"] = round(s_helada, 2)

    s_altitud = 1.0 if altitud_ok else 0.0
    desglose["altitud"] = s_altitud

    if not habitat_ok:
        return {"score": 0.0, "desglose": desglose, "motivo": "Hábitat no favorable"}

     # 7. Geología
    if geologia and especie.get("geologia_optima"):
        if geologia in especie["geologia_optima"]:
            s_geologia = 1.0
        elif "mixto" in especie["geologia_optima"] or geologia == "mixto":
            s_geologia = 0.7
        else:
            s_geologia = 0.3
        desglose["geologia"] = round(s_geologia, 2)
    else:
        s_geologia = 1.0
        desglose["geologia"] = 1.0

    # 8. Orientación
    if orientacion and especie.get("orientacion_optima"):
        if orientacion in especie["orientacion_optima"]:
            s_orientacion = 1.0
        elif orientacion == "plano":
            s_orientacion = 0.8
        else:
            s_orientacion = 0.3
        desglose["orientacion"] = round(s_orientacion, 2)
    else:
        s_orientacion = 1.0
        desglose["orientacion"] = 1.0
    
    # Reequilibrar pesos (bajar un poco los demás para meter geologia)
    pesos = {
        "lluvia_60d": 0.07,
        "lluvia_20d": 0.22,
        "dias_lluvia_20d": 0.16,
        "t_min": 0.12,
        "t_max": 0.04,
        "heladas": 0.12,
        "altitud": 0.04,
        "geologia": 0.08,
        "orientacion": 0.05,
    }
    # Sumar todos los factores, incluyendo geologia
    score = (
        pesos["lluvia_60d"] * s_lluvia_60
        + pesos["lluvia_20d"] * s_lluvia_20
        + pesos["dias_lluvia_20d"] * s_eventos
        + pesos["t_min"] * s_t_min
        + pesos["t_max"] * s_t_max
        + pesos["heladas"] * s_helada
        + pesos["altitud"] * s_altitud
        + pesos["geologia"] * s_geologia
        + pesos["orientacion"] * s_orientacion
    )

    if dias < 3:
        score *= 0.6

    if mes_actual is not None:
        score *= _factor_temporada(especie, mes_actual)

    return {
        "score": round(score, 3),
        "desglose": desglose,
        "motivo": _motivo(desglose, especie, score),
    }


def _motivo(desglose: dict, especie: dict, score: float) -> str:
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