"""
Moteur de calcul capacitaire réglementaire.

Responsabilités :
- Calculer l'emprise au sol maximale, la surface de plancher, le nombre de niveaux
- Déduire les surfaces non constructibles : reculs (shapely), stationnement, espaces verts
- Estimer la fourchette de logements constructibles
- Retourner un objet EtudeCapacitaire avec les alertes issues des contraintes PLU
"""

from dataclasses import dataclass, field
from typing import Optional

from src.api.models import Parcelle
from src.parser.rules_model import ReglesUrbanisme

# Constantes de calcul
_HAUTEUR_NIVEAU_M = 3.0        # hauteur moyenne par niveau
_HAUTEUR_MAX_DEFAUT_M = 12.0   # si non précisé dans le PLU
_EMPRISE_MAX_DEFAUT_PCT = 60.0 # si non précisé dans le PLU
_SURFACE_T2_M2 = 50.0          # surface moyenne T2 (estimation logements max)
_SURFACE_T3_M2 = 65.0          # surface moyenne T3 (estimation logements min)
_RATIO_HABITABLE = 0.75        # part de la surface de plancher réellement habitable
_SURFACE_PAR_PLACE_M2 = 16.0  # surface par place de stationnement aérien (place + manœuvre)
_SURFACE_LOGT_PROVISION_M2 = 60.0  # surface moyenne provisoire pour estimer nb logts avant déduction parking


@dataclass
class EtudeCapacitaire:
    emprise_sol_max_m2: float       # emprise nette (reculs + EV) = footprint constructible
    emprise_brute_m2: float         # plafond PLU brut (avant déductions)
    emprise_apres_reculs_m2: float  # après buffer shapely
    surface_ev_m2: float            # emprise déduite pour espaces verts
    surface_parking_m2: float       # surface de plancher déduite pour stationnement
    sp_brute_m2: float              # surface de plancher brute (emprise_nette × niveaux)
    surface_plancher_max_m2: float  # surface de plancher nette (après déduction parking)
    hauteur_max_m: float
    nb_niveaux_estimes: int
    nb_logements_estimes_min: int
    nb_logements_estimes_max: int
    alertes: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)


def _appliquer_reculs(
    geometrie: dict,
    surface_m2: float,
    recul_voirie_m: Optional[float],
    recul_limites_m: Optional[float],
) -> float:
    """
    Calcule la surface constructible après application des reculs via buffer shapely.

    La géométrie est en coordonnées géographiques (degrés). Le recul en mètres est converti
    en degrés avec une approximation valable pour la France métropolitaine (47°N) :
      1° lat ≈ 111 000 m  |  1° lon ≈ 74 000 m → moyenne isotrope : 1° ≈ 111 000 m

    Le ratio de réduction (surface après buffer / surface avant) est appliqué à surface_m2
    (valeur officielle cadastre, plus précise que le calcul en degrés carrés).

    Retourne 0.0 si la géométrie est un Point (non bufferable).
    """
    try:
        from shapely.geometry import shape
        if geometrie.get("type") == "Point":
            return 0.0
        geom = shape(geometrie)
        if geom.is_empty or geom.area <= 0:
            return surface_m2
        recul_m = max(recul_voirie_m or 0.0, recul_limites_m or 0.0)
        if recul_m <= 0:
            return surface_m2
        # Conversion mètres → degrés (approximation France, 47°N)
        recul_deg = recul_m / 111_000
        constructible = geom.buffer(-recul_deg)
        if constructible.is_empty:
            return 0.0
        # Appliquer le ratio de réduction à la surface officielle
        ratio = max(0.0, constructible.area / geom.area)
        return ratio * surface_m2
    except Exception:
        return 0.0


def calculer_capacite(
    parcelle: Parcelle,
    regles: ReglesUrbanisme,
    surface_t2_m2: float = _SURFACE_T2_M2,
    surface_t3_m2: float = _SURFACE_T3_M2,
    ratio_habitable: float = _RATIO_HABITABLE,
) -> EtudeCapacitaire:
    """
    Calcule la capacité constructible d'une parcelle selon les règles PLU.

    Applique 3 déductions successives sur l'emprise brute PLU :
    1. Reculs obligatoires (calcul géométrique shapely)
    2. Espaces verts réglementaires
    3. Surface de stationnement estimée

    Args:
        parcelle         : données cadastrales de la parcelle
        regles           : règles PLU extraites (LLM ou saisie manuelle)
        surface_t2_m2    : surface unitaire T2 pour estimation haute des logements
        surface_t3_m2    : surface unitaire T3 pour estimation basse des logements
        ratio_habitable  : ratio surface habitable / surface plancher brute
    """
    alertes: list[str] = list(regles.contraintes)
    hypotheses: list[str] = []

    # -------------------------------------------------------------------------
    # Emprise brute PLU
    # -------------------------------------------------------------------------
    if regles.emprise_sol_max_pct is not None:
        emprise_pct = regles.emprise_sol_max_pct
    elif regles.emprise_non_reglementee:
        emprise_pct = 100.0
        alertes.append(
            "Emprise au sol non réglementée par le PLU — 100% du polygone utilisé. "
            "Les reculs, espaces verts et stationnement limitent la surface constructible effective."
        )
    else:
        emprise_pct = _EMPRISE_MAX_DEFAUT_PCT
        hypotheses.append(
            f"Emprise au sol non précisée — valeur par défaut appliquée : {_EMPRISE_MAX_DEFAUT_PCT}%"
        )

    emprise_brute_m2 = parcelle.surface_m2 * (emprise_pct / 100)

    # -------------------------------------------------------------------------
    # Hauteur et niveaux
    # -------------------------------------------------------------------------
    if regles.hauteur_max_m is not None:
        hauteur_m = regles.hauteur_max_m
    else:
        hauteur_m = _HAUTEUR_MAX_DEFAUT_M
        hypotheses.append(
            f"Hauteur non précisée — valeur par défaut appliquée : {_HAUTEUR_MAX_DEFAUT_M} m"
        )

    nb_niveaux = max(1, int(hauteur_m / _HAUTEUR_NIVEAU_M))

    # -------------------------------------------------------------------------
    # Déduction 1 — Reculs (buffer shapely sur polygone parcelle)
    # -------------------------------------------------------------------------
    has_reculs = (regles.recul_voirie_m and regles.recul_voirie_m > 0) or \
                 (regles.recul_limites_m and regles.recul_limites_m > 0)

    if has_reculs:
        surface_apres_reculs = _appliquer_reculs(
            parcelle.geometrie, parcelle.surface_m2, regles.recul_voirie_m, regles.recul_limites_m
        )
        if surface_apres_reculs > 0:
            # L'emprise après reculs ne peut pas dépasser l'emprise brute PLU
            emprise_apres_reculs_m2 = min(surface_apres_reculs, emprise_brute_m2)
        else:
            # Fallback si shapely échoue (Point ou géométrie invalide)
            emprise_apres_reculs_m2 = emprise_brute_m2
            hypotheses.append("Reculs non calculables géométriquement (géométrie Point) — déduction non appliquée")
        if regles.recul_voirie_m and regles.recul_voirie_m > 0:
            alertes.append(f"Recul obligatoire de {regles.recul_voirie_m} m par rapport à la voirie")
        if regles.recul_limites_m and regles.recul_limites_m > 0:
            alertes.append(f"Recul de {regles.recul_limites_m} m par rapport aux limites séparatives")
    else:
        emprise_apres_reculs_m2 = emprise_brute_m2

    # -------------------------------------------------------------------------
    # Déduction 2 — Espaces verts réglementaires
    # -------------------------------------------------------------------------
    if regles.espace_vert_min_pct and regles.espace_vert_min_pct > 0:
        surface_ev_m2 = parcelle.surface_m2 * (regles.espace_vert_min_pct / 100)
    else:
        surface_ev_m2 = 0.0

    # -------------------------------------------------------------------------
    # Emprise nette = emprise après reculs − EV
    # Le stationnement est déduit de la surface de plancher (SP), pas de l'emprise au sol.
    # Plancher à 5% de l'emprise brute pour éviter résultats aberrants.
    # -------------------------------------------------------------------------
    emprise_nette_m2 = emprise_apres_reculs_m2 - surface_ev_m2
    emprise_nette_m2 = max(emprise_nette_m2, emprise_brute_m2 * 0.05)

    # -------------------------------------------------------------------------
    # Surface de plancher brute
    # -------------------------------------------------------------------------
    if regles.surface_plancher_max_m2 is not None:
        sp_brute_m2 = regles.surface_plancher_max_m2
        if emprise_nette_m2 > 0:
            nb_niveaux = max(1, round(sp_brute_m2 / emprise_nette_m2))
    else:
        sp_brute_m2 = emprise_nette_m2 * nb_niveaux

    # -------------------------------------------------------------------------
    # Déduction 3 — Stationnement (sur la surface de plancher, pas sur l'emprise)
    # -------------------------------------------------------------------------
    if regles.stationnement_par_logt and regles.stationnement_par_logt > 0:
        lgt_provisoire = max(1, int(sp_brute_m2 * ratio_habitable / _SURFACE_LOGT_PROVISION_M2))
        surface_parking_m2 = lgt_provisoire * regles.stationnement_par_logt * _SURFACE_PAR_PLACE_M2
    else:
        surface_parking_m2 = 0.0

    # SP nette = SP brute − parking (plancher à 5% de la SP brute)
    sp_max_m2 = max(sp_brute_m2 - surface_parking_m2, sp_brute_m2 * 0.05)

    # Alerte zone non constructible
    if regles.zone and (regles.zone.startswith("N") or regles.zone.startswith("A")):
        alertes.append(
            f"Zone {regles.zone} : constructibilité très limitée voire nulle — "
            "vérifier les dispositions spécifiques avant tout projet"
        )

    # -------------------------------------------------------------------------
    # Estimation logements
    # -------------------------------------------------------------------------
    surface_habitable = sp_max_m2 * ratio_habitable
    lgt_min = max(0, int(surface_habitable / surface_t3_m2))
    lgt_max = max(0, int(surface_habitable / surface_t2_m2))
    hypotheses.append(
        f"Estimation logements : T2 ({surface_t2_m2:.0f} m²) à T3 ({surface_t3_m2:.0f} m²), "
        f"ratio habitable {ratio_habitable:.0%}"
    )

    return EtudeCapacitaire(
        emprise_sol_max_m2=round(emprise_nette_m2, 1),
        emprise_brute_m2=round(emprise_brute_m2, 1),
        emprise_apres_reculs_m2=round(emprise_apres_reculs_m2, 1),
        surface_ev_m2=round(surface_ev_m2, 1),
        surface_parking_m2=round(surface_parking_m2, 1),
        sp_brute_m2=round(sp_brute_m2, 1),
        surface_plancher_max_m2=round(sp_max_m2, 1),
        hauteur_max_m=hauteur_m,
        nb_niveaux_estimes=nb_niveaux,
        nb_logements_estimes_min=lgt_min,
        nb_logements_estimes_max=lgt_max,
        alertes=alertes,
        hypotheses=hypotheses,
    )
