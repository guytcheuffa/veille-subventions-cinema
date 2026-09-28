"""Garde-fou budgétaire indépendant de la plateforme du provider.

Contrairement à Anthropic (prépayé — le solde chargé est un plafond dur au
niveau du compte, impossible à dépasser), QwenCloud/DashScope fonctionne en
POST-payé : rien n'empêche nativement de dépasser un budget mensuel visé, et
la seule alerte disponible (Spending Alert Limit côté Console) peut arriver
en retard ("Free quota data is subject to a delay of several minutes",
d'après leur propre doc).

Ce module impose donc un plafond CÔTÉ CODE, entièrement indépendant de la
plateforme : avant chaque appel réel, on vérifie que le coût estimé de
l'appel ne ferait pas dépasser BUDGET_LIMIT_USD pour le mois en cours. Si
c'est le cas, l'appel est bloqué AVANT d'être envoyé — aucun coût engagé.
Après un appel réussi, le coût réel (basé sur les tokens effectivement
consommés, renvoyés par l'API) est enregistré dans data/budget.json, qui
sert de mémoire persistante d'un run à l'autre (et d'un mois à l'autre,
avec remise à zéro automatique).
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

BUDGET_PATH = "data/budget.json"

# Plafond mensuel en dollars, configurable via variable d'environnement.
# 2$ par défaut — largement au-dessus du coût réel observé pour ce projet
# (~$0.35-0.40/mois en run hebdomadaire sur les 16 sources avec Anthropic ;
# encore moins cher avec Qwen), donc ce plafond sert de FILET DE SÉCURITÉ
# contre un bug (boucle infinie, cron mal configuré), pas de budget normal.
BUDGET_LIMIT_USD = float(os.environ.get("BUDGET_LIMIT_USD", "2.0"))


class BudgetExceededError(Exception):
    """Levée quand un appel LLM ferait dépasser le budget mensuel fixé.
    L'appel n'est PAS envoyé quand cette exception est levée."""


def _mois_courant() -> str:
    return datetime.date.today().strftime("%Y-%m")


def _load(path: str | Path = BUDGET_PATH) -> dict:
    path = Path(path)
    if not path.exists():
        return {"month": _mois_courant(), "spent_usd": 0.0}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("month") != _mois_courant():
        # Nouveau mois : remise à zéro automatique.
        return {"month": _mois_courant(), "spent_usd": 0.0}
    return data


def _save(data: dict, path: str | Path = BUDGET_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def verifier_budget(cout_estime_usd: float, path: str | Path = BUDGET_PATH) -> None:
    """À appeler AVANT chaque appel API réel. Lève BudgetExceededError si
    (dépense déjà enregistrée ce mois + cout_estime_usd) dépasserait
    BUDGET_LIMIT_USD — dans ce cas, l'appel ne doit PAS être envoyé."""
    data = _load(path)
    if data["spent_usd"] + cout_estime_usd > BUDGET_LIMIT_USD:
        raise BudgetExceededError(
            f"Budget mensuel dépassé : {data['spent_usd']:.4f}$ déjà "
            f"dépensés ce mois ({data['month']}), + ~{cout_estime_usd:.4f}$ "
            f"estimés pour cet appel > limite de {BUDGET_LIMIT_USD}$ "
            f"(variable BUDGET_LIMIT_USD). Appel bloqué avant envoi, aucun "
            f"coût réel engagé pour cet appel."
        )


def enregistrer_depense(cout_reel_usd: float, path: str | Path = BUDGET_PATH) -> float:
    """À appeler APRÈS un appel réussi, avec le coût réel calculé à partir
    des tokens effectivement consommés (pas l'estimation a priori). Ajoute
    ce montant à la dépense du mois courant, sauvegarde, et retourne le
    nouveau total dépensé ce mois."""
    data = _load(path)
    data["spent_usd"] += cout_reel_usd
    _save(data, path)
    return data["spent_usd"]


def etat_budget(path: str | Path = BUDGET_PATH) -> dict:
    """Retourne l'état actuel du budget (mois, dépensé, restant, limite) —
    utile pour affichage/diagnostic sans faire d'appel API."""
    data = _load(path)
    return {
        "month": data["month"],
        "spent_usd": round(data["spent_usd"], 4),
        "limit_usd": BUDGET_LIMIT_USD,
        "remaining_usd": round(BUDGET_LIMIT_USD - data["spent_usd"], 4),
    }
