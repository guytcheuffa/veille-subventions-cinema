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
