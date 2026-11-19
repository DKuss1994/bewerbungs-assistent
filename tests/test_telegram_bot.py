import sys
import threading
import time
from datetime import datetime, timedelta

import pytest

from src.config import PROJEKT_DIR
from src.database import BewerbungsDB
from src.email_handler import EmailHandler
from src.telegram_bot import LEER, BewerbungsBot, TelegramDialog, installiere_dienst

CHAT = 42

ANZEIGE = """Support 2nd Level (m/w/d)
bei
Muster IT GmbH
Wir suchen Verstärkung für unseren Support mit Java und Linux. Du betreust
Systeme, analysierst Störungen und hilfst Kunden weiter.
Bewerbungen bitte an: bewerbung@muster-it.de"""


class FakeAPI:
    def __init__(self):
        self.gesendet = []
        self.dateien = []

    def rufe(self, methode, http_timeout=30, **daten):
        self.gesendet.append((methode, daten))
        return {}

    def sende_datei(self, chat_id, pfad):
        self.dateien.append(pfad)

    @property
    def texte(self):
        return [d["text"] for m, d in self.gesendet if m == "sendMessage"]


def nachricht(text, chat=CHAT):
    return {"message": {"chat": {"id": chat}, "text": text}}


def knopf(wert, chat=CHAT):
    return {
        "callback_query": {"id": "1", "data": wert, "message": {"chat": {"id": chat}}}
    }


# ---------- DIALOG ----------
def test_dialog_frage_mit_knoepfen_und_leer():
    api = FakeAPI()
    dialog = TelegramDialog(api, CHAT)
    dialog.antworten.put(LEER)
    assert dialog.frage("Firma [X]:") == ""
    tastatur = api.gesendet[0][1]["reply_markup"]["inline_keyboard"]
    assert tastatur[0][0]["callback_data"] == LEER

    dialog.antworten.put("j")
    assert dialog.frage("Senden?", optionen=["j", "n", "o"]) == "j"
    assert len(api.gesendet[1][1]["reply_markup"]["inline_keyboard"][0]) == 3


def test_dialog_abbrechen_und_timeout():
    dialog = TelegramDialog(FakeAPI(), CHAT, timeout=0.01)
    dialog.antworten.put("/abbrechen")
    assert dialog.frage("?") is None
    assert dialog.frage("?") is None  # keine Antwort → Abbruch
    assert not dialog.wartet


def test_dialog_ohne_trennlinien_und_gestueckelt():
    api = FakeAPI()
    TelegramDialog(api, CHAT).zeige("=" * 60 + "\nHallo\n" + "x" * 5000)
    assert api.texte[0].startswith("Hallo") and "====" not in api.texte[0]
    assert len(api.texte) == 2


def test_dialog_schickt_nur_dateien(tmp_path):
    api = FakeAPI()
    (tmp_path / "a.pdf").write_bytes(b"x")
    TelegramDialog(api, CHAT).zeige_dateien([tmp_path / "a.pdf", tmp_path])
    assert api.dateien == [tmp_path / "a.pdf"]


# ---------- BOT ----------
def test_ohne_chat_id_nennt_bot_die_id(config):
    api = FakeAPI()
    BewerbungsBot(config, api, None).verarbeite(nachricht("hallo", chat=123))
    assert "123" in api.texte[0]


def test_fremde_chats_werden_ignoriert(config):
    api = FakeAPI()
    bot = BewerbungsBot(config, api, CHAT)
    bot.verarbeite(nachricht(ANZEIGE, chat=999))
    assert api.gesendet == [] and bot.thread is None


def test_kurzer_text_und_leere_seite_starten_nichts(config, monkeypatch):
    monkeypatch.setattr("src.telegram_bot.lade_anzeige", lambda url: "Bitte JS")
    api = FakeAPI()
    bot = BewerbungsBot(config, api, CHAT)
    bot.verarbeite(nachricht("hallo"))
    bot.verarbeite(nachricht("https://example.org/job"))
    assert bot.thread is None
    assert "kaum Text" in api.texte[-1]


def test_link_wird_geladen_und_startet_bewerbung(config, monkeypatch):
    geladen = []
    monkeypatch.setattr(
        "src.telegram_bot.lade_anzeige", lambda url: geladen.append(url) or ANZEIGE
    )
    bot = BewerbungsBot(config, FakeAPI(), CHAT)
    gestartet = []
    bot._starte_bewerbung = lambda text, link: gestartet.append((text, link))

    bot.verarbeite(nachricht("Schau mal: https://jobs.example.org/123 "))

    assert geladen == ["https://jobs.example.org/123"]
    assert gestartet == [(ANZEIGE, "https://jobs.example.org/123")]


def warte_auf_frage(bot, sekunden=20):
    ende = time.time() + sekunden
    while not (bot.dialog and bot.dialog.wartet):
        assert bot.thread.is_alive(), "Bewerbung ist vorzeitig beendet"
        assert time.time() < ende, "Bot stellt keine Frage"
        time.sleep(0.01)


def test_ganze_bewerbung_per_chat(config, monkeypatch):
    pytest.importorskip("weasyprint")
    monkeypatch.setattr("src.main.erstelle_llm_client", lambda c: None)
    for ordner in ("zeugnisse", "zertifikate"):
        (config.data_dir / ordner).mkdir()
    gesendet = []
    monkeypatch.setattr(
        EmailHandler, "send_application", lambda self, *a: gesendet.append(a) or True
    )
    api = FakeAPI()
    bot = BewerbungsBot(config, api, CHAT)

    bot.verarbeite(nachricht(ANZEIGE))
    for _ in ("Firma", "Position", "E-Mail"):  # erkannte Werte übernehmen
        warte_auf_frage(bot)
        bot.verarbeite(knopf(LEER))
    warte_auf_frage(bot)
    bot.verarbeite(knopf("o"))  # PDFs aufs Handy
    warte_auf_frage(bot)
    bot.verarbeite(knopf("j"))
    bot.thread.join(20)

    assert gesendet[0][0] == "bewerbung@muster-it.de"
    assert [p.name for p in api.dateien][0] == "Lebenslauf_Muster_IT_GmbH.pdf"
    assert BewerbungsDB(config.db_path).get_all_bewerbungen()[0]["kanal"] == "email"
    assert any("Bewerbung gespeichert" in t for t in api.texte)


# ---------- BEFEHLE UND POSTFACH ----------
def test_befehle_report_und_hilfe(config):
    api = FakeAPI()
    bot = BewerbungsBot(config, api, CHAT)
    bot.verarbeite(nachricht("/report"))
    bot.verarbeite(nachricht("/cooldown"))
    bot.verarbeite(nachricht("/start"))
    assert "BEWERBUNGS-REPORT" in api.texte[0]
    assert "Cooldown" in api.texte[1] and "/postfach" in api.texte[2]


def test_postfach_nur_wenn_faellig(config):
    bot = BewerbungsBot(config, FakeAPI(), CHAT, intervall_tage=7)
    gestartet = []
    bot._starte = lambda ziel, *args: gestartet.append(args)

    bot.pruefe_wenn_faellig()  # noch nie geprüft → jetzt
    assert gestartet == [(True,)]

    bot.db.setze_zustand("postfach_geprueft", datetime.now().isoformat())
    bot.db.setze_zustand("stellen_gesucht", datetime.now().isoformat())
    bot.pruefe_wenn_faellig()
    assert len(gestartet) == 1  # erst in 7 Tagen wieder

    vor_8_tagen = (datetime.now() - timedelta(days=8)).isoformat()
    bot.db.setze_zustand("postfach_geprueft", vor_8_tagen)
    bot.pruefe_wenn_faellig()
    assert len(gestartet) == 2

    aus = BewerbungsBot(config, FakeAPI(), CHAT, intervall_tage=None)
    aus._starte = lambda ziel, *args: gestartet.append(args)
    aus.pruefe_wenn_faellig()
    assert len(gestartet) == 2  # POSTFACH_PRUEFEN=aus, Stellen heute schon gesucht


def test_stellensuche_taeglich_und_per_befehl(config, monkeypatch):
    bot = BewerbungsBot(config, FakeAPI(), CHAT, intervall_tage=None)
    gestartet = []
    bot._starte = lambda ziel, *args: gestartet.append((ziel.__name__, args))
    bot.pruefe_wenn_faellig()
    assert gestartet == [("_stellen_routine", (True,))]
    bot.verarbeite(nachricht("/stellen"))
    assert gestartet[-1] == ("_stellen_routine", (False,))

    aufrufe = []
    monkeypatch.setattr(
        "src.telegram_bot.Vorschlaege.pruefe", lambda self, a: aufrufe.append(a)
    )
    api = FakeAPI()
    BewerbungsBot(config, api, CHAT)._stellen_routine(TelegramDialog(api, CHAT), True)
    assert aufrufe == [True] and bot.db.lies_zustand("stellen_gesucht")


def test_postfach_routine_meldet_nur_neues(config, monkeypatch):
    monkeypatch.setattr("src.telegram_bot.erstelle_llm_client", lambda c: None)
    monkeypatch.setattr(
        "src.telegram_bot.ImapReader.check_postfach",
        lambda self: self.melde("📭 Keine neuen Rückmeldungen gefunden."),
    )
    api = FakeAPI()
    bot = BewerbungsBot(config, api, CHAT)
    dialog = TelegramDialog(api, CHAT)

    bot._postfach_routine(dialog, True)
    assert api.texte == []  # automatisch und nichts Neues → kein Spam
    assert bot.db.lies_zustand("postfach_geprueft")

    bot._postfach_routine(dialog, False)  # per /postfach → immer Antwort
    assert "Keine neuen" in api.texte[0]


def test_lange_nachricht_waehrend_frage_wird_nicht_verschluckt(config):
    api = FakeAPI()
    bot = BewerbungsBot(config, api, CHAT)
    bot.dialog = TelegramDialog(api, CHAT)
    bot.dialog.wartet = True
    bot.thread = threading.Thread(target=lambda: time.sleep(0.5))
    bot.thread.start()

    bot.verarbeite(nachricht(ANZEIGE))

    assert bot.dialog.antworten.empty() and "offene Frage" in api.texte[-1]


def test_dienst_datei_ohne_feste_pfade(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    inhalt = installiere_dienst().read_text()
    assert f"WorkingDirectory={PROJEKT_DIR}" in inhalt
    assert f"ExecStart={sys.executable} -m src.telegram_bot" in inhalt


def test_api_wiederholt_bei_verbindungsabbruch(monkeypatch):
    import requests

    from src import telegram_bot

    monkeypatch.setattr(telegram_bot, "WIEDERHOLEN_NACH", (0, 0))
    versuche = []

    class Antwort:
        def json(self):
            return {"ok": True, "result": "da"}

    def post(url, **kwargs):
        versuche.append(url)
        if len(versuche) < 3:
            raise requests.ConnectionError("Connection reset by peer")
        return Antwort()

    monkeypatch.setattr(telegram_bot.requests, "post", post)
    assert telegram_bot.TelegramAPI("x").rufe("sendMessage", text="hi") == "da"
    assert len(versuche) == 3

    versuche.clear()
    monkeypatch.setattr(
        telegram_bot.requests,
        "post",
        lambda url, **kw: versuche.append(url)
        or (_ for _ in ()).throw(requests.ConnectionError("weg")),
    )
    try:
        telegram_bot.TelegramAPI("x").rufe("sendMessage", text="hi")
        assert False, "hätte aufgeben müssen"
    except requests.ConnectionError:
        assert len(versuche) == 3
