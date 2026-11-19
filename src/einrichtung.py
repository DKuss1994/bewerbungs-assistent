"""Einrichtung für neue Nutzer: legt fehlende Dateien in data/ mit Beispielinhalt
an (nichts Vorhandenes wird überschrieben) und prüft, was noch fehlt.

    python -m src.einrichtung           # anlegen + prüfen
    python -m src.einrichtung pruefen   # nur prüfen"""

import json
import os
import shutil
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import List, Optional

from src.config import AppConfig
from src.dialog import Dialog, TerminalDialog
from src.kriterien import DATEI as KRITERIEN
from src.kriterien import Kriterien

ANSCHREIBEN = """{name}

{unternehmen}

Bewerbung als {position}

Sehr geehrte Damen und Herren,

mit großem Interesse habe ich Ihre Stellenanzeige als {position} gelesen.
Hier zwei Sätze, warum du zur Stelle passt und was dich antreibt.

{projekte_abschnitt}

Über die Einladung zu einem persönlichen Gespräch freue ich mich sehr.

Mit freundlichen Grüßen
{name}
"""

PROJEKTE = [
    {
        "id": "beispiel",
        "name": "Beispielprojekt",
        "tech": "Python, SQLite",
        "beschreibung": "Was das Projekt macht und was du dabei gelernt hast.",
        "link": "",
        "tags": ["python", "sql", "backend"],
    }
]

PROFILTEXTE = {
    "softwareentwicklung": {
        "subtitle": "Junior Softwareentwickler",
        "profil": "Zwei, drei Sätze über dich für Entwickler-Stellen.",
        "kernkompetenzen": ["Python", "Git", "SQL"],
    },
    "itsupport": {
        "subtitle": "IT-Support",
        "profil": "Zwei, drei Sätze über dich für Support-Stellen.",
        "kernkompetenzen": ["Windows", "Linux", "Netzwerk"],
    },
}

LEBENSLAUF = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 15mm; }}
  body {{ font-family: sans-serif; font-size: 10pt; }}
  h1 {{ margin-bottom: 0; }}
  .skill-category, .project {{ margin-bottom: 6px; }}
</style>
</head>
<body>
<h1>{name}</h1>
<div><!-- START_SUBTITLE -->Untertitel<!-- END_SUBTITLE --></div>
<p>E-Mail · Telefon · Wohnort · {github}</p>

<h2>Profil</h2>
<div><!-- START_PROFIL -->Profiltext<!-- END_PROFIL --></div>

<h2>Kernkompetenzen</h2>
<div><!-- START_KERNKOMPETENZEN -->Kernkompetenzen<!-- END_KERNKOMPETENZEN --></div>

<h2>Berufserfahrung</h2>
<p>Hier trägst du deinen Werdegang ein – dieser Teil bleibt immer gleich.</p>

<h2>Kenntnisse</h2>
<div><!-- START_SKILLS -->Skills<!-- END_SKILLS --></div>

<h2>Projekte</h2>
<div><!-- START_PROJECTS -->Projekte<!-- END_PROJECTS --></div>
</body>
</html>
"""


def _schreibe(pfad: Path, inhalt: str, dialog: Dialog) -> None:
    if pfad.exists():
        return
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(inhalt, encoding="utf-8")
    dialog.zeige(f"   📝 angelegt: {pfad}")


def _json(daten) -> str:
    return json.dumps(daten, indent=2, ensure_ascii=False) + "\n"


def lege_an(config: AppConfig, dialog: Dialog) -> bool:
    """Fehlende Dateien anlegen. False bei Abbruch."""
    data = config.data_dir
    profil_pfad = data / "profil.json"
    if profil_pfad.exists():
        name = json.loads(profil_pfad.read_text(encoding="utf-8"))["name"]
        github = ""
    else:
        dialog.zeige("\n👋 Ein paar Angaben für dein Profil (später änderbar):")
        name = dialog.frage("   Vor- und Nachname: ")
        if not name:
            dialog.zeige("❌ Ohne Namen geht es nicht – Abbruch.")
            return False
        github = dialog.frage("   GitHub-/Portfolio-Link (leer = keiner): ")
        seit = dialog.frage("   Berufserfahrung seit (Jahr, leer = keine Angabe): ")
        if github is None or seit is None:
            return False
        profil = {
            "name": name,
            "github": github,
            "lebenslauf_html": "lebenslauf.html",
            "anschreiben_vorlage": "anschreiben_vorlage.txt",
            "email": {
                "betreff": "Bewerbung als {position} - {name}",
                "text": "Sehr geehrte Damen und Herren,\n\nhiermit bewerbe ich mich "
                "auf die Stelle {position} bei {unternehmen}.\n\nMeine "
                "Bewerbungsunterlagen finden Sie im Anhang.\n\nIch freue mich auf "
                "Ihre Rückmeldung.\n\nMit freundlichen Grüßen\n{name}",
            },
            "skills": {
                "Programmierung": ["Python", "SQL"],
                "Werkzeuge": ["Git", "Linux"],
            },
        }
        if seit and seit.isdigit() and 1950 < int(seit) <= date.today().year:
            profil["berufserfahrung_seit"] = int(seit)
        _schreibe(profil_pfad, _json(profil), dialog)

    _schreibe(data / "anschreiben_vorlage.txt", ANSCHREIBEN, dialog)
    _schreibe(data / "projekte.json", _json(PROJEKTE), dialog)
    _schreibe(data / "profiltexte.json", _json(PROFILTEXTE), dialog)
    _schreibe(
        config.html_template,  # Dateiname aus profil.json
        LEBENSLAUF.format(name=name, github=github),
        dialog,
    )
    for ordner in (config.zeugnisse_ordner, config.zertifikate_ordner):
        ordner.mkdir(parents=True, exist_ok=True)
    _lege_kriterien_an(config, dialog)

    env, vorlage = config.base_dir / ".env", config.base_dir / ".env.example"
    if not env.exists() and vorlage.exists():
        shutil.copy(vorlage, env)
        dialog.zeige(f"   📝 angelegt: {env} (Zugangsdaten dort eintragen)")
    return True


def _lege_kriterien_an(config: AppConfig, dialog: Dialog) -> None:
    """Stellensuche einrichten – nur mit PLZ, leer = (noch) nicht suchen."""
    pfad = config.data_dir / KRITERIEN
    if pfad.exists():
        return
    plz = dialog.frage(
        "   PLZ für die automatische Stellensuche (leer = keine Suche): "
    )
    if not plz or not plz.strip().isdigit() or len(plz.strip()) != 5:
        return
    _schreibe(pfad, _json(asdict(Kriterien(plz=plz.strip()))), dialog)


def pruefe(config: AppConfig) -> List[str]:
    """Was ist eingerichtet, was fehlt? Eine Zeile je Punkt."""
    data = config.data_dir
    zeilen = []

    def punkt(ok: bool, text: str, hinweis: str = "") -> None:
        zeilen.append(
            f"{'✅' if ok else '⚠️'} {text}" + ("" if ok else f" – {hinweis}")
        )

    for datei in ("profil.json", "projekte.json", "profiltexte.json"):
        punkt((data / datei).exists(), datei, "python -m src.einrichtung")
    if (data / "profil.json").exists():
        punkt(
            config.html_template.exists(),
            "Lebenslauf-Vorlage",
            "fehlt in data/lebenslauf/",
        )
        punkt(
            config.anschreiben_vorlage.exists(),
            "Anschreiben-Vorlage",
            "fehlt in data/",
        )
    kriterien = Kriterien.laden(config)
    punkt(
        bool(kriterien and kriterien.plz),
        "Stellensuche (optional)",
        f"data/{KRITERIEN} mit PLZ – python -m src.einrichtung",
    )
    anlagen = len(list(config.zeugnisse_ordner.glob("*.pdf"))) + len(
        list(config.zertifikate_ordner.glob("*.pdf"))
    )
    punkt(anlagen > 0, f"{anlagen} PDF(s) als Anlage", "Zeugnisse nach data/zeugnisse/")

    punkt(
        bool(os.getenv("EMAIL_ABSENDER") and os.getenv("EMAIL_PASSWORT")),
        "E-Mail-Versand (SMTP)",
        "EMAIL_ABSENDER/EMAIL_PASSWORT in .env",
    )
    punkt(
        bool(os.getenv("IMAP_USER") and os.getenv("IMAP_PASSWORD")),
        "Postfach lesen (IMAP)",
        "IMAP_USER/IMAP_PASSWORD in .env",
    )
    punkt(
        bool(os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
        "Telegram-Bot (optional)",
        "TELEGRAM_TOKEN/TELEGRAM_CHAT_ID in .env",
    )
    try:
        import weasyprint  # noqa: F401

        punkt(True, "PDF-Erzeugung (WeasyPrint)")
    except (ImportError, OSError):
        punkt(False, "PDF-Erzeugung (WeasyPrint)", "pip install -r requirements.txt")
    punkt(
        _ki_erreichbar(config), "Lokale KI (optional)", "ollama serve + ollama pull …"
    )
    return zeilen


def _ki_erreichbar(config: AppConfig) -> bool:
    from src.llm_client import LLMClient

    try:
        return LLMClient.from_config(config).ist_verfuegbar()
    except ValueError:
        return False


def main(
    args: Optional[List[str]] = None,
    config: Optional[AppConfig] = None,
    dialog: Optional[Dialog] = None,
):
    args = sys.argv[1:] if args is None else args
    config, dialog = config or AppConfig(), dialog or TerminalDialog()
    nur_pruefen = args == ["pruefen"]
    if not nur_pruefen and not lege_an(config, dialog):
        sys.exit(1)
    dialog.zeige("\n🔧 Stand der Einrichtung:\n" + "\n".join(pruefe(config)))
    if nur_pruefen:
        return
    dialog.zeige(
        "\nAls Nächstes: data/lebenslauf/lebenslauf.html, data/profil.json und "
        "data/projekte.json mit deinen Daten füllen, dann ausprobieren mit\n"
        "   python preview.py <stellenanzeige.txt|link>"
    )


if __name__ == "__main__":
    main()
