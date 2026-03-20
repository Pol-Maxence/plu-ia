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
    """
    from PIL import Image as PILImage, ImageDraw

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

    # Bounding box + marge de 20%
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
    size = (cols * 256, rows * 256)
    canvas = PILImage.new("RGBA", size, (200, 200, 200, 255))

    # Couche 1 : fond topo IGN
    for xi, tx in enumerate(range(x_min, x_max + 1)):
        for yi, ty in enumerate(range(y_min, y_max + 1)):
            tile = _get_tile(zoom, tx, ty, "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2")
            if tile:
                canvas.paste(tile, (xi * 256, yi * 256))

    # Couche 2 : parcelles cadastrales (délimitations + numéros), opacité 85%
    cadastre_layer = PILImage.new("RGBA", size, (0, 0, 0, 0))
    for xi, tx in enumerate(range(x_min, x_max + 1)):
        for yi, ty in enumerate(range(y_min, y_max + 1)):
            tile = _get_tile(zoom, tx, ty, "CADASTRALPARCELS.PARCELLAIRE_EXPRESS")
            if tile:
                cadastre_layer.paste(tile, (xi * 256, yi * 256))
    # Ajuster l'opacité de la couche cadastrale
    r2, g2, b2, a2 = cadastre_layer.split()
    a2 = a2.point(lambda p: int(p * 0.85))
    cadastre_layer = PILImage.merge("RGBA", (r2, g2, b2, a2))
    canvas = PILImage.alpha_composite(canvas, cadastre_layer)

    # Couche 3 : contour de la/des parcelle(s) analysée(s) (rouge semi-transparent)
    overlay = PILImage.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for ring in all_rings:
        pixels = [_lon_lat_to_pixel(c[0], c[1], zoom, x_min, y_min) for c in ring]
        if len(pixels) >= 2:
            draw.polygon(pixels, fill=(220, 38, 38, 60))
            draw.line(pixels + [pixels[0]], fill=(220, 38, 38, 230), width=4)
    canvas = PILImage.alpha_composite(canvas, overlay)
    canvas = canvas.convert("RGB")

    # Recadrer sur la bounding box utile avec marges
    px_min, py_max = _lon_lat_to_pixel(lon_min, lat_min, zoom, x_min, y_min)
    px_max, py_min = _lon_lat_to_pixel(lon_max, lat_max, zoom, x_min, y_min)
    px_min = max(0, px_min); py_min = max(0, py_min)
    px_max = min(canvas.width, px_max); py_max = min(canvas.height, py_max)
    if px_max > px_min and py_max > py_min:
        canvas = canvas.crop((px_min, py_min, px_max, py_max))

    # Conversion en image ReportLab
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    buf.seek(0)
    return Image(buf, width=9 * cm, height=9 * cm)


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

    data = [[
        Paragraph(
            f"<b><font size=16 color='#10B981'>{etude.emprise_sol_max_m2:.0f} m²</font></b><br/>"
            f"<font size=8 color='#64748B'>Emprise au sol max</font><br/>"
            f"<font size=8 color='#64748B'>({regles.emprise_sol_max_pct or 60:.0f}% de {parcelle.surface_m2:.0f} m²)</font>",
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
    story.append(Paragraph("PLU·IA", s["titre"]))
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
    t_info = Table(info_rows, colWidths=[4 * cm, 4.5 * cm])
    t_info.setStyle(_table_style())

    if carte:
        bloc_haut = Table([[carte, t_info]], colWidths=[8.5 * cm, 8.5 * cm])
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
    story.append(Spacer(1, 0.5 * cm))

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
                        f"{regles.emprise_sol_max_pct} %" if regles.emprise_sol_max_pct else "Non précisée"])
    regles_rows.append(["Hauteur maximale",
                        f"{regles.hauteur_max_m} m" if regles.hauteur_max_m else "Non précisée"])
    regles_rows.append(["Surface plancher max",
                        f"{regles.surface_plancher_max_m2} m²" if regles.surface_plancher_max_m2 else "Non précisée"])
    regles_rows.append(["Recul voirie",
                        f"{regles.recul_voirie_m} m" if regles.recul_voirie_m else "Non précisé"])
    regles_rows.append(["Recul limites séparatives",
                        f"{regles.recul_limites_m} m" if regles.recul_limites_m else "Non précisé"])

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
        f"Rapport généré le {date.today().strftime('%d/%m/%Y')} par PLU·IA — analyse indicative uniquement. "
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
