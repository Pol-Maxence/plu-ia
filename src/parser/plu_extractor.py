"""
Extraction des règles d'urbanisme depuis le texte brut d'un PLU via LLM (Claude).

Responsabilités :
- Envoyer le texte PLU (ou un extrait ciblé sur la zone) à Claude
- Structurer la réponse en objet ReglesUrbanisme via JSON
- Gérer les cas où le LLM retourne un JSON invalide ou incomplet
"""

import json
import logging
import re

import anthropic

from src.parser.rules_model import ReglesUrbanisme

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-20250514"

_SYSTEM_PROMPT = """Tu es un expert en droit de l'urbanisme français.
Tu analyses des règlements PLU et extrais les règles sous forme de JSON structuré.
Tu réponds UNIQUEMENT avec un objet JSON valide, sans markdown, sans explication.
La zone analysée est imposée dans la requête. Tu dois retourner exactement ce code de zone dans le champ "zone", même si le texte mentionne d'autres sous-secteurs."""

_USER_PROMPT_TEMPLATE = """Analyse le règlement PLU ci-dessous pour la zone {zone} et extrais les règles applicables.

ATTENTION SOUS-SECTEURS : Le texte peut lister des règles différentes par sous-secteur (ex: "en UCc : 15m, autres secteurs : 9m"). Tu dois extraire UNIQUEMENT les valeurs qui s'appliquent à {zone}. Si {zone} n'est pas explicitement nommé, utilise la règle "autres secteurs" ou la règle générale. Ignore toutes les valeurs explicitement réservées à d'autres sous-secteurs.

RÈGLEMENT PLU (extrait) :
{texte}

Réponds UNIQUEMENT avec un JSON valide respectant exactement ce schéma :
{{
  "zone": "string — code de zone (ex: UA)",
  "emprise_sol_max_pct": float ou null — pourcentage emprise au sol max (ex: 60.0),
  "emprise_non_reglementee": bool — true si le texte dit explicitement "non réglementée" pour l'emprise,
  "hauteur_max_m": float ou null — hauteur maximale en mètres,
  "surface_plancher_max_m2": float ou null — surface de plancher maximale si COS défini,
  "usages_autorises": ["string", ...] — liste des usages autorisés,
  "usages_interdits": ["string", ...] — liste des usages interdits,
  "contraintes": ["string", ...] — alertes importantes (servitudes, zones N/A, ABF...),
  "recul_voirie_m": float ou null — recul/retrait/marge de recul obligatoire par rapport à la voirie, voies publiques, domaine public routier, en mètres (null si alignement sur voisins ou non précisé),
  "recul_voirie_alignement": bool — true si le PLU impose un alignement sur les constructions voisines existantes (implantation en ordre continu, à l'alignement du voisinage),
  "recul_limites_m": float ou null — recul par rapport aux limites séparatives (valeur minimale si formule),
  "recul_limites_formule": string ou null — formule si le recul est exprimé par rapport à la hauteur (ex: "H/2", "H/3"),
  "stationnement_par_logt": float ou null — nombre de places de stationnement obligatoires par logement,
  "espace_vert_min_pct": float ou null — pourcentage minimum du terrain devant être en espaces verts, espaces libres, espaces plantés, espaces non imperméabilisés, pleine terre, coefficient de végétalisation (prendre le pourcentage global, pas la pleine terre seule),
  "hauteur_egout_m": float ou null — hauteur à l'égout du toit, hauteur de sablière, hauteur de corniche, hauteur de gouttière, hauteur de l'acrotère (toit terrasse) en mètres, si précisée séparément du faîtage,
  "verbatims": {{
    "emprise_sol_max_pct": "citation exacte du texte PLU justifiant la valeur, ou null",
    "emprise_non_reglementee": "citation exacte ou null",
    "hauteur_max_m": "citation exacte ou null",
    "hauteur_egout_m": "citation exacte ou null",
    "recul_voirie_m": "citation exacte ou null",
    "recul_voirie_alignement": "citation exacte ou null",
    "recul_limites_m": "citation exacte ou null",
    "recul_limites_formule": "citation exacte ou null",
    "stationnement_par_logt": "citation exacte ou null",
    "espace_vert_min_pct": "citation exacte ou null"
  }}
}}

Exemples few-shot :
- "emprise au sol ne peut excéder 40% de la superficie du terrain" → emprise_sol_max_pct: 40.0, emprise_non_reglementee: false
- "Emprise au sol des constructions / Non réglementé." → emprise_sol_max_pct: null, emprise_non_reglementee: true
- "hauteur maximale des constructions : 12 mètres" → hauteur_max_m: 12.0
- "La hauteur des constructions est limitée à 9 mètres à l'égout du toit" → hauteur_max_m: 9.0
- "hauteur maximale : R+2 (9 m)" → hauteur_max_m: 9.0
- "La hauteur au faîtage est limitée à 9 mètres. La hauteur à l'égout du toit ne peut excéder 7 mètres." → hauteur_max_m: 9.0, hauteur_egout_m: 7.0
- "Hauteur maximale des constructions : 12 mètres au faîtage, 9 mètres à l'égout." → hauteur_max_m: 12.0, hauteur_egout_m: 9.0
- "les constructions à usage d'habitation sont autorisées" → usages_autorises: ["habitation"]
- "tout dépôt de matériaux est interdit" → usages_interdits: ["dépôt de matériaux"]
- "secteur classé monument historique" → contraintes: ["classement monument historique"]
- "il sera réalisé 1 place de stationnement par logement" → stationnement_par_logt: 1.0
- "2 places de stationnement par logement dont 1 visiteur" → stationnement_par_logt: 2.0
- "60 m² de surface de plancher = 1 place, avec un minimum de 1 place par logement" → stationnement_par_logt: 1.0
- "les espaces verts doivent représenter au moins 20% de la superficie du terrain" → espace_vert_min_pct: 20.0
- "30% de la surface du terrain sera traité en espaces verts" → espace_vert_min_pct: 30.0
- "Emprise espaces verts : 40% de surface d'espaces verts (25% de surface de pleine terre)" → espace_vert_min_pct: 40.0
- "les constructions doivent être implantées à l'alignement des constructions existantes" → recul_voirie_alignement: true, recul_voirie_m: null
- "implantation en ordre continu sur rue" → recul_voirie_alignement: true, recul_voirie_m: null
- "recul minimum de 5 m par rapport à la voie" → recul_voirie_alignement: false, recul_voirie_m: 5.0
- "Marge de recul : 3 mètres minimum par rapport à l'emprise publique" → recul_voirie_m: 3.0
- "Un retrait de 4 mètres est imposé par rapport à la limite du domaine public routier" → recul_voirie_m: 4.0
- "Les constructions doivent être implantées à une distance des limites séparatives au moins égale à la moitié de leur hauteur (H/2), sans pouvoir être inférieure à 3 mètres." → recul_limites_m: 3.0, recul_limites_formule: "H/2"
- "Retrait entre bâtiments : D ≥ H/2 minimum 2,5 mètres" → recul_limites_m: 2.5, recul_limites_formule: "H/2"

- "non réglementée en secteur UCb" (pour zone UCb) → emprise_sol_max_pct: null, emprise_non_reglementee: true
- "15% en UCa, UCe — non réglementée en UCb — 25% en UCc, UCd" (pour zone UCb) → emprise_sol_max_pct: null, emprise_non_reglementee: true  [UCb = non réglementée]
- "en secteurs UCc et UCe : 15 mètres ; autres secteurs : 9 mètres" (pour zone UCb) → hauteur_max_m: 9.0  [UCb est dans "autres secteurs"]
- "autres secteurs : 9 mètres au point le plus haut et 4,50 mètres à l'égout du toit" (pour zone UCb) → hauteur_max_m: 9.0, hauteur_egout_m: 4.5
- "Pour les secteurs UCa, UCb, et UCd, à la moitié de la hauteur mesurée à l'égout du toit avec un minimum de 3 mètres" (pour zone UCb) → recul_limites_m: 3.0, recul_limites_formule: "H/2"
- "Pour le secteur UCc, à la hauteur mesurée à l'égout du toit avec un minimum de 3 mètres" (pour zone UCb) → IGNORER, concerne UCc uniquement

Note : certains PLUi utilisent un format tableau avec des titres libres ("Emprise au sol", "Hauteur maximale") suivis de leur valeur. Lis attentivement le contexte autour de ces titres pour extraire les valeurs numériques.

Pour les verbatims : copie la phrase ou la règle exacte du texte PLU d'où provient chaque valeur extraite. Si une règle n'est pas trouvée dans le texte, mettre null pour le verbatim correspondant.

Si une valeur est absente du texte, mettre null (pas 0, pas de valeur inventée)."""


def _nettoyer_json(texte: str) -> str:
    """Extrait le JSON d'une réponse LLM qui pourrait contenir du markdown."""
    # Cherche un bloc ```json ... ``` ou juste { ... }
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texte, re.DOTALL)
    if match:
        return match.group(1)
    match = re.search(r"\{.*\}", texte, re.DOTALL)
    if match:
        return match.group(0)
    raise ValueError("Aucun JSON trouvé dans la réponse du LLM")


def _appel_llm(texte_tronque: str, zone: str, client: anthropic.Anthropic) -> ReglesUrbanisme:
    """Appel LLM unique avec un texte déjà tronqué."""
    prompt = _USER_PROMPT_TEMPLATE.format(zone=zone, texte=texte_tronque)
    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=2000,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        contenu = response.content[0].text
        json_str = _nettoyer_json(contenu)
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error("JSON invalide retourné par le LLM pour zone %r : %s", zone, e)
        raise
    except Exception as e:
        logger.error("Erreur appel LLM pour zone %r : %s", zone, e)
        raise

    data["zone"] = zone  # toujours imposer la zone fournie
    data.setdefault("usages_autorises", [])
    data.setdefault("usages_interdits", [])
    data.setdefault("contraintes", [])
    data.setdefault("recul_voirie_m", None)
    data.setdefault("recul_voirie_alignement", False)
    data.setdefault("recul_limites_m", None)
    data.setdefault("recul_limites_formule", None)
    data.setdefault("emprise_non_reglementee", False)
    data.setdefault("stationnement_par_logt", None)
    data.setdefault("espace_vert_min_pct", None)
    data.setdefault("hauteur_egout_m", None)

    verbatims_raw = data.pop("verbatims", {}) or {}
    data["verbatims"] = {k: v for k, v in verbatims_raw.items() if v and isinstance(v, str)}
    return ReglesUrbanisme(**data)


def extraire_regles_plu(
    texte_plu: str,
    zone: str,
    client: anthropic.Anthropic,
    max_chars: int = 15000,
) -> ReglesUrbanisme:
    """
    Utilise Claude pour extraire les règles PLU en JSON structuré.

    Stratégie à deux niveaux :
    1. Premier appel avec max_chars (15 000 par défaut, pas cher)
    2. Si aucune valeur clé trouvée et texte plus long → retry à 40 000 chars

    Args:
        texte_plu  : texte brut du règlement (ou extrait ciblé sur la zone)
        zone       : code de zone PLU (ex: "UA")
        client     : instance Anthropic déjà initialisée
        max_chars  : taille max du texte envoyé au LLM au premier appel

    Returns:
        ReglesUrbanisme structuré
    """
    texte = texte_plu or "(aucun texte disponible)"
    regles = _appel_llm(texte[:max_chars], zone, client)

    # Retry avec plus de contexte si aucune valeur clé extraite
    _champs_cles = [
        regles.emprise_sol_max_pct, regles.hauteur_max_m,
        regles.recul_voirie_m, regles.recul_limites_m, regles.espace_vert_min_pct,
    ]
    if (
        all(v is None for v in _champs_cles)
        and not regles.emprise_non_reglementee
        and len(texte) > max_chars
    ):
        logger.info(
            "Zone %r : aucune valeur extraite avec %d chars, retry à 40 000 chars",
            zone, max_chars,
        )
        regles = _appel_llm(texte[:40_000], zone, client)

    return regles


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    logging.basicConfig(level=logging.INFO)
    load_dotenv()

    client = anthropic.Anthropic()
    texte_test = """
    ZONE UA — Zone urbaine mixte dense
    Article UA 9 — Emprise au sol
    L'emprise au sol des constructions ne peut excéder 70% de la superficie du terrain.
    Article UA 10 — Hauteur maximale
    La hauteur maximale des constructions est fixée à 18 mètres.
    Article UA 2 — Usages autorisés
    Sont autorisés : les constructions à usage d'habitation, les commerces au rez-de-chaussée.
    """
    regles = extraire_regles_plu(texte_test, "UA", client)
    print(regles)
