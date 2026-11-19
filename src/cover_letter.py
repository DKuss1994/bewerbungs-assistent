import re
from typing import Dict, List, Optional

from src.config import AppConfig
from src.learning import LearningSystem
from src.llm_client import LLMClient, LLMError


class CoverLetterGenerator:
    def __init__(self, config: AppConfig, client: Optional[LLMClient] = None):
        self.config = config
        self.client = client
        self.learning = LearningSystem(config.db_path)

    def _lade_vorlage(self, berufsrichtung: Optional[str]) -> str:
        """Deine Vorlage (data/anschreiben_*.txt) passend zur Berufsrichtung."""
        pfad = self.config.anschreiben_vorlage_fuer(berufsrichtung)
        with open(pfad, "r", encoding="utf-8") as f:
            return f.read()

    def generate(
        self,
        stellen_text: str,
        unternehmen: str,
        position: str,
        projekte: Optional[List[Dict]] = None,
        berufsrichtung: Optional[str] = None,
    ) -> dict:
        """Füllt die Vorlage; nur der Projekt-Absatz wird (optional) per KI erzeugt."""
        projekte = projekte or []
        projekte_abschnitt = self._get_projekte_text(stellen_text, projekte)

        werte = {
            "position": position,
            "unternehmen": unternehmen,
            "projekte_abschnitt": projekte_abschnitt,
            "name": self.config.profil["name"],
        }
        if self.config.jahre_erfahrung is not None:
            werte["jahre_erfahrung"] = self.config.jahre_erfahrung
        try:
            full_text = self._lade_vorlage(berufsrichtung).format(**werte)
        except KeyError as e:
            raise ValueError(
                f"❌ Anschreiben-Vorlage nutzt den Platzhalter {e}, "
                "der nicht gefüllt werden kann (für {jahre_erfahrung} "
                '"berufserfahrung_seit" in profil.json eintragen).'
            ) from None
        return {"status": "erstellt", "text": full_text}

    def _fallback_text(self, projekte: List[Dict]) -> str:
        """Fester Absatz aus den gewählten Projekten – ohne KI, immer korrekt."""
        github = self.config.profil.get("github", "")
        if not projekte:
            return f"Mein GitHub-Profil finden Sie unter {github}." if github else ""
        saetze = [
            "Um meine Kenntnisse praktisch zu vertiefen, habe ich eigene Projekte umgesetzt:"
        ]
        for p in projekte:
            saetze.append(f"{p['name']} ({p['tech']}): {p['beschreibung']}")
        if github:
            saetze.append(f"Mein GitHub-Profil finden Sie unter {github}.")
        return " ".join(saetze)

    def _get_projekte_text(self, stellen_text: str, projekte: List[Dict]) -> str:
        fallback = self._fallback_text(projekte)
        if not self.client or not projekte:
            return fallback

        projekt_liste = "\n".join(
            f"- {p['name']} ({p['tech']}): {p['beschreibung']}" for p in projekte
        )
        prompt = f"""
Formuliere die folgenden Projektbeschreibungen um zu einem zusammenhängenden Absatz für ein Bewerbungsanschreiben.

PROJEKTE:
{projekt_liste}

REGELN:
- Ich-Form, sachlich, Deutsch, 3-4 Sätze.
- Beginne mit: "Um meine Kenntnisse praktisch zu vertiefen, habe ich"
- Pro Projekt ein Satz. Nenne jedes Projekt beim Namen.
- Verwende NUR Fakten aus den Beschreibungen oben. Füge KEINE Fähigkeiten, Technologien oder Bewertungen hinzu.
- Schreibe NUR den Absatz.
"""
        prompt = self.learning.apply_learning_to_prompt(prompt, "anschreiben")
        try:
            text = self.client.generate(prompt, temperature=0.3, timeout=300).strip()
        except LLMError as e:
            print(f"   ⚠️ KI nicht verfügbar, nutze Standardtext ({e})")
            return fallback

        if not self._ist_brauchbar(text, projekte):
            print("   ⚠️ KI-Absatz unbrauchbar, nutze Standardtext")
            return fallback
        github = self.config.profil.get("github", "")
        if github and github not in text:
            text += f" Mein GitHub-Profil finden Sie unter {github}."
        return text

    @staticmethod
    def _ist_brauchbar(text: str, projekte: List[Dict]) -> bool:
        """Grobe Qualitätsprüfung der KI-Antwort."""
        if not 80 <= len(text) <= 1200:
            return False
        # Kein Chinesisch/Japanisch/Kyrillisch o.ä.
        if re.search(r"[^\x00-\x7FäöüÄÖÜß–„“”’€…•]", text):
            return False
        # Muss mindestens ein echtes Projekt nennen
        if not any(p["name"].lower() in text.lower() for p in projekte):
            return False
        # Keine erfundenen Zahlen ("40 % schneller") – nur solche aus den Projekten
        quelle = " ".join(
            f"{p['name']} {p['tech']} {p['beschreibung']}" for p in projekte
        )
        if set(re.findall(r"\d+", text)) - set(re.findall(r"\d+", quelle)):
            return False
        # Keine Markdown-Reste oder Platzhalter
        return not re.search(r"[#*{}\[\]]", text)
