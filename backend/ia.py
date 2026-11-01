"""
Generación del resumen con un LLM controlado.
"""
import httpx
import json
import os
from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_API_URL = "https://api.openai.com/v1/chat/completions"
LLM_MODEL = "gpt-4o-mini"


def _system_prompt() -> str:
    return """Eres un asistente que redacta resúmenes sobre probabilidad de setas.
Recibirás un JSON con datos ya calculados. Tu única tarea es redactar un texto claro en español.

REGLAS ESTRICTAS (obligatorias):
1. SOLO puedes usar la información del JSON. No añadas datos externos.
2. NUNCA afirmes que hay setas en la zona. Usa expresiones como "condiciones favorables", "probabilidad alta/media/baja".
3. NUNCA des consejos de comestibilidad ni identifiques especies.
4. NUNCA inventes cifras, hábitats ni motivos que no estén en el JSON.
5. Si el JSON indica "fuera_de_temporada": true, menciona que la especie no está en su temporada habitual.
6. Termina siempre con: "Recuerda que estos datos son orientativos."
7. Máximo 4 frases. Tono neutral y directo.
"""


async def generar_resumen_con_llm(
    especie_nombre: str,
    zona_txt: str,
    num_zonas: int,
    radio_km: float,
    mejor_habitat: str,
    mejor_color: str,
    mejor_prob: float,
    mejor_motivo: str,
    fuera_de_temporada: bool,
    meses_especie: list[int],
    ia_activa: bool = True,
) -> str | None:
    """
    Genera el resumen con un LLM. Devuelve None si la IA está desactivada,
    si no hay clave, o si falla, para que main.py use la plantilla.
    """
    if not ia_activa:
        print("[IA] Desactivada, usando plantilla")
        return None
    if not LLM_API_KEY:
        print("[IA] Sin OPENAI_API_KEY, usando plantilla")
        return None

    datos = {
        "especie": especie_nombre,
        "zona": zona_txt,
        "zonas_favorables": num_zonas,
        "radio_km": radio_km,
        "mejor_habitat": mejor_habitat,
        "mejor_probabilidad": mejor_color,
        "mejor_porcentaje": round(mejor_prob * 100),
        "mejor_motivo": mejor_motivo,
        "fuera_de_temporada": fuera_de_temporada,
        "meses_especie": meses_especie,
    }

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": f"Datos: {json.dumps(datos, ensure_ascii=False)}"},
        ],
        "temperature": 0.2,
        "max_tokens": 200,
    }

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(LLM_API_URL, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            texto = data["choices"][0]["message"]["content"].strip()
            if _contiene_alucinacion(texto):
                print(f"LLM alucinó, usando fallback. Texto: {texto}")
                return None
            return texto
    except Exception as e:
        print(f"Error llamando al LLM: {e}")
        return None


def _contiene_alucinacion(texto: str) -> bool:
    prohibidas = [
        "hay setas", "encontrarás setas", "puedes coger", "son comestibles",
        "sal a buscar", "abundantes setas", "muchos ejemplares",
    ]
    texto_lower = texto.lower()
    return any(p in texto_lower for p in prohibidas)


def generar_resumen_plantilla(
    especie_nombre: str, zona_txt: str, num_zonas: int, radio_km: float,
    mejor_habitat: str, mejor_color: str, mejor_prob: float, mejor_motivo: str,
    fuera_de_temporada: bool,
) -> str:
    if num_zonas == 0:
        txt = (
            f"Para la zona {zona_txt} y la especie {especie_nombre}: "
            f"no se han encontrado zonas con condiciones suficientemente favorables "
            f"en un radio de {int(radio_km)} km. "
        )
        if fuera_de_temporada:
            txt += "Además, esta especie no está en su temporada habitual. "
        return txt + "Recuerda que estos datos son orientativos."

    txt = (
        f"Para la zona {zona_txt} y la especie {especie_nombre}: "
        f"se han identificado {num_zonas} zonas con condiciones favorables "
        f"en un radio de {int(radio_km)} km. "
        f"La mejor zona es de hábitat {mejor_habitat} "
        f"con una probabilidad {mejor_color} ({int(mejor_prob*100)}%). "
        f"{mejor_motivo}. "
    )
    if fuera_de_temporada:
        txt += "AVISO: esta especie no está en su temporada habitual. "
    return txt + "Recuerda que estos datos son orientativos y no garantizan la presencia de setas."