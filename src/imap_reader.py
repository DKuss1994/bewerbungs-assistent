import email
import imaplib
import os
from datetime import datetime, timedelta
from email.header import decode_header
from email.policy import default
from typing import Callable, Dict, List, Optional

from dotenv import load_dotenv

from src.config import AppConfig
from src.database import BewerbungsDB
from src.learning import LERN_REGELN, LearningSystem
from src.llm_client import LLMClient

load_dotenv()

ABSAGE_GRUENDE = [
    "fehlende_erfahrung",
    "tech_stack",
    "nicht_qualifiziert",
    "ueberqualifiziert",
    "gehalt",
    "team_fit",
    "zu_viele_bewerber",
    "sonstiges",
]

# Ohne KI: ein Grund zählt nur, wenn er ausdrücklich dasteht. Einzelwörter wie
# "Profil", "Erfahrung" oder "Team" stehen in fast jeder Standard-Absage.
GRUND_PHRASEN = {
    "ueberqualifiziert": ("überqualifiziert",),
    "fehlende_erfahrung": (
        "mehr berufserfahrung",
        "mehr erfahrung",
        "mehr praktische erfahrung",
        "fehlende erfahrung",
        "mangelnde erfahrung",
        "nicht genügend erfahrung",
        "nicht ausreichend erfahrung",
        "erfahrenere",
    ),
    "tech_stack": (
        "fehlende kenntnisse",
        "fehlenden kenntnisse",
        "nicht die geforderten kenntnisse",
    ),
    "nicht_qualifiziert": (
        "nicht dem anforderungsprofil",
        "nicht den anforderungen",
    ),
    "gehalt": ("gehaltsvorstellung", "vergütungsvorstellung"),
    "zu_viele_bewerber": (
        "vielzahl an bewerbungen",
        "vielzahl von bewerbungen",
        "zahlreiche bewerbungen",
        "sehr viele bewerbungen",
    ),
}

# IMAP-Suche: alles, was nach Rückmeldung auf eine Bewerbung aussieht
IMAP_SUCHE = (
    '(OR OR OR SUBJECT "Bewerbung" SUBJECT "Absage" '
    'SUBJECT "Einladung" SUBJECT "Interview")'
)


class ImapReader:
    def __init__(
        self,
        config: AppConfig,
        client: Optional[LLMClient] = None,
        melde: Callable[[str], None] = print,
    ):
        """melde: wohin Meldungen gehen – Terminal (print) oder Chat."""
        self.config = config
        self.melde = melde
        self.client = client
        self.db = BewerbungsDB(config.db_path)
        self.learning = LearningSystem(config.db_path)

        self.imap_server = os.getenv("IMAP_SERVER", "imap.web.de")
        self.imap_port = int(os.getenv("IMAP_PORT", 993))
        self.username = os.getenv("IMAP_USER", "")
        self.password = os.getenv("IMAP_PASSWORD", "")

    def check_postfach(self, tage: int = 60) -> List[Dict]:
        """Liest Rückmeldungen der letzten `tage` Tage und verarbeitet sie."""
        if not self.username or not self.password:
            self.melde("❌ IMAP_USER/IMAP_PASSWORD nicht in .env gesetzt.")
            return []

        seit = (datetime.now() - timedelta(days=tage)).strftime("%d-%b-%Y")
        ergebnisse = []
        try:
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.username, self.password)
            mail.select("INBOX", readonly=True)
            status, ids = mail.search(None, f"(SINCE {seit} {IMAP_SUCHE})")

            for num in (ids[0].split() if status == "OK" and ids[0] else []):
                status, data = mail.fetch(num, "(RFC822)")
                if status != "OK":
                    continue
                msg = email.message_from_bytes(data[0][1], policy=default)
                ergebnis = self.verarbeite_mail(
                    message_id=msg.get("Message-ID", f"ohne-id-{num.decode()}"),
                    betreff=self._decode_header(msg.get("Subject", "")),
                    absender=self._decode_header(msg.get("From", "")),
                    body=self._get_body(msg),
                )
                if ergebnis:
                    ergebnisse.append(ergebnis)

            mail.logout()
        except Exception as e:
            self.melde(f"❌ Fehler beim IMAP-Reader: {e}")

        if not ergebnisse:
            self.melde("📭 Keine neuen Rückmeldungen gefunden.")
        return ergebnisse

    def verarbeite_mail(
        self, message_id: str, betreff: str, absender: str, body: str
    ) -> Optional[Dict]:
        """Ordnet eine Mail einer Bewerbung zu, erkennt Typ/Grund und lernt daraus."""
        if self.db.mail_verarbeitet(message_id):
            return None

        bewerbung = self.db.finde_bewerbung(absender, betreff, body[:2000])
        if not bewerbung:
            # Nicht markieren: evtl. passt sie nach einer späteren Bewerbung
            return None

        typ = self.erkenne_typ(f"{betreff}\n{body}")
        grund = self.kategorisiere_absage(body) if typ == "absage" else None

        if typ in ("absage", "einladung"):
            self.db.add_rueckmeldung(bewerbung["id"], typ, grund)
            regeln = {**LERN_REGELN, **self.config.profil.get("lern_regeln", {})}
            if grund in regeln:
                self.learning.add_learning("anschreiben", grund, regeln[grund])
        self.db.markiere_mail(message_id, bewerbung["id"])

        icon = {"absage": "❌", "einladung": "🎉"}.get(typ, "📨")
        self.melde(
            f"{icon} {bewerbung['unternehmen']} ({bewerbung['position']}): {typ}"
            + (f" – Grund: {grund}" if grund else "")
        )
        return {
            "bewerbung_id": bewerbung["id"],
            "unternehmen": bewerbung["unternehmen"],
            "typ": typ,
            "grund": grund,
            "betreff": betreff,
        }

    # ---------- KLASSIFIKATION (KI mit Keyword-Fallback) ----------
    def erkenne_typ(self, text: str) -> str:
        if self.client:
            typ = self.client.klassifiziere(
                text,
                "Ist diese E-Mail auf eine Bewerbung eine Absage, eine Einladung "
                "(zum Gespräch/Interview) oder etwas anderes (z.B. Eingangsbestätigung)?",
                ["absage", "einladung", "sonstiges"],
            )
            if typ:
                return typ
        return self._typ_per_keywords(text)

    def kategorisiere_absage(self, text: str) -> str:
        if self.client:
            grund = self.client.klassifiziere(
                text,
                "Aus welchem Grund wurde diese Bewerbung abgelehnt? Wähle nur dann "
                "einen konkreten Grund, wenn er AUSDRÜCKLICH in der Mail steht. "
                "Allgemeine Floskeln wie „nach sorgfältiger Prüfung“ oder „für "
                "andere Kandidaten entschieden“ sind kein Grund → sonstiges.",
                ABSAGE_GRUENDE,
            )
            if grund:
                return grund
        return self._grund_per_keywords(text)

    @staticmethod
    def _typ_per_keywords(text: str) -> str:
        t = text.lower()
        if any(
            w in t
            for w in (
                "leider",
                "absage",
                "nicht berücksichtigen",
                "anderen kandidaten",
                "unfortunately",
            )
        ):
            return "absage"
        if any(
            w in t
            for w in (
                "einladung",
                "vorstellungsgespräch",
                "interview",
                "kennenlernen",
                "termin",
            )
        ):
            return "einladung"
        return "sonstiges"

    @staticmethod
    def _grund_per_keywords(text: str) -> str:
        t = " ".join(text.lower().split())  # Zeilenumbrüche mitten im Satz
        for grund, phrasen in GRUND_PHRASEN.items():
            if any(p in t for p in phrasen):
                return grund
        return "sonstiges"

    # ---------- MAIL-PARSING ----------
    def _decode_header(self, header) -> str:
        if not header:
            return ""
        teile = []
        for part, encoding in decode_header(str(header)):
            if isinstance(part, bytes):
                try:
                    teile.append(part.decode(encoding or "utf-8", errors="replace"))
                except LookupError:
                    teile.append(part.decode("utf-8", errors="replace"))
            else:
                teile.append(str(part))
        return " ".join(teile)

    def _get_body(self, msg) -> str:
        """Extrahiert den Text-Teil der E-Mail."""
        teil = msg.get_body(preferencelist=("plain", "html"))
        if teil is None:
            return ""
        try:
            return teil.get_content()
        except (LookupError, UnicodeDecodeError):
            payload = teil.get_payload(decode=True) or b""
            return payload.decode("utf-8", errors="replace")
