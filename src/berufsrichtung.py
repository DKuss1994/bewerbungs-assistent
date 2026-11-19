"""Erkennt die Berufsrichtung aus einer Stellenanzeige."""

from typing import Dict

from src.kompetenzen import kommt_vor

# Keywords pro Berufsrichtung
BERUFSRICHTUNG_KEYWORDS: Dict[str, list] = {
    "itsupport": [
        "support",
        "helpdesk",
        "help desk",
        "1st level",
        "2nd level",
        "first level",
        "second level",
        "ticketsystem",
        "remedy",
        "sdp",
        "kundenanfragen",
        "störung",
        "störungen",
        "troubleshooting",
        "systemadministration",
        "systemadministrator",
        "administrator",
        "netzwerk",
        "dns",
        "dhcp",
        "vpn",
        "firewall",
        "windows server",
        "linux",
        "ubuntu",
        "rhel",
        "red hat",
        "vmware",
        "hyper-v",
        "kvm",
        "virtualisierung",
        "monitoring",
        "checkmk",
        "grafana",
        "prometheus",
        "solarwinds",
        "backup",
        "veeam",
        "commvault",
        "bacula",
        "active directory",
        "office365",
        "ms teams",
        "microsoft 365",
        "powershell",
        "bash",
        "ansible",
        "puppet",
        "chef",
        "edr",
        "siem",
        "splunk",
        "elastic",
    ],
    "softwareentwicklung": [
        "softwareentwickler",
        "software developer",
        "software engineer",
        "entwickler",
        "developer",
        "programmierer",
        "backend",
        "frontend",
        "fullstack",
        "full-stack",
        "java",
        "python",
        "javascript",
        "typescript",
        "c#",
        "c++",
        "spring boot",
        "spring",
        "django",
        "flask",
        "react",
        "angular",
        "rest api",
        "rest-api",
        "restful",
        "agile entwicklung",
        "scrum team",
        "unit-tests",
        "tdd",
        "clean code",
    ],
}


class Berufsrichtung:
    """Erkennt die Berufsrichtung anhand von Keyword-Treffern."""

    def __init__(self):
        self.richtungen = list(BERUFSRICHTUNG_KEYWORDS.keys())

    def _zaehle_treffer(self, text: str) -> Dict[str, int]:
        """Zählt Keyword-Treffer pro Berufsrichtung."""
        text_lower = text.lower()
        return {
            richtung: sum(1 for kw in keywords if kommt_vor(kw, text_lower))
            for richtung, keywords in BERUFSRICHTUNG_KEYWORDS.items()
        }

    def erkenne(self, stellen_text: str) -> str:
        """Erkennt die Berufsrichtung anhand von Keywords."""
        punkte = self._zaehle_treffer(stellen_text)

        support = punkte.get("itsupport", 0)
        entwicklung = punkte.get("softwareentwicklung", 0)

        # Support deutlich stärker
        if support > entwicklung * 1.3:
            return "itsupport"

        # Entwicklung stärker
        if entwicklung > support:
            return "softwareentwicklung"

        # Support hat Treffer, Entwicklung nicht
        if support > 0 and entwicklung == 0:
            return "itsupport"

        # Default
        return "softwareentwicklung"

    def print_analyse(self, stellen_text: str):
        """Zeigt eine Übersicht der Keyword-Treffer."""
        text_lower = stellen_text.lower()
        print("\n🔎 Berufsrichtungs-Analyse:")
        for richtung, keywords in BERUFSRICHTUNG_KEYWORDS.items():
            treffer = [kw for kw in keywords if kommt_vor(kw, text_lower)]
            print(f"   {richtung}: {len(treffer)} Treffer")
            if treffer:
                print(f"      → {', '.join(treffer[:8])}")
        print(f"   ➡️  Erkannt: {self.erkenne(stellen_text)}")
