"""
Modèle de données pour les règles d'urbanisme extraites d'un PLU.

Contient la dataclass ReglesUrbanisme :
- zone, emprise_sol_max_pct, hauteur_max_m, surface_plancher_max_m2
- usages_autorises, usages_interdits, contraintes
- recul_voirie_m, recul_limites_m
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReglesUrbanisme:
    zone: str                                    # ex: "UA", "UB", "N"
    emprise_sol_max_pct: Optional[float]         # % de la parcelle (ex: 60.0)
    hauteur_max_m: Optional[float]               # hauteur maximale en mètres
    surface_plancher_max_m2: Optional[float]     # surface de plancher max (si COS défini)
    usages_autorises: list[str] = field(default_factory=list)
    usages_interdits: list[str] = field(default_factory=list)
    contraintes: list[str] = field(default_factory=list)   # alertes réglementaires
    recul_voirie_m: Optional[float] = None       # recul par rapport à la voirie
    recul_limites_m: Optional[float] = None      # recul par rapport aux limites séparatives
    emprise_non_reglementee: bool = False        # True si le PLU indique explicitement "non réglementée"
    stationnement_par_logt: Optional[float] = None  # places de stationnement par logement (ex: 1.0)
    espace_vert_min_pct: Optional[float] = None     # % minimum du terrain en espace vert
