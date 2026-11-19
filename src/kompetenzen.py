import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

import pdfplumber

from src.database import verbinde

# Kuratierte Liste von IT-Kompetenzen, die wir in Texten suchen
SKILL_KEYWORDS = {
    # Programmierung
    "Java": ["java"],
    "Python": ["python"],
    "JavaScript": ["javascript", "js"],
    "TypeScript": ["typescript"],
    "C#": ["c#", "csharp"],
    "C++": ["c++"],
    "PHP": ["php"],
    "Go": ["golang"],
    "Rust": ["rust"],
    # Web
    "HTML": ["html"],
    "CSS": ["css"],
    "React": ["react"],
    "Angular": ["angular"],
    "Vue": ["vue"],
    "Node.js": ["node.js", "nodejs"],
    "Django": ["django"],
    "Flask": ["flask"],
    "Spring Boot": ["spring boot", "spring-boot"],
    # Datenbanken
    "SQL": ["sql"],
    "MySQL": ["mysql"],
    "PostgreSQL": ["postgresql", "postgres"],
    "MongoDB": ["mongodb"],
    "SQLite": ["sqlite"],
    "Oracle": ["oracle db"],
    # DevOps / Cloud
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Jenkins": ["jenkins"],
    "GitLab": ["gitlab"],
    "GitHub Actions": ["github actions"],
    "CI/CD": ["ci/cd", "continuous integration"],
    "Ansible": ["ansible"],
    "Terraform": ["terraform"],
    "AWS": ["aws", "amazon web services"],
    "Azure": ["azure"],
    "Google Cloud": ["google cloud", "gcp"],
    # Betriebssysteme
    "Linux": ["linux"],
    "Ubuntu": ["ubuntu"],
    "Debian": ["debian"],
    "Red Hat": ["red hat", "rhel"],
    "Windows": ["windows"],
    "Windows Server": ["windows server"],
    "macOS": ["macos", "mac os"],
    # Netzwerk
    "TCP/IP": ["tcp/ip"],
    "DNS": ["dns"],
    "DHCP": ["dhcp"],
    "VPN": ["vpn"],
    "Firewall": ["firewall"],
    "VLAN": ["vlan"],
    "Netzwerk": ["netzwerk", "network"],
    # Tools
    "Git": ["git"],
    "Maven": ["maven"],
    "Gradle": ["gradle"],
    "Jira": ["jira"],
    "Confluence": ["confluence"],
    "IntelliJ": ["intellij"],
    "VS Code": ["vs code", "vscode"],
    "Eclipse": ["eclipse"],
    # IT-Support / Office
    "Office365": ["office365", "office 365", "microsoft 365", "ms 365"],
    "MS Teams": ["ms teams", "microsoft teams"],
    "Active Directory": ["active directory"],
    "Helpdesk": ["helpdesk", "help desk"],
    "Ticketsystem": ["ticketsystem", "remedy", "sdp"],
    "Monitoring": ["monitoring", "checkmk", "grafana", "prometheus", "solarwinds"],
    "Virtualisierung": ["vmware", "hyper-v", "kvm", "virtualisierung"],
    "Backup": ["veeam", "commvault", "bacula", "backup"],
    # Security
    "EDR": ["edr", "endpoint detection"],
    "SIEM": ["siem", "splunk", "elastic stack"],
    "IT-Security": ["it-security", "it-sicherheit"],
    # Methoden
    "Scrum": ["scrum"],
    "Kanban": ["kanban"],
    "Agile": ["agile", "agil"],
    "TDD": ["tdd", "test-driven"],
    "DevOps": ["devops"],
    # Sprachen
    "PowerShell": ["powershell"],
    "Bash": ["bash", "shell"],
}


def kommt_vor(keyword: str, text_lower: str) -> bool:
    """Prüft, ob ein Keyword im (kleingeschriebenen) Text vorkommt.

    Kurze Keywords (≤ 4 Zeichen) müssen als eigenes Wort stehen, damit z.B.
    "git" nicht in "Digitalisierung" oder "js" nicht in "json" gefunden wird.
    Längere Keywords dürfen Teil eines Wortes sein ("Linux-Kenntnisse").
    """
    keyword = keyword.lower()
    if len(keyword) > 4:
        return keyword in text_lower
    muster = rf"(?<![a-z0-9äöüß]){re.escape(keyword)}(?![a-z0-9äöüß])"
    return re.search(muster, text_lower) is not None


def finde_skills(text: str) -> List[str]:
    """Gibt alle Skills aus SKILL_KEYWORDS zurück, die im Text vorkommen."""
    text_lower = text.lower()
    return [
        skill
        for skill, keywords in SKILL_KEYWORDS.items()
        if any(kommt_vor(kw, text_lower) for kw in keywords)
    ]


class KompetenzenDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_table()

    def _init_table(self):
        with verbinde(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kompetenzen (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_name TEXT NOT NULL UNIQUE,
                    level TEXT,
                    quelle TEXT,
                    kontext TEXT,
                    datum_erfasst TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def add_kompetenz(
        self, skill_name: str, level: str, quelle: str, kontext: str = ""
    ):
        """Fügt eine Kompetenz hinzu oder aktualisiert sie."""
        with verbinde(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO kompetenzen (skill_name, level, quelle, kontext)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(skill_name) DO UPDATE SET
                    level = excluded.level,
                    quelle = excluded.quelle,
                    kontext = excluded.kontext,
                    datum_erfasst = CURRENT_TIMESTAMP
            """,
                (skill_name, level, quelle, kontext),
            )
            conn.commit()

    def get_kompetenz(self, skill_name: str) -> Optional[Dict]:
        with verbinde(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM kompetenzen WHERE skill_name = ?", (skill_name,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_kompetenzen(self) -> List[Dict]:
        with verbinde(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM kompetenzen ORDER BY skill_name")
            return [dict(row) for row in cursor.fetchall()]

    def hat_kompetenz(self, skill_name: str) -> Optional[str]:
        """Gibt das Level zurück ('ja', 'teilweise', 'nein', 'lernend') oder None."""
        komp = self.get_kompetenz(skill_name)
        return komp["level"] if komp else None

    def extract_from_text(self, text: str, quelle: str) -> List[str]:
        """Sucht bekannte Skills im Text und speichert sie (level='ja')."""
        gefunden = []
        for skill in finde_skills(text):
            if not self.hat_kompetenz(skill):
                self.add_kompetenz(skill, "ja", quelle)
                gefunden.append(skill)
        return gefunden

    def extract_from_html(self, html_path: Path) -> List[str]:
        """Liest HTML und extrahiert Skills."""
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        # HTML-Tags entfernen
        text = re.sub(r"<[^>]+>", " ", html)
        return self.extract_from_text(text, quelle="lebenslauf")

    def extract_from_pdf(self, pdf_path: Path) -> List[str]:
        """Liest PDF und extrahiert Skills."""
        text = ""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    seite = page.extract_text()
                    if seite:
                        text += seite + "\n"
        except Exception as e:
            print(f"⚠️ Fehler beim Lesen von {pdf_path.name}: {e}")
            return []
        return self.extract_from_text(text, quelle=f"zertifikat:{pdf_path.name}")

    def print_uebersicht(self):
        """Zeigt alle bekannten Kompetenzen."""
        alle = self.get_all_kompetenzen()
        if not alle:
            print("📭 Noch keine Kompetenzen erfasst.")
            return

        print(f"\n📚 Bekannte Kompetenzen ({len(alle)}):")
        print("=" * 60)
        for k in alle:
            level_icon = {
                "ja": "✅",
                "teilweise": "🟡",
                "nein": "❌",
                "lernend": "📖",
            }.get(k["level"], "❓")
            print(f"  {level_icon} {k['skill_name']:30} [{k['level']}] ({k['quelle']})")
