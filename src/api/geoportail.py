"""
Module d'accès à l'API Géoportail de l'Urbanisme (apicarto.ign.fr).

Responsabilités :
- Récupérer le zonage PLU à partir de coordonnées GPS (lat/lon)
- Récupérer le texte du règlement PLU depuis le Géoportail de l'Urbanisme
"""

import json
import logging
import re
import requests
from src.api.models import ZonePLU

logger = logging.getLogger(__name__)

_BASE_GPU = "https://apicarto.ign.fr/api/gpu"
_BASE_GPU_DOC = "https://www.geoportail-urbanisme.gouv.fr/api"


def get_zonage_plu(lat: float, lon: float) -> ZonePLU:
    """
    Récupère le zonage PLU applicable à des coordonnées GPS.
    Utilise apicarto.ign.fr/api/gpu/zone-urba.
    """
    try:
        geom = json.dumps({"type": "Point", "coordinates": [lon, lat]})
        r = requests.get(
            f"{_BASE_GPU}/zone-urba",
            params={"geom": geom},
            timeout=15,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
        if not features:
            raise ValueError(f"Aucun zonage PLU trouvé pour ({lat}, {lon})")
        props = features[0]["properties"]
        # partition = identifiant du document d'urbanisme (ex: "75056_PLU_...")
        partition = props.get("partition", "")
        # code_insee extrait de la partition si disponible
        code_insee = re.match(r"(\d{5})", partition)
        code_insee = code_insee.group(1) if code_insee else partition
        return ZonePLU(
            zone=props.get("libelle", ""),
            libelle=props.get("libelong", props.get("libelle", "")),
            partition=partition,
            nomfic=props.get("nomfic", ""),
        )
    except Exception as e:
        logger.error("Erreur récupération zonage PLU (%s, %s) : %s", lat, lon, e)
        raise


def get_documents_urba(code_insee: str) -> list[dict]:
    """
    Liste les documents d'urbanisme disponibles pour une commune.
    Retourne une liste de documents (PLU, PLUi, POS, CC...).
    """
    try:
        r = requests.get(
            f"{_BASE_GPU}/document",
            params={"codeDep": code_insee[:2], "codeCommune": code_insee},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("features", [])
    except Exception as e:
        logger.error("Erreur récupération documents urba %r : %s", code_insee, e)
        raise


def get_reglement_plu_text(partition: str, nomfic: str) -> str:
    """
    Télécharge le PDF du règlement PLU et en extrait le texte.

    Args:
        partition : identifiant GPU du document (ex: "DU_75056")
        nomfic    : nom du fichier PDF (ex: "75056_reglement_20230101.pdf")

    Retourne le texte extrait du PDF, ou une chaîne vide en cas d'échec.
    """
    import io
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams
    except ImportError:
        logger.error("pdfminer.six non installé — pip install pdfminer.six")
        return ""

    url = f"https://piece-ecrite.geoportail-urbanisme.gouv.fr/{partition}/{nomfic}"
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()

        output = io.StringIO()
        extract_text_to_fp(io.BytesIO(r.content), output, laparams=LAParams())
        texte = output.getvalue()
        logger.info("PLU téléchargé : %d caractères extraits (%s)", len(texte), nomfic)
        return texte
    except Exception as e:
        logger.error("Erreur récupération règlement PLU %r/%r : %s", partition, nomfic, e)
        return ""


def extraire_section_zone(texte_plu: str, zone: str) -> str:
    """
    Extrait la section du règlement PLU correspondant à la zone donnée.
    Évite d'envoyer un document entier (parfois 500+ pages) au LLM.

    Stratégie : cherche les occurrences du libellé de zone et extrait
    le contexte environnant (±3000 caractères).
    """
    if not texte_plu:
        return ""

    # Cherche les titres de section correspondant à la zone (ex: "ZONE UA", "Article UA")
    pattern = re.compile(
        rf"(?:ZONE\s+{re.escape(zone)}|Article\s+{re.escape(zone)}[\s\.\-])",
        re.IGNORECASE,
    )
    match = pattern.search(texte_plu)
    if not match:
        # Fallback : retourner les 8000 premiers caractères
        logger.warning("Section zone %r non trouvée, fallback sur début du document", zone)
        return texte_plu[:8000]

    start = max(0, match.start() - 200)
    end = min(len(texte_plu), match.start() + 6000)
    return texte_plu[start:end]


if __name__ == "__main__":
    # Test manuel — coordonnées Paris 16e
    logging.basicConfig(level=logging.INFO)
    zone = get_zonage_plu(lat=48.8566, lon=2.3522)
    print(zone)
