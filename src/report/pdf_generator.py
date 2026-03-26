"""
Génération du rapport PDF d'étude capacitaire via ReportLab.

Responsabilités :
- Mettre en page les données de la Parcelle, des ReglesUrbanisme et de l'EtudeCapacitaire
- Inclure : carte de localisation, schéma volumétrique, tableaux, alertes, disclaimer
- Sauvegarder le PDF au chemin spécifié
"""

import io
import logging
from datetime import date

import requests
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image, KeepTogether
)
from reportlab.graphics.shapes import Drawing, Rect, Line, String

from src.api.models import Parcelle
from src.parser.rules_model import ReglesUrbanisme
from src.engine.capacity import EtudeCapacitaire

logger = logging.getLogger(__name__)

# Palette
_BLEU       = colors.HexColor("#1A3A5C")
_BLEU_CLAIR = colors.HexColor("#2563EB")
_TEAL       = colors.HexColor("#0D9488")
_ORANGE     = colors.HexColor("#EA580C")
_VERT       = colors.HexColor("#10B981")
_GRIS_CLAIR = colors.HexColor("#F1F5F9")
_GRIS       = colors.HexColor("#64748B")
_GRIS_BD    = colors.HexColor("#CBD5E1")

PAGE_W = A4[0] - 4 * cm   # largeur utile


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "titre": ParagraphStyle(
            "titre", parent=base["Title"],
            textColor=_BLEU, fontSize=22, spaceAfter=2, leading=26,
        ),
        "sous_titre": ParagraphStyle(
            "sous_titre", parent=base["Normal"],
            textColor=_GRIS, fontSize=10, spaceAfter=14,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"],
            textColor=_BLEU, fontSize=12, spaceBefore=16, spaceAfter=6,
            borderPad=0,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"],
            fontSize=9, leading=14, textColor=colors.HexColor("#334155"),
        ),
        "label": ParagraphStyle(
            "label", parent=base["Normal"],
            fontSize=8.5, textColor=_GRIS, leading=13,
        ),
        "alerte": ParagraphStyle(
            "alerte", parent=base["Normal"],
            textColor=_ORANGE, fontSize=9, leading=13,
        ),
        "hypothese": ParagraphStyle(
            "hypothese", parent=base["Normal"],
            textColor=_GRIS, fontSize=8.5, leading=13, fontName="Helvetica-Oblique",
        ),
        "disclaimer": ParagraphStyle(
            "disclaimer", parent=base["Normal"],
            textColor=_GRIS, fontSize=7.5, leading=11, fontName="Helvetica-Oblique",
        ),
    }


# ---------------------------------------------------------------------------
# Helper verbatim
# ---------------------------------------------------------------------------

def _cell_avec_verbatim(valeur_str: str, champ: str, regles: ReglesUrbanisme) -> object:
    """
    Retourne un Paragraph ReportLab avec la valeur + citation PLU en italique gris
    si un verbatim est disponible pour ce champ, sinon retourne la chaîne brute.
    """
    verbatim = regles.verbatims.get(champ) if regles.verbatims else None
    if not verbatim:
        return valeur_str
    citation = verbatim[:130] + ("…" if len(verbatim) > 130 else "")
    # Échapper les caractères XML spéciaux dans la citation
    citation = citation.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = _styles()
    return Paragraph(
        f'{valeur_str}<br/><font size="7" color="#94A3B8"><i>« {citation} »</i></font>',
        s["body"],
    )


# ---------------------------------------------------------------------------
# Carte de localisation (tuiles IGN + contour parcelle)
# ---------------------------------------------------------------------------

def _lon_lat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    """Convertit lon/lat en coordonnées de tuile (x, y) slippy map."""
    import math
    n = 2 ** zoom
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def _lon_lat_to_pixel(lon: float, lat: float, zoom: int, x0_tile: int, y0_tile: int) -> tuple[int, int]:
    """Convertit lon/lat en pixel dans l'image assemblée (origine = coin haut-gauche de la tuile x0,y0)."""
    import math
    n = 2 ** zoom
    px = (lon + 180) / 360 * n * 256 - x0_tile * 256
    py = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n * 256 - y0_tile * 256
    return int(px), int(py)


def _get_tile(zoom: int, x: int, y: int, layer: str = "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2"):
    """Télécharge une tuile IGN et retourne une image PIL."""
    from PIL import Image as PILImage
    url = (
        f"https://data.geopf.fr/wmts"
        f"?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
        f"&LAYER={layer}"
        f"&STYLE=normal&TILEMATRIXSET=PM"
        f"&TILEMATRIX={zoom}&TILEROW={y}&TILECOL={x}"
        f"&FORMAT=image%2Fpng"
    )
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return PILImage.open(io.BytesIO(r.content)).convert("RGBA")
    except Exception:
        return None


def _extraire_rings(geometrie: dict) -> list:
    """Extrait tous les anneaux de coordonnées d'une géométrie GeoJSON."""
    if geometrie["type"] == "Polygon":
        return geometrie["coordinates"]
    elif geometrie["type"] == "MultiPolygon":
        return [ring for poly in geometrie["coordinates"] for ring in poly]
    elif geometrie["type"] == "Point":
        lon, lat = geometrie["coordinates"]
        return [[[lon, lat]]]
    return []


def _carte_localisation(geometries: "list[dict] | dict", zoom: int = 18) -> Image | None:
    """
    Génère une carte IGN avec le contour de la/des parcelle(s) dessiné en rouge.
    Accepte une seule géométrie GeoJSON ou une liste pour le mode multi-parcelles.
    Utilise un supersampling 2× pour des contours anti-aliasés sans pixelisation.
    """
    from PIL import Image as PILImage, ImageDraw

    SCALE = 2  # supersampling : dessin à 2×, downscale Lanczos → contours nets

    if isinstance(geometries, dict):
        geometries = [geometries]

    # Extraire tous les anneaux de toutes les géométries
    all_rings_by_geom = [_extraire_rings(g) for g in geometries]
    all_rings = [ring for rings in all_rings_by_geom for ring in rings]
    if not all_rings:
        return None

    all_coords = [pt for ring in all_rings for pt in ring]
    lons = [c[0] for c in all_coords]
    lats = [c[1] for c in all_coords]

    # Bounding box + marge de 30%
    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)
    marge_lon = max((lon_max - lon_min) * 0.3, 0.0003)
    marge_lat = max((lat_max - lat_min) * 0.3, 0.0003)
    lon_min -= marge_lon; lon_max += marge_lon
    lat_min -= marge_lat; lat_max += marge_lat

    # Tuiles couvrant la bounding box
    x_min, y_max = _lon_lat_to_tile(lon_min, lat_min, zoom)  # lat_min → y_max (y inversé)
    x_max, y_min = _lon_lat_to_tile(lon_max, lat_max, zoom)

    # Limiter à une grille 3×3 max pour éviter trop de requêtes
    if (x_max - x_min + 1) * (y_max - y_min + 1) > 9:
        zoom = max(15, zoom - 1)
        x_min, y_max = _lon_lat_to_tile(lon_min, lat_min, zoom)
        x_max, y_min = _lon_lat_to_tile(lon_max, lat_max, zoom)

    cols = x_max - x_min + 1
    rows = y_max - y_min + 1
    tile_px = 256 * SCALE  # taille des tuiles au niveau 2×
    size_2x = (cols * tile_px, rows * tile_px)
    canvas = PILImage.new("RGBA", size_2x, (200, 200, 200, 255))

    # Couche 1 : fond topo IGN (tuiles upscalées 2×)
    for xi, tx in enumerate(range(x_min, x_max + 1)):
        for yi, ty in enumerate(range(y_min, y_max + 1)):
            tile = _get_tile(zoom, tx, ty, "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2")
            if tile:
                tile_2x = tile.resize((tile_px, tile_px), PILImage.LANCZOS)
                canvas.paste(tile_2x, (xi * tile_px, yi * tile_px))

    # Couche 2 : parcelles cadastrales, opacité 85%
    cadastre_layer = PILImage.new("RGBA", size_2x, (0, 0, 0, 0))
    for xi, tx in enumerate(range(x_min, x_max + 1)):
        for yi, ty in enumerate(range(y_min, y_max + 1)):
            tile = _get_tile(zoom, tx, ty, "CADASTRALPARCELS.PARCELLAIRE_EXPRESS")
            if tile:
                tile_2x = tile.resize((tile_px, tile_px), PILImage.LANCZOS)
                cadastre_layer.paste(tile_2x, (xi * tile_px, yi * tile_px))
    r2, g2, b2, a2 = cadastre_layer.split()
    a2 = a2.point(lambda p: int(p * 0.85))
    cadastre_layer = PILImage.merge("RGBA", (r2, g2, b2, a2))
    canvas = PILImage.alpha_composite(canvas, cadastre_layer)

    # Couche 3 : contour de la/des parcelle(s) — dessin 2× pour anti-aliasing
    overlay = PILImage.new("RGBA", size_2x, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for ring in all_rings:
        pixels_1x = [_lon_lat_to_pixel(c[0], c[1], zoom, x_min, y_min) for c in ring]
        pixels_2x = [(x * SCALE, y * SCALE) for x, y in pixels_1x]
        if len(pixels_2x) >= 2:
            draw.polygon(pixels_2x, fill=(220, 38, 38, 55))
            draw.line(pixels_2x + [pixels_2x[0]], fill=(220, 38, 38, 220), width=3)
    canvas = PILImage.alpha_composite(canvas, overlay)

    # Downscale 2× → 1× via Lanczos (effet anti-aliasing sur les contours)
    canvas = canvas.convert("RGB")
    size_1x = (cols * 256, rows * 256)
    canvas = canvas.resize(size_1x, PILImage.LANCZOS)

    # Recadrer sur la bounding box utile avec marges
    px_min, py_max = _lon_lat_to_pixel(lon_min, lat_min, zoom, x_min, y_min)
    px_max, py_min = _lon_lat_to_pixel(lon_max, lat_max, zoom, x_min, y_min)
    px_min = max(0, px_min); py_min = max(0, py_min)
    px_max = min(canvas.width, px_max); py_max = min(canvas.height, py_max)
    if px_max > px_min and py_max > py_min:
        canvas = canvas.crop((px_min, py_min, px_max, py_max))

    # Conversion en image ReportLab — taille ajustée pour tenir dans la colonne sans débordement
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", dpi=(150, 150))
    buf.seek(0)
    return Image(buf, width=8.2 * cm, height=8.2 * cm)


# ---------------------------------------------------------------------------
# Schéma volumétrique 2D
# ---------------------------------------------------------------------------

def _schema_volumetrique(
    etude: EtudeCapacitaire,
    regles: ReglesUrbanisme,
    largeur: float = PAGE_W,
) -> Drawing:
    """
    Génère un schéma de coupe de façade simplifié :
    - parcelle au sol avec reculs
    - gabarit constructible en bleu
    - cotations hauteur et reculs
    """
    h_dessin = 160
    drawing = Drawing(largeur, h_dessin)

    # Dimensions de rendu (normalisées)
    marge_h = 30        # marge gauche/droite dans le dessin
    sol_y = 30          # ligne de sol
    larg_utile = largeur - 2 * marge_h

    # Largeur parcelle représentée (100% = larg_utile)
    # Reculs en proportion (on suppose parcelle ~15m de large pour le schéma)
    largeur_parcelle_m = 15.0
    recul_g = regles.recul_voirie_m or 0
    recul_d = regles.recul_limites_m or 0
    recul_g_px = (recul_g / largeur_parcelle_m) * larg_utile
    recul_d_px = (recul_d / largeur_parcelle_m) * larg_utile

    hauteur_max_m = etude.hauteur_max_m
    hauteur_px = min(110, hauteur_max_m * 5)   # 1m = 5px, max 110px

    x0 = marge_h
    x1 = marge_h + larg_utile

    # Sol (ligne noire)
    drawing.add(Line(x0 - 10, sol_y, x1 + 10, sol_y, strokeColor=colors.black, strokeWidth=1.5))

    # Parcelle (gris clair)
    drawing.add(Rect(
        x0, sol_y, larg_utile, 8,
        fillColor=_GRIS_CLAIR, strokeColor=_GRIS_BD, strokeWidth=0.5
    ))

    # Zone constructible (bleu semi-transparent)
    cx0 = x0 + recul_g_px
    cx1 = x1 - recul_d_px
    larg_const = cx1 - cx0
    if larg_const > 10:
        drawing.add(Rect(
            cx0, sol_y + 8, larg_const, hauteur_px,
            fillColor=colors.HexColor("#DBEAFE"), strokeColor=_BLEU_CLAIR, strokeWidth=1
        ))

    # Cote hauteur (trait vertical + texte)
    cote_x = x1 + 8
    drawing.add(Line(cote_x, sol_y + 8, cote_x, sol_y + 8 + hauteur_px,
                     strokeColor=_BLEU, strokeWidth=0.7, strokeDashArray=[2, 2]))
    drawing.add(Line(cote_x - 3, sol_y + 8, cote_x + 3, sol_y + 8,
                     strokeColor=_BLEU, strokeWidth=0.7))
    drawing.add(Line(cote_x - 3, sol_y + 8 + hauteur_px, cote_x + 3, sol_y + 8 + hauteur_px,
                     strokeColor=_BLEU, strokeWidth=0.7))
    drawing.add(String(
        cote_x + 5, sol_y + 8 + hauteur_px / 2 - 4,
        f"{hauteur_max_m:.0f} m",
        fontSize=7.5, fillColor=_BLEU, fontName="Helvetica-Bold"
    ))

    # Recul voirie (gauche)
    if recul_g > 0:
        drawing.add(Line(x0, sol_y - 8, x0 + recul_g_px, sol_y - 8,
                         strokeColor=_ORANGE, strokeWidth=0.7, strokeDashArray=[3, 2]))
        drawing.add(String(
            x0 + recul_g_px / 2 - 8, sol_y - 18,
            f"recul {recul_g:.0f}m",
            fontSize=7, fillColor=_ORANGE
        ))

    # Recul limites (droite)
    if recul_d > 0:
        drawing.add(Line(x1 - recul_d_px, sol_y - 8, x1, sol_y - 8,
                         strokeColor=_TEAL, strokeWidth=0.7, strokeDashArray=[3, 2]))
        drawing.add(String(
            x1 - recul_d_px + 2, sol_y - 18,
            f"recul {recul_d:.0f}m",
            fontSize=7, fillColor=_TEAL
        ))

    # Légende bas
    drawing.add(String(
        x0, 4,
        f"Schéma indicatif · Zone {regles.zone} · Gabarit PLU (non à l'échelle)",
        fontSize=6.5, fillColor=_GRIS
    ))

    return drawing


# ---------------------------------------------------------------------------
# Tableau de synthèse coloré
# ---------------------------------------------------------------------------

def _tableau_synthese(etude: EtudeCapacitaire, parcelle: Parcelle, regles: ReglesUrbanisme) -> Table:
    """
    4 grandes cases colorées : emprise / surface plancher / hauteur / logements.
    """
    vert_bg  = colors.HexColor("#DCFCE7")
    bleu_bg  = colors.HexColor("#DBEAFE")
    teal_bg  = colors.HexColor("#CCFBF1")
    orange_bg = colors.HexColor("#FFEDD5")

    if regles.emprise_non_reglementee:
        _label_emprise = f"Non réglementée · 100% appliqué"
    elif regles.emprise_sol_max_pct:
        _label_emprise = f"{regles.emprise_sol_max_pct:.0f}% de {parcelle.surface_m2:.0f} m²"
    else:
        _label_emprise = f"60% de {parcelle.surface_m2:.0f} m² (défaut)"

    data = [[
        Paragraph(
            f"<b><font size=16 color='#10B981'>{etude.emprise_sol_max_m2:.0f} m²</font></b><br/>"
            f"<font size=8 color='#64748B'>Emprise au sol max</font><br/>"
            f"<font size=8 color='#64748B'>({_label_emprise})</font>",
            _styles()["body"]
        ),
        Paragraph(
            f"<b><font size=16 color='#2563EB'>{etude.surface_plancher_max_m2:.0f} m²</font></b><br/>"
            f"<font size=8 color='#64748B'>Surface de plancher max</font>",
            _styles()["body"]
        ),
        Paragraph(
            f"<b><font size=16 color='#0D9488'>{etude.hauteur_max_m:.0f} m</font></b><br/>"
            f"<font size=8 color='#64748B'>Hauteur max</font><br/>"
            f"<font size=8 color='#64748B'>R+{etude.nb_niveaux_estimes - 1} ({etude.nb_niveaux_estimes} niveaux)</font>",
            _styles()["body"]
        ),
        Paragraph(
            f"<b><font size=16 color='#EA580C'>{etude.nb_logements_estimes_min}–{etude.nb_logements_estimes_max}</font></b><br/>"
            f"<font size=8 color='#64748B'>Logements estimés</font><br/>"
            f"<font size=8 color='#64748B'>(T2 50m² à T3 65m²)</font>",
            _styles()["body"]
        ),
    ]]

    col_w = PAGE_W / 4
    t = Table(data, colWidths=[col_w] * 4, rowHeights=[2.2 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), vert_bg),
        ("BACKGROUND", (1, 0), (1, 0), bleu_bg),
        ("BACKGROUND", (2, 0), (2, 0), teal_bg),
        ("BACKGROUND", (3, 0), (3, 0), orange_bg),
        ("BOX",        (0, 0), (0, 0), 1, _VERT),
        ("BOX",        (1, 0), (1, 0), 1, _BLEU_CLAIR),
        ("BOX",        (2, 0), (2, 0), 1, _TEAL),
        ("BOX",        (3, 0), (3, 0), 1, _ORANGE),
        ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",   (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 8),
    ]))
    return t


# ---------------------------------------------------------------------------
# Tableau données classique
# ---------------------------------------------------------------------------

def _table_style() -> TableStyle:
    return TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  _BLEU),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [_GRIS_CLAIR, colors.white]),
        ("GRID",         (0, 0), (-1, -1), 0.3, _GRIS_BD),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING",   (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
    ])


# ---------------------------------------------------------------------------
# Section déductions appliquées
# ---------------------------------------------------------------------------

def _section_deductions(etude: EtudeCapacitaire, s: dict) -> Table:
    """
    Tableau récapitulatif des déductions appliquées.
    - Emprise : reculs + espaces verts → footprint constructible
    - Surface de plancher : stationnement estimé → SP nette
    """
    rows = [["Calcul de la surface de plancher", ""]]

    # --- Emprise ---
    rows.append(["Emprise brute PLU", f"{etude.emprise_brute_m2:.0f} m²"])
    if etude.emprise_apres_reculs_m2 < etude.emprise_brute_m2:
        delta_reculs = etude.emprise_apres_reculs_m2 - etude.emprise_brute_m2
        rows.append(["− Reculs obligatoires", f"{delta_reculs:.0f} m²"])
    if etude.surface_ev_m2 > 0:
        rows.append(["− Espaces verts réglementaires", f"−{etude.surface_ev_m2:.0f} m²"])
    rows.append(["Emprise nette estimée", f"{etude.emprise_sol_max_m2:.0f} m²"])

    # --- Surface de plancher ---
    rows.append([
        f"× {etude.nb_niveaux_estimes} niveaux → SP brute",
        f"{etude.sp_brute_m2:.0f} m²",
    ])
    if etude.surface_parking_m2 > 0:
        rows.append(["− Stationnement (surface de plancher)", f"−{etude.surface_parking_m2:.0f} m²"])
    rows.append(["Surface de plancher max", f"{etude.surface_plancher_max_m2:.0f} m²"])

    t = Table(rows, colWidths=[PAGE_W * 0.65, PAGE_W * 0.35])
    style = [
        # En-tête
        ("BACKGROUND",   (0, 0), (-1, 0), _BLEU),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("SPAN",         (0, 0), (-1, 0)),
        # Ligne total (dernière)
        ("FONTNAME",     (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND",   (0, -1), (-1, -1), colors.HexColor("#DBEAFE")),
        ("LINEABOVE",    (0, -1), (-1, -1), 0.8, _BLEU_CLAIR),
        # Style général
        ("FONTSIZE",     (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS",(0, 1), (-1, -2), [_GRIS_CLAIR, colors.white]),
        ("GRID",         (0, 0), (-1, -1), 0.3, _GRIS_BD),
        ("ALIGN",        (1, 0), (1, -1), "RIGHT"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
    ]
    t.setStyle(TableStyle(style))
    return t


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

def generer_rapport(
    parcelle: Parcelle,
    regles: ReglesUrbanisme,
    etude: EtudeCapacitaire,
    output: str = "rapport.pdf",
    all_parcelles: "list[Parcelle] | None" = None,
) -> None:
    """
    Génère le rapport PDF complet d'étude capacitaire.
    all_parcelles : liste des parcelles individuelles (mode multi-parcelles).
    """
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    s = _styles()
    story = []

    # --- En-tête ---
    story.append(Paragraph("zonR", s["titre"]))
    story.append(Paragraph(
        f"Étude capacitaire réglementaire · {parcelle.commune} · Zone {regles.zone}",
        s["sous_titre"]
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=_BLEU_CLAIR, spaceAfter=10))

    # --- Bloc carte + infos parcelle côte à côte ---
    geometries = [p.geometrie for p in all_parcelles] if all_parcelles else parcelle.geometrie
    carte = _carte_localisation(geometries)

    if all_parcelles and len(all_parcelles) > 1:
        refs_txt = "\n".join(p.ref_cadastrale for p in all_parcelles)
        info_rows = [
            ["Références cadastrales", refs_txt],
            ["Commune",               parcelle.commune],
            ["Code INSEE",            parcelle.code_insee],
            ["Surface totale",        f"{parcelle.surface_m2:.0f} m²"],
            ["Zone PLU",              regles.zone],
            ["Date du rapport",       date.today().strftime("%d/%m/%Y")],
        ]
    else:
        info_rows = [
            ["Référence cadastrale", parcelle.ref_cadastrale],
            ["Commune",              parcelle.commune],
            ["Code INSEE",           parcelle.code_insee],
            ["Surface parcelle",     f"{parcelle.surface_m2:.0f} m²"],
            ["Zone PLU",             regles.zone],
            ["Date du rapport",      date.today().strftime("%d/%m/%Y")],
        ]
        if parcelle.adresse:
            info_rows.insert(0, ["Adresse analysée", parcelle.adresse])
    _col2_w = PAGE_W - 8.5 * cm
    t_info = Table(info_rows, colWidths=[_col2_w * 0.45, _col2_w * 0.55])
    t_info.setStyle(_table_style())

    if carte:
        bloc_haut = Table([[carte, t_info]], colWidths=[8.5 * cm, PAGE_W - 8.5 * cm])
        bloc_haut.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING",   (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
        ]))
        story.append(bloc_haut)
    else:
        story.append(Paragraph("Parcelle analysée", s["h2"]))
        story.append(t_info)

    story.append(Spacer(1, 0.5 * cm))

    # --- Synthèse 4 cases colorées ---
    story.append(Paragraph("Synthèse capacitaire", s["h2"]))
    story.append(_tableau_synthese(etude, parcelle, regles))
    story.append(Spacer(1, 0.4 * cm))

    # --- Section déductions — toujours affichée (montre emprise → SP brute → SP nette) ---
    deductions_appliquees = True
    if deductions_appliquees:
        story.append(_section_deductions(etude, s))
        story.append(Spacer(1, 0.4 * cm))

    # --- Schéma volumétrique ---
    story.append(KeepTogether([
        Paragraph("Schéma de gabarit", s["h2"]),
        _schema_volumetrique(etude, regles),
    ]))
    story.append(Spacer(1, 0.3 * cm))

    # --- Règles PLU détaillées ---
    from reportlab.platypus import PageBreak
    story.append(PageBreak())
    story.append(Paragraph("Règles PLU applicables", s["h2"]))
    regles_rows = [["Paramètre", "Valeur PLU"]]
    regles_rows.append(["Emprise au sol max",
                        _cell_avec_verbatim(
                            f"{regles.emprise_sol_max_pct} %" if regles.emprise_sol_max_pct else "Non précisée",
                            "emprise_sol_max_pct", regles,
                        )])
    regles_rows.append(["Hauteur maximale",
                        _cell_avec_verbatim(
                            f"{regles.hauteur_max_m} m" if regles.hauteur_max_m else "Non précisée",
                            "hauteur_max_m", regles,
                        )])
    regles_rows.append(["Surface plancher max",
                        f"{regles.surface_plancher_max_m2} m²" if regles.surface_plancher_max_m2 else "Non précisée"])
    if regles.recul_voirie_alignement and regles.recul_voirie_m:
        _recul_voirie_str = f"{regles.recul_voirie_m} m + alignement voisins"
        _recul_voirie_champ = "recul_voirie_alignement"
    elif regles.recul_voirie_alignement:
        _recul_voirie_str = "Alignement sur constructions voisines"
        _recul_voirie_champ = "recul_voirie_alignement"
    elif regles.recul_voirie_m:
        _recul_voirie_str = f"{regles.recul_voirie_m} m"
        _recul_voirie_champ = "recul_voirie_m"
    else:
        _recul_voirie_str = "Non précisé"
        _recul_voirie_champ = "recul_voirie_m"
    regles_rows.append(["Recul voirie",
                        _cell_avec_verbatim(_recul_voirie_str, _recul_voirie_champ, regles)])
    regles_rows.append(["Recul limites séparatives",
                        _cell_avec_verbatim(
                            f"{regles.recul_limites_m} m" if regles.recul_limites_m else "Non précisé",
                            "recul_limites_m", regles,
                        )])
    regles_rows.append(["Stationnement",
                        _cell_avec_verbatim(
                            f"{regles.stationnement_par_logt} pl./logt" if regles.stationnement_par_logt else "Non précisé",
                            "stationnement_par_logt", regles,
                        )])
    regles_rows.append(["Espaces verts min",
                        _cell_avec_verbatim(
                            f"{regles.espace_vert_min_pct:.0f} %" if regles.espace_vert_min_pct else "Non précisé",
                            "espace_vert_min_pct", regles,
                        )])

    t_regles = Table(regles_rows, colWidths=[7 * cm, 10 * cm])
    t_regles.setStyle(_table_style())
    story.append(t_regles)
    story.append(Spacer(1, 0.3 * cm))

    if regles.usages_autorises:
        story.append(Paragraph("<b>Usages autorisés :</b> " + " · ".join(regles.usages_autorises), s["body"]))
    if regles.usages_interdits:
        story.append(Paragraph("<b>Usages interdits :</b> " + " · ".join(regles.usages_interdits), s["body"]))
    story.append(Spacer(1, 0.3 * cm))

    # --- Alertes ---
    if etude.alertes:
        story.append(KeepTogether([
            Paragraph("Alertes réglementaires", s["h2"]),
            *[Paragraph(f"⚠ {a}", s["alerte"]) for a in etude.alertes],
        ]))
        story.append(Spacer(1, 0.2 * cm))

    # --- Hypothèses ---
    if etude.hypotheses:
        story.append(Paragraph("Hypothèses appliquées", s["h2"]))
        for h in etude.hypotheses:
            story.append(Paragraph(f"• {h}", s["hypothese"]))
        story.append(Spacer(1, 0.2 * cm))

    # --- Disclaimer ---
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_GRIS_BD))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(
        f"Rapport généré le {date.today().strftime('%d/%m/%Y')} par zonR — analyse indicative uniquement. "
        "Ce document ne constitue pas un acte professionnel ni une consultation juridique. "
        "Les estimations sont issues d'une lecture automatisée du règlement PLU et doivent être "
        "vérifiées par un professionnel qualifié (architecte, urbaniste) avant toute décision.",
        s["disclaimer"]
    ))

    try:
        doc.build(story)
        logger.info("Rapport PDF généré : %s", output)
    except Exception as e:
        logger.error("Erreur génération PDF : %s", e)
        raise
