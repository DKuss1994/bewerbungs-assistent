"""Nachfassen: kurze, freundliche Nachfrage, wenn eine Firma nach einiger Zeit
nicht auf eine E-Mail-Bewerbung antwortet. Geht nur nach deinem „ja“ raus.

Eigener Text: in profil.json "nachfass": {"betreff": …, "text": …} mit den
Platzhaltern {position}, {unternehmen}, {datum}, {name}."""

from datetime import date, datetime
from typing import Dict, Optional

from src.config import AppConfig
from src.database import BewerbungsDB
from src.dialog import Dialog
from src.email_handler import EmailHandler

NACHFASS_TAGE = 14
STANDARD_MAIL = {
    "betreff": "Nachfrage zu meiner Bewerbung als {position}",
    "text": "Sehr geehrte Damen und Herren,\n\n"
    "am {datum} habe ich mich bei Ihnen als {position} beworben. Ich interessiere "
    "mich weiterhin sehr für die Stelle und möchte freundlich nachfragen, ob es "
    "zum Stand meiner Bewerbung schon Neuigkeiten gibt.\n\n"
    "Über eine kurze Rückmeldung freue ich mich.\n\n"
    "Mit freundlichen Grüßen\n{name}",
}


class Nachfassen:
    def __init__(
        self,
        config: AppConfig,
        dialog: Dialog,
        email: Optional[EmailHandler] = None,
        tage: int = NACHFASS_TAGE,
    ):
        self.config = config
        self.dialog = dialog
        self.email = email or EmailHandler()
        self.tage = tage
        self.db = BewerbungsDB(config.db_path)

    def mail(self, bewerbung: Dict) -> tuple:
        vorlage = {**STANDARD_MAIL, **self.config.profil.get("nachfass", {})}
        gesendet = datetime.fromisoformat(bewerbung["datum_gesendet"])
        platzhalter = {
            "position": bewerbung["position"],
            "unternehmen": bewerbung["unternehmen"],
            "datum": gesendet.strftime("%d.%m.%Y"),
            "name": self.config.profil["name"],
        }
        return (
            vorlage["betreff"].format(**platzhalter),
            vorlage["text"].format(**platzhalter),
        )

    def pruefe(self) -> int:
        """Fragt bei jeder Bewerbung ohne Antwort nach. Rückgabe: Anzahl gesendet."""
        gesendet = 0
        for b in self.db.nachfass_kandidaten(self.tage):
            betreff, text = self.mail(b)
            tage = (datetime.now() - datetime.fromisoformat(b["datum_gesendet"])).days
            self.dialog.zeige(
                f"⏰ Seit {tage} Tagen keine Antwort von {b['unternehmen']} "
                f"({b['position']}).\n📧 An: {b['empfaenger']}\n"
                f"   Betreff: {betreff}\n\n{text}"
            )
            antwort = self.dialog.frage(
                "\nNachfass-Mail senden? [j]a / [n]ein, nie / [s]päter: ",
                optionen=["j", "n", "s"],
            )
            if antwort is None:
                break
            antwort = antwort.lower()[:1]
            if antwort == "j":
                if self.email.send_application(b["empfaenger"], betreff, text, []):
                    self.db.markiere_nachgefasst(b["id"], date.today().isoformat())
                    self.dialog.zeige("✅ Nachgefasst.")
                    gesendet += 1
                else:
                    self.dialog.zeige(
                        "❌ Versand fehlgeschlagen – ich frage später nochmal."
                    )
            elif antwort == "n":
                self.db.markiere_nachgefasst(b["id"], "nein")
            # "s" (später): bleibt offen, nächstes Mal wieder fragen
        return gesendet
