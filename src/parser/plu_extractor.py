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
from typing import Optional

import anthropic

from src.parser.rules_model import ReglesUrbanisme

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-20250514"

_SYSTEM_PROMPT = """Tu es un expert en droit de l'urbanisme français.
Tu analyses des règlements PLU et extrais les règles sous forme de JSON structuré.
Tu réponds UNIQUEMENT avec un objet JSON valide, sans markdown, sans explication."""

_USER_PROMPT_TEMPLATE = """Analyse le règlement PLU ci-dessous pour la zone {zone} et extrais les règles applicables.

RÈGLEMENT PLU (extrait) :
{texte}

Réponds UNIQUEMENT avec un JSON valide respectant exactement ce schéma :
{{
  "zone": "string — code de zone (ex: UA)",
  "emprise_sol_max_pct": float ou null — pourcentage emprise au sol max (ex: 60.0),
  "hauteur_max_m": float ou null — hauteur maximale en mètres,
  "surface_plancher_max_m2": float ou null — surface de plancher maximale si COS défini,
  "usages_autorises": ["string", ...] — liste des usages autorisés,
  "usages_interdits": ["string", ...] — liste des usages interdits,
  "contraintes": ["string", ...] — alertes importantes (servitudes, zones N/A, ABF...),
  "recul_voirie_m": float ou null — recul obligatoire par rapport à la voirie,
  "recul_limites_m": float ou null — recul par rapport aux limites séparatives
}}

Exemples few-shot :
- "emprise au sol ne peut excéder 40% de la superficie du terrain" → emprise_sol_max_pct: 40.0
- "hauteur maximale des constructions : 12 mètres" → hauteur_max_m: 12.0
- "les constructions à usage d'habitation sont autorisées" → usages_autorises: ["habitation"]
- "tout dépôt de matériaux est interdit" → usages_interdits: ["dépôt de matériaux"]
- "secteur classé monument historique" → contraintes: ["classement monument historique"]

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


def extraire_regles_plu(
    texte_plu: str,
    zone: str,
    client: anthropic.Anthropic,
    max_chars: int = 8000,
) -> ReglesUrbanisme:
    """
    Utilise Claude pour extraire les règles PLU en JSON structuré.

    Args:
        texte_plu  : texte brut du règlement (ou extrait ciblé sur la zone)
        zone       : code de zone PLU (ex: "UA")
        client     : instance Anthropic déjà initialisée
        max_chars  : taille max du texte envoyé au LLM (protection contre les docs trop longs)

    Returns:
        ReglesUrbanisme structuré
    """
    texte_tronque = texte_plu[:max_chars] if texte_plu else "(aucun texte disponible)"
    prompt = _USER_PROMPT_TEMPLATE.format(zone=zone, texte=texte_tronque)

    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=1500,
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

    # Valeurs par défaut pour les champs liste si absents
    data.setdefault("usages_autorises", [])
    data.setdefault("usages_interdits", [])
    data.setdefault("contraintes", [])
    data.setdefault("recul_voirie_m", None)
    data.setdefault("recul_limites_m", None)

    return ReglesUrbanisme(**data)


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
