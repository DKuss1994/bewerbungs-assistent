"""Anrufen: Erinnerung mit Spickzettel, wenn eine Firma auch nach der
Nachfass-Mail nicht antwortet – bzw. bei Portal-Bewerbungen (keine Adresse
zum Nachfassen) nach 14 Tagen. Nach dem Anruf kurz das Ergebnis eintragen,
es fließt wie eine Rückmeldung per Mail in die Auswertung."""

import json
from datetime import date, datetime
from typing import Dict, Optional

from src.config import AppConfig
from src.database import BewerbungsDB
from src.dialog import Dialog
from src.nachfassen import NACHFASS_TAGE

NACH_MAIL_TAGE = 7  # so lange nach der Nachfass-Mail warten
ERNEUT_TAGE = 3  # nach „später“ erst dann wieder erinnern
MAX_SKILLS = 6  # Schwerpunkte auf dem Spickzettel


def _datum(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d.%m.%Y")


class Anrufen:
    def __init__(self, config: AppConfig, dialog: Dialog):
        self.config = config
        self.dialog = dialog
        self.db = BewerbungsDB(config.db_path)

    def kandidaten(self):
        return self.db.anruf_kandidaten(NACH_MAIL_TAGE, NACHFASS_TAGE, ERNEUT_TAGE)

    def _projekt_namen(self, ids: Optional[str]) -> str:
        try:
            projekte = json.loads(
                (self.config.data_dir / "projekte.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            projekte = []
        namen = {p["id"]: p["name"] for p in projekte}
        return ", ".join(namen.get(i, i) for i in (ids or "").split(",") if i)

    def spickzettel(self, b: Dict) -> str:
        """Alles, was man fürs Telefonat braucht, auf einen Blick."""
        tage = (datetime.now() - datetime.fromisoformat(b["datum_gesendet"])).days
        weg = f"per Mail an {b['empfaenger']}" if b["empfaenger"] else "über Portal"
        zeilen = [f"📞 Anruf fällig: {b['unternehmen']} – {b['position']}"]
        if b["ansprechpartner"]:
            zeilen.append(f"👤 {b['ansprechpartner']}")
        zeilen.append(
            f"☎️ {b['telefon']}"
            if b["telefon"]
            else "☎️ Nummer stand nicht in der Anzeige – Webseite/Impressum der Firma"
        )
        verlauf = f"🗓️ Beworben am {_datum(b['datum_gesendet'])} {weg}"
        if b["nachgefasst"] and b["nachgefasst"] != "nein":
            verlauf += f", nachgefragt am {_datum(b['nachgefasst'])}"
        zeilen.append(f"{verlauf} – seit {tage} Tagen keine Antwort")
        skills = [s for s in (b["skills_gefordert"] or "").split(", ") if s]
        if skills:
            zeilen.append(f"🎯 Gefordert: {', '.join(skills[:MAX_SKILLS])}")
        if b["agent_self_score"] is not None:
            zeilen.append(f"📊 Passung: {b['agent_self_score']} %")
        projekte = self._projekt_namen(b["projekte"])
        if projekte:
            zeilen.append(f"🧩 Deine Projekte im Anschreiben: {projekte}")
        if b["link"]:
            zeilen.append(f"🔗 {b['link']}")
        zeilen += [
            "",
            "💬 Vorschlag: „Guten Tag, mein Name ist "
            f"{self.config.profil['name']}. Ich habe mich am "
            f"{_datum(b['datum_gesendet'])} als {b['position']} beworben und "
            "wollte fragen, wie der Stand ist.“",
        ]
        return "\n".join(zeilen)

    def pruefe(self) -> int:
        """Erinnert an jeden fälligen Anruf. Rückgabe: Anzahl angerufen."""
        angerufen = 0
        for b in self.kandidaten():
            self.dialog.zeige(self.spickzettel(b))
            antwort = self.dialog.frage(
                "\nAngerufen? [j]a / [s]päter / [n]ein, nicht anrufen: ",
                optionen=["j", "s", "n"],
            )
            if antwort is None:
                break
            antwort = antwort.lower()[:1]
            if antwort == "j":
                self.db.setze_feld(b["id"], "anruf", date.today().isoformat())
                self._ergebnis(b)
                angerufen += 1
            elif antwort == "s":
                self.db.setze_feld(b["id"], "anruf_erinnert", date.today().isoformat())
            elif antwort == "n":
                self.db.setze_feld(b["id"], "anruf", "nein")
        return angerufen

    def _ergebnis(self, b: Dict) -> None:
        antwort = self.dialog.frage(
            "Was kam raus? [e]inladung / [a]bsage / [w]artet noch – "
            "oder kurz eintippen: ",
            optionen=["e", "a", "w"],
        )
        if not antwort:
            return
        kurz = antwort.lower()
        if kurz in ("e", "einladung"):
            self.db.add_rueckmeldung(b["id"], "einladung")
            self.dialog.zeige("🎉 Einladung eingetragen – viel Erfolg!")
        elif kurz in ("a", "absage"):
            self.db.add_rueckmeldung(b["id"], "absage")
            self.dialog.zeige("📭 Absage eingetragen.")
        elif kurz not in ("w", "wartet"):
            self.db.setze_feld(b["id"], "notiz", antwort)
            self.dialog.zeige("📝 Notiz gespeichert.")
