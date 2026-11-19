#!/usr/bin/env python
"""Hauptprogramm des Bewerbungs-Agenten."""

import html
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.analytics import Analytics
from src.anrufen import Anrufen
from src.config import AppConfig
from src.cooldown import CooldownManager
from src.cover_letter import CoverLetterGenerator
from src.database import BewerbungsDB
from src.dialog import Dialog, TerminalDialog
from src.email_handler import EmailHandler
from src.gap_check import GapCheck
from src.imap_reader import ImapReader
from src.kompetenz_frage import KompetenzFrage
from src.kompetenzen import KompetenzenDB
from src.llm_client import LLMClient
from src.nachfassen import Nachfassen
from src.resume_builder import ResumeBuilder, skill_name
from src.stellen_info import (
    EMAIL,
    StellenInfo,
    erkenne_stelle,
    finde_ansprechpartner,
    finde_telefon,
)
from src.stellen_link import URL, lies_anzeige
from src.uebersicht import alle_bewerbungen, schliesse_ohne_antwort, uebersicht

NUTZUNG = """❌ Nutzung:
  python -m src.main <stelle.txt|link>  Bewerbung – Firma, Position, E-Mail werden erkannt (mit Vorschau + Bestätigung)
  python -m src.main <stelle.txt|link> <unternehmen> <position> <empfaenger>   dasselbe, alles von Hand
  python -m src.main postfach      Rückmeldungen (Absagen/Einladungen) einlesen und daraus lernen
  python -m src.main report        Statistik und Gelerntes anzeigen
  python -m src.main cooldown      Re-Bewerbungen prüfen
  python -m src.main nachfassen    bei Firmen ohne Antwort nachfragen (mit Vorschau + Bestätigung)
  python -m src.main anrufen       Anruf-Erinnerungen mit Spickzettel (nach Nachfass-Mail ohne Antwort)
  python -m src.main offen         Übersicht: was läuft, was ist fällig
  python -m src.main alle          Alle Bewerbungen auf einen Blick
  python -m src.main kompetenzen   Bekannte Kompetenzen anzeigen
  python -m src.main stellen       Neue Stellen suchen und zum Bewerben vorschlagen (data/kriterien.json)

Beispiel: python -m src.main Stellenanzeigen/firma.txt"""


# DIN-A4-Brief: 2 cm Rand, 11 pt – ein Anschreiben soll auf eine Seite passen
ANSCHREIBEN_HTML = (
    "<style>@page {{ size: A4; margin: 20mm 22mm; }}</style>"
    "<pre style='font-family: sans-serif; font-size: 11pt; line-height: 1.35; "
    "white-space: pre-wrap; margin: 0;'>{text}</pre>"
)


def erstelle_llm_client(config: AppConfig) -> Optional[LLMClient]:
    """KI-Client, falls erreichbar – sonst None (dann greifen die Fallbacks)."""
    try:
        client = LLMClient.from_config(config)
    except ValueError as e:
        print(f"⚠️ {e} – arbeite ohne KI.")
        return None
    if not client.ist_verfuegbar():
        print("⚠️ KI nicht erreichbar (läuft 'ollama serve'?) – arbeite ohne KI.")
        return None
    return client


def frage_email(dialog: Dialog, frage: str) -> Optional[str]:
    """Fragt so lange, bis eine gültige Adresse kommt – Tippfehler fallen
    so vor dem Versand auf. "" = keine Adresse, None = Abbruch."""
    while True:
        antwort = dialog.frage(frage)
        if not antwort:
            return antwort
        if re.fullmatch(EMAIL, antwort.strip()):
            return antwort.strip()
        dialog.zeige(
            f"❌ „{antwort}“ ist keine gültige E-Mail-Adresse – bitte nochmal."
        )


def frage_stellendaten(
    info: StellenInfo, mit_email: bool = True, dialog: Optional[Dialog] = None
) -> Optional[StellenInfo]:
    """Zeigt die erkannten Daten; leere Antwort übernimmt, Eingabe überschreibt.
    Leere E-Mail = Bewerbung über ein Portal. None, wenn Firma/Position fehlen."""
    dialog = dialog or TerminalDialog()
    dialog.zeige("\n🔎 Aus der Anzeige erkannt (Enter = übernehmen):")
    werte = {}
    for attr, label in [("unternehmen", "Firma"), ("position", "Position")]:
        vorschlag = getattr(info, attr) or ""
        antwort = dialog.frage(
            f"   {label} [{vorschlag}]: " if vorschlag else f"   {label} ❓: "
        )
        if antwort is None:
            return None
        werte[attr] = antwort or vorschlag
        if not werte[attr]:
            dialog.zeige(f"❌ {label} fehlt – Abbruch.")
            return None

    if mit_email:
        if info.email:
            frage = f"   E-Mail [{info.email}]: "
        else:
            frage = "   E-Mail ❓ – Adresse eintippen (leer = Bewerbung über Portal): "
        antwort = frage_email(dialog, frage)
        if antwort is None:
            return None
        werte["email"] = antwort or info.email or None
    return StellenInfo(**{**vars(info), **werte})


def ordnername(unternehmen: str) -> str:
    """Firmenname als Ordner-/Dateiname: ohne Leerzeichen und Sonderzeichen,
    damit man im Terminal ohne Anführungszeichen hineinkommt ("ABC GmbH" → "ABC_GmbH").
    """
    return re.sub(r"[^\w.\-]+", "_", unternehmen).strip("_")


def sammle_anlagen(config: AppConfig) -> List[Path]:
    """Zeugnisse + Zertifikate – jede Datei nur einmal, auch wenn beide
    Ordner (z.B. per Symlink) auf dasselbe Verzeichnis zeigen."""
    gesehen, anlagen = set(), []
    for ordner in (config.zeugnisse_ordner, config.zertifikate_ordner):
        for pfad in sorted(ordner.glob("*.pdf")):
            if pfad.resolve() not in gesehen:
                gesehen.add(pfad.resolve())
                anlagen.append(pfad)
    return anlagen


class BewerbungsAgent:
    """Orchestriert den gesamten Bewerbungsprozess.

    Alle Fragen und Meldungen laufen über `dialog` (Terminal oder Chat-Bot)."""

    def __init__(
        self, config: Optional[AppConfig] = None, dialog: Optional[Dialog] = None
    ):
        self.config = config or AppConfig()
        self.dialog = dialog or TerminalDialog()
        self.client = erstelle_llm_client(self.config)
        self.db = BewerbungsDB(self.config.db_path)
        self.kompetenzen = KompetenzenDB(self.config.db_path)
        self.resume_builder = ResumeBuilder(self.config)
        text_client = (
            LLMClient.from_config(self.config, text=True) if self.client else None
        )
        self.cover_letter = CoverLetterGenerator(self.config, text_client)
        self.email = EmailHandler()
        self.gap_check = GapCheck(self.kompetenzen)
        self.kompetenz_frage = KompetenzFrage(self.kompetenzen)
        self._profil_skills_eintragen()

    def _profil_skills_eintragen(self):
        """Skills aus profil.json gelten als bekannt (kein erneutes Nachfragen)."""
        text = " ".join(
            skill_name(e)
            for eintraege in self.config.profil["skills"].values()
            for e in eintraege
        )
        self.kompetenzen.extract_from_text(text, quelle="profil")

    # ---------- UNTERLAGEN ----------
    def erstelle_unterlagen(
        self, stellen_text: str, unternehmen: str, position: str, ausgabe_ordner: Path
    ) -> Dict:
        """Erzeugt Lebenslauf + Anschreiben als PDF (ohne Versand, ohne DB)."""
        self.dialog.zeige("   📄 Passe Lebenslauf an...")
        resume = self.resume_builder.anpassen_fuer_stelle(stellen_text)

        self.dialog.zeige("   📝 Erstelle Anschreiben...")
        cover = self.cover_letter.generate(
            stellen_text,
            unternehmen,
            position,
            projekte=resume["projekte"],
            berufsrichtung=resume["berufsrichtung"],
        )

        ausgabe_ordner.mkdir(parents=True, exist_ok=True)
        resume_path = ausgabe_ordner / f"Lebenslauf_{ordnername(unternehmen)}.pdf"
        cover_path = ausgabe_ordner / f"Anschreiben_{ordnername(unternehmen)}.pdf"
        pdfs = {
            resume_path: resume["html"],
            cover_path: ANSCHREIBEN_HTML.format(text=html.escape(cover["text"])),
        }
        for pfad, inhalt in pdfs.items():
            seiten = self.resume_builder.speichere_pdf(inhalt, pfad)
            if seiten > 1:
                self.dialog.zeige(
                    f"   ⚠️ {pfad.name} hat {seiten} Seiten statt 1 – "
                    "Inhalt passt nicht, bitte PDF prüfen!"
                )
        (ausgabe_ordner / "Anschreiben.txt").write_text(cover["text"], encoding="utf-8")

        return {
            "resume": resume,
            "cover": cover,
            "resume_path": resume_path,
            "cover_path": cover_path,
        }

    # ---------- BEWERBEN ----------
    def bewerben(
        self,
        stellen_text: str,
        unternehmen: str,
        position: str,
        empfaenger: Optional[str],
        link: Optional[str] = None,
    ) -> Optional[int]:
        """Komplette Bewerbung: Prüfen → Unterlagen → Vorschau → Bestätigung → Senden.
        Ohne Empfänger: Bewerbung über ein Portal – alle Dateien kommen in einen
        Ordner zum Hochladen, gespeichert wird nach deiner Bestätigung.
        link: Adresse der Anzeige, landet für spätere Nachfragen in der DB."""
        portal = not empfaenger
        self.dialog.zeige(f"\n🚀 Bewerbung für {position} bei {unternehmen}")

        # 1. Duplikat-Prüfung
        if self.db.hat_doppelte_bewerbung(unternehmen, position, tage=30):
            self.dialog.zeige(
                f"⚠️ Du hast dich in den letzten 30 Tagen bereits auf {position} "
                f"bei {unternehmen} beworben – Abbruch."
            )
            return None
        frueher = self.db.letzte_bewerbung_bei(unternehmen)
        if frueher and not self._trotz_frueherer_bewerbung(frueher):
            self.dialog.zeige("⏸️ Abgebrochen.")
            return None

        # 2. Kompetenz-Check + Nachfragen (die DB lernt deine Skills dazu)
        gap = self.gap_check.finde_luecken(stellen_text)
        self.dialog.zeige(self.gap_check.gap_report(gap))
        if gap["unbekannt"]:
            self.kompetenz_frage.frage_nach(gap["unbekannt"], self.dialog)
            gap = self.gap_check.finde_luecken(stellen_text)  # mit deinen Antworten
        passung = self.gap_check.passung(gap)

        # 3. Unterlagen erstellen
        ordner = self.config.bewerbungen_ordner / ordnername(unternehmen)
        unterlagen = self.erstelle_unterlagen(
            stellen_text, unternehmen, position, ordner
        )
        # Die Anzeige verschwindet oft aus dem Netz – für Gespräch und Anruf aufheben
        (ordner / "Stellenanzeige.txt").write_text(stellen_text, encoding="utf-8")
        kontakt = {
            "ansprechpartner": finde_ansprechpartner(stellen_text) or "",
            "telefon": finde_telefon(stellen_text) or "",
        }

        # 4. Vorschau + Bestätigung
        profil = self.config.profil
        platzhalter = {
            "position": position,
            "unternehmen": unternehmen,
            "name": profil["name"],
        }
        betreff = profil["email"]["betreff"].format(**platzhalter)
        email_text = profil["email"]["text"].format(**platzhalter)
        anlagen = sammle_anlagen(self.config)
        if portal:
            # Alles in einen Ordner – so lädt man es im Portal in einem Rutsch hoch
            anlagen = [Path(shutil.copy2(a, ordner / a.name)) for a in anlagen]
        anhaenge = [unterlagen["resume_path"], unterlagen["cover_path"], *anlagen]

        self._zeige_vorschau(
            unterlagen, empfaenger, betreff, anhaenge, ordner, passung, kontakt
        )
        while (antwort := self._bestaetigt(unterlagen, ordner, portal)) == "m":
            # Portal → doch per E-Mail: Adresse nachtragen, neue Vorschau
            neu = frage_email(
                self.dialog, "\n📧 An welche E-Mail-Adresse? (leer = zurück): "
            )
            if neu is None:
                antwort = "n"
                break
            if neu:
                empfaenger, portal = neu, False
                self._zeige_vorschau(
                    unterlagen, empfaenger, betreff, anhaenge, ordner, passung, kontakt
                )
        if antwort != "j":
            self.dialog.zeige(f"\n⏸️ Nicht gespeichert. Unterlagen liegen in: {ordner}")
            return None

        # 5. Senden – nur bei Erfolg in die Datenbank
        if not portal and not self.email.send_application(
            empfaenger, betreff, email_text, anhaenge
        ):
            self.dialog.zeige(
                f"❌ Versand fehlgeschlagen. Unterlagen liegen in: {ordner}"
            )
            return None

        bewerbung_id = self.db.insert_bewerbung(
            unternehmen=unternehmen,
            position=position,
            tech_stack=", ".join(gap["gefordert"]) or "Unbekannt",
            agent_score=passung,
            berufsrichtung=unterlagen["resume"]["berufsrichtung"],
            projekte=[p["id"] for p in unterlagen["resume"]["projekte"]],
            skills_gefordert=gap["gefordert"],
            empfaenger=empfaenger or "",
            kanal="portal" if portal else "email",
            previous_id=frueher["id"] if frueher else None,
            link=link or "",
            **kontakt,
        )
        self.dialog.zeige(f"✅ Bewerbung gespeichert (ID: {bewerbung_id})")
        return bewerbung_id

    def _trotz_frueherer_bewerbung(self, frueher: Dict) -> bool:
        """Zeigt die letzte Bewerbung bei derselben Firma. Läuft nach einer
        Absage noch der Cooldown, wird nachgefragt. False = abbrechen."""
        datum = datetime.fromisoformat(frueher["datum_gesendet"]).strftime("%d.%m.%Y")
        hinweis = (
            f"ℹ️ Bei {frueher['unternehmen']} hast du dich am {datum} schon "
            f"beworben ({frueher['position']}, Status: {frueher['status']})."
        )
        bis = frueher["cooldown_until"] or ""
        if frueher["status"] != "absage" or bis <= datetime.now().isoformat():
            self.dialog.zeige(hinweis)
            return True
        antwort = self.dialog.frage(
            f"{hinweis}\n⚠️ Nach der Absage läuft der Cooldown noch bis "
            f"{datetime.fromisoformat(bis).strftime('%d.%m.%Y')} – so bald "
            "wirkt eine neue Bewerbung oft ungeduldig.\n"
            "Trotzdem bewerben? [j]a / [n]ein: ",
            optionen=["j", "n"],
        )
        return (antwort or "").lower() in ("j", "ja")

    def _zeige_vorschau(
        self,
        unterlagen: Dict,
        empfaenger,
        betreff,
        anhaenge,
        ordner,
        passung: int,
        kontakt: Dict,
    ):
        resume = unterlagen["resume"]
        zeilen = [
            "",
            "=" * 60,
            "👀 VORSCHAU",
            "=" * 60,
            f"🎯 Berufsrichtung: {resume['berufsrichtung']}",
            f"🧩 Projekte: {', '.join(p['name'] for p in resume['projekte'])}",
            f"📊 Passung: {passung} % der geforderten Skills",
        ]
        if empfaenger:
            zeilen += [f"📧 An: {empfaenger}", f"   Betreff: {betreff}"]
        else:
            zeilen.append(
                f"📮 Bewerbung über Portal – alle Dateien liegen in:\n   {ordner}"
            )
        if any(kontakt.values()):
            zeilen.append(
                "☎️ Kontakt für Nachfragen: "
                + " · ".join(w for w in kontakt.values() if w)
            )
        groesse = sum(Path(a).stat().st_size for a in anhaenge) / 1_000_000
        zeilen.append(f"📎 Anhänge ({len(anhaenge)}, {groesse:.1f} MB):")
        zeilen += [f"   • {Path(a).name}" for a in anhaenge]
        zeilen.append("\n📝 Anschreiben (Auszug Projekte):")
        zeilen += [
            f"   {absatz}"
            for absatz in unterlagen["cover"]["text"].split("\n\n")
            if any(p["name"] in absatz for p in resume["projekte"])
        ]
        self.dialog.zeige("\n".join(zeilen))

    def _bestaetigt(self, unterlagen: Dict, ordner: Path, portal: bool) -> str:
        """ "j" = senden/abgeschickt, "n" = nicht speichern, "m" = E-Mail nachtragen."""
        if portal:
            frage = (
                "\n📮 Im Portal abgeschickt? [j]a / [n]ein / [o] Ordner + PDFs öffnen"
                " / [m] doch per E-Mail senden: "
            )
            optionen = ["j", "n", "o", "m"]
        else:
            frage = "\n📬 Jetzt senden? [j]a / [n]ein / [o] PDFs öffnen: "
            optionen = ["j", "n", "o"]
        while True:
            antwort = self.dialog.frage(frage, optionen=optionen)
            if antwort is None:
                return "n"
            antwort = antwort.lower()
            if antwort in ("j", "ja"):
                return "j"
            if antwort in ("n", "nein", ""):
                return "n"
            if antwort == "m" and portal:
                return "m"
            if antwort == "o":
                zu_oeffnen = [unterlagen["resume_path"], unterlagen["cover_path"]]
                if portal:
                    zu_oeffnen.append(ordner)
                self.dialog.zeige_dateien(zu_oeffnen)


def main():
    """CLI-Einstiegspunkt."""
    args = sys.argv[1:]
    config = AppConfig()
    if args in (["nachfassen"], ["anrufen"], ["offen"]):
        abgeschlossen = schliesse_ohne_antwort(config)
        if abgeschlossen:
            print(abgeschlossen + "\n")

    if len(args) == 1 and args[0] == "postfach":
        ImapReader(config, erstelle_llm_client(config)).check_postfach()
    elif len(args) == 1 and args[0] == "report":
        Analytics(config).print_report()
    elif len(args) == 1 and args[0] == "cooldown":
        CooldownManager(config).check_rebewerbung()
    elif len(args) == 1 and args[0] == "nachfassen":
        Nachfassen(config, TerminalDialog()).pruefe()
    elif len(args) == 1 and args[0] == "anrufen":
        Anrufen(config, TerminalDialog()).pruefe()
    elif len(args) == 1 and args[0] == "offen":
        print(uebersicht(config))
    elif len(args) == 1 and args[0] == "alle":
        print(alle_bewerbungen(config))
    elif len(args) == 1 and args[0] == "kompetenzen":
        KompetenzenDB(config.db_path).print_uebersicht()
    elif len(args) == 1 and args[0] == "stellen":
        from src.vorschlaege import Vorschlaege  # importiert selbst src.main

        Vorschlaege(config, TerminalDialog()).pruefe(automatisch=False)
    elif len(args) == 1 and (Path(args[0]).is_file() or URL.fullmatch(args[0])):
        stellen_text = lies_anzeige(args[0])
        agent = BewerbungsAgent(config)
        info = frage_stellendaten(erkenne_stelle(stellen_text, agent.client))
        if info:
            agent.bewerben(
                stellen_text,
                info.unternehmen,
                info.position,
                info.email,
                link=args[0] if URL.fullmatch(args[0]) else None,
            )
    elif len(args) == 4:
        stelle_file, unternehmen, position, empfaenger = args
        stellen_text = lies_anzeige(stelle_file)
        BewerbungsAgent(config).bewerben(
            stellen_text,
            unternehmen,
            position,
            empfaenger,
            link=stelle_file if URL.fullmatch(stelle_file) else None,
        )
    else:
        print(NUTZUNG)
        sys.exit(1)


if __name__ == "__main__":
    main()
