"""
Consulta local de CORINE Land Cover 2018 para obtener el hábitat en un punto.
El bbox se reproyecta a EPSG:25830 porque pyogrio lo interpreta en el CRS
del fichero, no en EPSG:4326.
"""
from pathlib import Path
import pyogrio
import geopandas as gpd
from pyproj import Transformer

CLC_PATH = Path(__file__).parent / "datos" / "CLC2018_GPKG" / "CLC2018_ES.gpkg"
CLC_LAYER = "CLC18_ES"
CLC_CRS = "EPSG:25830"

CLC_A_HABITAT = {
    "311": "robledal",
    "312": "pinar",
    "313": "mixto",
    "321": "pastizal",
    "322": "matorral",
    "323": "matorral",
    "324": "matorral",
    "333": "erial",
    "211": "erial",
    "231": "pastizal",
    "242": "mixto",
    "243": "pastizal",
}

_transformer = Transformer.from_crs("EPSG:4326", CLC_CRS, always_xy=True)


def _bbox_en_utm(lat: float, lng: float, radio_m: float = 1500) -> tuple:
    x, y = _transformer.transform(lng, lat)
    return (x - radio_m, y - radio_m, x + radio_m, y + radio_m)


# def obtener_habitat(lat: float, lng: float) -> dict:
#     gdf = pyogrio.read_dataframe(
#         CLC_PATH,
#         layer=CLC_LAYER,
#         bbox=_bbox_en_utm(lat, lng),
#         columns=["CODE_18", "AREA_HA", "geometry"],
#     )

#     if gdf.empty:
#         return {"habitat": "desconocido", "clc_code": None, "area_ha": None}

#     x, y = _transformer.transform(lng, lat)
#     punto = gpd.points_from_xy([x], [y])[0]

#     contiene = gdf[gdf.contains(punto)]

#     if contiene.empty:
#         idx = gdf.distance(punto).idxmin()
#         fila = gdf.loc[idx]
#     else:
#         fila = contiene.iloc[0]

#     codigo = str(fila["CODE_18"])
#     return {
#         "habitat": CLC_A_HABITAT.get(codigo[:3], "desconocido"),
#         "clc_code": codigo,
#         "area_ha": float(fila["AREA_HA"]),
#     }
"""
Consulta local de CORINE Land Cover 2018.
- obtener_habitat(lat, lng): hábitat para un punto.
- obtener_poligonos(lat, lng, radio_km): polígonos en un área.
"""


CLC_PATH = Path(__file__).parent / "datos" / "CLC2018_GPKG" / "CLC2018_ES.gpkg"
CLC_LAYER = "CLC18_ES"
CLC_CRS = "EPSG:25830"

CLC_A_HABITAT = {
    "111": "urbano", "112": "urbano", "121": "urbano", "122": "urbano",
    "123": "urbano", "124": "urbano", "131": "erial", "132": "erial",
    "133": "erial", "141": "urbano", "142": "urbano",
    "211": "erial", "212": "erial", "213": "erial",
    "221": "erial", "222": "erial", "223": "erial",
    "231": "pastizal", "241": "pastizal", "242": "mixto",
    "243": "pastizal", "244": "mixto",
    "311": "robledal", "312": "pinar", "313": "mixto",
    "321": "pastizal", "322": "matorral", "323": "matorral",
    "324": "matorral",
    "331": "erial", "332": "erial", "333": "erial",
    "334": "erial", "335": "erial",
    "411": "erial", "412": "erial",
    "421": "erial", "422": "erial", "423": "erial",
    "511": "erial", "512": "erial",
    "521": "erial", "522": "erial", "523": "erial",
}

_transformer = Transformer.from_crs("EPSG:4326", CLC_CRS, always_xy=True)
_transformer_back = Transformer.from_crs(CLC_CRS, "EPSG:4326", always_xy=True)


def _bbox_en_utm(lat: float, lng: float, radio_m: float = 1500) -> tuple:
    x, y = _transformer.transform(lng, lat)
    return (x - radio_m, y - radio_m, x + radio_m, y + radio_m)


def obtener_habitat(lat: float, lng: float) -> dict:
    gdf = pyogrio.read_dataframe(
        CLC_PATH, layer=CLC_LAYER,
        bbox=_bbox_en_utm(lat, lng),
        columns=["CODE_18", "AREA_HA", "geometry"],
        use_arrow=True,
    )
    if gdf.empty:
        return {"habitat": "desconocido", "clc_code": None, "area_ha": None}

    x, y = _transformer.transform(lng, lat)
    punto = gpd.points_from_xy([x], [y])[0]
    contiene = gdf[gdf.contains(punto)]
    fila = contiene.iloc[0] if not contiene.empty else gdf.loc[gdf.distance(punto).idxmin()]
    codigo = str(fila["CODE_18"])
    return {
        "habitat": CLC_A_HABITAT.get(codigo[:3], "desconocido"),
        "clc_code": codigo,
        "area_ha": float(fila["AREA_HA"]),
    }


def obtener_poligonos(lat: float, lng: float, radio_km: float = 15.0) -> list[dict]:
    """
    Devuelve los polígonos de CORINE dentro de un radio alrededor del punto,
    con su hábitat, centroide (lat/lng) y geometría en EPSG:4326.
    """
    radio_m = radio_km * 1000
    gdf = pyogrio.read_dataframe(
        CLC_PATH, layer=CLC_LAYER,
        bbox=_bbox_en_utm(lat, lng, radio_m),
        columns=["CODE_18", "AREA_HA", "geometry"],
        use_arrow=True,
    )
    if gdf.empty:
        return []

    # Reproyectar a EPSG:4326 para devolver GeoJSON estándar
    gdf = gdf.to_crs("EPSG:4326")

    resultados = []
    for _, fila in gdf.iterrows():
        codigo = str(fila["CODE_18"])
        habitat = CLC_A_HABITAT.get(codigo[:3], "desconocido")
        if habitat in ("urbano", "desconocido"):
            continue  # no nos interesan para setas
        centroide = fila.geometry.centroid
        resultados.append({
            "habitat": habitat,
            "clc_code": codigo,
            "area_ha": float(fila["AREA_HA"]),
            "lat": centroide.y,
            "lng": centroide.x,
            "geometry": fila.geometry,
        })
    return resultados
    