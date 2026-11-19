"""Demo mit erfundenen Daten – ohne KI, ohne Postfach, ohne Internet.

    python -m src.demo

Legt alles in demo/ an (wird bei jedem Start neu erzeugt) und zeigt die
Pipeline einmal komplett: Anzeige lesen → Unterlagen erzeugen → Rückmeldungen
aus dem Postfach einordnen → Übersicht, Anruf-Spickzettel und Report."""

import html
import json
import shutil
from datetime import datetime, timedelta

from src.analytics import Analytics
from src.anrufen import Anrufen
from src.config import PROJEKT_DIR, AppConfig
from src.cover_letter import CoverLetterGenerator
from src.database import BewerbungsDB, verbinde
from src.dialog import TerminalDialog
from src.einrichtung import ANSCHREIBEN, LEBENSLAUF, PROFILTEXTE
from src.gap_check import GapCheck
from src.imap_reader import ImapReader
from src.kompetenzen import KompetenzenDB
from src.main import ANSCHREIBEN_HTML, ordnername
from src.resume_builder import ResumeBuilder
from src.stellen_info import erkenne_stelle
from src.uebersicht import uebersicht

DEMO_DIR = PROJEKT_DIR / "demo"

PROFIL = {
    "name": "Max Mustermann",
    "github": "github.com/max-mustermann",
    "lebenslauf_html": "lebenslauf.html",
    "anschreiben_vorlage": "anschreiben_vorlage.txt",
    "email": {
        "betreff": "Bewerbung als {position} - {name}",
        "text": "Sehr geehrte Damen und Herren,\n\nanbei meine Unterlagen.\n\n{name}",
    },
    "skills": {
        "Programmierung": ["Python", "Java", "SQL"],
        "Werkzeuge": ["Git", "Docker", "Linux"],
    },
}

PROJEKTE = [
    {
        "id": "tracker",
        "name": "Bewerbungs-Assistent",
        "tech": "Python, SQLite, Ollama",
        "beschreibung": "CLI und Telegram-Bot, die Bewerbungen verwalten und "
        "Rückmeldungen mit lokaler KI auswerten.",
        "link": "",
        "tags": ["python", "sql", "ki", "automatisierung"],
    },
    {
        "id": "rest",
        "name": "Bibliotheks-API",
        "tech": "Java, Spring Boot, PostgreSQL",
        "beschreibung": "REST-API mit Tests und Docker-Setup.",
        "link": "",
        "tags": ["java", "spring", "backend", "api", "docker"],
    },
    {
        "id": "homelab",
        "name": "Homelab",
        "tech": "Linux, Docker, Bash",
        "beschreibung": "Eigener Server mit Backups und Monitoring.",
        "link": "",
        "tags": ["linux", "bash", "docker", "netzwerk"],
    },
]

ANZEIGE = """Junior Python-Entwickler (m/w/d)
Arbeitgeber: Datenwerk Nord GmbH

Du entwickelst mit uns interne Werkzeuge in Python und SQL, betreust
Docker-Container und automatisierst Abläufe. Git ist für dich selbstverständlich,
erste Erfahrung mit Kubernetes ist ein Plus.

Fragen beantwortet dir gern Frau Lea Beispiel unter 040 123456-0.
Bewerbungen bitte an: karriere@datenwerk-nord.example
"""

# (Firma, Position, vor Tagen, E-Mail, nachgefasst vor Tagen, Passung, Telefon)
BEWERBUNGEN = [
    ("Datenwerk Nord GmbH", "Junior Python-Entwickler (m/w/d)", 3, True, None, 80, ""),
    ("Hafenlogistik AG", "IT-Support (m/w/d)", 16, True, None, 65, ""),
    (
        "Kontor Software GmbH",
        "Backend-Entwickler (m/w/d)",
        24,
        True,
        8,
        70,
        "040 98765",
    ),
    ("Portalfirma SE", "Systemadministrator (m/w/d)", 18, False, None, 55, "089 4711"),
    ("Nordlicht IT GmbH", "Junior Developer (m/w/d)", 21, True, None, 85, ""),
    ("Sirius Systems AG", "Java-Entwickler (m/w/d)", 30, True, None, 45, ""),
]

MAILS = [
    (
        "<demo-1@example>",
        "Ihre Bewerbung",
        "Nordlicht IT <jobs@nordlicht-it.example>",
        "Vielen Dank! Wir möchten Sie gern kennenlernen und laden Sie zu einem "
        "Vorstellungsgespräch ein.",
    ),
    (
        "<demo-2@example>",
        "Ihre Bewerbung als Java-Entwickler",
        "Sirius Systems <hr@sirius.example>",
        "Leider haben wir uns für Kandidaten mit mehr Berufserfahrung entschieden.",
    ),
]


def _titel(text: str) -> None:
    print(f"\n{'─' * 60}\n{text}\n{'─' * 60}")


def lege_daten_an() -> AppConfig:
    if DEMO_DIR.exists():
        shutil.rmtree(DEMO_DIR)
    data = DEMO_DIR / "data"
    (data / "lebenslauf").mkdir(parents=True)
    dateien = {
        "profil.json": json.dumps(PROFIL, indent=2, ensure_ascii=False),
        "projekte.json": json.dumps(PROJEKTE, indent=2, ensure_ascii=False),
        "profiltexte.json": json.dumps(PROFILTEXTE, indent=2, ensure_ascii=False),
        "anschreiben_vorlage.txt": ANSCHREIBEN.replace(
            "Hier zwei Sätze, warum du zur Stelle passt und was dich antreibt.",
            "Nach einigen Jahren in der Logistik habe ich mir das Programmieren\n"
            "selbst beigebracht und möchte es nun zu meinem Beruf machen.",
        ),
        "lebenslauf/lebenslauf.html": LEBENSLAUF.format(
            name=PROFIL["name"], github=PROFIL["github"]
        ),
    }
    for name, inhalt in dateien.items():
        (data / name).write_text(inhalt, encoding="utf-8")
    (DEMO_DIR / "stellenanzeige.txt").write_text(ANZEIGE, encoding="utf-8")
    return AppConfig(base_dir=DEMO_DIR)


def zeige_erkennung(config: AppConfig) -> None:
    _titel("1️⃣  Stellenanzeige lesen (Regeln, ohne KI)")
    info = erkenne_stelle(ANZEIGE)
    for feld, wert in vars(info).items():
        print(f"   {feld:16} {wert}")
    kompetenzen = KompetenzenDB(config.db_path)
    kompetenzen.extract_from_text("Python Java SQL Git Docker Linux", quelle="profil")
    gap_check = GapCheck(kompetenzen)
    gap = gap_check.finde_luecken(ANZEIGE)
    print(gap_check.gap_report(gap))
    print(f"   📊 Passung: {gap_check.passung(gap)} %")


def zeige_unterlagen(config: AppConfig) -> None:
    _titel("2️⃣  Unterlagen erzeugen (deterministisch, keine erfundenen Inhalte)")
    resume = ResumeBuilder(config).anpassen_fuer_stelle(ANZEIGE)
    cover = CoverLetterGenerator(config, None).generate(
        ANZEIGE,
        "Datenwerk Nord GmbH",
        "Junior Python-Entwickler",
        projekte=resume["projekte"],
        berufsrichtung=resume["berufsrichtung"],
    )
    print(f"   🎯 Berufsrichtung: {resume['berufsrichtung']}")
    print(f"   🧩 Projekte: {', '.join(p['name'] for p in resume['projekte'])}")
    print("\n   ✉️ Anschreiben:\n")
    print("\n".join(f"      {z}" for z in cover["text"].splitlines()))

    ordner = DEMO_DIR / "ausgabe" / ordnername("Datenwerk Nord GmbH")
    ordner.mkdir(parents=True)
    (ordner / "Lebenslauf.html").write_text(resume["html"], encoding="utf-8")
    try:
        builder = ResumeBuilder(config)
        builder.speichere_pdf(resume["html"], ordner / "Lebenslauf.pdf")
        builder.speichere_pdf(
            ANSCHREIBEN_HTML.format(text=html.escape(cover["text"])),
            ordner / "Anschreiben.pdf",
        )
        print(f"\n   📄 PDFs gespeichert in {ordner}")
    except (ImportError, OSError) as e:
        print(f"\n   ⚠️ Keine PDFs ({e}) – HTML liegt in {ordner}")


def fuelle_datenbank(config: AppConfig) -> None:
    db = BewerbungsDB(config.db_path)
    jetzt = datetime.now()
    for firma, position, tage, mail, nachgefasst, passung, telefon in BEWERBUNGEN:
        domain = ordnername(firma).lower().replace("_", "-")
        bid = db.insert_bewerbung(
            firma,
            position=position,
            agent_score=passung,
            berufsrichtung="softwareentwicklung",
            projekte=["tracker", "rest"],
            skills_gefordert=["Python", "SQL", "Docker", "Git"],
            empfaenger=f"jobs@{domain}.example" if mail else "",
            kanal="email" if mail else "portal",
            telefon=telefon,
            ansprechpartner="Frau Lea Beispiel" if telefon else "",
        )
        with verbinde(config.db_path) as conn:  # Bewerbung in die Vergangenheit legen
            conn.execute(
                "UPDATE bewerbungen SET datum_gesendet = ? WHERE id = ?",
                ((jetzt - timedelta(days=tage)).isoformat(), bid),
            )
        if nachgefasst:
            datum = (jetzt - timedelta(days=nachgefasst)).date().isoformat()
            db.markiere_nachgefasst(bid, datum)


def zeige_postfach(config: AppConfig) -> None:
    _titel("3️⃣  Postfach: Rückmeldungen zuordnen und daraus lernen")
    reader = ImapReader(config, client=None)  # ohne KI: Keyword-Regeln
    for message_id, betreff, absender, text in MAILS:
        print(f"\n   📨 Von {absender}: „{text[:60]}…“")
        reader.verarbeite_mail(message_id, betreff, absender, text)


def main() -> None:
    print("🎬 Demo mit erfundenen Daten (Ordner: demo/)")
    config = lege_daten_an()
    zeige_erkennung(config)
    zeige_unterlagen(config)
    fuelle_datenbank(config)
    zeige_postfach(config)

    _titel("4️⃣  Was ist fällig? (python -m src.main offen)")
    print(uebersicht(config))

    anrufen = Anrufen(config, TerminalDialog())
    kandidaten = anrufen.kandidaten()
    if kandidaten:
        _titel("5️⃣  Anruf-Spickzettel (python -m src.main anrufen)")
        print(anrufen.spickzettel(kandidaten[0]))

    _titel("6️⃣  Report (python -m src.main report)")
    print(Analytics(config).bericht())


if __name__ == "__main__":
    main()
