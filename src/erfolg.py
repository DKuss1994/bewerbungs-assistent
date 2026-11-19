"""Smarte Datenbank: lernt aus Einladungen und Absagen, was funktioniert."""

import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from src.database import verbinde

# Wie stark eine Rückmeldung die Projekt-Auswahl beeinflusst
BONUS_EINLADUNG = 1.0
MALUS_ABSAGE = -0.25


def passungs_stufe(score) -> str:
    """Passung (0–100) grob einteilen, damit sich Ergebnisse vergleichen lassen."""
    if score is None:
        return "unbekannt"
    if score >= 75:
        return "hoch (ab 75 %)"
    return "mittel (50–74 %)" if score >= 50 else "niedrig (unter 50 %)"


class ErfolgsTracker:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _bewerbungen_mit_ergebnis(self) -> List[Dict]:
        with verbinde(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("""
                    SELECT status, projekte, berufsrichtung, agent_self_score
                    FROM bewerbungen
                    WHERE status IN ('einladung', 'absage')
                """).fetchall()
            except sqlite3.OperationalError:
                # Tabelle/Spalten existieren noch nicht
                return []
            return [dict(r) for r in rows]

    def projekt_bonus(self) -> Dict[str, float]:
        """Bonuspunkte pro Projekt-ID: Einladungen zählen stark, Absagen leicht negativ."""
        bonus: Dict[str, float] = defaultdict(float)
        for b in self._bewerbungen_mit_ergebnis():
            wert = BONUS_EINLADUNG if b["status"] == "einladung" else MALUS_ABSAGE
            for pid in filter(None, (b["projekte"] or "").split(",")):
                bonus[pid] += wert
        return dict(bonus)

    def statistik(self) -> Dict[str, Dict[str, Dict[str, int]]]:
        """Einladungen/Absagen pro Projekt, Berufsrichtung und Passungs-Stufe."""
        stats = {
            k: defaultdict(lambda: {"einladung": 0, "absage": 0})
            for k in ("projekte", "berufsrichtung", "passung")
        }
        for b in self._bewerbungen_mit_ergebnis():
            for pid in filter(None, (b["projekte"] or "").split(",")):
                stats["projekte"][pid][b["status"]] += 1
            if b["berufsrichtung"]:
                stats["berufsrichtung"][b["berufsrichtung"]][b["status"]] += 1
            stats["passung"][passungs_stufe(b["agent_self_score"])][b["status"]] += 1
        return {k: dict(v) for k, v in stats.items()}
