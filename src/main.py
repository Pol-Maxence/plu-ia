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
import os
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


def run(adresse: str | None = None, ref_cadastrale: str | None = None, output: str = "rapport.pdf") -> None:
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

    # --- Étape 2 : zonage PLU ---
    logger.info("Récupération zonage PLU...")
    # Coordonnées depuis le centroïde de la géométrie (Point ou Polygon)
    geom = parcelle.geometrie
    if geom["type"] == "Point":
        lon, lat = geom["coordinates"]
    else:
        # Centroïde approximatif pour les polygones
        coords = geom["coordinates"][0]
        lon = sum(c[0] for c in coords) / len(coords)
        lat = sum(c[1] for c in coords) / len(coords)

    zonage = get_zonage_plu(lat=lat, lon=lon)
    logger.info("Zone PLU : %s (%s)", zonage.zone, zonage.libelle)

    # --- Étape 3 : texte du règlement PLU ---
    logger.info("Téléchargement règlement PLU (partition : %s)...", zonage.partition)
    texte_plu = get_reglement_plu_text(zonage.partition)
    texte_zone = extraire_section_zone(texte_plu, zonage.zone)
    logger.info("Texte PLU extrait : %d caractères", len(texte_zone))

    # --- Étape 4 : extraction LLM ---
    logger.info("Analyse PLU par IA (zone %s)...", zonage.zone)
    regles = extraire_regles_plu(texte_zone, zonage.zone, client)
    logger.info("Règles extraites : emprise=%s%%, hauteur=%sm", regles.emprise_sol_max_pct, regles.hauteur_max_m)

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
    logger.info("Génération du rapport PDF...")
    generer_rapport(parcelle, regles, etude, output=output)
    logger.info("✓ Rapport généré : %s", output)


def main() -> None:
    parser = argparse.ArgumentParser(description="PLU·IA — Étude capacitaire automatisée")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("adresse", nargs="?", help="Adresse postale (ex: '15 rue de la Paix, Paris')")
    group.add_argument("--ref", dest="ref_cadastrale", help="Référence cadastrale (ex: 75056000BX0042)")
    parser.add_argument("--output", default="rapport.pdf", help="Chemin du fichier PDF de sortie")
    args = parser.parse_args()

    try:
        run(adresse=args.adresse, ref_cadastrale=args.ref_cadastrale, output=args.output)
    except Exception as e:
        logger.error("Erreur pipeline : %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
