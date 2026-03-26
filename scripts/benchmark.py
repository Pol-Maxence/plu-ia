"""
Benchmark de l'extraction PLU contre les données ground truth KelFoncier.

Mesure la précision champ par champ, identifie les patterns d'échec avec
les extraits PLU correspondants, et suggère des few-shots pour améliorer le prompt.

Usage :
    python scripts/benchmark.py                      # tous les sites avec ground truth
    python scripts/benchmark.py --refresh Arras      # re-extraire + benchmark
    python scripts/benchmark.py --failures           # affiche extraits PLU pour chaque échec
    python scripts/benchmark.py --suggest-shots      # génère les few-shots manquants
"""

import argparse
import dataclasses
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

import anthropic

from src.api.cadastre import get_parcelle_by_ref
from src.api.geoportail import get_zonage_plu, get_reglement_plu_text, extraire_section_zone
from src.parser.plu_extractor import extraire_regles_plu
from src.parser.rules_model import ReglesUrbanisme

logging.basicConfig(level=logging.WARNING)

GROUND_TRUTH_DIR = Path(__file__).parent / "ground_truth"
CACHE_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_TOL = 0.12   # ±12% tolérance numérique

# -----------------------------------------------------------------------------
# Chargement ground truth
# -----------------------------------------------------------------------------

def _charger_ground_truths() -> list[dict]:
    """Charge tous les fichiers JSON de ground truth (sauf template)."""
    gts = []
    for f in sorted(GROUND_TRUTH_DIR.glob("*.json")):
        if f.name == "template.json":
            continue
        with open(f, encoding="utf-8") as fh:
            gts.append(json.load(fh))
    return gts


def _refs_from_gt(gt: dict) -> list[str]:
    """Retourne la liste des références cadastrales depuis un ground truth."""
    ref = gt["ref"]
    return ref.split("_") if "_" in ref and ref[0].isdigit() else [ref]


def _cache_key(refs: list[str]) -> str:
    return "_".join(refs)

# -----------------------------------------------------------------------------
# Cache (réutilise la logique de validate_extraction)
# -----------------------------------------------------------------------------

def _load_regles(key: str) -> ReglesUrbanisme | None:
    path = CACHE_DIR / f"{key}_regles.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    try:
        return ReglesUrbanisme(**data)
    except TypeError:
        return None


def _save_regles(key: str, regles: ReglesUrbanisme) -> None:
    path = CACHE_DIR / f"{key}_regles.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dataclasses.asdict(regles), f, ensure_ascii=False, indent=2)


def _load_full_plu(key: str) -> str | None:
    path = CACHE_DIR / f"{key}_full_plu.txt"
    return path.read_text(encoding="utf-8") if path.exists() else None


def _save_full_plu(key: str, texte: str) -> None:
    (CACHE_DIR / f"{key}_full_plu.txt").write_text(texte, encoding="utf-8")


def _load_toc(key: str) -> list | None:
    path = CACHE_DIR / f"{key}_toc.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_toc(key: str, toc: list) -> None:
    with open(CACHE_DIR / f"{key}_toc.json", "w", encoding="utf-8") as f:
        json.dump(toc, f, ensure_ascii=False)


def _load_section(key: str) -> str | None:
    path = CACHE_DIR / f"{key}_section.txt"
    return path.read_text(encoding="utf-8") if path.exists() else None


def _save_section(key: str, texte: str) -> None:
    (CACHE_DIR / f"{key}_section.txt").write_text(texte, encoding="utf-8")


def _clear_cache(key: str) -> None:
    for suffix in ("_regles.json", "_full_plu.txt", "_toc.json", "_section.txt", "_plu_text.txt"):
        p = CACHE_DIR / f"{key}{suffix}"
        if p.exists():
            p.unlink()


def _centroid(geom: dict) -> tuple[float, float]:
    if geom["type"] == "Point":
        return geom["coordinates"][0], geom["coordinates"][1]
    coords = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
    return (
        sum(c[0] for c in coords) / len(coords),
        sum(c[1] for c in coords) / len(coords),
    )


def extraire_pour_gt(gt: dict, force: bool = False) -> tuple[ReglesUrbanisme, str]:
    """Extrait les règles PLU pour un ground truth (avec cache)."""
    refs = _refs_from_gt(gt)
    key = _cache_key(refs)

    if force:
        _clear_cache(key)

    regles = _load_regles(key)
    if regles is not None:
        return regles, regles.zone

    parcelle = get_parcelle_by_ref(refs[0])
    lon, lat = _centroid(parcelle.geometrie)
    zonage = get_zonage_plu(lat=lat, lon=lon)

    # Niveau 1 : texte complet + TOC (téléchargement si absent)
    texte_brut = _load_full_plu(key)
    toc = _load_toc(key)
    if texte_brut is None:
        texte_brut, toc = get_reglement_plu_text(zonage.partition, zonage.nomfic)
        _save_full_plu(key, texte_brut)
        _save_toc(key, toc or [])

    # Niveau 2 : section zone (recalcul si absente)
    section = _load_section(key)
    if section is None:
        section = extraire_section_zone(texte_brut, zonage.zone, toc or [])
        _save_section(key, section)

    client = anthropic.Anthropic()
    regles = extraire_regles_plu(section, zonage.zone, client)
    _save_regles(key, regles)
    return regles, zonage.zone

# -----------------------------------------------------------------------------
# Comparaison enrichie
# -----------------------------------------------------------------------------

def _pct_num(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _ok(v: bool) -> str:
    return "OK" if v else "XX"


def comparer(gt: dict, regles: ReglesUrbanisme) -> list[dict]:
    """
    Compare les règles extraites au ground truth.
    Retourne une liste de dicts {champ, attendu, extrait, score, detail}.
    score : "OK" | "~~" | "XX" | "—"
    """
    r = dataclasses.asdict(regles)
    c = gt["champs"]
    lignes = []

    def _ligne(champ, attendu, extrait, score, detail=""):
        lignes.append({"champ": champ, "attendu": str(attendu), "extrait": str(extrait), "score": score, "detail": detail})

    def _num(gt_val, extrait_val, champ, label_attendu=None):
        attendu = _pct_num(gt_val)
        extrait = _pct_num(extrait_val)
        label = label_attendu or str(attendu)
        if attendu is None:
            _ligne(champ, "—", extrait or "null", "—")
            return
        if extrait is None:
            _ligne(champ, label, "null", "XX", "non extrait")
        elif abs(extrait - attendu) / max(abs(attendu), 1) <= _TOL:
            _ligne(champ, label, extrait, "OK")
        else:
            _ligne(champ, label, extrait, "XX", f"écart {extrait - attendu:+.1f}")

    def _bool(gt_val, extrait_val, champ):
        if gt_val is None:
            _ligne(champ, "—", extrait_val, "—")
        elif extrait_val == gt_val:
            _ligne(champ, gt_val, extrait_val, "OK")
        else:
            _ligne(champ, gt_val, extrait_val, "XX")

    # -- Emprise --
    emp = c.get("emprise_sol_max_pct", {})
    if emp.get("non_reglementee"):
        ok = r.get("emprise_non_reglementee") is True
        _ligne("emprise_sol_max_pct", "NR", "NR" if ok else f"{r.get('emprise_sol_max_pct')}/NR={r.get('emprise_non_reglementee')}", "OK" if ok else "XX")
    else:
        _num(emp.get("valeur"), r.get("emprise_sol_max_pct"), "emprise_sol_max_pct")

    # -- Hauteur faitage --
    haut = c.get("hauteur_max_m", {})
    attendu_h = haut.get("valeur")
    if attendu_h is None:
        _ligne("hauteur_max_m", "—", r.get("hauteur_max_m") or "null", "—")
    else:
        label_h = f"{attendu_h} m ({haut.get('type') or '?'})"
        if haut.get("source_oap"):
            label_h += " [OAP]"
        _num(attendu_h, r.get("hauteur_max_m"), "hauteur_max_m", label_h)
        # Vérifier si source_oap déclenche une alerte
        if haut.get("source_oap"):
            has_alerte = any("OAP" in str(a).upper() or "oap" in str(a) for a in (r.get("contraintes") or []))
            _ligne("hauteur_oap_alerte", "alerte OAP", "oui" if has_alerte else "non", "OK" if has_alerte else "~~",
                   "OAP non signalé dans contraintes" if not has_alerte else "")

    # -- Hauteur égout --
    attendu_egout = haut.get("valeur_egout")
    _num(attendu_egout, r.get("hauteur_egout_m"), "hauteur_egout_m")

    # -- Recul voirie --
    rv = c.get("recul_voirie_m", {})
    if rv.get("alignement"):
        ok = r.get("recul_voirie_alignement") is True
        _ligne("recul_voirie_alignement", "True (alignement)", r.get("recul_voirie_alignement"), "OK" if ok else "XX")
    else:
        _num(rv.get("valeur"), r.get("recul_voirie_m"), "recul_voirie_m")

    # -- Recul limites --
    rl = c.get("recul_limites_m", {})
    formule = rl.get("formule")
    if formule:
        # Priorité : champ structuré recul_limites_formule
        formula_key = formule.replace("/", "").replace(" ", "").upper()
        formule_extraite = (r.get("recul_limites_formule") or "").upper().replace("/", "").replace(" ", "")
        verb = (r.get("verbatims") or {}).get("recul_limites_m", "")
        if formule_extraite and formula_key in formule_extraite:
            score, detail = "OK", "formule capturée"
        elif formula_key in (verb or "").upper().replace("/", "").replace(" ", ""):
            score, detail = "~~", "formule dans verbatim"
        else:
            score, detail = "XX", "formule non capturée"
        _ligne("recul_limites_m", f"formule {formule}", r.get("recul_limites_formule") or r.get("recul_limites_m") or "null", score, detail)
    else:
        _num(rl.get("valeur"), r.get("recul_limites_m"), "recul_limites_m")

    # -- Stationnement --
    stat = c.get("stationnement_par_logt", {})
    attendu_acc = stat.get("accession")
    if attendu_acc is not None:
        _num(attendu_acc, r.get("stationnement_par_logt"), "stationnement_par_logt")
    else:
        _ligne("stationnement_par_logt", "—", r.get("stationnement_par_logt") or "null", "—")

    # -- Espaces verts --
    ev = c.get("espace_vert_min_pct", {})
    _num(ev.get("valeur"), r.get("espace_vert_min_pct"), "espace_vert_min_pct")

    return lignes

# -----------------------------------------------------------------------------
# Affichage et rapport
# -----------------------------------------------------------------------------

W = 62


def _afficher_case(gt: dict, regles: ReglesUrbanisme, lignes: list[dict], show_failures: bool) -> tuple[int, int]:
    ref = gt["ref"][:30]
    commune = gt.get("commune", "")
    zone = gt.get("zone", "")
    print(f"\n{'=' * W}")
    print(f"  {commune} — {zone}  [{ref}]")
    print(f"{'=' * W}")
    print(f"  {'Champ':<28} {'Attendu':<18} {'Extrait':<12} Score")
    print(f"  {'-' * (W-2)}")

    ok = total = 0
    failures = []

    for l in lignes:
        score = l["score"]
        if score in ("OK", "~~"):
            ok += 1
        if score != "—":
            total += 1
        detail = f"  ← {l['detail']}" if l["detail"] else ""
        print(
            f"  {l['champ']:<28} {l['attendu']:<18} {l['extrait']:<12} {score}{detail}"
        )
        if score == "XX" and l["detail"]:
            failures.append(l)

    print(f"\n  Score : {ok}/{total}")

    if show_failures and failures:
        _afficher_failures(failures, regles)

    return ok, total


def _afficher_failures(failures: list[dict], regles: ReglesUrbanisme) -> None:
    """Affiche les extraits PLU associés aux champs en échec."""
    verbatims = regles.verbatims or {}
    print(f"\n  ECHECS — contexte PLU :")
    for f in failures:
        champ = f["champ"]
        verb = verbatims.get(champ)
        print(f"\n  ┌ {champ} (attendu: {f['attendu']}) {'-' * max(0, W - len(champ) - 20)}")
        if verb:
            print(f"  | Verbatim PLU : « {verb[:120]} »")
        else:
            print(f"  | Aucun verbatim capturé pour ce champ")
        print(f"  └{'-' * (W-2)}")


def _suggerer_shots(all_failures: list[tuple[str, str, str]]) -> None:
    """Affiche des few-shots à ajouter au prompt depuis les échecs collectés."""
    if not all_failures:
        return
    print(f"\n{'=' * W}")
    print("  FEW-SHOTS SUGGERES (à ajouter dans plu_extractor.py)")
    print(f"{'=' * W}")
    seen = set()
    for champ, attendu, verbatim in all_failures:
        key = (champ, attendu[:40])
        if key in seen or not verbatim:
            continue
        seen.add(key)
        verb_short = verbatim[:100].replace("\n", " ")
        print(f"\n  Champ : {champ}")
        print(f"  Texte PLU : « {verb_short}... »")
        print(f"  → {champ}: {attendu}")
        print(f"  Ajouter dans _USER_PROMPT_TEMPLATE few-shots :")
        print(f'  - "{verb_short[:60]}..." → {champ}: {attendu}')


def _afficher_resume(scores: list[tuple[str, int, int]]) -> None:
    print(f"\n{'=' * W}")
    print("  RESUME GLOBAL")
    print(f"{'=' * W}")
    total_ok = total_champs = 0
    for nom, ok, total in scores:
        if total == 0:
            etat = "(aucun champ testé)"
        elif ok == total:
            etat = "OK complet"
        elif ok >= total * 0.75:
            etat = "partiel"
        else:
            etat = "echec"
        print(f"  {nom:<40} {ok}/{total:<4} {etat}")
        total_ok += ok
        total_champs += total

    if total_champs:
        pct = total_ok / total_champs * 100
        print(f"\n  Total : {total_ok}/{total_champs}  ({pct:.0f}%)")

    # Champs les plus défaillants
    from collections import Counter
    champ_echecs: Counter = Counter()
    # (rempli ci-dessous via all_failures)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Benchmark extraction PLU vs ground truth KelFoncier")
    parser.add_argument("--refresh", nargs="?", const="ALL", metavar="NOM",
                        help="Forcer re-extraction (sans arg = tous, sinon filtre partiel sur commune/zone/ref)")
    parser.add_argument("--only", metavar="NOM",
                        help="Filtrer les sites (commune, zone ou ref) sans forcer la re-extraction")
    parser.add_argument("--failures", action="store_true",
                        help="Afficher les extraits PLU pour chaque champ en échec")
    parser.add_argument("--suggest-shots", action="store_true",
                        help="Générer des few-shots suggérés depuis les échecs")
    args = parser.parse_args()

    gts = _charger_ground_truths()
    if not gts:
        print(f"Aucun fichier ground truth trouvé dans {GROUND_TRUTH_DIR}")
        print("  → Lancer : python scripts/import_kelfoncier.py --excel FICHIER.xlsx")
        return

    scores = []
    all_failures_shots: list[tuple[str, str, str]] = []
    champ_echecs: dict[str, int] = {}

    for gt in gts:
        nom = f"{gt.get('commune', '?')} {gt.get('zone', '?')}"
        refs = _refs_from_gt(gt)
        key = _cache_key(refs)

        filtre = args.only or (args.refresh if args.refresh != "ALL" else None)
        if filtre and filtre.lower() not in nom.lower() and not any(filtre in r for r in refs):
            continue

        force = args.refresh == "ALL" or (
            args.refresh and args.refresh != "ALL"
            and (args.refresh.lower() in nom.lower() or any(args.refresh in r for r in refs))
        )

        print(f"\n[{nom}]  {refs[0]}")
        try:
            regles, zone_api = extraire_pour_gt(gt, force=force)
            lignes = comparer(gt, regles)
            ok, total = _afficher_case(gt, regles, lignes, show_failures=args.failures)
            scores.append((nom, ok, total))

            # Collecter les échecs pour suggest-shots
            if args.suggest_shots:
                verbatims = regles.verbatims or {}
                for l in lignes:
                    if l["score"] == "XX":
                        champ = l["champ"]
                        champ_echecs[champ] = champ_echecs.get(champ, 0) + 1
                        all_failures_shots.append((champ, l["attendu"], verbatims.get(champ, "")))

        except Exception as e:
            print(f"  ERREUR : {e}")
            scores.append((nom, 0, 0))

    _afficher_resume(scores)

    if champ_echecs:
        print(f"\n  CHAMPS LES PLUS DEFAILLANTS :")
        for champ, n in sorted(champ_echecs.items(), key=lambda x: -x[1]):
            print(f"    {champ:<35} {n} echec(s)")

    if args.suggest_shots:
        _suggerer_shots(all_failures_shots)


if __name__ == "__main__":
    main()
