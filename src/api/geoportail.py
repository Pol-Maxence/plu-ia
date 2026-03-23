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


def get_zonage_geojson(lat: float, lon: float) -> dict | None:
    """
    Retourne la feature GeoJSON complète (géométrie + propriétés) de la zone PLU.
    Utilisée pour afficher le polygone de zone sur la carte interactive.
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
            return None
        return features[0]
    except Exception as e:
        logger.error("Erreur récupération GeoJSON zone PLU (%s, %s) : %s", lat, lon, e)
        return None


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


def _get_document_id(partition: str) -> str | None:
    """
    Récupère l'identifiant UUID du document d'urbanisme en production pour une partition.
    Utilise l'API www.geoportail-urbanisme.gouv.fr/api/document.
    """
    try:
        r = requests.get(
            f"{_BASE_GPU_DOC}/document",
            params={"partition": partition},
            timeout=10,
        )
        r.raise_for_status()
        docs = r.json()
        for doc in docs:
            if doc.get("status") == "document.production":
                return doc["id"]
        logger.warning("Aucun document en production trouvé pour partition %r", partition)
        return None
    except Exception as e:
        logger.error("Erreur récupération ID document %r : %s", partition, e)
        return None


def get_reglement_plu_text(partition: str, nomfic: str) -> str:
    """
    Télécharge le PDF du règlement PLU et en extrait le texte.

    Args:
        partition : identifiant GPU du document (ex: "DU_75056")
        nomfic    : nom du fichier PDF (ex: "75056_reglement_20230101.pdf")

    Retourne le texte extrait du PDF, ou une chaîne vide en cas d'échec.
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        logger.error("pymupdf non installé — pip install pymupdf")
        return ""

    # Récupération de l'ID du document pour construire l'URL de téléchargement
    doc_id = _get_document_id(partition)
    if not doc_id:
        logger.error("Impossible de récupérer l'ID du document pour %r", partition)
        return ""

    url = f"{_BASE_GPU_DOC}/document/{doc_id}/files/{nomfic}"
    try:
        r = requests.get(url, timeout=60, allow_redirects=True)
        r.raise_for_status()

        doc = fitz.open(stream=r.content, filetype="pdf")
        texte = "".join(page.get_text() for page in doc)
        logger.info("PLU téléchargé : %d caractères extraits (%s)", len(texte), nomfic)
        return texte
    except Exception as e:
        logger.error("Erreur récupération règlement PLU %r/%r : %s", partition, nomfic, e)
        return ""


def extraire_section_zone(texte_plu: str, zone: str) -> str:
    """
    Extrait la section du règlement PLU correspondant à la zone donnée.
    Évite d'envoyer un document entier (parfois 500+ pages) au LLM.

    Stratégie :
    1. Cherche le début de la section via plusieurs patterns (titres courants)
    2. Si sous-zone (ex: UCb), retente avec la zone de base (UC)
    3. Extrait jusqu'au début de la zone suivante (pas une fenêtre fixe)
    """
    if not texte_plu:
        return ""

    def _chercher(z: str) -> re.Match | None:
        """Cherche le début de la section pour un code de zone donné."""
        patterns = [
            # Titre de chapitre explicite : "ZONE UA", "Zone UG"
            rf"(?:^|\n)\s*(?:ZONE|Zone)\s+{re.escape(z)}\b",
            # Titre sur deux lignes (format PLUi pymupdf) : "zone\nUV7.1"
            rf"(?:^|\n)\s*(?:ZONE|Zone|zone)\s*\n\s*{re.escape(z)}\b",
            # Article numéroté : "ARTICLE UA 1", "Article UC 1"
            rf"(?:^|\n)\s*ARTICLE\s+{re.escape(z)}\s+\d",
            # Article simple : "Article UA -", "Article UA."
            rf"(?:^|\n)\s*Article\s+{re.escape(z)}[\s\.\-–]",
            # Titre avec tiret : "UA - Dispositions", "UC –"
            rf"(?:^|\n)\s*{re.escape(z)}\s*[-–]\s",
            # Chapitre/Titre : "CHAPITRE UA", "TITRE UC"
            rf"(?:^|\n)\s*(?:CHAPITRE|TITRE|SECTION)\s+(?:[\w\s]*\s+)?{re.escape(z)}\b",
            # Dispositions applicables à la zone : "Dispositions applicables à la zone UA"
            rf"Dispositions applicables\s+(?:à\s+)?(?:la\s+)?zone\s+{re.escape(z)}\b",
        ]
        for p in patterns:
            m = re.search(p, texte_plu, re.IGNORECASE | re.MULTILINE)
            if m:
                return m
        return None

    match = _chercher(zone)

    # Si sous-zone non trouvée, tenter avec la zone de base (ex: UCb → UC)
    zone_base = re.match(r"([A-Z]+)", zone)
    if not match and zone_base and zone_base.group(1) != zone:
        z_base = zone_base.group(1)
        match = _chercher(z_base)
        if match:
            logger.info("Sous-zone %r non trouvée, section extraite depuis la zone de base %r", zone, z_base)

    if not match:
        logger.warning("Section zone %r non trouvée, fallback sur début du document", zone)
        return texte_plu[:8000]

    # Début de la section
    start = max(0, match.start() - 100)

    # Code de base de la zone courante : partie majuscule uniquement (ex: UCb → UC, N → N)
    zone_courante_base = re.match(r"([A-Z]+)", zone)
    zone_courante_base = zone_courante_base.group(1) if zone_courante_base else zone

    # Fin : chercher le début d'une AUTRE zone dans les 60 000 caractères suivants
    fenetre = texte_plu[match.end(): match.end() + 60_000]
    end = match.end() + min(30_000, len(fenetre))  # fallback

    for m in re.finditer(
        r"(?:^|\n)\s*(?:ZONE|Zone)\s+([A-Z]{1,4}[a-z]?)\b",
        fenetre,
        re.MULTILINE,
    ):
        code = m.group(1).upper()
        if code.startswith(zone_courante_base) or zone_courante_base.startswith(code):
            continue
        end = match.end() + m.start()
        break

    section_complete = texte_plu[start:end]

    # Cibler les articles clés : emprise (art. 9) et hauteur (art. 10) dans les PLU classiques.
    # Ces articles contiennent les valeurs numériques utiles au calcul capacitaire.
    articles_cles = _extraire_articles_cles(section_complete, zone_courante_base)
    if articles_cles:
        extrait = articles_cles
    else:
        extrait = section_complete[:12_000]

    logger.info("Section zone %r extraite : %d caractères (pos %d→%d)", zone, len(extrait), start, end)
    return extrait


def _extraire_articles_cles(section: str, zone_base: str) -> str:
    """
    Extrait les passages clés d'une section PLU pour le calcul capacitaire.
    Supporte deux formats :
    - PLU classique : "ARTICLE UC 9 : EMPRISE AU SOL"
    - PLUi tableau  : titres libres "Emprise au sol" / "Hauteur maximale"
    Retourne une chaîne vide si rien trouvé.
    """
    extraits = []

    # --- Format 1 : articles numérotés (PLU classique) ---
    for num in ["9", "10", "11", "14"]:
        m = re.search(
            rf"(?:ARTICLE\s+{re.escape(zone_base)}\s*{num}\b|"
            rf"{re.escape(zone_base)}\s*{num}\s*[-:]\s*(?:EMPRISE|HAUTEUR|STATIONNEMENT|COS|COEFFICIENT))",
            section,
            re.IGNORECASE,
        )
        if m:
            extraits.append(section[max(0, m.start() - 20): m.start() + 2_000])

    # --- Format 2 : titres libres (PLUi / tableau) ---
    # Chercher "Emprise au sol" et "Hauteur" comme titres de ligne de tableau
    mots_cles = [
        r"Emprise\s+au\s+sol\s+des\s+constructions",
        r"Emprise\s+au\s+sol",
        r"Hauteur\s+maximale\s+des\s+constructions",
        r"Hauteur\s+(?:maximale|des\s+constructions|maximale\s+autorisée)",
    ]
    for pattern in mots_cles:
        m = re.search(pattern, section, re.IGNORECASE)
        if m:
            # Extraire un bloc centré sur le titre (±1500 chars) pour capturer label + valeur
            debut = max(0, m.start() - 200)
            fin = min(len(section), m.start() + 1_500)
            extraits.append(section[debut:fin])

    if not extraits:
        return ""

    # Dédupliquer les extraits qui se chevauchent
    seen_positions: set[int] = set()
    result = []
    for extrait in extraits:
        pos = section.find(extrait[:50])
        if pos not in seen_positions:
            seen_positions.add(pos)
            result.append(extrait)

    return "\n\n---\n\n".join(result)


if __name__ == "__main__":
    # Test manuel — coordonnées Paris 16e
    logging.basicConfig(level=logging.INFO)
    zone = get_zonage_plu(lat=48.8566, lon=2.3522)
    print(zone)
