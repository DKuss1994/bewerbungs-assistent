"""Stellensuche über die Jobbörse der Bundesagentur für Arbeit.

Die Suche ist eine offene Schnittstelle (fester, öffentlicher API-Schlüssel),
liefert aber nur Eckdaten. Den Anzeigentext holt stellen_link.py von der
öffentlichen Detailseite (schema.org-JobPosting)."""

from dataclasses import dataclass
from typing import Callable, List, Optional

import requests

from src.stellen_link import KOPFZEILEN, lade_anzeige

SUCHE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
DETAIL_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}"
API_KEY = {"X-API-Key": "jobboerse-jobsuche"}
SEITE = 100  # Treffer je Anfrage (Maximum der Schnittstelle)
QUELLE = "ba"


@dataclass
class Fund:
    """Eine gefundene Stelle, bevor sie bewertet ist."""

    quelle: str
    ref: str
    titel: str
    firma: str
    ort: str
    km: Optional[int]
    link: str
    homeoffice: bool = False
    remote: Optional[int] = None  # Prozent, falls angegeben
    quereinstieg: bool = False  # von der Firma als geeignet markiert
    minijob: bool = False


def _zahl(wert) -> Optional[int]:
    """Die Schnittstelle liefert Zahlen teils als Text ("12")."""
    try:
        return int(float(wert))
    except (TypeError, ValueError):
        return None


def _fund(e: dict) -> Fund:
    adresse = (e.get("stellenlokationen") or [{}])[0].get("adresse", {})
    ref = e["referenznummer"]
    return Fund(
        quelle=QUELLE,
        ref=ref,
        titel=e.get("stellenangebotsTitel", ""),
        firma=e.get("firma", ""),
        ort=adresse.get("ort", ""),
        km=_zahl(e.get("entfernung")),
        link=DETAIL_URL.format(ref=ref),
        homeoffice=bool(e.get("homeofficemoeglich")),
        remote=_zahl(e.get("homeofficeprozent")),
        quereinstieg=bool(e.get("quereinstiegGeeignet")),
        minijob=bool(e.get("istGeringfuegigeBeschaeftigung")),
    )


class BAJobsuche:
    def __init__(self, hole: Optional[Callable[..., dict]] = None):
        """hole: ersetzt in Tests die HTTP-Anfrage (bekommt die Parameter)."""
        self._hole = hole or self._anfrage

    @staticmethod
    def _anfrage(**parameter) -> dict:
        antwort = requests.get(
            SUCHE_URL,
            params=parameter,
            headers={**KOPFZEILEN, **API_KEY},
            timeout=30,
        )
        antwort.raise_for_status()
        return antwort.json()

    def suche(self, begriff: str, plz: str, umkreis: int, tage: int) -> List[Fund]:
        """Stellen (keine Ausbildung) zu `begriff` im Umkreis um `plz`,
        veröffentlicht in den letzten `tage` Tagen."""
        daten = self._hole(
            was=begriff,
            wo=plz,
            umkreis=umkreis,
            angebotsart=1,
            veroeffentlichtseit=tage,
            size=SEITE,
            page=1,
        )
        return [_fund(e) for e in daten.get("ergebnisliste", [])]

    @staticmethod
    def anzeigentext(fund: Fund) -> str:
        return lade_anzeige(fund.link)
