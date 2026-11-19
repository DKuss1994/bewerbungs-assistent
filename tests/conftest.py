import json

import pytest

from src.config import AppConfig

TEMPLATE_HTML = """<html><body>
<div><!-- START_SUBTITLE -->alt<!-- END_SUBTITLE --></div>
<div><!-- START_PROFIL -->alt<!-- END_PROFIL --></div>
<div><!-- START_KERNKOMPETENZEN -->alt<!-- END_KERNKOMPETENZEN --></div>
<div><!-- START_SKILLS -->alt<!-- END_SKILLS --></div>
<div><!-- START_PROJECTS -->alt<!-- END_PROJECTS --></div>
</body></html>"""

PROJEKTE = [
    {
        "id": "backend",
        "name": "BackendApp",
        "tech": "Java, Spring Boot",
        "beschreibung": "REST-API mit Tests.",
        "link": "",
        "tags": ["java", "spring", "backend", "api"],
    },
    {
        "id": "linux",
        "name": "LinuxConfig",
        "tech": "Linux, Bash",
        "beschreibung": "Eigene Linux-Konfiguration.",
        "link": "https://example.org",
        "tags": ["linux", "bash"],
    },
    {
        "id": "app",
        "name": "MobileApp",
        "tech": "Flutter",
        "beschreibung": "App-Prototyp.",
        "link": "",
        "tags": ["mobile", "flutter"],
    },
]

PROFILTEXTE = {
    "softwareentwicklung": {
        "subtitle": "Junior Developer",
        "profil": "Profil Entwicklung",
        "kernkompetenzen": ["Java"],
    },
    "itsupport": {
        "subtitle": "IT-Support",
        "profil": "Profil Support",
        "kernkompetenzen": ["Linux"],
    },
}

PROFIL = {
    "name": "Max Muster",
    "github": "https://github.com/max",
    "lebenslauf_html": "lebenslauf.html",
    "anschreiben_vorlage": "anschreiben_vorlage.txt",
    "email": {
        "betreff": "Bewerbung als {position} - {name}",
        "text": "Hallo {unternehmen}, {name}",
    },
    "skills": {
        "Programmierung": ["Java 17+", "Python", "SQL"],
        "Betriebssysteme": ["Linux (Ubuntu)", "Windows-Grundlagen"],
        "Netzwerk": [{"name": "Netzwerk (DNS, DHCP)", "keywords": ["dns", "dhcp"]}],
    },
}

VORLAGE = "Anschreiben {position} bei {unternehmen}\n\n{projekte_abschnitt}\n\n{name}"


@pytest.fixture
def config(tmp_path):
    data = tmp_path / "data"
    (data / "lebenslauf").mkdir(parents=True)
    (data / "lebenslauf" / "lebenslauf.html").write_text(
        TEMPLATE_HTML, encoding="utf-8"
    )
    (data / "projekte.json").write_text(json.dumps(PROJEKTE), encoding="utf-8")
    (data / "profiltexte.json").write_text(json.dumps(PROFILTEXTE), encoding="utf-8")
    (data / "profil.json").write_text(json.dumps(PROFIL), encoding="utf-8")
    (data / "anschreiben_vorlage.txt").write_text(VORLAGE, encoding="utf-8")
    return AppConfig(base_dir=tmp_path, llm_provider="ollama")


class FakeLLM:
    """Ersetzt die KI in Tests: gibt feste Antworten zurück oder wirft einen Fehler."""

    def __init__(self, antwort="", fehler=None):
        self.antwort = antwort
        self.fehler = fehler
        self.prompts = []

    def generate(self, prompt, temperature=0.3, timeout=180):
        self.prompts.append(prompt)
        if self.fehler:
            raise self.fehler
        return self.antwort

    def klassifiziere(self, text, frage, optionen):
        if self.fehler:
            return None
        return self.antwort if self.antwort in optionen else None


class FakeDialog:
    """Ersetzt Terminal/Chat in Tests: feste Antworten, merkt sich alle Ausgaben."""

    def __init__(self, *antworten):
        self.antworten = list(antworten)
        self.ausgaben = []
        self.fragen = []
        self.geoeffnet = []

    def zeige(self, text):
        self.ausgaben.append(text)

    def frage(self, text, optionen=None):
        self.fragen.append((text, optionen))
        return self.antworten.pop(0) if self.antworten else None

    def zeige_dateien(self, pfade):
        self.geoeffnet += list(pfade)

    @property
    def text(self):
        return "\n".join(self.ausgaben)
