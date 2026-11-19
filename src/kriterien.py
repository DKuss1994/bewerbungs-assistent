"""Was als Stelle für dich in Frage kommt – aus data/kriterien.json.

Entfernung und Remote-Anteil hängen zusammen: bis `km_max` passt jede Stelle,
bis `km_max_mit_remote` nur mit mindestens `remote_ab_prozent` Homeoffice,
und egal wie weit weg nur bei 100 % remote.

Bewertet wird ohne KI: Skill-Passung (wie beim Bewerben) plus Hinweise auf
Quereinstieg, minus Hürden wie „mehrjährige Berufserfahrung“."""

import json
import re
from dataclasses import dataclass, field, fields
from typing import List, Optional, Tuple

from src.config import AppConfig
from src.kompetenzen import kommt_vor

DATEI = "kriterien.json"

# Titel mit diesen Wörtern passen nicht zu einem Quereinstieg bzw. sind keine
# richtige Stelle
TITEL_NICHT = [
    "Senior",
    "Lead",
    "Leiter",
    "Leitung",
    "Head of",
    "Principal",
    "Architekt",
    "Werkstudent",
    "Praktikum",
    "Praktikant",
    "Ausbildung",
    "Azubi",
    "Duales Studium",
    "Minijob",
    "Aushilfe",
]

SUCHBEGRIFFE = [
    "IT-Support",
    "First Level Support",
    "Second Level Support",
    "Helpdesk",
    "Service Desk",
    "Technischer Support",
    "IT-Administrator",
    "Systemadministrator",
    "IT-Servicetechniker",
    "Anwendungsbetreuer",
    "Junior Softwareentwickler",
    "Quereinsteiger IT",
]

QUEREINSTIEG = {
    "Quereinstieg": r"quereinst(?:ieg|eiger)",
    "Berufseinstieg": r"berufseinst(?:ieg|eiger)|berufsanfänger",
    "Junior": r"\bjunior\b",
    "Einarbeitung": r"einarbeitung",
    "ohne Erfahrung möglich": r"(?:auch ohne|keine) (?:berufs)?erfahrung"
    r"(?: (?:notwendig|erforderlich|nötig|vorausgesetzt))?",
}
HUERDEN = {
    "mehrjährige Erfahrung": r"mehrjährige[nr]? (?:berufs|praxis)?erfahrung",
    "mehrere Jahre Erfahrung": r"(?:mindestens|min\.|über|mehr als)?\s*\b[3-9]\+?\s*"
    r"jahre?n? (?:berufs|praxis)?erfahrung",
    # „Studium oder Ausbildung / vergleichbare Qualifikation“ ist keine Hürde
    "Studium verlangt": r"abgeschlossene[sn]? (?:hochschul)?studium"
    r"(?![^.\n]{0,80}(?:oder|bzw|vergleichbar|ausbildung))",
}
PUNKTE_QUEREINSTIEG = 15
PUNKTE_HUERDE = 15
PUNKTE_NAH = 5

# „60 % remote“, „100% Homeoffice“, „3 Tage mobil“, „full remote“ …
REMOTE_WORT = r"(?:remote|home-?office|mobil(?:es arbeiten)?|von zu hause)"
REMOTE_PROZENT = re.compile(rf"(\d{{2,3}})\s*%\s*(?:\w+\s+){{0,2}}{REMOTE_WORT}", re.I)
REMOTE_TAGE = re.compile(
    rf"\b([1-5])\s*(?:tage?|x)\s*(?:pro woche|die woche|wöchentlich|/\s*woche)?\s*"
    rf"(?:im\s+|ins\s+)?{REMOTE_WORT}",
    re.I,
)
REMOTE_VOLL = re.compile(
    rf"(?:full[- ]?|fully |komplett |vollständig |ausschließlich |rein |100 ?% )"
    rf"(?:im\s+)?{REMOTE_WORT}|remote[- ]only|standortunabhängig|"
    rf"deutschlandweit (?:remote|im homeoffice)",
    re.I,
)


@dataclass
class Kriterien:
    plz: str = ""
    km_optimal: int = 15
    km_max: int = 50
    km_max_mit_remote: int = 100
    remote_ab_prozent: int = 60
    suchbegriffe: List[str] = field(default_factory=lambda: list(SUCHBEGRIFFE))
    titel_nicht: List[str] = field(default_factory=lambda: list(TITEL_NICHT))
    firmen_nicht: List[str] = field(default_factory=list)
    min_punkte: int = 40
    max_vorschlaege: int = 10

    @classmethod
    def laden(cls, config: AppConfig) -> Optional["Kriterien"]:
        """None, wenn data/kriterien.json fehlt – dann sucht der Agent nicht."""
        pfad = config.data_dir / DATEI
        if not pfad.exists():
            return None
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        bekannt = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in daten.items() if k in bekannt})

    # ---------- PRÜFUNGEN ----------
    def titel_ausgeschlossen(self, titel: str) -> Optional[str]:
        """Das Wort, an dem der Titel scheitert, sonst None."""
        t = titel.lower()
        return next((w for w in self.titel_nicht if kommt_vor(w, t)), None)

    def firma_ausgeschlossen(self, firma: str) -> bool:
        f = firma.lower()
        return any(n.lower() in f for n in self.firmen_nicht)

    def ort_passt(self, km: Optional[int], remote: Optional[int]) -> Tuple[bool, str]:
        """(passt?, Begründung) aus Entfernung in km und Remote-Anteil in %."""
        if remote == 100:
            return True, "100 % remote"
        if km is None:
            return False, "Ort unbekannt"
        mit = f", {remote} % remote" if remote else ""
        if km <= self.km_max:
            return True, f"{km} km{mit}"
        if km <= self.km_max_mit_remote and (remote or 0) >= self.remote_ab_prozent:
            return True, f"{km} km{mit}"
        return False, f"{km} km{mit or ', kein Remote-Anteil bekannt'}"


def remote_anteil(text: str) -> Optional[int]:
    """Homeoffice-Anteil in Prozent laut Anzeigentext, None = steht nicht drin."""
    werte = [min(100, int(m)) for m in REMOTE_PROZENT.findall(text)]
    werte += [int(t) * 20 for t in REMOTE_TAGE.findall(text)]
    if REMOTE_VOLL.search(text):
        werte.append(100)
    return max(werte) if werte else None


def quereinstieg_hinweise(text: str) -> List[str]:
    t = text.lower()
    return [name for name, muster in QUEREINSTIEG.items() if re.search(muster, t)]


def huerden(text: str) -> List[str]:
    t = " ".join(text.lower().split())
    return [name for name, muster in HUERDEN.items() if re.search(muster, t)]


def punkte(passung: int, quereinstieg: bool, huerden_anzahl: int, nah: bool) -> int:
    """Gesamtwert 0–100 für die Reihenfolge der Vorschläge."""
    wert = passung + PUNKTE_QUEREINSTIEG * quereinstieg + PUNKTE_NAH * nah
    wert -= PUNKTE_HUERDE * min(huerden_anzahl, 2)
    return max(0, min(100, wert))
