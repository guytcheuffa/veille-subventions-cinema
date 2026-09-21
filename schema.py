"""Schéma de sortie structurée pour l'extraction des calendriers d'aides
régionales au cinéma.

Conçu pour couvrir les 4 patterns structurels observés sur les sites testés :
1. Date simple en texte libre (Région Sud)
2. Sessions multi-étapes en texte (Auvergne-Rhône-Alpes)
3. Fenêtres de dates sur sous-domaine dédié (Normandie - bon lien)
4. Aucune date en HTML, calendrier uniquement dans un PDF lié (Normandie - page principale)
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PeriodeCle(BaseModel):
    """Une échéance datée : date ponctuelle ou fenêtre (début + fin)."""

    label: str = Field(
        description="Nom de l'étape tel que formulé sur le site, ex: "
        "'dépôt des dossiers', 'comité de sélection', 'commission permanente', "
        "'rendez-vous préalable'"
    )
    date_debut: str | None = Field(
        default=None,
        description="Format YYYY-MM-DD. Renseigné uniquement si l'étape est une "
        "fenêtre ('du X au Y'). Laisser null pour une date ponctuelle.",
    )
    date_fin: str = Field(
        description="Format YYYY-MM-DD. Pour une date ponctuelle, c'est LA date. "
        "Pour une fenêtre, c'est la date de clôture (celle qui compte pour l'alerte)."
    )
    heure_limite: str | None = Field(
        default=None, description="Format HH:MM si une heure précise est mentionnée."
    )


class ActionPrealable(BaseModel):
    """Action obligatoire avant dépôt, sous peine d'inéligibilité (ex: RDV préalable)."""

    type_action: str = Field(
        description="Ex: 'prise de rendez-vous téléphonique préalable'"
    )
    condition_declenchement: str = Field(
        description="Contexte qui rend cette action obligatoire. Mettre "
        "'systématique' si elle s'applique à tous les dossiers, ou décrire la "
        "condition si elle est seulement conditionnelle (ex: 'si cumul d'aides "
        "avec une autre collectivité')."
    )
    fenetre: PeriodeCle | None = Field(
        default=None,
        description="Fenêtre de dates pour réaliser cette action, si précisée.",
    )
    consequence_si_non_respect: str = Field(
        description="Ex: 'la demande de soutien ne sera pas possible'"
    )
    lien_reservation: str | None = None


class Commission(BaseModel):
    """Une session/commission d'un fonds d'aide régional au cinéma."""

    region: str
    fonds: str = Field(description="Nom du fonds tel qu'affiché, ex: 'FACCAM'")
    organisme: str = Field(
        description="Structure gestionnaire, ex: 'Normandie Images', 'Région Sud'"
    )
    typologie_oeuvre: str = Field(
        description="Ex: 'court métrage de fiction, animation et documentaire'"
    )
    etape_filiere: str = Field(
        description="'ecriture' | 'developpement' | 'production' | "
        "'post-production' | 'autre'"
    )
    session_label: str | None = Field(
        default=None,
        description="Ex: 'Session 1 - 2027', '3ème session'. Null si le fonds "
        "n'a qu'une seule échéance annuelle.",
    )
    etapes: list[PeriodeCle] = Field(
        default_factory=list,
        description="Toutes les étapes datées de cette session, dans l'ordre "
        "chronologique. Au minimum une étape (le dépôt) doit être présente.",
    )
    actions_prealables: list[ActionPrealable] = Field(default_factory=list)
    conditions_eliminatoires: list[str] = Field(
        default_factory=list,
        description="Conditions d'éligibilité formulées comme critères bloquants, "
        "en texte libre, une par élément de liste.",
    )
    montant_min_eur: int | None = None
    montant_max_eur: int | None = None
    lien_formulaire: str | None = None
    url_source: str
    date_extraction: str = Field(
        description="Date du jour de l'extraction, format YYYY-MM-DD — sert au "
        "diff/dédup et à la détection de fraîcheur."
    )


class ExtractionResult(BaseModel):
    """Résultat complet pour une page/région donnée — une page peut contenir 0,
    1 ou plusieurs Commission (ex: plusieurs sessions listées sur une même page)."""

    commissions: list[Commission]
    aucune_date_trouvee: bool = Field(
        description="True si le texte ne contient aucune date exploitable — "
        "signal pour déclencher le fallback PDF."
    )
    lien_pdf_calendrier: str | None = Field(
        default=None,
        description="Si aucune_date_trouvee=true, lien vers un éventuel PDF "
        "'calendrier'/'dates de dépôt' détecté sur la page, à traiter en 2e passe.",
    )
