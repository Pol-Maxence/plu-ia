"""
Modèles de données partagés pour les appels API.

Contient les dataclasses :
- Parcelle : données cadastrales d'une parcelle (référence, surface, commune, géométrie GeoJSON)
- ZonePLU  : zonage PLU retourné par le Géoportail (zone, libellé, partition)
"""

from dataclasses import dataclass


@dataclass
class Parcelle:
    ref_cadastrale: str      # ex: "75056000BX0042"
    surface_m2: float
    commune: str
    code_insee: str          # ex: "75116" (code dept + code commune)
    geometrie: dict          # GeoJSON geometry (Point ou Polygon)


@dataclass
class ZonePLU:
    zone: str                # ex: "UA", "UB", "N", "A"
    libelle: str             # libellé complet de la zone
    partition: str           # identifiant commune pour le Géoportail (ex: "75056")
