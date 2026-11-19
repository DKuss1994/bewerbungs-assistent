"""Tagesliste: neue Stellen suchen und dir die passenden zur Entscheidung zeigen.
„Bewerben“ startet den normalen Ablauf mit Vorschau und Bestätigung."""

from typing import Dict, Optional

from src.config import AppConfig
from src.dialog import Dialog
from src.kriterien import DATEI, Kriterien
from src.main import BewerbungsAgent, frage_stellendaten
from src.stellen import StellenDB, StellenSuche
from src.stellen_info import bereinige_position, erkenne_stelle


def karte(s: Dict) -> str:
    return (
        f"💼 {s['titel']}\n🏢 {s['firma']} – {s['ort']}\n"
        f"📍 {s['gruende']}\n⭐ {s['punkte']} Punkte\n🔗 {s['link']}"
    )


class Vorschlaege:
    def __init__(
        self,
        config: AppConfig,
        dialog: Dialog,
        suche: Optional[StellenSuche] = None,
        agent: Optional[BewerbungsAgent] = None,
    ):
        self.config = config
        self.dialog = dialog
        self.suche = suche
        self.agent = agent
        self.db = StellenDB(config.db_path)

    def pruefe(self, automatisch: bool = True) -> int:
        """Sucht neue Stellen und fragt bei jedem Vorschlag nach.
        automatisch: still bleiben, wenn nichts eingerichtet oder nichts Neues.
        Rückgabe: Anzahl Bewerbungen."""
        kriterien = Kriterien.laden(self.config)
        if not kriterien or not kriterien.plz:
            if not automatisch:
                self.dialog.zeige(
                    f"⚙️ Für die Stellensuche data/{DATEI} mit deiner PLZ anlegen "
                    "(python -m src.einrichtung)."
                )
            return 0
        if not automatisch:
            self.dialog.zeige("🔎 Suche neue Stellen …")
        suche = self.suche or StellenSuche(
            self.config, kriterien, melde=self.dialog.zeige
        )
        zahlen = suche.lauf()

        liste = self.db.vorschlaege(kriterien.max_vorschlaege)
        if not liste:
            if not automatisch:
                self.dialog.zeige(
                    f"📭 Keine passenden Stellen ({zahlen['neu']} neue geprüft)."
                )
            return 0
        self.dialog.zeige(
            f"💼 {len(liste)} Stellen-Vorschläge – {zahlen['neu']} neue Stellen "
            f"gefunden, {zahlen['vorgeschlagen']} davon passen."
        )

        beworben = 0
        for s in liste:
            self.dialog.zeige(karte(s))
            antwort = self.dialog.frage(
                "\n[b]ewerben / [i]gnorieren / [s]päter / [x] Schluss für heute: ",
                optionen=["b", "i", "s", "x"],
            )
            if antwort is None or antwort.lower()[:1] == "x":
                break
            antwort = antwort.lower()[:1]
            if antwort == "i":
                self.db.setze_status(s["id"], "ignoriert")
            elif antwort == "b":
                bewerbung_id = self._bewerben(s)
                self.db.setze_status(
                    s["id"], "beworben" if bewerbung_id else "abgebrochen", bewerbung_id
                )
                beworben += bool(bewerbung_id)
            # "s" (später): bleibt vorgeschlagen, kommt beim nächsten Mal wieder
        return beworben

    def _bewerben(self, s: Dict) -> Optional[int]:
        self.agent = self.agent or BewerbungsAgent(self.config, dialog=self.dialog)
        info = erkenne_stelle(s["text"], self.agent.client)
        # Firma und Titel aus der Jobbörse sind verlässlicher als aus dem Text
        info.unternehmen = s["firma"] or info.unternehmen
        info.position = bereinige_position(s["titel"]) or info.position
        info = frage_stellendaten(info, dialog=self.dialog)
        if not info:
            return None
        return self.agent.bewerben(
            s["text"], info.unternehmen, info.position, info.email, link=s["link"]
        )
