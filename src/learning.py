import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.database import verbinde

# Was der Agent aus einem Absage-Grund für künftige Anschreiben lernt.
# Die Regeln landen im Prompt für den Projekt-Absatz – sie dürfen nichts
# verlangen, was nicht in den Projektbeschreibungen steht (sonst erfindet die KI).
# Eigene Formulierungen in profil.json: "lern_regeln": {"fehlende_erfahrung": "…"}
LERN_REGELN = {
    "fehlende_erfahrung": "Stelle bei jedem Projekt heraus, was praktisch gebaut "
    "wurde und wofür – nur mit Angaben aus den Beschreibungen.",
    "tech_stack": "Nenne bei jedem Projekt die eingesetzten Technologien aus der "
    "Beschreibung ausdrücklich.",
    "nicht_qualifiziert": "Beschreibe konkret, was jedes Projekt leistet. Nenne nur "
    "Ergebnisse, die in der Beschreibung stehen, und erfinde keine Zahlen.",
}

# Frühere Regel-Texte verlangten Fakten, die nicht in den Projektbeschreibungen
# stehen (Zahlen, frühere Berufe, Kundenkontakt) → neu formuliert bzw. abgeschaltet
VERALTETE_REGELN = {
    "Betone praktische Projekte und die Übertragbarkeit der Erfahrung aus "
    "früheren Tätigkeiten.": "fehlende_erfahrung",
    "Nenne explizit, welche Technologien der Stelle in den Projekten eingesetzt "
    "wurden oder gerade gelernt werden.": "tech_stack",
    "Stelle konkrete, nachprüfbare Ergebnisse der Projekte heraus "
    "(z.B. Anzahl Tests, gesparte Zeit).": "nicht_qualifiziert",
    "Betone Zusammenarbeit mit Technikern, Kollegen und Kunden.": None,
}


class LearningSystem:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_table()
        self._ersetze_veraltete_regeln()

    def _init_table(self):
        with verbinde(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lern_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kategorie TEXT NOT NULL,
                    ausloeser TEXT,
                    aenderung TEXT NOT NULL,
                    aktiv INTEGER DEFAULT 1,
                    erstellt_am TEXT DEFAULT CURRENT_TIMESTAMP,
                    angewendet_bei INTEGER
                )
            """)
            conn.commit()

    def _ersetze_veraltete_regeln(self):
        with verbinde(self.db_path) as conn:
            for alt, grund in VERALTETE_REGELN.items():
                if grund:
                    conn.execute(
                        "UPDATE lern_feedback SET aenderung = ? WHERE aenderung = ?",
                        (LERN_REGELN[grund], alt),
                    )
                else:
                    conn.execute(
                        "UPDATE lern_feedback SET aktiv = 0 WHERE aenderung = ?",
                        (alt,),
                    )
            conn.commit()

    def add_learning(self, kategorie: str, ausloeser: str, aenderung: str) -> bool:
        """Speichert eine Lern-Regel. Gibt False zurück, wenn sie schon aktiv ist."""
        with verbinde(self.db_path) as conn:
            vorhanden = conn.execute(
                "SELECT 1 FROM lern_feedback "
                "WHERE kategorie = ? AND aenderung = ? AND aktiv = 1",
                (kategorie, aenderung),
            ).fetchone()
            if vorhanden:
                return False
            conn.execute(
                """
                INSERT INTO lern_feedback (kategorie, ausloeser, aenderung)
                VALUES (?, ?, ?)
            """,
                (kategorie, ausloeser, aenderung),
            )
            conn.commit()
            return True

    def get_active_learnings(self) -> List[Dict]:
        with verbinde(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM lern_feedback WHERE aktiv = 1")
            return [dict(row) for row in cursor.fetchall()]

    def apply_learning_to_prompt(self, prompt: str, kategorie: str) -> str:
        learnings = self.get_active_learnings()
        relevant = [l for l in learnings if l["kategorie"] == kategorie]
        if not relevant:
            return prompt
        lern_text = "\n\nZUSÄTZLICHE ANWEISUNGEN AUS VORHERIGEN ERFAHRUNGEN:\n"
        # dict.fromkeys: jede Anweisung nur einmal, auch nach dem Umschreiben
        for aenderung in dict.fromkeys(l["aenderung"] for l in relevant):
            lern_text += f"- {aenderung}\n"
        return prompt + lern_text
