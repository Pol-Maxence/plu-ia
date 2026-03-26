"""
Module d'accès à l'API Géoportail de l'Urbanisme (apicarto.ign.fr).

Responsabilités :
- Récupérer le zonage PLU à partir de coordonnées GPS (lat/lon)
- Récupérer le texte du règlement PLU depuis le Géoportail de l'Urbanisme
"""

import json
import logging
import re
import urllib3
import requests

# Désactive le warning SSL pour geoportail-urbanisme.gouv.fr dont le certificat
# n'est pas reconnu par le store Python/Windows en local.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
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
            verify=False,
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


def get_reglement_plu_text(partition: str, nomfic: str) -> tuple[str, list]:
    """
    Télécharge le PDF du règlement PLU et en extrait le texte avec signets.

    Args:
        partition : identifiant GPU du document (ex: "DU_75056")
        nomfic    : nom du fichier PDF (ex: "75056_reglement_20230101.pdf")

    Retourne (texte_complet, toc) où :
    - texte_complet : texte de toutes les pages avec marqueurs \\f[PAGE N] entre elles
    - toc : liste de signets PDF [[level, title, page], ...] ou [] si aucun signet
    En cas d'échec retourne ("", []).
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        logger.error("pymupdf non installé — pip install pymupdf")
        return "", []

    # Récupération de l'ID du document pour construire l'URL de téléchargement
    doc_id = _get_document_id(partition)
    if not doc_id:
        logger.error("Impossible de récupérer l'ID du document pour %r", partition)
        return "", []

    url = f"{_BASE_GPU_DOC}/document/{doc_id}/files/{nomfic}"
    try:
        r = requests.get(url, timeout=60, allow_redirects=True, verify=False)
        r.raise_for_status()

        doc = fitz.open(stream=r.content, filetype="pdf")
        toc = doc.get_toc()  # [[level, title, page], ...] ou [] si pas de signets
        # Marqueurs de page pour permettre la navigation TOC sans ré-ouvrir le PDF
        texte = ""
        for i, page in enumerate(doc):
            texte += f"\f[PAGE {i + 1}]\n{page.get_text()}"
        logger.info(
            "PLU téléchargé : %d caractères, %d signets (%s)",
            len(texte), len(toc), nomfic,
        )
        return texte, toc
    except Exception as e:
        logger.error("Erreur récupération règlement PLU %r/%r : %s", partition, nomfic, e)
        return "", []


def _zone_re(zone: str) -> str:
    """
    Convertit un code de zone en pattern regex tolérant les espaces entre chiffres et lettres.
    Exemples : '1AUm' → '1\\s*AUm', 'UV7.1' → 'UV\\s*7\\.1', 'UCb' → 'UCb' (inchangé).
    Nécessaire car certains PLU écrivent '1 AUm' (avec espace) alors que l'API retourne '1AUm'.
    """
    esc = re.escape(zone)
    # Insérer \\s* aux frontières chiffre→lettre et lettre→chiffre
    esc = re.sub(r'(\d)([A-Za-z])', r'\1\\s*\2', esc)
    esc = re.sub(r'([A-Za-z])(\d)', r'\1\\s*\2', esc)
    return esc


_RUBRIQUE_RE = re.compile(
    r"emprise|hauteur|stationnement|implantation|recul|retrait"
    r"|espaces?\s*(?:verts?|libres?|non\s+b[aâ]tis?|pleine\s+terre|v[eé]g[eé]tal)"
    r"|plantations?|coefficient\s+de\s+v[eé]g[eé]talisation"
    r"|dispositions?\s+r[eé]glementaires?|cas\s+g[eé]n[eé]ral",
    re.IGNORECASE,
)


def extraire_section_via_toc(texte: str, zone: str, toc: list) -> str:
    """
    Extraction de section basée sur les signets PDF (doc.get_toc()).

    Cherche le code de zone dans les titres de signets, puis extrait le texte
    compris entre les marqueurs \\f[PAGE N] correspondants.

    Pour les PLUi avec TOC profonde (ex: Bordeaux Métropole), si la zone a des
    sous-signets correspondant aux rubriques clés (emprise, hauteur, stationnement,
    implantation…), on extrait uniquement ces sous-sections ciblées plutôt que
    tout le texte depuis le début de zone (qui peut faire 40+ pages).

    Args:
        texte : texte complet du PDF avec marqueurs \\f[PAGE N]
        zone  : code de zone PLU (ex: "UA", "UM16", "URs2d9")
        toc   : signets PDF [[level, title, page], ...]

    Retourne "" si la zone n'est pas trouvée dans les signets.
    """
    esc = _zone_re(zone)
    zone_idx: int | None = None
    zone_level: int | None = None
    zone_page: int | None = None
    end_page: int | None = None

    for i, (level, title, page) in enumerate(toc):
        if re.search(rf"\b{esc}\b", title, re.IGNORECASE):
            zone_idx = i
            zone_level = level
            zone_page = page
            for level2, _, page2 in toc[i + 1:]:
                if level2 <= level:
                    end_page = page2
                    break
            break

    if zone_page is None:
        return ""

    # --- Stratégie A : sous-signets sur les rubriques clés (PLUi profond) ---
    # Si la zone a des signets enfants correspondant à emprise/hauteur/stationnement...,
    # extraire ces sous-sections ciblées plutôt que les 25 000 premiers chars de la zone.
    if zone_idx is not None:
        pages_cles: list[tuple[str, int]] = []  # (titre, page_debut)
        for j in range(zone_idx + 1, len(toc)):
            clevel, ctitle, cpage = toc[j]
            if clevel <= zone_level:
                break
            if _RUBRIQUE_RE.search(ctitle):
                pages_cles.append((ctitle, cpage))

        if pages_cles:
            # Trier : "dispositions réglementaires / cas général" en premier (valeurs numériques),
            # ensuite les autres rubriques. À priorité égale, ordre croissant de page.
            _PRIO_RE = re.compile(r"dispositions?\s+r[eé]glementaires?|cas\s+g[eé]n[eé]ral", re.IGNORECASE)
            pages_cles.sort(key=lambda x: (0 if _PRIO_RE.search(x[0]) else 1, x[1]))

            # Dédupliquer : ignorer les bookmarks qui tombent sur une page déjà extraite
            seen_pages: set[int] = set()
            extraits = []
            for ctitle, cpage in pages_cles:
                if cpage in seen_pages:
                    continue
                seen_pages.add(cpage)
                start_marker = f"\f[PAGE {cpage}]"
                si = texte.find(start_marker)
                if si == -1:
                    continue
                # Taille fixe 6 000 chars (≈4 pages) — ne pas borner au prochain signet
                # car les valeurs numériques sont souvent sur la page suivante du titre.
                chunk = texte[si: si + 6_000]
                chunk = re.sub(r"\f\[PAGE \d+\]\n", "\n", chunk)
                extraits.append(f"### {ctitle}\n{chunk}")

            if extraits:
                result = "\n\n---\n\n".join(extraits)
                logger.info(
                    "Section zone %r extraite via %d sous-signets PDF (%d chars)",
                    zone, len(extraits), len(result),
                )
                return result[:40_000]

    # --- Stratégie B : plage de pages zone complète (PLU simple) ---
    start_marker = f"\f[PAGE {zone_page}]"
    end_marker = f"\f[PAGE {end_page}]" if end_page else None
    start_idx = texte.find(start_marker)
    if start_idx == -1:
        return ""

    end_idx = texte.find(end_marker, start_idx) if end_marker else len(texte)
    if end_idx == -1:
        end_idx = len(texte)

    # Limiter à 60 000 chars (≈20 pages) avant de retirer les marqueurs
    section = texte[start_idx: min(end_idx, start_idx + 60_000)]
    section = re.sub(r"\f\[PAGE \d+\]\n", "\n", section)
    return section[:25_000]


def extraire_section_zone(texte_plu: str, zone: str, toc: list | None = None) -> str:
    """
    Extrait la section du règlement PLU correspondant à la zone donnée.
    Évite d'envoyer un document entier (parfois 500+ pages) au LLM.

    Stratégie :
    1. Si toc non vide → extraire_section_via_toc (signets PDF, robuste)
    2. Sinon → regex sur le texte brut (fallback, formats variés)
    3. Si regex échoue → 8 000 premiers caractères (dernier recours)
    """
    if not texte_plu:
        return ""

    # --- Stratégie 1 : signets PDF (robuste, indépendant du format textuel) ---
    if toc:
        result = extraire_section_via_toc(texte_plu, zone, toc)
        if result:
            logger.info("Section zone %r extraite via signets PDF (%d chars)", zone, len(result))
            return result
        logger.warning("Zone %r absente des signets PDF, fallback regex", zone)

    # --- Stratégie 2 : regex sur le texte brut ---
    # Détecte une ligne de table des matières :
    # - commence par des espaces/points/underscores sur ≥6 chars (ex: "........ 23")
    # - ou se termine par un numéro de page seul (ex: ".................. 23")
    # - ou contient la pattern "....N" dans les 250 chars suivants le match
    _TOC_START = re.compile(r"[\s\._]{6,}|\d{1,3}\s*$")
    _TOC_INNER = re.compile(r"\.{4,}\s*\d{1,3}\s*(?:\n|$)", re.MULTILINE)

    def _est_toc(suite: str) -> bool:
        """Retourne True si la suite indique une entrée de table des matières."""
        stripped = suite.lstrip()
        return bool(_TOC_START.match(stripped)) or bool(_TOC_INNER.search(suite))

    def _chercher(z: str) -> re.Match | None:
        """
        Cherche le début de la section pour un code de zone donné.
        Ignore les correspondances dans la table des matières.
        Utilise (?!\\w) au lieu de \\b pour gérer les zones finissant par un
        caractère non-alphanumérique (ex: UAa+).
        """
        esc = _zone_re(z)
        patterns = [
            # Titre de chapitre explicite : "ZONE UA", "Zone UG", "ZONE 1 AUm"
            rf"(?:^|\n)\s*(?:ZONE|Zone)\s+{esc}(?!\w)",
            # Titre sur deux lignes (format PLUi pymupdf) : "zone\nUV7.1"
            rf"(?:^|\n)\s*(?:ZONE|Zone|zone)\s*\n\s*{esc}(?!\w)",
            # Article numéroté : "ARTICLE UA 1", "ARTICLE 1 AUm 1"
            rf"(?:^|\n)\s*ARTICLE\s+{esc}\s+\d",
            # Article simple : "Article UA -", "Article UA."
            rf"(?:^|\n)\s*Article\s+{esc}[\s\.\-–]",
            # Titre avec tiret : "UA - Dispositions", "UC –"
            rf"(?:^|\n)\s*{esc}\s*[-–]\s",
            # Chapitre/Titre : "CHAPITRE UA", "TITRE UC"
            rf"(?:^|\n)\s*(?:CHAPITRE|TITRE|SECTION)\s+(?:[\w\s]*\s+)?{esc}(?!\w)",
            # Dispositions applicables à la zone : "Dispositions applicables à la zone UA"
            rf"Dispositions applicables\s+(?:à\s+)?(?:la\s+)?zone\s+{esc}(?!\w)",
        ]
        for p in patterns:
            for m in re.finditer(p, texte_plu, re.IGNORECASE | re.MULTILINE):
                # Ignorer les entrées de table des matières
                suite = texte_plu[m.end(): m.end() + 250]
                if _est_toc(suite):
                    continue
                return m
        return None

    match = _chercher(zone)

    # Fallback 1 : supprimer les suffixes non-alphanumériques (ex: UAa+ → UAa)
    zone_stripped = re.sub(r'[^a-zA-Z0-9]+$', '', zone)
    if not match and zone_stripped != zone:
        match = _chercher(zone_stripped)
        if match:
            logger.info("Zone %r non trouvée, section extraite depuis %r (strip suffixe)", zone, zone_stripped)

    # Fallback 2 : zone de base (majuscules uniquement, ex: UCb → UC, UAa+ → UA)
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
        extrait = section_complete[:15_000]

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

    _zre = _zone_re(zone_base)

    # --- Format 1 : articles numérotés classiques (PLU) ---
    # Articles 6=recul voirie, 7=recul limites, 8=entre bâtiments,
    # 9=emprise, 10=hauteur, 11=aspect, 12=stationnement, 13=espaces verts, 14=COS
    for num in ["6", "7", "8", "9", "10", "11", "12", "13", "14"]:
        m = re.search(
            rf"(?:ARTICLE\s+{_zre}\s*{num}\b|"
            rf"{_zre}\s*{num}\s*[-:]\s*(?:EMPRISE|HAUTEUR|STATIONNEMENT|COS|COEFFICIENT))",
            section,
            re.IGNORECASE,
        )
        if m:
            extraits.append(section[max(0, m.start() - 20): m.start() + 2_000])

    # --- Format 1b : article par titre de rubrique (PLUi, numéro variable) ---
    # Ex: "ARTICLE UA 5 : HAUTEUR DES CONSTRUCTIONS"
    # Ex: "ARTICLE UA 6 : IMPLANTATIONS DES CONSTRUCTIONS PAR RAPPORT AUX VOIES"
    # Le pattern tolère jusqu'à 80 caractères entre le séparateur (:) et le mot-clé
    # pour couvrir les titres PLUi avec "DES CONSTRUCTIONS" ou autre texte intermédiaire.
    for rubrique in [
        r"HAUTEUR\b",
        r"EMPRISE\s+AU\s+SOL",
        r"STATIONNEMENT",
        r"IMPLANTATION[S]?\s+(?:[^\n]{0,40})?PAR\s+RAPPORT\s+AUX\s+VOIES",
        r"IMPLANTATION[S]?\s+(?:[^\n]{0,40})?PAR\s+RAPPORT\s+AUX\s+LIMITES",
        r"ESPACES\s+(?:NON\s+BATIS|LIBRES|VERTS|VEGETALISES)",
        r"TRAITEMENT\s+(?:DES\s+)?ESPACES",
    ]:
        m = re.search(
            rf"ARTICLE\s+{_zre}\s+\d+\s*[:\-–][^\n]{{0,80}}{rubrique}",
            section,
            re.IGNORECASE,
        )
        if m:
            extraits.append(section[max(0, m.start() - 20): m.start() + 3_000])

    # --- Format 2 : titres libres (PLUi tableau ou PLU avec titres en clair) ---
    mots_cles = [
        r"Emprise\s+au\s+sol\s+des\s+constructions",
        r"Emprise\s+au\s+sol",
        r"Hauteur\s+maximale\s+des\s+constructions",
        r"Hauteur\s+(?:maximale|des\s+constructions|maximale\s+autorisée)",
        r"Espaces\s+(?:non\s+b[aâ]tis|libres|verts|v[eé]g[eé]talis[eé]s)",
        r"Traitement\s+(?:des\s+)?espaces",
    ]
    for pattern in mots_cles:
        m = re.search(pattern, section, re.IGNORECASE)
        if m:
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
