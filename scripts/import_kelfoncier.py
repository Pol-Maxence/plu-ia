"""
Import automatisé des données KelFoncier vers le format ground truth JSON.

Deux modes :
  --excel FICHIER.xlsx    Importe depuis un export Excel KelFoncier
  --pdf   FICHIER.pdf     Importe depuis un PDF d'étude KelFoncier (via LLM)

Usage :
  python scripts/import_kelfoncier.py --excel "Saint-Valery_000_AH_0186.xlsx"
  python scripts/import_kelfoncier.py --excel "Saint-Valery_000_AH_0186.xlsx" --ref 80XXX000AH0186
  python scripts/import_kelfoncier.py --pdf "examples/Arras/20240723 Arras - Michonneau.pdf" --ref 62041000AB0570

Le fichier JSON est sauvegardé dans scripts/ground_truth/{ref}.json
"""

import argparse
import json
import logging
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

GROUND_TRUTH_DIR = Path(__file__).parent / "ground_truth"
GROUND_TRUTH_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Lookup code INSEE depuis l'API Geo gouv.fr
# ─────────────────────────────────────────────────────────────────────────────

def _get_insee_code(commune_name: str, code_postal: str | None = None) -> str | None:
    """Récupère le code INSEE (5 chiffres) depuis geo.api.gouv.fr."""
    params: dict = {"nom": commune_name, "fields": "code,nom", "boost": "population", "limit": "5"}
    if code_postal:
        params["codePostal"] = code_postal
    url = "https://geo.api.gouv.fr/communes?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        if data:
            return str(data[0]["code"])
    except Exception as exc:
        logger.debug("Lookup INSEE échoué pour %s : %s", commune_name, exc)
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Helpers de conversion de valeurs Excel
# ─────────────────────────────────────────────────────────────────────────────

def _parse_pct(val) -> float | None:
    """Convertit une valeur Excel en pourcentage float (ex: '60 %' → 60.0, 0.6 → 60.0)."""
    if val is None:
        return None
    s = str(val).strip()
    # Supprimer unités
    s = re.sub(r"[%\s]", "", s)
    try:
        v = float(s.replace(",", "."))
        # Excel stocke parfois les % en décimal (0.6 = 60%)
        if 0 < v <= 1.0:
            v = v * 100
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


def _parse_m(val) -> float | None:
    """Convertit une valeur Excel en mètres (ex: '3 m' → 3.0)."""
    if val is None:
        return None
    s = re.sub(r"[m²\s]", "", str(val).strip().replace(",", "."))
    try:
        v = float(s)
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


def _parse_float(val) -> float | None:
    """Convertit une valeur Excel en float."""
    if val is None:
        return None
    s = re.sub(r"[^\d,.\-]", "", str(val)).replace(",", ".")
    try:
        v = float(s)
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


def _parse_int(val) -> int | None:
    v = _parse_float(val)
    return int(v) if v is not None else None


def _oui_non(val) -> bool:
    """Convertit 'Oui'/'Non' en bool."""
    if val is None:
        return False
    return str(val).strip().lower() in ("oui", "yes", "true", "1")


def _lire_kv(ws) -> dict:
    """Lit un onglet Excel au format clé-valeur vertical (col A = libellé, col B = valeur)."""
    result = {}
    for row in ws.iter_rows(min_row=1, values_only=True):
        if row[0] is not None and len(row) > 1 and row[1] is not None:
            result[str(row[0]).strip()] = row[1]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Parseur onglet PLU KelFoncier (3e onglet)
# ─────────────────────────────────────────────────────────────────────────────

_HTML_TAG = re.compile(r"<[^>]+>")


def _nettoyer(s: str) -> str:
    """Supprime les balises HTML et normalise les espaces."""
    return re.sub(r"\s+", " ", _HTML_TAG.sub(" ", str(s))).strip()


def _premier_m(s: str) -> float | None:
    """Extrait le premier nombre en mètres dans une chaîne."""
    m = re.search(r"(\d+[,.]?\d*)\s*m[èe]tres?", _nettoyer(s), re.I)
    return float(m.group(1).replace(",", ".")) if m else None


def _premier_pct(s: str) -> float | None:
    """Extrait le premier pourcentage dans une chaîne."""
    m = re.search(r"(\d+[,.]?\d*)\s*%", _nettoyer(s))
    return float(m.group(1).replace(",", ".")) if m else None


def _tous_pct(s: str) -> list:
    """Extrait tous les pourcentages dans une chaîne."""
    return [float(x.replace(",", ".")) for x in re.findall(r"(\d+[,.]?\d*)\s*%", _nettoyer(s))]


def _parser_plu_onglet(rows: dict) -> dict:
    """
    Extrait les règles PLU depuis l'onglet 'PLU' KelFoncier.
    Fonctionne en format structuré (valeurs propres) et textuel HTML (<br>).
    `rows` = dict colA→colB issu de _lire_kv().
    """
    def get(*keys):
        for k in keys:
            if k in rows:
                return str(rows[k])
        return None

    # Zone
    zone = get("Sous-zone principale")

    # Emprise au sol
    emp_txt = get("Emprise au sol maximale autorisée", "Emprise au sol autorisée")
    emp_val = _premier_pct(emp_txt) if emp_txt else None
    emp_nr  = bool(emp_txt and re.search(r"non.r[ée]glement", emp_txt, re.I))
    if emp_nr:
        emp_val = None

    # Hauteur faitage
    hf_txt  = get("Hauteur au faitage", "Hauteur au fa\u00eetage")
    hf_val  = _premier_m(hf_txt) if hf_txt else None
    hf_type = "faitage" if hf_val else None

    # Hauteur égout
    he_txt = get("Hauteur \u00e0 l'\u00e9gout", "Hauteur a l'egout")
    he_val = _premier_m(he_txt) if he_txt else None

    # Niveaux proxy
    niv_txt = get("Niveaux pour un immeuble collectif", "Nombre de niveaux")
    niv_val = _parse_float(niv_txt) if niv_txt else None

    # Recul voirie ("Marge de recul")
    rv_txt = get("Marge de recul", "Retrait par rapport aux voies", "Retrait par rapport aux voies publiques")
    rv_val = _premier_m(rv_txt) if rv_txt else None

    # Retrait limites latérales
    rl_txt = get(
        "Retrait par rapport aux limites lat\u00e9rales",
        "Retrait minimal par rapport aux limites lat\u00e9rales",
        "Retrait par rapport aux limites separatives",
    )
    rl_val, rl_formule, rl_min = None, None, None
    if rl_txt:
        clean = _nettoyer(rl_txt)
        if re.search(r"limites?\s+s[eé]paratives?|en\s+limites?", clean, re.I):
            rl_val = 0.0   # implantation sur limite autorisée
        if re.search(r"H/2|[Hh]auteur.*moit|moiti.*[Hh]auteur", clean):
            rl_formule = "H/2"
            m = re.search(r"(\d+[,.]?\d*)\s*m[èe]tres?", clean)
            rl_min = float(m.group(1).replace(",", ".")) if m else None
            rl_val = rl_min   # valeur minimale
        elif rl_val is None:
            rl_val = _premier_m(rl_txt)

    # Retrait entre bâtiments → fallback pour formule H/2 si pas dans limites lat.
    rb_txt = get("Retrait entre b\u00e2timents", "Retrait entre batiments")
    if rb_txt and rl_formule is None:
        clean_rb = _nettoyer(rb_txt)
        if re.search(r"H/2|[Hh]auteur.*moit|moiti.*[Hh]auteur", clean_rb):
            rl_formule = "H/2"
            m = re.search(r"(\d+[,.]?\d*)\s*m[èe]tres?", clean_rb)
            if m:
                rl_min = float(m.group(1).replace(",", "."))
                if rl_val is None:
                    rl_val = rl_min

    # Espaces verts
    ev_txt = get("Emprise espaces verts", "Espaces verts", "Espace vert")
    ev_val, pt_val = None, None
    if ev_txt:
        pcts = _tous_pct(ev_txt)
        ev_val = pcts[0] if pcts else None
        pt_val = pcts[1] if len(pcts) >= 2 else None

    # Stationnement
    stat_txt = get("Stationnement obligatoire")
    stat_acc, stat_soc = None, None
    if stat_txt:
        clean_st = _nettoyer(stat_txt)
        if re.search(r"1\s*place\s*(?:par|/)\s*logement", clean_st, re.I):
            stat_acc = 1.0
        elif re.search(r"\d+[,.]?\d*\s*m.*=.*1\s*place", clean_st, re.I):
            stat_acc = 1.0  # ratio surface → 1 place minimum
        if re.search(r"1\s*place\s*(?:par|/)\s*logement\s*social", clean_st, re.I):
            stat_soc = 1.0

    return {
        "zone": zone,
        "emp_val": emp_val, "emp_nr": emp_nr,
        "hf_val": hf_val, "hf_type": hf_type, "he_val": he_val, "niv_val": niv_val,
        "rv_val": rv_val,
        "rl_val": rl_val, "rl_formule": rl_formule, "rl_min": rl_min,
        "ev_val": ev_val, "pt_val": pt_val,
        "stat_acc": stat_acc, "stat_soc": stat_soc,
        "raw": {k: _nettoyer(str(v)) for k, v in rows.items()},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Import depuis Excel KelFoncier
# ─────────────────────────────────────────────────────────────────────────────

def importer_excel(path: str, ref: str | None = None) -> dict:
    """
    Parse un fichier Excel KelFoncier et retourne un dict ground truth.

    Structure Excel attendue :
    - Onglet 0 ("parcelle") : info parcelle + contraintes (clé/valeur vertical)
    - Onglet 1 ("faisabilité") : étude capacitaire KelFoncier (non utilisée pour ground truth)
    - Onglet 2 ("PLU") : règles brutes du PLU par champ (structuré ou textuel HTML)
    """
    try:
        import openpyxl
    except ImportError:
        logger.error("openpyxl non installé — pip install openpyxl")
        raise

    wb = openpyxl.load_workbook(path, data_only=True)
    sheets = wb.worksheets

    o1 = _lire_kv(sheets[0]) if len(sheets) > 0 else {}   # info parcelle

    # Onglet PLU — chercher par nom "plu" (priorité) ou "glement"
    plu_ws = next(
        (ws for ws in sheets if "plu" in ws.title.lower() or "glement" in ws.title.lower()),
        None,
    )
    plu = _parser_plu_onglet(_lire_kv(plu_ws)) if plu_ws else {}

    # ── Référence cadastrale ──
    # Numéro contient "PREFIX SECTION NUM" (ex: "000 AB 0570") → on prend le dernier token
    prefixe = str(o1.get("Préfixe", "")).strip()
    section = str(o1.get("Section", "")).strip()
    numero_raw = str(o1.get("Numéro", "")).strip()
    numero_parts = numero_raw.split()
    numero = numero_parts[-1] if numero_parts else numero_raw.replace(" ", "")
    commune_name = str(o1.get("Commune", "")).strip()
    # Extraire code postal depuis l'adresse pour affiner le lookup INSEE
    adresse = str(o1.get("Adresse", ""))
    cp_match = re.search(r"\b(\d{5})\b", adresse)
    code_postal = cp_match.group(1) if cp_match else None
    insee = _get_insee_code(commune_name, code_postal) if commune_name else None
    ref_auto = f"{insee}{prefixe}{section}{numero}" if insee else f"{prefixe}{section}{numero}"
    if not insee:
        logger.warning("Code INSEE non trouvé pour '%s' — ref partielle : %s", commune_name, ref_auto)
    ref_finale = ref or ref_auto or Path(path).stem

    # ── Zone (priorité : onglet PLU > onglet parcelle) ──
    zone = plu.get("zone") or str(o1.get("Sous-zone", "")).strip() or None

    # ── Contraintes réglementaires (onglet parcelle) ──
    champs_parcelle = {
        "Commune", "Préfixe", "Section", "Numéro", "Adresse",
        "Surface en m²", "Longitude", "Latitude", "Sous-zone",
    }
    contraintes_risques = {
        k: str(v)
        for k, v in o1.items()
        if k not in champs_parcelle and v and str(v).strip() not in ("", "0", "Non")
    }

    gt = {
        "ref": ref_finale,
        "source": "KelFoncier",
        "date": "",
        "commune": commune_name or None,
        "zone": zone,
        "champs": {
            "emprise_sol_max_pct": {
                "valeur": plu.get("emp_val"),
                "non_reglementee": bool(plu.get("emp_nr")),
                "condition": None,
                "source_oap": False,
            },
            "hauteur_max_m": {
                "valeur": plu.get("hf_val"),
                "type": plu.get("hf_type"),
                "valeur_egout": plu.get("he_val"),
                "condition": None,
                "source_oap": False,
                "_nb_niveaux_kelfoncier": plu.get("niv_val"),
            },
            "recul_voirie_m": {
                "valeur": plu.get("rv_val"),
                "alignement": False,
                "formule": None,
            },
            "recul_limites_m": {
                "valeur": plu.get("rl_val"),
                "formule": plu.get("rl_formule"),
                "minimum_m": plu.get("rl_min"),
            },
            "stationnement_par_logt": {
                "accession": plu.get("stat_acc"),
                "social": plu.get("stat_soc"),
                "velos": None,
            },
            "espace_vert_min_pct": {
                "valeur": plu.get("ev_val"),
                "pleine_terre_pct": plu.get("pt_val"),
                "biotope": None,
            },
            "source_oap_global": False,
            "notes": "",
            "_contraintes_kelfoncier": contraintes_risques,
            "_plu_kelfoncier_raw": plu.get("raw", {}),
        },
    }

    logger.info(
        "Excel importé : ref=%s zone=%s emprise=%s hauteur=%s recul_v=%s ev=%s stat=%s",
        ref_finale, zone,
        plu.get("emp_val"), plu.get("hf_val"),
        plu.get("rv_val"), plu.get("ev_val"), plu.get("stat_acc"),
    )
    return gt


# ─────────────────────────────────────────────────────────────────────────────
# Import depuis PDF KelFoncier via LLM
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_KELFONCIER = (
    "Tu lis une étude capacitaire réglementaire produite par le logiciel KelFoncier. "
    "Extrais les données vérifiées au format JSON demandé. "
    "Réponds UNIQUEMENT avec un objet JSON valide, sans markdown."
)

_PROMPT_KELFONCIER = """\
Analyse le document KelFoncier ci-dessous et extrais les informations PLU vérifiées.

TEXTE DU DOCUMENT :
{texte}

Réponds UNIQUEMENT avec ce JSON :
{{
  "commune": "string ou null",
  "zone": "string — code de zone PLU (ex: UA, UCb, 1AUm)",
  "emprise_sol_max_pct": {{
    "valeur": float ou null,
    "non_reglementee": bool,
    "condition": "string ou null"
  }},
  "hauteur_max_m": {{
    "valeur": float ou null,
    "type": "faitage" ou "egout" ou "acrotere" ou null,
    "valeur_egout": float ou null,
    "condition": "string ou null",
    "source_oap": bool
  }},
  "recul_voirie_m": {{
    "valeur": float ou null,
    "alignement": bool,
    "formule": "string ou null"
  }},
  "recul_limites_m": {{
    "valeur": float ou null,
    "formule": "string ou null — ex: H/2",
    "minimum_m": float ou null
  }},
  "stationnement_par_logt": {{
    "accession": float ou null,
    "social": float ou null,
    "velos": float ou null
  }},
  "espace_vert_min_pct": {{
    "valeur": float ou null,
    "pleine_terre_pct": float ou null,
    "biotope": float ou null
  }},
  "source_oap_global": bool,
  "notes": "string — règles conditionnelles ou complexes non capturées ailleurs"
}}

Si une valeur n'est pas mentionnée dans le document, mettre null (pas 0).\
"""


def importer_pdf(path: str, ref: str | None = None) -> dict:
    """
    Extrait les données KelFoncier depuis un PDF d'étude via LLM Claude.
    """
    try:
        import fitz
    except ImportError:
        logger.error("pymupdf non installé — pip install pymupdf")
        raise

    import anthropic

    # Extraction texte PDF
    doc = fitz.open(path)
    texte = "".join(page.get_text() for page in doc)
    texte = texte[:20_000]  # limiter pour le LLM
    logger.info("PDF lu : %d caractères (%s)", len(texte), path)

    prompt = _PROMPT_KELFONCIER.format(texte=texte)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        system=_SYSTEM_KELFONCIER,
        messages=[{"role": "user", "content": prompt}],
    )
    contenu = response.content[0].text

    # Nettoyer le JSON
    import re as _re
    m = _re.search(r"\{.*\}", contenu, _re.DOTALL)
    if not m:
        raise ValueError(f"Aucun JSON trouvé dans la réponse LLM : {contenu[:200]}")
    data = json.loads(m.group(0))

    ref_finale = ref or Path(path).stem

    gt = {
        "ref": ref_finale,
        "source": "KelFoncier",
        "date": "",
        "commune": data.get("commune"),
        "zone": data.get("zone"),
        "champs": {
            "emprise_sol_max_pct": data.get("emprise_sol_max_pct", {"valeur": None, "non_reglementee": False, "condition": None, "source_oap": False}),
            "hauteur_max_m": data.get("hauteur_max_m", {"valeur": None, "type": None, "valeur_egout": None, "condition": None, "source_oap": False}),
            "recul_voirie_m": data.get("recul_voirie_m", {"valeur": None, "alignement": False, "formule": None}),
            "recul_limites_m": data.get("recul_limites_m", {"valeur": None, "formule": None, "minimum_m": None}),
            "stationnement_par_logt": data.get("stationnement_par_logt", {"accession": None, "social": None, "velos": None}),
            "espace_vert_min_pct": data.get("espace_vert_min_pct", {"valeur": None, "pleine_terre_pct": None, "biotope": None}),
            "source_oap_global": data.get("source_oap_global", False),
            "notes": data.get("notes", ""),
        },
    }

    logger.info(
        "PDF importé (LLM) : ref=%s zone=%s",
        ref_finale, gt["zone"],
    )
    return gt


# ─────────────────────────────────────────────────────────────────────────────
# Sauvegarde
# ─────────────────────────────────────────────────────────────────────────────

def importer_dossier(path_dir: str, output_dir: Path = GROUND_TRUTH_DIR, force: bool = False) -> list:
    """Traite tous les fichiers *.xlsx d'un dossier et génère les JSONs ground truth."""
    dossier = Path(path_dir)
    fichiers = sorted(f for f in dossier.glob("*.xlsx") if not f.name.startswith("~$"))
    if not fichiers:
        print(f"  Aucun fichier .xlsx trouvé dans {dossier}")
        return []
    resultats = []
    for f in fichiers:
        try:
            gt = importer_excel(str(f))
            ref = gt["ref"].replace(" ", "_")
            dest = output_dir / f"{ref}.json"
            if dest.exists() and not force:
                print(f"  [SKIP] {f.name} -> {dest.name} (existe deja, --force pour ecraser)")
                continue
            path = sauvegarder(gt, output_dir)
            resultats.append(path)
        except Exception as e:
            logger.error("Erreur sur %s : %s", f.name, e)
    return resultats


def sauvegarder(gt: dict, output_dir: Path = GROUND_TRUTH_DIR) -> Path:
    """Sauvegarde le ground truth JSON dans le répertoire ground_truth/."""
    ref = gt["ref"].replace(" ", "_")
    path = output_dir / f"{ref}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(gt, f, ensure_ascii=False, indent=2)
    print(f"  Sauvegardé : {path}")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Importe les données KelFoncier vers le format ground truth JSON"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--excel", metavar="FICHIER.xlsx", help="Fichier Excel KelFoncier")
    group.add_argument("--pdf", metavar="FICHIER.pdf", help="PDF d'étude KelFoncier")
    group.add_argument("--dir", metavar="DOSSIER", help="Dossier contenant des fichiers Excel KelFoncier")
    parser.add_argument("--ref", metavar="REF", help="Référence cadastrale (optionnel, sinon déduite)")
    parser.add_argument("--force", action="store_true", help="Ecraser les JSONs existants (mode --dir)")
    parser.add_argument("--out", metavar="DIR", default=str(GROUND_TRUTH_DIR),
                        help=f"Répertoire de sortie (défaut: {GROUND_TRUTH_DIR})")
    args = parser.parse_args()

    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.dir:
        print(f"Import dossier : {args.dir}")
        paths = importer_dossier(args.dir, output_dir, force=args.force)
        print(f"\n{len(paths)} fichier(s) importe(s) dans {output_dir}")
        return

    if args.excel:
        print(f"Import Excel : {args.excel}")
        gt = importer_excel(args.excel, ref=args.ref)
    else:
        print(f"Import PDF (LLM) : {args.pdf}")
        gt = importer_pdf(args.pdf, ref=args.ref)

    path = sauvegarder(gt, output_dir)

    print(f"\nResultat :")
    print(f"  Reference : {gt['ref']}")
    print(f"  Commune   : {gt.get('commune')}")
    print(f"  Zone      : {gt.get('zone')}")
    champs = gt["champs"]
    print(f"  Emprise   : {champs['emprise_sol_max_pct']['valeur']} %  (NR={champs['emprise_sol_max_pct']['non_reglementee']})")
    print(f"  Niveaux   : {champs['hauteur_max_m'].get('_nb_niveaux_kelfoncier')}  Hauteur: {champs['hauteur_max_m']['valeur']} m")
    print(f"  Recul lim : {champs['recul_limites_m']['valeur']} m  formule: {champs['recul_limites_m']['formule']}")
    print(f"  Stationn. : {champs['stationnement_par_logt']['accession']} pl/logt")
    print(f"\n  -> Completer manuellement les champs null : hauteur, recul voirie, espaces verts")
    print(f"  -> Fichier : {path}")


if __name__ == "__main__":
    main()
