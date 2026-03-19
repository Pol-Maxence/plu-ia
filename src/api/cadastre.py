"""
Module d'accès aux APIs cadastrales.

Responsabilités :
- Géocoder une adresse postale via api-adresse.data.gouv.fr
- Récupérer les informations cadastrales (surface, géométrie, référence)
  via apicarto.ign.fr/api/cadastre/parcelle
- Retourner un objet Parcelle structuré
"""

import logging
import requests
from src.api.models import Parcelle

logger = logging.getLogger(__name__)


def geocoder_adresse(adresse: str) -> tuple[float, float]:
    """
    Convertit une adresse postale en coordonnées GPS (longitude, latitude).
    Utilise l'API adresse.data.gouv.fr — gratuite, sans clé.

    Retourne : (longitude, latitude)
    """
    try:
        r = requests.get(
            "https://api-adresse.data.gouv.fr/search/",
            params={"q": adresse, "limit": 1},
            timeout=10,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
        if not features:
            raise ValueError(f"Adresse introuvable : {adresse!r}")
        lon, lat = features[0]["geometry"]["coordinates"]
        return lon, lat
    except Exception as e:
        logger.error("Erreur géocodage adresse %r : %s", adresse, e)
        raise


def get_parcelle_by_coords(lon: float, lat: float) -> Parcelle:
    """
    Récupère les données cadastrales de la parcelle à partir de coordonnées GPS.
    Utilise apicarto.ign.fr/api/cadastre/parcelle.
    """
    try:
        r = requests.get(
            "https://apicarto.ign.fr/api/cadastre/parcelle",
            params={"lon": lon, "lat": lat},
            timeout=10,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
        if not features:
            raise ValueError(f"Aucune parcelle trouvée aux coordonnées ({lat}, {lon})")
        data = features[0]
        props = data["properties"]
        code_insee = props.get("code_dep", "") + props.get("code_com", "")
        return Parcelle(
            ref_cadastrale=props.get("numero", ""),
            surface_m2=float(props.get("contenance", 0)),
            commune=props.get("nom_com", ""),
            code_insee=code_insee,
            geometrie=data["geometry"],
        )
    except Exception as e:
        logger.error("Erreur récupération parcelle (%s, %s) : %s", lat, lon, e)
        raise


def get_parcelle_by_address(adresse: str) -> Parcelle:
    """
    Pipeline complet : adresse postale → objet Parcelle.
    Enchaîne le géocodage et la récupération cadastrale.
    """
    lon, lat = geocoder_adresse(adresse)
    return get_parcelle_by_coords(lon, lat)


def get_parcelle_by_ref(ref_cadastrale: str) -> Parcelle:
    """
    Récupère une parcelle directement depuis sa référence cadastrale.
    ex: "75056000BX0042"
    """
    try:
        r = requests.get(
            "https://apicarto.ign.fr/api/cadastre/parcelle",
            params={"numero": ref_cadastrale},
            timeout=10,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
        if not features:
            raise ValueError(f"Parcelle introuvable : {ref_cadastrale!r}")
        data = features[0]
        props = data["properties"]
        code_insee = props.get("code_dep", "") + props.get("code_com", "")
        return Parcelle(
            ref_cadastrale=ref_cadastrale,
            surface_m2=float(props.get("contenance", 0)),
            commune=props.get("nom_com", ""),
            code_insee=code_insee,
            geometrie=data["geometry"],
        )
    except Exception as e:
        logger.error("Erreur récupération parcelle %r : %s", ref_cadastrale, e)
        raise


if __name__ == "__main__":
    # Test manuel — parcelle de référence Paris 16e
    logging.basicConfig(level=logging.INFO)
    p = get_parcelle_by_ref("75056000BX0042")
    print(p)
