"""
Point d'entrée CLI du pipeline PLU·IA.

Usage :
    python -m src.main "15 rue de la Paix, Paris"
    python -m src.main --ref 75056000BX0042

Pipeline :
    adresse → cadastre → géoportail PLU → extraction LLM → calcul capacitaire → rapport PDF
"""

import argparse
import logging
import sys

import anthropic
from dotenv import load_dotenv

from src.api.cadastre import get_parcelle_by_address, get_parcelle_by_ref
from src.api.geoportail import get_zonage_plu, get_reglement_plu_text, extraire_section_zone
from src.parser.plu_extractor import extraire_regles_plu
from src.engine.capacity import calculer_capacite
from src.report.pdf_generator import generer_rapport

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def run(adresse: str | None = None, ref_cadastrale: str | None = None, output: str = "rapport.pdf", confirm: bool = False) -> None:
    """
    Exécute le pipeline complet et génère le rapport PDF.

    Args:
        adresse        : adresse postale (ex: "15 rue de la Paix, Paris")
        ref_cadastrale : référence cadastrale (ex: "75056000BX0042")
        output         : chemin du fichier PDF de sortie
    """
    client = anthropic.Anthropic()

    # --- Étape 1 : récupération parcelle ---
    if ref_cadastrale:
        logger.info("Récupération parcelle : %s", ref_cadastrale)
        parcelle = get_parcelle_by_ref(ref_cadastrale)
    elif adresse:
        logger.info("Récupération parcelle : %s", adresse)
        parcelle = get_parcelle_by_address(adresse)
    else:
        raise ValueError("Fournir une adresse ou une référence cadastrale")

    logger.info("Parcelle : %s — %s m² — %s", parcelle.ref_cadastrale, parcelle.surface_m2, parcelle.commune)

    # --- Confirmation interactive ---
    if confirm:
        print(f"\n  Référence : {parcelle.ref_cadastrale}")
        print(f"  Commune   : {parcelle.commune}")
        print(f"  Surface   : {parcelle.surface_m2:.0f} m²")
        if parcelle.adresse:
            print(f"  Adresse   : {parcelle.adresse}")
        reponse = input("\nContinuer l'analyse sur cette parcelle ? [o/N] ").strip().lower()
        if reponse not in ("o", "oui", "y", "yes"):
            logger.info("Analyse annulée par l'utilisateur.")
            sys.exit(0)

    # --- Étape 2 : zonage PLU ---
    logger.info("Récupération zonage PLU...")
    # Centroïde approximatif selon le type GeoJSON retourné par l'API IGN
    geom = parcelle.geometrie
    if geom["type"] == "Point":
        lon, lat = geom["coordinates"]
    elif geom["type"] == "Polygon":
        coords = geom["coordinates"][0]
        lon = sum(c[0] for c in coords) / len(coords)
        lat = sum(c[1] for c in coords) / len(coords)
    elif geom["type"] == "MultiPolygon":
        # Prendre le premier anneau du premier polygone
        coords = geom["coordinates"][0][0]
        lon = sum(c[0] for c in coords) / len(coords)
        lat = sum(c[1] for c in coords) / len(coords)
    else:
        raise ValueError(f"Type de géométrie non supporté : {geom['type']}")

    zonage = get_zonage_plu(lat=lat, lon=lon)
    logger.info("Zone PLU : %s (%s)", zonage.zone, zonage.libelle)

    # --- Étape 3 : texte du règlement PLU ---
    logger.info("Téléchargement règlement PLU (partition : %s)...", zonage.partition)
    texte_plu = get_reglement_plu_text(zonage.partition, zonage.nomfic)
    texte_zone = extraire_section_zone(texte_plu, zonage.zone)
    logger.info("Texte PLU extrait : %d caractères", len(texte_zone))

    # --- Étape 4 : extraction LLM ---
    logger.info("Analyse PLU par IA (zone %s)...", zonage.zone)
    regles = extraire_regles_plu(texte_zone, zonage.zone, client)
    emprise_log = "non réglementée" if regles.emprise_non_reglementee else (f"{regles.emprise_sol_max_pct}%" if regles.emprise_sol_max_pct else "inconnue")
    logger.info("Règles extraites : emprise=%s, hauteur=%sm", emprise_log, regles.hauteur_max_m)

    # --- Étape 5 : calcul capacitaire ---
    logger.info("Calcul capacitaire...")
    etude = calculer_capacite(parcelle, regles)
    logger.info(
        "Résultats : SP max=%sm² — %s à %s logements",
        etude.surface_plancher_max_m2,
        etude.nb_logements_estimes_min,
        etude.nb_logements_estimes_max,
    )

    # --- Étape 6 : rapport PDF ---
    import os as _os
    _os.makedirs(_os.path.dirname(output) or ".", exist_ok=True)
    logger.info("Génération du rapport PDF...")
    generer_rapport(parcelle, regles, etude, output=output)
    logger.info("✓ Rapport généré : %s", output)


def run_multi(refs: list[str], output: str = "output/rapport.pdf") -> None:
    """
    Exécute le pipeline sur plusieurs parcelles adjacentes et génère un rapport PDF consolidé.

    Args:
        refs   : liste de références cadastrales (ex: ["75056000BX0042", "75056000BX0043"])
        output : chemin du fichier PDF de sortie
    """
    if not refs:
        raise ValueError("Fournir au moins une référence cadastrale")

    client = anthropic.Anthropic()

    # --- Étape 1 : récupération de toutes les parcelles ---
    parcelles = []
    for ref in refs:
        logger.info("Récupération parcelle : %s", ref)
        parcelles.append(get_parcelle_by_ref(ref))

    surface_totale = sum(p.surface_m2 for p in parcelles)
    logger.info(
        "%d parcelle(s) récupérée(s) — surface totale : %s m²",
        len(parcelles), surface_totale,
    )

    # --- Étape 2 : zonage PLU (centroïde de la première parcelle) ---
    geom = parcelles[0].geometrie
    if geom["type"] == "Point":
        lon, lat = geom["coordinates"]
    elif geom["type"] == "Polygon":
        coords = geom["coordinates"][0]
        lon = sum(c[0] for c in coords) / len(coords)
        lat = sum(c[1] for c in coords) / len(coords)
    elif geom["type"] == "MultiPolygon":
        coords = geom["coordinates"][0][0]
        lon = sum(c[0] for c in coords) / len(coords)
        lat = sum(c[1] for c in coords) / len(coords)
    else:
        raise ValueError(f"Type de géométrie non supporté : {geom['type']}")

    logger.info("Récupération zonage PLU...")
    zonage = get_zonage_plu(lat=lat, lon=lon)
    logger.info("Zone PLU : %s (%s)", zonage.zone, zonage.libelle)

    # --- Étape 3 : texte du règlement PLU ---
    logger.info("Téléchargement règlement PLU (partition : %s)...", zonage.partition)
    texte_plu = get_reglement_plu_text(zonage.partition, zonage.nomfic)
    texte_zone = extraire_section_zone(texte_plu, zonage.zone)
    logger.info("Texte PLU extrait : %d caractères", len(texte_zone))

    # --- Étape 4 : extraction LLM ---
    logger.info("Analyse PLU par IA (zone %s)...", zonage.zone)
    regles = extraire_regles_plu(texte_zone, zonage.zone, client)

    # --- Étape 5 : calcul capacitaire sur parcelle synthétique (surface totale) ---
    from src.api.models import Parcelle
    parcelle_synthetique = Parcelle(
        ref_cadastrale=" + ".join(refs),
        surface_m2=surface_totale,
        commune=parcelles[0].commune,
        code_insee=parcelles[0].code_insee,
        geometrie=parcelles[0].geometrie,
    )
    logger.info("Calcul capacitaire sur surface totale %s m²...", surface_totale)
    etude = calculer_capacite(parcelle_synthetique, regles)
    logger.info(
        "Résultats : SP max=%s m² — %s à %s logements",
        etude.surface_plancher_max_m2,
        etude.nb_logements_estimes_min,
        etude.nb_logements_estimes_max,
    )

    # --- Étape 6 : rapport PDF consolidé ---
    import os as _os
    _os.makedirs(_os.path.dirname(output) or ".", exist_ok=True)
    logger.info("Génération du rapport PDF consolidé...")
    generer_rapport(parcelle_synthetique, regles, etude, output=output, all_parcelles=parcelles)
    logger.info("✓ Rapport généré : %s", output)


def main() -> None:
    parser = argparse.ArgumentParser(description="PLU·IA — Étude capacitaire automatisée")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("adresse", nargs="?", help="Adresse postale (ex: '15 rue de la Paix, Paris')")
    group.add_argument("--ref", dest="ref_cadastrale", help="Référence cadastrale (ex: 75056000BX0042)")
    group.add_argument("--refs", nargs="+", dest="refs_multiples", metavar="REF",
                       help="Plusieurs références cadastrales pour analyse multi-parcelles")
    parser.add_argument("--output", default="output/rapport.pdf", help="Chemin du fichier PDF de sortie")
    parser.add_argument("--confirm", action="store_true", help="Demander confirmation avant de continuer après identification de la parcelle")
    args = parser.parse_args()

    try:
        if args.refs_multiples:
            run_multi(refs=args.refs_multiples, output=args.output)
        else:
            run(adresse=args.adresse, ref_cadastrale=args.ref_cadastrale, output=args.output, confirm=args.confirm)
    except Exception as e:
        logger.error("Erreur pipeline : %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
