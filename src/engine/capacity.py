"""
Moteur de calcul capacitaire réglementaire.

Responsabilités :
- Calculer l'emprise au sol maximale, la surface de plancher, le nombre de niveaux
- Estimer la fourchette de logements constructibles (T2/T3)
- Retourner un objet EtudeCapacitaire avec les alertes issues des contraintes PLU
"""

from dataclasses import dataclass, field

from src.api.models import Parcelle
from src.parser.rules_model import ReglesUrbanisme

# Constantes de calcul
_HAUTEUR_NIVEAU_M = 3.0        # hauteur moyenne par niveau
_HAUTEUR_MAX_DEFAUT_M = 12.0   # si non précisé dans le PLU
_EMPRISE_MAX_DEFAUT_PCT = 60.0 # si non précisé dans le PLU
_SURFACE_T2_M2 = 50.0          # surface moyenne T2 (estimation logements max)
_SURFACE_T3_M2 = 65.0          # surface moyenne T3 (estimation logements min)
_RATIO_HABITABLE = 0.75        # part de la surface de plancher réellement habitable


@dataclass
class EtudeCapacitaire:
    emprise_sol_max_m2: float
    surface_plancher_max_m2: float
    hauteur_max_m: float
    nb_niveaux_estimes: int
    nb_logements_estimes_min: int
    nb_logements_estimes_max: int
    alertes: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)  # valeurs par défaut utilisées


def calculer_capacite(parcelle: Parcelle, regles: ReglesUrbanisme) -> EtudeCapacitaire:
    """
    Calcule la capacité constructible d'une parcelle selon les règles PLU.

    Toutes les valeurs manquantes sont remplacées par des valeurs par défaut
    conservative — signalées dans le champ hypotheses.
    """
    alertes: list[str] = list(regles.contraintes)
    hypotheses: list[str] = []

    # --- Emprise au sol ---
    if regles.emprise_sol_max_pct is not None:
        emprise_pct = regles.emprise_sol_max_pct
    else:
        emprise_pct = _EMPRISE_MAX_DEFAUT_PCT
        hypotheses.append(
            f"Emprise au sol non précisée — valeur par défaut appliquée : {_EMPRISE_MAX_DEFAUT_PCT}%"
        )

    emprise_max_m2 = parcelle.surface_m2 * (emprise_pct / 100)

    # --- Hauteur et niveaux ---
    if regles.hauteur_max_m is not None:
        hauteur_m = regles.hauteur_max_m
    else:
        hauteur_m = _HAUTEUR_MAX_DEFAUT_M
        hypotheses.append(
            f"Hauteur non précisée — valeur par défaut appliquée : {_HAUTEUR_MAX_DEFAUT_M} m"
        )

    nb_niveaux = max(1, int(hauteur_m / _HAUTEUR_NIVEAU_M))

    # --- Surface de plancher ---
    if regles.surface_plancher_max_m2 is not None:
        # COS ou gabarit de surface explicite dans le PLU
        sp_max_m2 = regles.surface_plancher_max_m2
    else:
        sp_max_m2 = emprise_max_m2 * nb_niveaux

    # Alerte zone non constructible
    if regles.zone.startswith("N") or regles.zone.startswith("A"):
        alertes.append(
            f"Zone {regles.zone} : constructibilité très limitée voire nulle — "
            "vérifier les dispositions spécifiques avant tout projet"
        )

    # Alerte reculs
    if regles.recul_voirie_m and regles.recul_voirie_m > 0:
        alertes.append(f"Recul obligatoire de {regles.recul_voirie_m} m par rapport à la voirie")
    if regles.recul_limites_m and regles.recul_limites_m > 0:
        alertes.append(f"Recul de {regles.recul_limites_m} m par rapport aux limites séparatives")

    # --- Estimation logements ---
    surface_habitable = sp_max_m2 * _RATIO_HABITABLE
    lgt_min = max(0, int(surface_habitable / _SURFACE_T3_M2))  # T3 → moins de logements
    lgt_max = max(0, int(surface_habitable / _SURFACE_T2_M2))  # T2 → plus de logements

    return EtudeCapacitaire(
        emprise_sol_max_m2=round(emprise_max_m2, 1),
        surface_plancher_max_m2=round(sp_max_m2, 1),
        hauteur_max_m=hauteur_m,
        nb_niveaux_estimes=nb_niveaux,
        nb_logements_estimes_min=lgt_min,
        nb_logements_estimes_max=lgt_max,
        alertes=alertes,
        hypotheses=hypotheses,
    )
