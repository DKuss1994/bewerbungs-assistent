"""Liest Firma, Position, Bewerbungs-E-Mail, Telefon und Ansprechpartner
aus einer Stellenanzeige.

Erst feste Regeln (schnell, nachvollziehbar), nur für Lücken die KI – und
deren Antwort zählt nur, wenn sie wörtlich in der Anzeige steht."""

import json
import re
from dataclasses import dataclass
from typing import Optional

from src.llm_client import LLMError

EMAIL = r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)*\.[a-zA-Z]{2,}"
# Adressen, an die man sich nicht bewirbt
EMAIL_IGNORIEREN = re.compile(r"no-?reply|datenschutz|privacy|dsgvo", re.I)
EMAIL_BEVORZUGT = re.compile(r"bewerb|job|karriere|career|recruit|personal|hr\b", re.I)

RECHTSFORM = (
    r"(?:GmbH(?:\s*&\s*Co\.?\s*KG)?|gGmbH|AG(?:\s*&\s*Co\.?\s*KG)?|SE|KGaA|KG|"
    r"UG(?:\s*\(haftungsbeschränkt\))?|OHG|GbR|e\.\s?V\.|mbH|Inc\.?|Ltd\.?)"
)
# 1–4 großgeschriebene Wörter direkt vor der Rechtsform ("Dein Team der NORDLICHT GmbH")
FIRMA = re.compile(rf"((?:[A-ZÄÖÜ0-9][\w&.\-]*\s+){{1,4}}{RECHTSFORM})(?!\w)")
ARBEITGEBER = re.compile(
    r"^\s*(?:Arbeitgeber|Unternehmen|Firma)\s*:\s*(.+)$", re.I | re.M
)

# (m/w/d), (w/d/m), (m/w/x), (all genders) …
GENDER = re.compile(
    r"\(\s*[mwdfxi](?:\s*/\s*[mwdfxi]){1,3}\s*\)|\(\s*(?:all\s+genders?|gn)\s*\)",
    re.I,
)


# +49 30 1234567, 030 / 123 45-67, (0171) 1234567 … – Ziffern-Anzahl prüft
# finde_telefon(); ohne Punkt, damit Daten (01.03.2026) nicht zählen
TELEFON = re.compile(r"(?:\+\d{2}|\(?0)[\d\s/()\-]{6,22}\d")
TELEFON_ZEILE = re.compile(r"\b(?:tel|telefon|fon|rufnummer|mobil|handy)\b|☎|📞", re.I)
FAX = re.compile(r"\bfax\b", re.I)
# "Frau Dr. Anna Muster", "Herr Max Müller-Lüdenscheidt"
PERSON = re.compile(
    r"\b(?:Frau|Herr|Herrn)\s+(?:Dr\.\s+|Prof\.\s+)?"
    r"[A-ZÄÖÜ][a-zäöüß\-]+(?:\s+[A-ZÄÖÜ][a-zäöüß\-]+)?"
)
KONTAKT_ZEILE = re.compile(r"ansprech|kontakt|fragen|wende", re.I)


@dataclass
class StellenInfo:
    unternehmen: Optional[str] = None
    position: Optional[str] = None
    email: Optional[str] = None
    telefon: Optional[str] = None
    ansprechpartner: Optional[str] = None


def _zeilen(text: str):
    return [z.strip() for z in text.splitlines() if z.strip()]


def finde_email(text: str) -> Optional[str]:
    kandidaten = [
        e.rstrip(".") for e in re.findall(EMAIL, text) if not EMAIL_IGNORIEREN.search(e)
    ]
    if not kandidaten:
        return None
    bevorzugt = [e for e in kandidaten if EMAIL_BEVORZUGT.search(e.split("@")[0])]
    return (bevorzugt or kandidaten)[0]


def finde_telefon(text: str) -> Optional[str]:
    """Erste Nummer aus einer Zeile mit "Tel."/"Telefon" …, sonst die erste
    Nummer überhaupt. Faxnummern zählen nicht."""
    kandidaten = []
    for zeile in _zeilen(text):
        if FAX.search(zeile) and not TELEFON_ZEILE.search(zeile):
            continue
        for m in TELEFON.finditer(zeile):
            nummer = re.sub(r"\s+", " ", m.group()).strip(" -/")
            if 8 <= len(re.sub(r"\D", "", nummer)) <= 15:
                kandidaten.append((not TELEFON_ZEILE.search(zeile), nummer))
    return min(kandidaten, key=lambda k: k[0])[1] if kandidaten else None


def finde_ansprechpartner(text: str) -> Optional[str]:
    """ "Frau/Herr …" – bevorzugt aus einer Zeile mit "Ansprechpartner",
    "Kontakt" o.ä. oder direkt darunter."""
    zeilen = _zeilen(text)
    kandidaten = []
    for i, zeile in enumerate(zeilen):
        kontakt = any(KONTAKT_ZEILE.search(z) for z in zeilen[max(0, i - 1) : i + 1])
        for m in PERSON.finditer(zeile):
            kandidaten.append((not kontakt, m.group().replace("Herrn", "Herr")))
    return min(kandidaten, key=lambda k: k[0])[1] if kandidaten else None


def finde_unternehmen(text: str) -> Optional[str]:
    treffer = ARBEITGEBER.search(text)
    if treffer:
        return treffer.group(1).strip()
    # Bevorzugt eine Zeile, die nur aus dem Firmennamen besteht
    kandidaten = []
    for zeile in _zeilen(text):
        for m in FIRMA.finditer(zeile):
            kandidaten.append((m.group(1).strip() != zeile, m.group(1).strip()))
    return min(kandidaten, key=lambda k: k[0])[1] if kandidaten else None


def bereinige_position(zeile: str) -> str:
    """ "Software Developer (w/d/m) Technical Support [befristet]" →
    "Software Developer Technical Support"."""
    zeile = GENDER.sub(" ", zeile)
    zeile = re.sub(r"\[.*?\]", " ", zeile)
    return re.sub(r"\s+", " ", zeile).strip(" -–|,")


def finde_position(text: str) -> Optional[str]:
    for zeile in _zeilen(text)[:40]:
        if GENDER.search(zeile) and len(zeile) <= 120:
            return bereinige_position(zeile) or None
    return None


def _normalisiere(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _steht_im_text(wert: str, text: str) -> bool:
    return bool(wert) and _normalisiere(wert) in _normalisiere(text)


def _finde_wert(daten, feld: str) -> Optional[str]:
    """Wert zu einem Schlüssel, auch wenn das kleine Modell verschachtelt."""
    if isinstance(daten, dict):
        if isinstance(daten.get(feld), str):
            return daten[feld].strip()
        for wert in daten.values():
            gefunden = _finde_wert(wert, feld)
            if gefunden:
                return gefunden
    return None


def _frage_ki(text: str, client, felder) -> dict:
    # Immer dasselbe feste Schema – fragt man nur ein Feld, verschachtelt das
    # 3B-Modell die Antwort
    prompt = (
        "Lies die Stellenanzeige. Gib NUR dieses JSON zurück:\n"
        '{"unternehmen": "<Name der Firma>", "position": "<Stellentitel>"}\n'
        "Übernimm die Werte wörtlich aus der Anzeige. Unbekannt → null.\n\n"
        f"ANZEIGE:\n{text[:3000]}"
    )
    try:
        antwort = client.generate(prompt, temperature=0.0, timeout=120)
        daten = json.loads(re.search(r"\{.*\}", antwort, re.DOTALL).group())
    except (LLMError, AttributeError, ValueError):
        return {}
    ergebnis = {}
    for feld in felder:
        wert = _finde_wert(daten, feld)
        if wert and _steht_im_text(wert, text):
            ergebnis[feld] = wert
    return ergebnis


def erkenne_stelle(text: str, client=None) -> StellenInfo:
    """Regeln zuerst; fehlt etwas und ist eine KI da, fragt sie nur die Lücken."""
    info = StellenInfo(
        unternehmen=finde_unternehmen(text),
        position=finde_position(text),
        email=finde_email(text),
        telefon=finde_telefon(text),
        ansprechpartner=finde_ansprechpartner(text),
    )
    luecken = [f for f in ("unternehmen", "position") if not getattr(info, f)]
    if luecken and client:
        for feld, wert in _frage_ki(text, client, luecken).items():
            if feld == "position":
                wert = bereinige_position(wert)
            setattr(info, feld, wert)
    return info
