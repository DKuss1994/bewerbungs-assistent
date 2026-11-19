import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

from src.config import AppConfig
from src.database import BewerbungsDB, verbinde
from src.learning import LearningSystem


class CooldownManager:
    def __init__(self, config: AppConfig):
        self.config = config
        self.db = BewerbungsDB(config.db_path)
        self.learning = LearningSystem(config.db_path)

    def get_rebewerbungs_kandidaten(self) -> List[Dict]:
        """Absagen mit abgelaufenem Cooldown, auf die noch keine neue Bewerbung
        bei derselben Firma folgte (die verweist per previous_id zurück)."""
        with verbinde(self.config.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM bewerbungen b
                WHERE status = 'absage'
                AND cooldown_until <= ?
                AND NOT EXISTS (SELECT 1 FROM bewerbungen n WHERE n.previous_id = b.id)
                ORDER BY cooldown_until ASC
            """,
                (datetime.now().isoformat(),),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_neue_skills(self, seit: str = "") -> List[str]:
        """Skills (ja/teilweise) aus der Kompetenz-DB, die seit `seit` erfasst wurden."""
        with verbinde(self.config.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT skill_name FROM kompetenzen
                WHERE level IN ('ja', 'teilweise')
                AND datum_erfasst > ?
                ORDER BY datum_erfasst DESC
                LIMIT 5
            """,
                (seit.replace("T", " ")[:19],),
            )
            return [row[0] for row in cursor.fetchall()]

    def bericht(self) -> str:
        """Empfehlungen für Re-Bewerbungen als Text (Terminal oder Chat)."""
        kandidaten = self.get_rebewerbungs_kandidaten()
        if not kandidaten:
            return "✅ Keine Cooldown-Kandidaten gefunden."

        z = ["\n🔄 COOLDOWN-CHECK", "=" * 60]
        for k in kandidaten:
            neue_skills = self.get_neue_skills(seit=k["datum_gesendet"])
            z.append(f"\n📌 {k['unternehmen']} ({k['position']})")
            z.append(f"   Beworben am: {k['datum_gesendet'][:10]}")
            z.append(f"   Cooldown abgelaufen am: {k['cooldown_until'][:10]}")
            if neue_skills:
                z.append(f"   🆕 Neue Skills: {', '.join(neue_skills)}")
                z.append(
                    "   💡 Empfehlung: Jetzt bewerben! Neue Skills sind vorhanden."
                )
            else:
                z.append("   ⚠️ Keine neuen Skills erlernt.")
                z.append(
                    "   💡 Empfehlung: Lerne erst einen neuen Skill (z.B. React, Docker)"
                )
        return "\n".join(z)

    def check_rebewerbung(self):
        print(self.bericht())
