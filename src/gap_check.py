from typing import Dict, List

from src.kompetenzen import KompetenzenDB, finde_skills


class GapCheck:
    def __init__(self, db: KompetenzenDB):
        self.db = db

    def extrahiere_anforderungen(self, stellen_text: str) -> List[str]:
        """Extrahiert alle geforderten Skills aus der Stellenanzeige."""
        return sorted(finde_skills(stellen_text))

    def finde_luecken(self, stellen_text: str) -> Dict:
        """
        Vergleicht Stelle mit bekannten Kompetenzen.
        Rückgabe:
        {
            "gefordert": [...],
            "bekannt": [...],
            "unbekannt": [...],
            "teilweise": [...]
        }
        """
        gefordert = self.extrahiere_anforderungen(stellen_text)
        bekannt = []
        unbekannt = []
        teilweise = []

        for skill in gefordert:
            level = self.db.hat_kompetenz(skill)
            if level is None:
                unbekannt.append(skill)
            elif level == "ja":
                bekannt.append(skill)
            elif level in ("teilweise", "lernend"):
                teilweise.append(skill)
            # level == "nein" → auch als unbekannt für dieses Mal betrachten, aber nicht erneut fragen
            elif level == "nein":
                pass  # bereits bekannt, dass nicht vorhanden

        return {
            "gefordert": gefordert,
            "bekannt": bekannt,
            "unbekannt": unbekannt,
            "teilweise": teilweise,
        }

    @staticmethod
    def passung(gap: Dict) -> int:
        """Wie gut du zur Stelle passt, 0–100: Anteil der geforderten Skills,
        die du kannst ("teilweise"/"lernend" zählt halb). Ohne erkannte
        Anforderungen 50 – dann lässt sich nichts sagen."""
        if not gap["gefordert"]:
            return 50
        punkte = len(gap["bekannt"]) + 0.5 * len(gap["teilweise"])
        return round(100 * punkte / len(gap["gefordert"]))

    def gap_report(self, gap: Dict) -> str:
        """Übersichtlicher Gap-Report als Text."""
        zeilen = ["", "=" * 60, "🔍 KOMPETENZ-CHECK", "=" * 60]
        abschnitte = [
            ("📋 Gefordert", gap["gefordert"], True),
            ("✅ Bereits bekannt", gap["bekannt"], True),
            ("🟡 Teilweise/Lernend", gap["teilweise"], False),
            ("❓ Unbekannt", gap["unbekannt"], False),
        ]
        for titel, skills, immer in abschnitte:
            if skills or immer:
                zeilen.append(f"\n{titel} ({len(skills)}):")
                zeilen += [f"   • {s}" for s in skills]
        return "\n".join(zeilen)
