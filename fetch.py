"""Récupération du contenu d'une page sous forme de texte/markdown exploitable
par le LLM, sans dépendre de sélecteurs CSS.

Deux stratégies :
- fetch direct (httpx) : suffisant pour les sites sans protection anti-bot.
- Jina Reader (r.jina.ai) : proxy gratuit qui rend la page (JS compris) et
  retourne du markdown propre. Nécessaire pour les sites protégés par un WAF
  (ex: auvergnerhonealpes.fr renvoie un statut 200 trompeur sur /shield?u=...
  en fetch direct).

fetch_pdf_text() est un fallback pour le cas où aucune date n'est trouvée dans
le HTML mais qu'un PDF de calendrier est référencé sur la page (cas Normandie
page principale).
"""

from __future__ import annotations

import httpx

JINA_READER_BASE = "https://r.jina.ai/"
TIMEOUT = 30


class PageInutilisable(Exception):
    """La page reçue n'est pas la page attendue : vérification anti-robot,
    accès refusé, contenu quasi vide ou mauvaise page (ex: page d'accueil)."""


# Signatures des pages de blocage, cherchées en tête de contenu (cas réels :
# Bretagne et Grand Est renvoient « Just a moment... Performing security
# verification » / « 403 Forbidden » via Jina Reader).
_SIGNATURES_BLOCAGE = (
    "just a moment", "performing security verification", "enable javascript and cookies",
    "requiring captcha", "returned error 403", "returned error 401", "access denied",
    "attention required", "verifying you are human", "checking your browser",
)
_TAILLE_MINIMALE = 400  # une vraie page d'aide fait bien plus


def verifier_contenu(texte: str, attendu: tuple[str, ...] = ()) -> None:
    """Lève PageInutilisable si `texte` n'est manifestement pas la page
    attendue. `attendu` : mots-clés dont AU MOINS UN doit figurer (ex:
    « dépôt », « session ») — utile quand le site renvoie une autre page en
    silence (AURA : page d'accueil à la place de la fiche de l'aide)."""
    debut = texte[:1200].lower()
    for signature in _SIGNATURES_BLOCAGE:
        if signature in debut:
            raise PageInutilisable(f"page de blocage anti-robot détectée (« {signature} »)")
    if len(texte.strip()) < _TAILLE_MINIMALE:
        raise PageInutilisable(f"contenu quasi vide ({len(texte.strip())} caractères)")
    if attendu and not any(mot.lower() in texte.lower() for mot in attendu):
        raise PageInutilisable(
            f"ce n'est pas la page attendue (aucun des mots {list(attendu)} n'y figure : "
            "probablement une autre page renvoyée à la place)"
        )


def fetch_page(url: str, use_jina: bool = False) -> str:
    """Retourne le contenu texte/markdown de la page à l'URL donnée."""
    target = f"{JINA_READER_BASE}{url}" if use_jina else url
    with httpx.Client(follow_redirects=True, timeout=TIMEOUT) as client:
        response = client.get(target, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        return response.text


def fetch_pdf_text(pdf_url: str) -> str:
    """Télécharge un PDF et en extrait le texte natif (pas d'OCR).

    À utiliser uniquement en 2e passe, quand extract() a renvoyé
    aucune_date_trouvee=true avec un lien_pdf_calendrier détecté.
    Nécessite `pypdf` (voir requirements.txt).
    """
    import io

    from pypdf import PdfReader

    with httpx.Client(follow_redirects=True, timeout=TIMEOUT) as client:
        response = client.get(pdf_url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()

    reader = PdfReader(io.BytesIO(response.content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    if not text.strip():
        raise ValueError(
            f"Aucun texte natif extrait de {pdf_url} — probablement un PDF "
            "scanné (image). De l'OCR serait nécessaire, hors scope de ce POC."
        )
    return text
