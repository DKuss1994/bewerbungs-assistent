"""Eingang der Pipeline: gefundene Stellen sammeln, vorsortieren, bewerten.

Jede Stelle landet einmal in der Tabelle `stellen` – auch aussortierte, damit
sie beim nächsten Lauf nicht wieder geprüft wird. Status:
verworfen → passt nicht (Grund in `gruende`), vorgeschlagen → wartet auf
deine Entscheidung, ignoriert / abgebrochen / beworben → erledigt."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

from src.config import AppConfig
from src.database import BewerbungsDB, normalisiere_firma, verbinde
from src.gap_check import GapCheck
from src.kompetenzen import KompetenzenDB
from src.kriterien import (
    Kriterien,
    huerden,
    punkte,
    quereinstieg_hinweise,
    remote_anteil,
)
from src.stellen_ba import BAJobsuche, Fund
from src.stellen_info import bereinige_position

ERSTE_SUCHE_TAGE = 28  # beim allerersten Lauf etwas weiter zurück
SUCHE_TAGE = 7
MAX_DETAILS = 40  # Anzeigentexte je Lauf – der Rest kommt beim nächsten Mal
MIN_TEXT = 200


def stellen_schluessel(firma: str, titel: str) -> tuple:
    """Gleiche Firma (Rechtsform egal) + gleicher Titel = dieselbe Stelle."""
    return normalisiere_firma(firma), bereinige_position(titel).lower()


class StellenDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with verbinde(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stellen (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    quelle TEXT NOT NULL,
                    ref TEXT NOT NULL,
                    titel TEXT,
                    firma TEXT,
                    ort TEXT,
                    km INTEGER,
                    remote INTEGER,
                    link TEXT,
                    text TEXT,
                    passung INTEGER,
                    punkte INTEGER,
                    gruende TEXT,
                    status TEXT NOT NULL,
                    gefunden_am TEXT NOT NULL,
                    entschieden_am TEXT,
                    bewerbung_id INTEGER,
                    UNIQUE(quelle, ref)
                )
            """)
            conn.commit()

    def _alle(self, sql: str = "", parameter: tuple = ()) -> List[Dict]:
        with verbinde(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"SELECT * FROM stellen {sql}", parameter).fetchall()
            return [dict(r) for r in rows]

    def ist_leer(self) -> bool:
        return not self._alle("LIMIT 1")

    def bekannt(self, quelle: str, ref: str) -> bool:
        return bool(self._alle("WHERE quelle = ? AND ref = ?", (quelle, ref)))

    def gleiche_stelle(self, firma: str, titel: str) -> bool:
        """Dieselbe Stelle unter anderer Nummer (neu eingestellt, andere Quelle)?"""
        schluessel = stellen_schluessel(firma, titel)
        return any(
            stellen_schluessel(s["firma"], s["titel"]) == schluessel
            for s in self._alle()
        )

    def speichere(
        self,
        fund: Fund,
        status: str,
        gruende: str,
        text: str = "",
        remote: Optional[int] = None,
        passung: Optional[int] = None,
        wert: Optional[int] = None,
    ) -> int:
        with verbinde(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO stellen (
                    quelle, ref, titel, firma, ort, km, remote, link, text,
                    passung, punkte, gruende, status, gefunden_am
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    fund.quelle,
                    fund.ref,
                    fund.titel,
                    fund.firma,
                    fund.ort,
                    fund.km,
                    remote,
                    fund.link,
                    text,
                    passung,
                    wert,
                    gruende,
                    status,
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def vorschlaege(self, anzahl: int) -> List[Dict]:
        return self._alle(
            "WHERE status = 'vorgeschlagen' ORDER BY punkte DESC, id LIMIT ?",
            (anzahl,),
        )

    def anzahl_vorschlaege(self) -> int:
        return len(self._alle("WHERE status = 'vorgeschlagen'"))

    def setze_status(
        self, stelle_id: int, status: str, bewerbung_id: Optional[int] = None
    ) -> None:
        with verbinde(self.db_path) as conn:
            conn.execute(
                "UPDATE stellen SET status = ?, entschieden_am = ?, bewerbung_id = ? "
                "WHERE id = ?",
                (status, datetime.now().isoformat(), bewerbung_id, stelle_id),
            )
            conn.commit()


class StellenSuche:
    def __init__(
        self,
        config: AppConfig,
        kriterien: Kriterien,
        ba: Optional[BAJobsuche] = None,
        melde: Callable[[str], None] = print,
    ):
        self.kriterien = kriterien
        self.ba = ba or BAJobsuche()
        self.melde = melde
        self.db = StellenDB(config.db_path)
        self.bewerbungen = BewerbungsDB(config.db_path)
        self.gap_check = GapCheck(KompetenzenDB(config.db_path))

    def lauf(self) -> Dict[str, int]:
        """Sucht, sortiert vor, bewertet. Rückgabe: Zahlen für die Meldung."""
        k = self.kriterien
        tage = ERSTE_SUCHE_TAGE if self.db.ist_leer() else SUCHE_TAGE
        funde: Dict[str, Fund] = {}
        for begriff in k.suchbegriffe:
            try:
                treffer = self.ba.suche(begriff, k.plz, k.km_max_mit_remote, tage)
            except (requests.RequestException, ValueError) as e:
                self.melde(f"⚠️ Suche nach „{begriff}“ fehlgeschlagen: {e}")
                continue
            for fund in treffer:
                if not self.db.bekannt(fund.quelle, fund.ref):
                    funde.setdefault(fund.ref, fund)

        zahlen = {"neu": len(funde), "vorgeschlagen": 0, "verworfen": 0}
        zu_lesen = []
        gesehen = set()  # dieselbe Stelle mehrfach im selben Lauf
        for fund in funde.values():
            schluessel = stellen_schluessel(fund.firma, fund.titel)
            grund = self._vorfilter(fund) or (
                "doppelt (andere Nummer)" if schluessel in gesehen else None
            )
            gesehen.add(schluessel)
            if grund:
                self.db.speichere(fund, "verworfen", grund)
                zahlen["verworfen"] += 1
            else:
                zu_lesen.append(fund)

        # Erst die vielversprechendsten lesen: Quereinstieg, dann nah
        zu_lesen.sort(key=lambda f: (not f.quereinstieg, f.km or 999))
        for fund in zu_lesen[:MAX_DETAILS]:
            status = self._bewerte(fund)
            if status:
                zahlen[status] += 1
        return zahlen

    def _vorfilter(self, fund: Fund) -> Optional[str]:
        """Grund zum Aussortieren anhand der Eckdaten, sonst None."""
        k = self.kriterien
        if fund.minijob:
            return "Minijob"
        wort = k.titel_ausgeschlossen(fund.titel)
        if wort:
            return f"Titel enthält „{wort}“"
        if k.firma_ausgeschlossen(fund.firma):
            return "Firma ausgeschlossen"
        if fund.km is not None and fund.km > k.km_max:
            # Weiter weg geht nur mit Homeoffice – ohne jede Angabe gar nicht
            if not fund.homeoffice or (fund.remote or 100) < k.remote_ab_prozent:
                return k.ort_passt(fund.km, fund.remote)[1]
        position = bereinige_position(fund.titel)
        if self.bewerbungen.hat_doppelte_bewerbung(fund.firma, position, tage=180):
            return "schon beworben"
        frueher = self.bewerbungen.letzte_bewerbung_bei(fund.firma)
        if (
            frueher
            and frueher["status"] == "absage"
            and (frueher["cooldown_until"] or "") > datetime.now().isoformat()
        ):
            return "Absage dieser Firma, Cooldown läuft"
        if self.db.gleiche_stelle(fund.firma, fund.titel):
            return "doppelt (andere Nummer)"
        return None

    def _bewerte(self, fund: Fund) -> Optional[str]:
        """Liest die Anzeige und speichert sie bewertet. None = nicht lesbar
        (wird nicht gespeichert, nächster Lauf versucht es erneut)."""
        try:
            text = self.ba.anzeigentext(fund)
        except requests.RequestException:
            return None
        if len(text) < MIN_TEXT:
            return None
        k = self.kriterien
        remote = fund.remote or remote_anteil(text)
        ort_ok, ort = k.ort_passt(fund.km, remote)
        if not ort_ok:
            self.db.speichere(fund, "verworfen", ort, text, remote)
            return "verworfen"

        passung = GapCheck.passung(self.gap_check.finde_luecken(text))
        hinweise = quereinstieg_hinweise(text)
        if fund.quereinstieg:
            hinweise.insert(0, "laut Firma für Quereinsteiger geeignet")
        probleme = huerden(text)
        nah = fund.km is not None and fund.km <= k.km_optimal
        wert = punkte(passung, bool(hinweise), len(probleme), nah)
        gruende = "; ".join(
            [ort, f"Passung {passung} %", *hinweise, *(f"⚠️ {p}" for p in probleme)]
        )
        status = "vorgeschlagen" if wert >= k.min_punkte else "verworfen"
        self.db.speichere(fund, status, gruende, text, remote, passung, wert)
        return status
