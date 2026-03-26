"""
Script de validation de l'extraction PLU sur des sites de référence réels.

Usage :
    python scripts/validate_extraction.py               # utilise le cache
    python scripts/validate_extraction.py --refresh     # reforce tout
    python scripts/validate_extraction.py --refresh Arras  # reforce un site

Les résultats intermédiaires (texte PLU + JSON LLM) sont mis en cache dans
tests/fixtures/cache/ pour éviter de rappeler les APIs à chaque lancement.

Avant d'exécuter :
- Remplir les champs "refs" pour les sites sans référence cadastrale
- Ajuster les champs "expected" depuis les études PDF dans examples/
"""

import argparse
import dataclasses
import json
import logging
import sys
from pathlib import Path

# Ajouter la racine du projet au path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import anthropic

from src.api.cadastre import get_parcelle_by_ref
from src.api.geoportail import get_zonage_plu, get_reglement_plu_text, extraire_section_zone
from src.parser.plu_extractor import extraire_regles_plu
from src.parser.rules_model import ReglesUrbanisme

logging.basicConfig(level=logging.WARNING)  # silencieux pendant la validation

CACHE_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# CASES — Sites de référence
# Remplir les champs "refs" et "expected" depuis les études PDF (examples/)
# "expected": None = champ non testé (affiché comme —)
# ─────────────────────────────────────────────────────────────────────────────
CASES = [
    {
        "nom": "Arras — Michonneau",
        "refs": ["62041000AB0570"],                    # TODO : remplir la ref cadastrale
        "expected": {
            "zone": "UAa",                   # zone UAa ou UAa+
            "emprise_sol_max_pct": 90.0,     # Nacarat : 90%
            "emprise_non_reglementee": False,
            "hauteur_max_m": 19.0,           # Nacarat : 19 m
            "espace_vert_min_pct": 10,       # à vérifier dans PLU
            "recul_voirie_m": None,          # à vérifier dans PLU
            "recul_voirie_alignement": True, # à vérifier dans PLU
            "recul_limites_m": None,         # à vérifier dans PLU
            "stationnement_par_logt": 1.25,  # règle complexe (accession/social)
        },
    },
    {
        "nom": "Lachelle — Cul de Sac",
        "refs": [
            "603370000A1140",
            "603370000A1142",
            "603370000A1139",
            "603370000A1141",
        ],
        "expected": {
            "zone": "UV7.1",
            "emprise_sol_max_pct": None,     # non réglementé
            "emprise_non_reglementee": True,
            "hauteur_max_m": 9,           # à vérifier dans PLU
            "espace_vert_min_pct": None,     # à vérifier dans PLU
            "recul_voirie_m": 3,          # à vérifier dans PLU
            "recul_voirie_alignement": True, # à vérifier dans PLU
            "recul_limites_m": 4,         # à vérifier dans PLU
            "stationnement_par_logt": 2.0,  # Nacarat : 1 pl/logt
        },
    },
    {
        "nom": "Senlis — Poteau",
        "refs": ["60612000AX0324"],
        "expected": {
            "zone": "UCb",
            "emprise_sol_max_pct": 75,     # CES à vérifier dans PLU
            "emprise_non_reglementee": False,
            "hauteur_max_m": None,           # à vérifier dans PLU
            "espace_vert_min_pct": 25,     # à vérifier dans PLU
            "recul_voirie_m": 5,          # à vérifier dans PLU
            "recul_limites_m": 5,         # à vérifier dans PLU
            "stationnement_par_logt": None,  # à vérifier dans PLU
        },
    },
    {
        "nom": "Aumont-en-Halatte — Apremont",
        "refs": ["600280000A0954"],                    # TODO : remplir la ref cadastrale
        "expected": {
            "zone": None,
            "emprise_sol_max_pct": None,
            "emprise_non_reglementee": None,
            "hauteur_max_m": None,
            "espace_vert_min_pct": None,
            "recul_voirie_m": None,
            "recul_limites_m": None,
            "stationnement_par_logt": None,
        },
    },
    {
        "nom": "Baron — Geais",
        "refs": ["600470000B0426"],                    # TODO : remplir la ref cadastrale
        "expected": {
            "zone": None,
            "emprise_sol_max_pct": None,
            "emprise_non_reglementee": None,
            "hauteur_max_m": None,
            "espace_vert_min_pct": None,
            "recul_voirie_m": None,
            "recul_limites_m": None,
            "stationnement_par_logt": None,
        },
    },
    {
        "nom": "Cramoisy — Moulin",
        "refs": ["60173000AC0029"],                    # TODO : remplir la ref cadastrale
        "expected": {
            "zone": None,
            "emprise_sol_max_pct": 30,
            "emprise_non_reglementee": None,
            "hauteur_max_m": 12,
            "espace_vert_min_pct": None,
            "recul_voirie_m": None,
            "recul_limites_m": None,
            "stationnement_par_logt": None,
        },
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────

def _cache_key(refs: list[str]) -> str:
    return "_".join(refs).replace(" ", "_")


def _load_regles_cache(key: str) -> ReglesUrbanisme | None:
    path = CACHE_DIR / f"{key}_regles.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return ReglesUrbanisme(**data)


def _save_regles_cache(key: str, regles: ReglesUrbanisme) -> None:
    path = CACHE_DIR / f"{key}_regles.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dataclasses.asdict(regles), f, ensure_ascii=False, indent=2)


def _load_plu_text_cache(key: str) -> str | None:
    path = CACHE_DIR / f"{key}_plu_text.txt"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _save_plu_text_cache(key: str, texte: str) -> None:
    path = CACHE_DIR / f"{key}_plu_text.txt"
    path.write_text(texte, encoding="utf-8")


def _clear_cache(key: str) -> None:
    for suffix in ("_regles.json", "_plu_text.txt"):
        p = CACHE_DIR / f"{key}{suffix}"
        if p.exists():
            p.unlink()

# ─────────────────────────────────────────────────────────────────────────────
# Extraction
# ─────────────────────────────────────────────────────────────────────────────

def _centroid(geom: dict) -> tuple[float, float]:
    """Retourne (lon, lat) du centroïde de la géométrie."""
    if geom["type"] == "Point":
        return geom["coordinates"][0], geom["coordinates"][1]
    elif geom["type"] == "Polygon":
        coords = geom["coordinates"][0]
    elif geom["type"] == "MultiPolygon":
        coords = geom["coordinates"][0][0]
    else:
        raise ValueError(f"Type de géométrie non supporté : {geom['type']}")
    lon = sum(c[0] for c in coords) / len(coords)
    lat = sum(c[1] for c in coords) / len(coords)
    return lon, lat


def extraire_case(case: dict, force_refresh: bool = False) -> tuple[ReglesUrbanisme, str]:
    """
    Extrait les règles PLU pour un cas (avec cache).
    Retourne (ReglesUrbanisme, zone_extraite).
    """
    refs = case["refs"]
    key = _cache_key(refs)

    if force_refresh:
        _clear_cache(key)

    regles = _load_regles_cache(key)
    if regles is not None:
        print(f"  [cache] Règles chargées depuis {key}_regles.json")
        return regles, regles.zone

    print(f"  [API] Récupération parcelle {refs[0]}...")
    parcelle = get_parcelle_by_ref(refs[0])
    lon, lat = _centroid(parcelle.geometrie)

    print(f"  [API] Zonage PLU ({lat:.5f}, {lon:.5f})...")
    zonage = get_zonage_plu(lat=lat, lon=lon)
    print(f"  [API] Zone détectée : {zonage.zone}")

    texte_plu = _load_plu_text_cache(key)
    if texte_plu is None:
        print(f"  [API] Téléchargement règlement PLU ({zonage.partition})...")
        texte_brut, toc = get_reglement_plu_text(zonage.partition, zonage.nomfic)
        texte_plu = extraire_section_zone(texte_brut, zonage.zone, toc)
        _save_plu_text_cache(key, texte_plu)
        print(f"  [API] Texte extrait : {len(texte_plu)} caractères")
    else:
        print(f"  [cache] Texte PLU chargé depuis cache ({len(texte_plu)} caractères)")

    print(f"  [LLM] Analyse PLU en cours...")
    client = anthropic.Anthropic()
    regles = extraire_regles_plu(texte_plu, zonage.zone, client)
    _save_regles_cache(key, regles)

    return regles, zonage.zone

# ─────────────────────────────────────────────────────────────────────────────
# Comparaison
# ─────────────────────────────────────────────────────────────────────────────

_TOL_NUMERIQUE = 0.10  # tolérance ±10% sur les valeurs numériques


def _comparer_zone(attendu: str, extrait: str) -> str:
    """Compare les codes de zone (UAa ≈ UAa+, UV7.1 = UV7.1)."""
    if attendu == extrait:
        return "✓"
    # "proche" si l'extrait commence par l'attendu ou vice-versa
    base_attendu = attendu.rstrip("+-0123456789.")
    base_extrait = extrait.rstrip("+-0123456789.") if extrait else ""
    if base_attendu and base_extrait and (
        extrait.startswith(attendu) or attendu.startswith(extrait) or
        base_attendu == base_extrait
    ):
        return "~"
    return "✗"


def _comparer_numerique(attendu: float, extrait) -> str:
    if extrait is None:
        return "✗"
    try:
        v = float(extrait)
    except (TypeError, ValueError):
        return "✗"
    if attendu == 0:
        return "✓" if v == 0 else "✗"
    if abs(v - attendu) / abs(attendu) <= _TOL_NUMERIQUE:
        return "✓"
    return "✗"


def _comparer_bool(attendu: bool, extrait) -> str:
    return "✓" if extrait == attendu else "✗"


def comparer(regles: ReglesUrbanisme, expected: dict, zone_api: str) -> list[dict]:
    """
    Compare les règles extraites aux valeurs attendues.
    Retourne une liste de lignes {champ, attendu, extrait, resultat}.
    """
    lignes = []
    regles_dict = dataclasses.asdict(regles)

    # Zone : utiliser la zone retournée par l'API GPU comme référence
    champ_zone = "zone"
    attendu_zone = expected.get("zone")
    if attendu_zone is not None:
        extrait_zone = zone_api  # zone GPU, plus fiable que le LLM
        lignes.append({
            "champ": champ_zone,
            "attendu": attendu_zone,
            "extrait": extrait_zone,
            "resultat": _comparer_zone(attendu_zone, extrait_zone),
        })
    else:
        lignes.append({
            "champ": champ_zone,
            "attendu": "—",
            "extrait": zone_api or "—",
            "resultat": "—",
        })

    # Autres champs
    champs_numeriques = {
        "emprise_sol_max_pct", "hauteur_max_m", "surface_plancher_max_m2",
        "recul_voirie_m", "recul_limites_m", "stationnement_par_logt",
        "espace_vert_min_pct",
    }
    champs_bool = {"emprise_non_reglementee", "recul_voirie_alignement"}

    for champ, attendu in expected.items():
        if champ == "zone":
            continue  # déjà traité

        extrait = regles_dict.get(champ)

        if attendu is None:
            # Champ non testé : afficher quand même la valeur extraite
            lignes.append({
                "champ": champ,
                "attendu": "—",
                "extrait": str(extrait) if extrait is not None else "null",
                "resultat": "—",
            })
        elif attendu == "NR":
            # Non Réglementé : emprise_sol_max_pct=null ET emprise_non_reglementee=True
            ok_nr = (extrait is None) and regles_dict.get("emprise_non_reglementee") is True
            lignes.append({
                "champ": champ,
                "attendu": "NR",
                "extrait": "NR" if ok_nr else f"{extrait} / NR={regles_dict.get('emprise_non_reglementee')}",
                "resultat": "✓" if ok_nr else "✗",
            })
        elif champ in champs_bool:
            lignes.append({
                "champ": champ,
                "attendu": str(attendu),
                "extrait": str(extrait),
                "resultat": _comparer_bool(attendu, extrait),
            })
        elif champ in champs_numeriques:
            lignes.append({
                "champ": champ,
                "attendu": str(attendu),
                "extrait": str(extrait) if extrait is not None else "null",
                "resultat": _comparer_numerique(attendu, extrait),
            })

    return lignes

# ─────────────────────────────────────────────────────────────────────────────
# Affichage
# ─────────────────────────────────────────────────────────────────────────────

def _afficher_case(nom: str, lignes: list[dict]) -> tuple[int, int]:
    """Affiche le tableau d'un site. Retourne (ok, total_testés)."""
    print(f"\n{'━' * 62}")
    print(f"  {nom}")
    print(f"{'━' * 62}")

    col_w = [28, 12, 12, 10]
    header = f"  {'Champ':<{col_w[0]}} {'Attendu':<{col_w[1]}} {'Extrait':<{col_w[2]}} {'Résultat'}"
    print(header)
    print(f"  {'-' * 60}")

    ok = total = 0
    for l in lignes:
        res = l["resultat"]
        if res in ("✓", "~"):
            ok += 1
        if res not in ("—",):
            total += 1

        symbole = {"✓": "✓", "~": "~", "✗": "✗", "—": "—"}.get(res, res)
        print(
            f"  {l['champ']:<{col_w[0]}} "
            f"{l['attendu']:<{col_w[1]}} "
            f"{l['extrait']:<{col_w[2]}} "
            f"{symbole}"
        )

    if total > 0:
        print(f"\n  Score : {ok}/{total} champ(s) testé(s) correct(s)")
    else:
        print(f"\n  (aucun champ attendu défini — à compléter dans CASES)")

    return ok, total


def _afficher_resume(scores: list[tuple[str, int, int]]) -> None:
    print(f"\n{'━' * 62}")
    print("  RÉSUMÉ")
    print(f"{'━' * 62}")
    total_ok = total_champs = 0
    for nom, ok, total in scores:
        if total == 0:
            etat = "(à compléter)"
        elif ok == total:
            etat = "✓ complet"
        elif ok >= total * 0.75:
            etat = "~ partiel"
        else:
            etat = "✗ échoué"
        print(f"  {nom:<40} {ok}/{total}  {etat}")
        total_ok += ok
        total_champs += total
    print(f"\n  Total : {total_ok}/{total_champs} champs corrects")

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Valide l'extraction PLU sur les sites de référence"
    )
    parser.add_argument(
        "--refresh",
        nargs="?",
        const="ALL",
        metavar="NOM_SITE",
        help="Forcer recalcul (sans argument = tous les sites, sinon nom partiel du site)",
    )
    args = parser.parse_args()

    scores = []

    for case in CASES:
        nom = case["nom"]
        refs = case["refs"]

        # Vérifier si les refs sont remplies
        if "TODO" in refs:
            print(f"\n[SKIP] {nom} — référence cadastrale non renseignée (TODO)")
            scores.append((nom, 0, 0))
            continue

        force = (
            args.refresh == "ALL" or
            (args.refresh and args.refresh.lower() in nom.lower())
        )

        print(f"\n[{nom}]")
        try:
            regles, zone_api = extraire_case(case, force_refresh=force)
            lignes = comparer(regles, case["expected"], zone_api)
            ok, total = _afficher_case(nom, lignes)
            scores.append((nom, ok, total))
        except Exception as e:
            print(f"  ERREUR : {e}")
            scores.append((nom, 0, 0))

    _afficher_resume(scores)


if __name__ == "__main__":
    main()
