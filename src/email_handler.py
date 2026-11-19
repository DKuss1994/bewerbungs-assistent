import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class EmailHandler:
    def __init__(self):
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.web.de")
        self.smtp_port = int(os.getenv("SMTP_PORT", 587))
        self.sender = os.getenv("EMAIL_ABSENDER")
        self.password = os.getenv("EMAIL_PASSWORT")

    def send_application(
        self,
        empfaenger: str,
        betreff: str,
        text: str,
        anhaenge: list,
        test_mode: bool = False,
    ) -> bool:
        """Sendet eine Bewerbungs-E-Mail mit Anhängen."""

        # Test-Modus: Nur Ausgabe, kein Versand
        if test_mode:
            print("\n🧪 TEST-MODUS AKTIV – E-Mail wird NICHT versendet.")
            print(f"   Empfänger: {empfaenger}")
            print(f"   Betreff: {betreff}")
            print(f"   Anhänge: {[str(p) for p in anhaenge]}")
            print("   Text:")
            print(text[:200] + "..." if len(text) > 200 else text)
            return True

        # Echter Versand
        if not self.sender or not self.password:
            print("❌ E-Mail-Zugangsdaten nicht konfiguriert!")
            print("Bitte .env-Datei mit EMAIL_ABSENDER und EMAIL_PASSWORT anlegen.")
            return False

        msg = self.baue_nachricht(empfaenger, betreff, text, anhaenge)
        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender, self.password)
                server.send_message(msg)
            print(f"✅ E-Mail an {empfaenger} gesendet!")
            return True
        except Exception as e:
            print(f"❌ Fehler beim Senden: {e}")
            return False

    def baue_nachricht(
        self, empfaenger: str, betreff: str, text: str, anhaenge: list
    ) -> MIMEMultipart:
        msg = MIMEMultipart()
        msg["From"] = self.sender
        msg["To"] = empfaenger
        msg["Subject"] = betreff
        # Ohne Datum und Message-ID werten viele Spamfilter die Mail ab
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(
            domain=(self.sender or "").partition("@")[2] or None
        )
        msg.attach(MIMEText(text, "plain", "utf-8"))

        for pfad in map(Path, anhaenge):
            if not pfad.exists():
                print(f"⚠️ Anhang nicht gefunden: {pfad}")
                continue
            typ = "pdf" if pfad.suffix.lower() == ".pdf" else "octet-stream"
            part = MIMEApplication(pfad.read_bytes(), _subtype=typ)
            # filename als Parameter: Umlaute ("Müller.pdf") werden korrekt kodiert
            part.add_header("Content-Disposition", "attachment", filename=pfad.name)
            msg.attach(part)
        return msg
