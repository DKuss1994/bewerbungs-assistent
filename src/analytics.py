import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from src.config import AppConfig
from src.database import BewerbungsDB, verbinde
from src.erfolg import ErfolgsTracker
from src.learning import LearningSystem


class Analytics:
    def __init__(self, config: AppConfig):
        self.config = config
        self.db = BewerbungsDB(config.db_path)

    def get_uebersicht(self) -> Dict:
        """Gibt eine Gesamtübersicht über alle Bewerbungen zurück."""
        with verbinde(self.config.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Gesamtstatistik
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as gesamt,
                    SUM(CASE WHEN status = 'absage' THEN 1 ELSE 0 END) as absagen,
                    SUM(CASE WHEN status = 'einladung' THEN 1 ELSE 0 END) as einladungen,
                    SUM(CASE WHEN status = 'gesendet' THEN 1 ELSE 0 END) as offen,
                    SUM(CASE WHEN status = 'geghosted' THEN 1 ELSE 0 END) as geghosted
                FROM bewerbungen
            """)
            stats = dict(cursor.fetchone())

            # Durchschnittliche Antwortzeit
            cursor = conn.execute("""
                SELECT AVG(dauer_tage) as avg_dauer
                FROM rueckmeldungen
                WHERE dauer_tage IS NOT NULL
            """)
            row = cursor.fetchone()
            stats["durchschnittliche_antwortzeit"] = (
                round(row["avg_dauer"], 1) if row["avg_dauer"] else None
            )

            # Absage-Gründe
            cursor = conn.execute("""
                SELECT grund_kategorie, COUNT(*) as anzahl
                FROM rueckmeldungen
                WHERE grund_kategorie IS NOT NULL
                GROUP BY grund_kategorie
                ORDER BY anzahl DESC
            """)
            stats["absage_gruende"] = [dict(row) for row in cursor.fetchall()]

            # Erfolgsquote pro Monat
            cursor = conn.execute("""
                SELECT 
                    strftime('%Y-%m', datum_gesendet) as monat,
                    COUNT(*) as gesamt,
                    SUM(CASE WHEN status = 'einladung' THEN 1 ELSE 0 END) as einladungen
                FROM bewerbungen
                GROUP BY monat
                ORDER BY monat DESC
                LIMIT 6
            """)
            stats["monatliche_erfolge"] = [dict(row) for row in cursor.fetchall()]

            return stats

    def bericht(self) -> str:
        """Übersichtlicher Report als Text (Terminal oder Chat)."""
        stats = self.get_uebersicht()
        z = ["", "=" * 60, "📊 BEWERBUNGS-REPORT", "=" * 60]

        z.append(f"\n📨 Gesamt: {stats['gesamt']}")
        z.append(f"✅ Einladungen: {stats['einladungen']}")
        z.append(f"❌ Absagen: {stats['absagen']}")
        z.append(f"⏳ Offen: {stats['offen']}")
        z.append(f"👻 Geghosted: {stats['geghosted']}")

        if stats["gesamt"] > 0:
            quote = round(stats["einladungen"] / stats["gesamt"] * 100, 1)
            z.append(f"\n📈 Erfolgsquote: {quote}%")

        if stats.get("durchschnittliche_antwortzeit"):
            z.append(
                f"\n⏱️ Durchschnittliche Antwortzeit: {stats['durchschnittliche_antwortzeit']} Tage"
            )

        if stats.get("absage_gruende"):
            z.append("\n🔍 Häufigste Absage-Gründe:")
            for grund in stats["absage_gruende"]:
                z.append(f"   - {grund['grund_kategorie']}: {grund['anzahl']}×")

        erfolg = ErfolgsTracker(self.config.db_path).statistik()
        for titel, schluessel in (
            ("🎯 Erfolg nach Berufsrichtung", "berufsrichtung"),
            ("🧩 Erfolg nach Projekt im Lebenslauf", "projekte"),
            ("📊 Erfolg nach Passung zur Stelle", "passung"),
        ):
            if erfolg[schluessel]:
                z.append(f"\n{titel}:")
                for name, werte in sorted(erfolg[schluessel].items()):
                    z.append(
                        f"   - {name}: {werte['einladung']} Einladung(en), "
                        f"{werte['absage']} Absage(n)"
                    )

        learnings = LearningSystem(self.config.db_path).get_active_learnings()
        if learnings:
            z.append("\n🧠 Gelernte Regeln:")
            for l in learnings:
                z.append(f"   - [{l['kategorie']}] {l['aenderung']}")

        z.append("\n" + "=" * 60)
        return "\n".join(z)

    def print_report(self):
        print(self.bericht())
