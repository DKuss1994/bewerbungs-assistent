"""Stellenanzeige von einer Webseite holen.

Viele Jobbörsen (StepStone, Indeed, Firmen-Karriereseiten …) legen die Anzeige
zusätzlich maschinenlesbar als schema.org-"JobPosting" (JSON-LD) in die Seite –
das ist am saubersten. Sonst bleibt der sichtbare Text der Seite.

Seiten, die ihren Inhalt erst per JavaScript laden, liefern so kaum Text;
dann den Anzeigentext von Hand kopieren."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional

import requests

URL = re.compile(r"https?://\S+")
# Manche Jobbörsen liefern ohne Browser-Kennung nur eine Fehlerseite
KOPFZEILEN = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9",
}
UNSICHTBAR = {"script", "style", "noscript", "svg", "head", "nav", "footer"}
ABSATZ = {"p", "div", "br", "li", "ul", "ol", "tr", "section", "article"}
ABSATZ |= {"h1", "h2", "h3", "h4", "h5", "h6"}


class _SeitenLeser(HTMLParser):
    """Sammelt sichtbaren Text und JSON-LD-Blöcke einer HTML-Seite."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.teile: List[str] = []
        self.json_ld: List[str] = []
        self._versteckt = 0
        self._in_json_ld = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self._in_json_ld = True
            self.json_ld.append("")
        elif tag in UNSICHTBAR:
            self._versteckt += 1
        elif tag in ABSATZ:
            self.teile.append("\n")

    def handle_endtag(self, tag):
        if self._in_json_ld and tag == "script":
            self._in_json_ld = False
        elif tag in UNSICHTBAR:
            self._versteckt = max(0, self._versteckt - 1)
        elif tag in ABSATZ:
            self.teile.append("\n")

    def handle_data(self, data):
        if self._in_json_ld:
            self.json_ld[-1] += data
        elif not self._versteckt:
            self.teile.append(data)

    @property
    def text(self) -> str:
        zeilen = (
            re.sub(r"\s+", " ", z).strip() for z in "".join(self.teile).split("\n")
        )
        return "\n".join(z for z in zeilen if z)


def html_zu_text(seite: str) -> str:
    leser = _SeitenLeser()
    leser.feed(seite)
    return leser.text


def _finde_jobposting(daten) -> Optional[dict]:
    """Sucht ein JobPosting – auch in Listen und "@graph"."""
    if isinstance(daten, list):
        return next(filter(None, map(_finde_jobposting, daten)), None)
    if not isinstance(daten, dict):
        return None
    typ = daten.get("@type")
    if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
        return daten
    return _finde_jobposting(daten.get("@graph", []))


def _jobposting_als_text(posting: dict) -> str:
    """Baut daraus einen Anzeigentext, den erkenne_stelle() versteht."""
    zeilen = [posting.get("title", "")]
    firma = posting.get("hiringOrganization") or {}
    if isinstance(firma, dict) and firma.get("name"):
        zeilen.append(f"Arbeitgeber: {firma['name']}")
    zeilen.append(html_zu_text(posting.get("description", "")))
    return "\n".join(z for z in zeilen if z).strip()


def anzeige_aus_html(seite: str) -> str:
    leser = _SeitenLeser()
    leser.feed(seite)
    for block in leser.json_ld:
        try:
            posting = _finde_jobposting(json.loads(block))
        except json.JSONDecodeError:
            continue
        if posting and posting.get("description"):
            return _jobposting_als_text(posting)
    return leser.text


def lade_anzeige(url: str) -> str:
    """Holt die Anzeige hinter einem Link; wirft requests.RequestException."""
    antwort = requests.get(url, headers=KOPFZEILEN, timeout=20)
    antwort.raise_for_status()
    return anzeige_aus_html(antwort.text)


def lies_anzeige(quelle: str) -> str:
    """Link oder Dateipfad → Anzeigentext."""
    if URL.fullmatch(quelle):
        return lade_anzeige(quelle)
    return Path(quelle).read_text(encoding="utf-8")
