"""
Génération du rapport PDF d'étude capacitaire via ReportLab.

Responsabilités :
- Mettre en page les données de la Parcelle, des ReglesUrbanisme et de l'EtudeCapacitaire
- Inclure : zonage, surfaces, hauteur, usages, alertes réglementaires, disclaimer juridique
- Sauvegarder le PDF au chemin spécifié
"""

import logging
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)

from src.api.models import Parcelle
from src.parser.rules_model import ReglesUrbanisme
from src.engine.capacity import EtudeCapacitaire

logger = logging.getLogger(__name__)

# Palette
_BLEU = colors.HexColor("#1A3A5C")
_BLEU_CLAIR = colors.HexColor("#2563EB")
_TEAL = colors.HexColor("#0D9488")
_ORANGE = colors.HexColor("#EA580C")
_GRIS_CLAIR = colors.HexColor("#F1F5F9")
_GRIS = colors.HexColor("#64748B")


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "titre": ParagraphStyle(
            "titre", parent=base["Title"],
            textColor=_BLEU, fontSize=20, spaceAfter=4
        ),
        "sous_titre": ParagraphStyle(
            "sous_titre", parent=base["Normal"],
            textColor=_GRIS, fontSize=10, spaceAfter=12
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"],
            textColor=_BLEU, fontSize=13, spaceBefore=14, spaceAfter=6
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"],
            fontSize=9.5, leading=14
        ),
        "alerte": ParagraphStyle(
            "alerte", parent=base["Normal"],
            textColor=_ORANGE, fontSize=9, leading=13
        ),
        "disclaimer": ParagraphStyle(
            "disclaimer", parent=base["Normal"],
            textColor=_GRIS, fontSize=8, leading=12, fontName="Helvetica-Oblique"
        ),
    }


def _table_style_principale() -> TableStyle:
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _BLEU),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_GRIS_CLAIR, colors.white]),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ])


def generer_rapport(
    parcelle: Parcelle,
    regles: ReglesUrbanisme,
    etude: EtudeCapacitaire,
    output: str = "rapport.pdf",
) -> None:
    """
    Génère le rapport PDF d'étude capacitaire.

    Args:
        parcelle : données cadastrales
        regles   : règles PLU extraites
        etude    : résultats du calcul capacitaire
        output   : chemin du fichier PDF de sortie
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
    story.append(Paragraph("Étude capacitaire réglementaire", s["sous_titre"]))
    story.append(HRFlowable(width="100%", thickness=1, color=_BLEU_CLAIR))
    story.append(Spacer(1, 0.4 * cm))

    # --- Informations parcelle ---
    story.append(Paragraph("Parcelle analysée", s["h2"]))
    data_parcelle = [
        ["Référence cadastrale", parcelle.ref_cadastrale],
        ["Commune", parcelle.commune],
        ["Code INSEE", parcelle.code_insee],
        ["Surface de la parcelle", f"{parcelle.surface_m2:,.0f} m²"],
        ["Zone PLU", f"{regles.zone} — {regles.zone}"],
    ]
    t = Table(data_parcelle, colWidths=[5 * cm, 11 * cm])
    t.setStyle(_table_style_principale())
    story.append(t)
    story.append(Spacer(1, 0.4 * cm))

    # --- Résultats capacitaires ---
    story.append(Paragraph("Résultats capacitaires", s["h2"]))
    data_cap = [
        ["Paramètre", "Valeur", "Base réglementaire"],
        [
            "Emprise au sol maximale",
            f"{etude.emprise_sol_max_m2:,.0f} m²",
            f"{regles.emprise_sol_max_pct or '—'} % de la parcelle",
        ],
        [
            "Surface de plancher maximale",
            f"{etude.surface_plancher_max_m2:,.0f} m²",
            "COS / gabarit PLU",
        ],
        [
            "Hauteur maximale",
            f"{etude.hauteur_max_m:.0f} m",
            f"≈ {etude.nb_niveaux_estimes} niveaux (R+{etude.nb_niveaux_estimes - 1})",
        ],
        [
            "Logements estimés",
            f"{etude.nb_logements_estimes_min} – {etude.nb_logements_estimes_max} logements",
            "T3 (65 m²) à T2 (50 m²)",
        ],
    ]
    t2 = Table(data_cap, colWidths=[5.5 * cm, 4 * cm, 6.5 * cm])
    t2.setStyle(_table_style_principale())
    story.append(t2)
    story.append(Spacer(1, 0.4 * cm))

    # --- Règles PLU ---
    story.append(Paragraph("Règles PLU applicables", s["h2"]))

    if regles.usages_autorises:
        story.append(Paragraph("<b>Usages autorisés :</b>", s["body"]))
        for usage in regles.usages_autorises:
            story.append(Paragraph(f"• {usage}", s["body"]))
        story.append(Spacer(1, 0.2 * cm))

    if regles.usages_interdits:
        story.append(Paragraph("<b>Usages interdits :</b>", s["body"]))
        for usage in regles.usages_interdits:
            story.append(Paragraph(f"• {usage}", s["body"]))
        story.append(Spacer(1, 0.2 * cm))

    reculs = []
    if regles.recul_voirie_m:
        reculs.append(f"Recul voirie : {regles.recul_voirie_m} m")
    if regles.recul_limites_m:
        reculs.append(f"Recul limites séparatives : {regles.recul_limites_m} m")
    if reculs:
        story.append(Paragraph("<b>Reculs :</b> " + " | ".join(reculs), s["body"]))
        story.append(Spacer(1, 0.2 * cm))

    # --- Alertes ---
    if etude.alertes:
        story.append(Paragraph("⚠ Alertes réglementaires", s["h2"]))
        for alerte in etude.alertes:
            story.append(Paragraph(f"⚠ {alerte}", s["alerte"]))
        story.append(Spacer(1, 0.2 * cm))

    # --- Hypothèses ---
    if etude.hypotheses:
        story.append(Paragraph("Hypothèses appliquées", s["h2"]))
        for h in etude.hypotheses:
            story.append(Paragraph(f"• {h}", s["body"]))
        story.append(Spacer(1, 0.2 * cm))

    # --- Disclaimer ---
    story.append(Spacer(1, 0.6 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_GRIS))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(
        f"Rapport généré le {date.today().strftime('%d/%m/%Y')} par PLU·IA. "
        "Ce document constitue une analyse indicative à titre informatif et ne saurait "
        "être assimilé à un acte professionnel ou à une consultation juridique. "
        "Les valeurs présentées sont des estimations basées sur une lecture automatisée "
        "du règlement PLU et doivent être vérifiées par un professionnel qualifié "
        "(architecte, urbaniste) avant toute décision d'investissement ou de construction.",
        s["disclaimer"]
    ))

    try:
        doc.build(story)
        logger.info("Rapport PDF généré : %s", output)
    except Exception as e:
        logger.error("Erreur génération PDF : %s", e)
        raise
