import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# Projekt-Ordner = Ordner über src/ (funktioniert auf jedem Rechner)
PROJEKT_DIR = Path(__file__).resolve().parent.parent


@dataclass
class AppConfig:
    base_dir: Path = PROJEKT_DIR

    # KI: "ollama" (lokal, Standard) oder "mistral" (Cloud, braucht MISTRAL_API_KEY)
    llm_provider: str = os.getenv("LLM_PROVIDER", "ollama")
    llm_model: str = os.getenv("LLM_MODEL", "")  # schnell: Einordnen von Mails
    llm_model_text: str = os.getenv("LLM_MODEL_TEXT", "")  # gut: Anschreiben-Absatz
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")

    # E-Mail (aus .env)
    smtp_server: str = os.getenv("SMTP_SERVER", "smtp.web.de")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    email_absender: str = os.getenv("EMAIL_ABSENDER", "")
    email_passwort: str = os.getenv("EMAIL_PASSWORT", "")

    _profil: dict = field(default=None, init=False, repr=False)

    # ---------- PROFIL (persönliche Daten aus data/profil.json) ----------
    @property
    def profil(self) -> dict:
        if self._profil is None:
            with open(self.data_dir / "profil.json", "r", encoding="utf-8") as f:
                self._profil = json.load(f)
        return self._profil

    # ---------- PFADE ----------
    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def html_template(self) -> Path:
        return self.data_dir / "lebenslauf" / self.profil["lebenslauf_html"]

    @property
    def anschreiben_vorlage(self) -> Path:
        return self.anschreiben_vorlage_fuer(None)

    def anschreiben_vorlage_fuer(self, berufsrichtung: Optional[str]) -> Path:
        """Vorlage passend zur Berufsrichtung. In profil.json entweder ein
        Dateiname oder {"standard": ..., "<richtung>": ...}."""
        vorlage = self.profil["anschreiben_vorlage"]
        if isinstance(vorlage, dict):
            vorlage = vorlage.get(berufsrichtung) or vorlage["standard"]
        return self.data_dir / vorlage

    @property
    def jahre_erfahrung(self) -> Optional[int]:
        """Berufsjahre aus "berufserfahrung_seit" (Jahr) – bleibt automatisch aktuell."""
        seit = self.profil.get("berufserfahrung_seit")
        return date.today().year - int(seit) if seit else None

    @property
    def zeugnisse_ordner(self) -> Path:
        return self.data_dir / "zeugnisse"

    @property
    def zertifikate_ordner(self) -> Path:
        return self.data_dir / "zertifikate"

    @property
    def bewerbungen_ordner(self) -> Path:
        return self.data_dir / "bewerbungen"

    @property
    def preview_ordner(self) -> Path:
        return self.data_dir / "preview"

    @property
    def db_path(self) -> Path:
        return self.base_dir / "bewerbungen.db"
