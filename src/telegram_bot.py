"""Telegram-Bot: Stellenanzeige schicken → Unterlagen bekommen → „ja“ zum Senden.

Start:  python -m src.telegram_bot
Autostart mit dem Rechner:  python -m src.telegram_bot --dienst
Braucht in .env: TELEGRAM_TOKEN (von @BotFather) und TELEGRAM_CHAT_ID
(ohne Chat-ID nennt dir der Bot sie bei der ersten Nachricht).

Läuft lokal: der Bot holt sich neue Nachrichten selbst ab (Long Polling),
kein Server und keine offenen Ports nötig. Er reagiert nur auf deinen Chat
und sendet nie ohne dein ausdrückliches „ja“.

Nebenbei prüft er regelmäßig das Postfach (POSTFACH_PRUEFEN=taeglich,
woechentlich oder aus), meldet Einladungen/Absagen, fragt, ob er bei
Firmen ohne Antwort nachfassen soll, erinnert mit Spickzettel ans Anrufen
und schickt einmal pro Woche eine Übersicht aller laufenden Bewerbungen.
Ist data/kriterien.json eingerichtet, sucht er außerdem täglich neue Stellen
und schlägt dir die passenden zum Bewerben vor."""

import io
import os
import queue
import re
import sys
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import requests

from src.analytics import Analytics
from src.anrufen import Anrufen
from src.config import PROJEKT_DIR, AppConfig
from src.cooldown import CooldownManager
from src.database import BewerbungsDB
from src.dialog import Dialog
from src.imap_reader import ImapReader
from src.main import BewerbungsAgent, erstelle_llm_client, frage_stellendaten
from src.nachfassen import Nachfassen
from src.stellen_info import erkenne_stelle
from src.stellen_link import URL, lade_anzeige
from src.uebersicht import alle_bewerbungen, schliesse_ohne_antwort, uebersicht
from src.vorschlaege import Vorschlaege

MAX_NACHRICHT = 4000  # Telegram erlaubt 4096 Zeichen
ANTWORT_TIMEOUT = 6 * 3600  # so lange wartet eine Rückfrage, dann Abbruch
ABBRECHEN = "/abbrechen"
LEER = "\x00leer"  # Knopf „übernehmen“ – Telegram kann keine leeren Nachrichten
INTERVALLE = {"taeglich": 1, "woechentlich": 7}  # Tage zwischen Postfach-Prüfungen
LETZTE_PRUEFUNG = "postfach_geprueft"  # Schlüssel in der Tabelle "zustand"
LETZTE_UEBERSICHT = "uebersicht_gesendet"
LETZTE_SUCHE = "stellen_gesucht"
UEBERSICHT_TAGE = 7
BEFEHLE = {
    "stellen": "Neue Stellen suchen und vorschlagen",
    "offen": "Übersicht: was läuft, was ist fällig",
    "alle": "Alle Bewerbungen auf einen Blick",
    "postfach": "Postfach prüfen, nachfassen, Anrufe",
    "report": "Statistik und Gelerntes",
    "cooldown": "Re-Bewerbungen prüfen",
    "hilfe": "Was kann der Bot?",
}
KNOPF_TEXTE = {
    "j": "✅ Ja",
    "n": "❌ Nein",
    "o": "📄 PDFs zeigen",
    "m": "📧 Per E-Mail",
    "s": "⏰ Später",
    "e": "🎉 Einladung",
    "a": "📭 Absage",
    "w": "⏳ Wartet noch",
    "b": "✍️ Bewerben",
    "i": "🙈 Ignorieren",
    "x": "🛑 Schluss",
    LEER: "⏎ Übernehmen / leer",
}


WIEDERHOLEN_NACH = (2, 5)  # Sekunden Pause vor dem 2. und 3. Versuch


class TelegramAPI:
    def __init__(self, token: str):
        self.url = f"https://api.telegram.org/bot{token}"
        self.datei_url = f"https://api.telegram.org/file/bot{token}"

    def _post(self, url: str, **kwargs) -> requests.Response:
        """POST mit neuen Versuchen bei kurzen Netz-Aussetzern.

        Nur bei Verbindungsfehlern (Nachricht kam nicht an), nicht bei
        Lese-Timeouts – sonst käme eine Nachricht womöglich doppelt."""
        for warte in WIEDERHOLEN_NACH:
            try:
                return requests.post(url, **kwargs)
            except requests.ConnectionError:
                threading.Event().wait(warte)
        return requests.post(url, **kwargs)

    def rufe(self, methode: str, http_timeout: int = 30, **daten) -> dict:
        antwort = self._post(f"{self.url}/{methode}", json=daten, timeout=http_timeout)
        ergebnis = antwort.json()
        if not ergebnis.get("ok"):
            raise RuntimeError(f"Telegram-Fehler: {ergebnis.get('description')}")
        return ergebnis["result"]

    def sende_datei(self, chat_id: int, pfad: Path) -> None:
        inhalt = (
            pfad.read_bytes()
        )  # nicht als offene Datei: bei neuem Versuch wieder von vorn
        self._post(
            f"{self.url}/sendDocument",
            data={"chat_id": chat_id},
            files={"document": (pfad.name, inhalt)},
            timeout=120,
        ).raise_for_status()

    def lade_datei(self, file_id: str) -> bytes:
        info = self.rufe("getFile", file_id=file_id)
        antwort = requests.get(f"{self.datei_url}/{info['file_path']}", timeout=60)
        antwort.raise_for_status()
        return antwort.content


class TelegramDialog(Dialog):
    """Dialog über einen Telegram-Chat. Läuft im Bewerbungs-Thread; die
    Antworten legt die Hauptschleife in `antworten`."""

    def __init__(
        self, api: TelegramAPI, chat_id: int, timeout: float = ANTWORT_TIMEOUT
    ):
        self.api = api
        self.chat_id = chat_id
        self.timeout = timeout
        self.antworten: "queue.Queue[str]" = queue.Queue()
        self.wartet = False

    def zeige(self, text: str) -> None:
        # Trennlinien aus dem Terminal sehen auf dem Handy nur unruhig aus
        text = "\n".join(z for z in text.splitlines() if not re.fullmatch(r"=+", z))
        text = text.strip()
        for i in range(0, len(text), MAX_NACHRICHT):
            self.api.rufe(
                "sendMessage", chat_id=self.chat_id, text=text[i : i + MAX_NACHRICHT]
            )

    def frage(self, text: str, optionen: Optional[List[str]] = None) -> Optional[str]:
        knoepfe = optionen or [LEER]
        tastatur = [
            [{"text": KNOPF_TEXTE.get(o, o), "callback_data": o} for o in knoepfe]
        ]
        self.api.rufe(
            "sendMessage",
            chat_id=self.chat_id,
            text=text.strip(),
            reply_markup={"inline_keyboard": tastatur},
        )
        self.wartet = True
        try:
            antwort = self.antworten.get(timeout=self.timeout)
        except queue.Empty:
            self.zeige("⌛ Keine Antwort – Bewerbung abgebrochen.")
            return None
        finally:
            self.wartet = False
        if antwort == ABBRECHEN:
            return None
        return "" if antwort == LEER else antwort.strip()

    def zeige_dateien(self, pfade: List[Path]) -> None:
        for pfad in map(Path, pfade):
            if pfad.is_file():  # Ordner lassen sich nicht verschicken
                self.api.sende_datei(self.chat_id, pfad)


def text_aus_pdf(daten: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(daten)) as pdf:
        return "\n".join(seite.extract_text() or "" for seite in pdf.pages)


class BewerbungsBot:
    def __init__(
        self,
        config: AppConfig,
        api: TelegramAPI,
        chat_id: Optional[int],
        intervall_tage: Optional[int] = 1,
    ):
        """intervall_tage: wie oft das Postfach geprüft wird (None = nie)."""
        self.config = config
        self.api = api
        self.chat_id = chat_id
        self.intervall_tage = intervall_tage
        self.db = BewerbungsDB(config.db_path)
        self.dialog: Optional[TelegramDialog] = None
        self.thread: Optional[threading.Thread] = None

    # ---------- HAUPTSCHLEIFE ----------
    def starte(self) -> None:
        print("🤖 Bot läuft – schick ihm eine Stellenanzeige. Beenden mit Strg+C.")
        try:  # Befehle im Telegram-Menü („/“) anzeigen
            self.api.rufe(
                "setMyCommands",
                commands=[{"command": b, "description": t} for b, t in BEFEHLE.items()],
            )
        except (requests.RequestException, RuntimeError):
            pass
        offset = None
        while True:
            self.pruefe_wenn_faellig()
            try:
                # Telegram hält die Anfrage bis zu 50 s offen, bis etwas kommt
                updates = self.api.rufe(
                    "getUpdates", http_timeout=60, offset=offset, timeout=50
                )
            except (requests.RequestException, RuntimeError) as e:
                print(f"⚠️ Verbindung zu Telegram: {e} – neuer Versuch …")
                threading.Event().wait(10)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    self.verarbeite(update)
                except Exception:
                    traceback.print_exc()

    # ---------- EINGEHENDE NACHRICHTEN ----------
    def verarbeite(self, update: dict) -> None:
        if "callback_query" in update:
            knopf = update["callback_query"]
            self.api.rufe("answerCallbackQuery", callback_query_id=knopf["id"])
            chat_id = knopf["message"]["chat"]["id"]
            if self._erlaubt(chat_id):
                self._antwort(knopf["data"])
            return

        nachricht = update.get("message")
        if not nachricht:
            return
        chat_id = nachricht["chat"]["id"]
        if self.chat_id is None:
            self.api.rufe(
                "sendMessage",
                chat_id=chat_id,
                text=f"👋 Deine Chat-ID ist {chat_id}.\n"
                f"Trag in .env ein: TELEGRAM_CHAT_ID={chat_id}\n"
                "und starte den Bot neu.",
            )
            return
        if not self._erlaubt(chat_id):
            return  # fremde Chats ignorieren

        text = nachricht.get("text", "")
        if self._laeuft():
            if self.dialog.wartet and len(text) >= 200:
                # keine Antwort, sondern wohl eine neue Anzeige – nicht verschlucken
                self._sende(
                    "❓ Bitte erst die offene Frage beantworten (oder "
                    f"{ABBRECHEN}), dann die Anzeige nochmal schicken."
                )
            elif self.dialog.wartet or text == ABBRECHEN:
                self._antwort(text)
            else:
                self._sende("⏳ Ich bin noch beschäftigt – einen Moment.")
            return

        if "document" in nachricht:
            text = self._lies_dokument(nachricht["document"])
            if text is None:
                return
        if text.startswith("/"):
            self._befehl(text[1:].split("@")[0].split()[0].lower() if text[1:] else "")
            return
        link = URL.search(text)
        if link and len(text) < 500:  # nur ein Link (evtl. mit "Schau mal: …")
            link = link.group()
            text = self._lies_link(link)
            if text is None:
                return
        else:
            link = None
        if len(text) < 200:
            self._sende(
                "Das ist zu kurz für eine Stellenanzeige – bitte den ganzen Text schicken."
            )
            return
        self._starte_bewerbung(text, link)

    def _lies_dokument(self, dokument: dict) -> Optional[str]:
        name = dokument.get("file_name", "").lower()
        if not name.endswith((".txt", ".pdf")):
            self._sende("📎 Bitte die Anzeige als Text, .txt oder .pdf schicken.")
            return None
        daten = self.api.lade_datei(dokument["file_id"])
        return (
            text_aus_pdf(daten)
            if name.endswith(".pdf")
            else daten.decode("utf-8", "replace")
        )

    def _lies_link(self, url: str) -> Optional[str]:
        self._sende("🔗 Lade die Anzeige …")
        try:
            text = lade_anzeige(url)
        except requests.RequestException as e:
            self._sende(f"❌ Seite nicht erreichbar ({e}) – bitte den Text schicken.")
            return None
        if len(text) < 200:
            self._sende(
                "🔗 Auf der Seite steht kaum Text (lädt sie per JavaScript?) – "
                "bitte den Text der Anzeige kopieren und schicken."
            )
            return None
        return text

    def _befehl(self, befehl: str) -> None:
        if befehl == "offen":
            self._sende(uebersicht(self.config))
        elif befehl == "alle":
            self._sende(alle_bewerbungen(self.config))
        elif befehl == "report":
            self._sende(Analytics(self.config).bericht())
        elif befehl == "cooldown":
            self._sende(CooldownManager(self.config).bericht())
        elif befehl == "postfach":
            self._starte(self._postfach_routine, False)
        elif befehl == "stellen":
            self._starte(self._stellen_routine, False)
        else:
            self._sende(
                "Schick mir eine Stellenanzeige: Text, Link oder .txt/.pdf – "
                "ich erstelle die Unterlagen und sende erst nach deinem ✅.\n\n"
                + "\n".join(f"/{b} – {t}" for b, t in BEFEHLE.items())
                + f"\n{ABBRECHEN} – laufende Bewerbung abbrechen"
            )

    # ---------- POSTFACH + NACHFASSEN ----------
    def pruefe_wenn_faellig(self) -> None:
        """Prüft das Postfach, wenn das Intervall seit der letzten Prüfung um ist,
        sonst einmal am Tag die Stellensuche. Die Zeitpunkte stehen in der DB –
        so zählen sie auch über Neustarts hinweg."""
        if self.chat_id is None or self._laeuft():
            return
        if self.intervall_tage is not None and self._faellig(
            LETZTE_PRUEFUNG, self.intervall_tage
        ):
            self._starte(self._postfach_routine, True)
        elif self._faellig(LETZTE_SUCHE, 1):
            self._starte(self._stellen_routine, True)

    def _faellig(self, schluessel: str, tage: int) -> bool:
        letzte = self.db.lies_zustand(schluessel)
        return not letzte or datetime.now() - datetime.fromisoformat(
            letzte
        ) >= timedelta(days=tage)

    def _postfach_routine(self, dialog: TelegramDialog, automatisch: bool) -> None:
        self.db.setze_zustand(LETZTE_PRUEFUNG, datetime.now().isoformat())
        meldungen: List[str] = []
        ImapReader(
            self.config, erstelle_llm_client(self.config), meldungen.append
        ).check_postfach()
        neu = [m for m in meldungen if not m.startswith("📭")]
        if neu or not automatisch:  # automatisch nur melden, wenn es Neues gibt
            dialog.zeige("📬 Postfach geprüft:\n" + "\n".join(meldungen))
        # erst nach dem Postfach: eine späte Antwort hat Vorrang
        abgeschlossen = schliesse_ohne_antwort(self.config)
        if abgeschlossen:
            dialog.zeige(abgeschlossen)
        Nachfassen(self.config, dialog).pruefe()
        Anrufen(self.config, dialog).pruefe()
        self._wochen_uebersicht(dialog, automatisch)

    def _wochen_uebersicht(self, dialog: TelegramDialog, automatisch: bool) -> None:
        """Einmal pro Woche – und nur, wenn gerade etwas läuft."""
        laeuft = any(
            b["status"] in ("gesendet", "einladung")
            for b in self.db.get_all_bewerbungen()
        )
        letzte = self.db.lies_zustand(LETZTE_UEBERSICHT)
        if (
            automatisch
            and laeuft
            and (
                not letzte
                or datetime.now() - datetime.fromisoformat(letzte)
                >= timedelta(days=UEBERSICHT_TAGE)
            )
        ):
            self.db.setze_zustand(LETZTE_UEBERSICHT, datetime.now().isoformat())
            dialog.zeige(uebersicht(self.config))

    def _stellen_routine(self, dialog: TelegramDialog, automatisch: bool) -> None:
        self.db.setze_zustand(LETZTE_SUCHE, datetime.now().isoformat())
        Vorschlaege(self.config, dialog).pruefe(automatisch)

    # ---------- ARBEIT IM HINTERGRUND ----------
    def _starte(self, ziel, *args) -> None:
        """Startet eine Aufgabe mit eigenem Dialog; Antworten kommen über den Chat."""
        self.dialog = TelegramDialog(self.api, self.chat_id)
        self.thread = threading.Thread(
            target=self._sicher, args=(ziel, self.dialog, *args), daemon=True
        )
        self.thread.start()

    @staticmethod
    def _sicher(ziel, dialog: TelegramDialog, *args) -> None:
        try:
            ziel(dialog, *args)
        except Exception as e:
            traceback.print_exc()
            dialog.zeige(f"💥 Fehler: {e}")

    def _starte_bewerbung(self, stellen_text: str, link: Optional[str] = None) -> None:
        self._starte(self._bewerbung, stellen_text, link)

    def _bewerbung(
        self, dialog: TelegramDialog, stellen_text: str, link: Optional[str] = None
    ) -> None:
        dialog.zeige("📥 Anzeige erhalten – ich lese sie …")
        agent = BewerbungsAgent(self.config, dialog=dialog)
        info = frage_stellendaten(
            erkenne_stelle(stellen_text, agent.client), dialog=dialog
        )
        if info:
            agent.bewerben(
                stellen_text, info.unternehmen, info.position, info.email, link=link
            )

    # ---------- HILFEN ----------
    def _laeuft(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _antwort(self, text: str) -> None:
        if self._laeuft():
            self.dialog.antworten.put(text)

    def _erlaubt(self, chat_id: int) -> bool:
        return self.chat_id is not None and chat_id == self.chat_id

    def _sende(self, text: str) -> None:
        TelegramDialog(self.api, self.chat_id).zeige(text)  # stückelt lange Texte


DIENST_NAME = "bewerbungs-bot"
DIENST = """[Unit]
Description=Bewerbungs-Agent Telegram-Bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory={ordner}
ExecStart={python} -m src.telegram_bot
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""


def installiere_dienst() -> Path:
    """Legt einen systemd-Benutzerdienst an, damit der Bot mit dem Rechner startet.
    Pfade kommen vom aktuellen Projekt und venv – passt auf jedem Rechner."""
    ziel = Path.home() / ".config" / "systemd" / "user" / f"{DIENST_NAME}.service"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(
        DIENST.format(ordner=PROJEKT_DIR, python=sys.executable), encoding="utf-8"
    )
    return ziel


def main():
    if sys.argv[1:] == ["--dienst"]:
        pfad = installiere_dienst()
        print(f"✅ Dienst angelegt: {pfad}\n\nJetzt einschalten:")
        print("   systemctl --user daemon-reload")
        print(f"   systemctl --user enable --now {DIENST_NAME}")
        print("\nLog ansehen:  journalctl --user -u " + DIENST_NAME + " -f")
        print("Auch ohne Anmeldung laufen lassen:  loginctl enable-linger $USER")
        return
    token = os.getenv("TELEGRAM_TOKEN", "")  # .env lädt src.config beim Import
    if not token:
        print("❌ TELEGRAM_TOKEN fehlt in .env (Token bekommst du von @BotFather).")
        raise SystemExit(1)
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    pruefen = os.getenv("POSTFACH_PRUEFEN", "taeglich").lower()
    if pruefen not in (*INTERVALLE, "aus"):
        print(f"❌ POSTFACH_PRUEFEN={pruefen} – erlaubt: taeglich, woechentlich, aus")
        raise SystemExit(1)
    BewerbungsBot(
        AppConfig(),
        TelegramAPI(token),
        int(chat_id) if chat_id else None,
        INTERVALLE.get(pruefen),
    ).starte()


if __name__ == "__main__":
    main()
