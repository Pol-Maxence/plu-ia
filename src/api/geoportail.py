"""
Module d'accès à l'API Géoportail de l'Urbanisme (apicarto.ign.fr).

Responsabilités :
- Récupérer le zonage PLU à partir de coordonnées GPS (lat/lon)
- Récupérer le texte du règlement PLU depuis le Géoportail de l'Urbanisme
"""

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
        r = requests.get(
            f"{_BASE_GPU}/zone-urba",
            params={"lon": lon, "lat": lat},
            timeout=10,
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


def get_reglement_plu_text(partition: str) -> str:
    """
    Récupère le texte brut du règlement PLU depuis le Géoportail de l'Urbanisme.
    partition = identifiant du document (ex: "75056_PLU_20230101")

    Retourne le contenu textuel du règlement (peut être long — plusieurs Mo).
    En cas d'échec, retourne une chaîne vide et logue l'erreur.
    """
    try:
        url = f"{_BASE_GPU_DOC}/{partition}/download"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.error("Erreur récupération règlement PLU %r : %s", partition, e)
        # Retourne vide plutôt que de bloquer tout le pipeline
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
