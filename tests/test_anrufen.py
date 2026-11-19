import sqlite3
from datetime import date, datetime, timedelta

from src.anrufen import Anrufen
from src.database import BewerbungsDB
from src.stellen_info import finde_ansprechpartner, finde_telefon
from src.telegram_bot import BewerbungsBot, TelegramDialog
from src.uebersicht import uebersicht
from tests.conftest import FakeDialog
from tests.test_telegram_bot import CHAT, FakeAPI

KONTAKT = """Muster GmbH
IT-Support (m/w/d)
Start 01.03.2026 in 10115 Berlin, Gehalt 45.000 €
Deine Ansprechpartnerin: Frau Anna Beispiel
Tel.: +49 (0)30 / 123 45-67
Fax: 030 1234568"""


def vor(tage, nur_datum=False):
    zeit = datetime.now() - timedelta(days=tage)
    return zeit.date().isoformat() if nur_datum else zeit.isoformat()


def bewerbung(db, firma, tage_her, nachgefasst=None, **kwargs):
    bid = db.insert_bewerbung(firma, position="Admin", **kwargs)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE bewerbungen SET datum_gesendet = ?, nachgefasst = ? WHERE id = ?",
            (vor(tage_her), nachgefasst, bid),
        )
    return bid


def ids(liste):
    return [b["id"] for b in liste]


def test_telefon_und_ansprechpartner_aus_anzeige():
    assert finde_telefon(KONTAKT) == "+49 (0)30 / 123 45-67"
    assert finde_ansprechpartner(KONTAKT) == "Frau Anna Beispiel"
    # Datum, PLZ und Gehalt sind keine Telefonnummern, Fax zählt nicht
    assert finde_telefon("Start 01.03.2026, 10115 Berlin, 45.000 €") is None
    assert finde_telefon("Fax: 030 1234568") is None
    assert finde_ansprechpartner("Bei Fragen wende dich an\nHerrn Dr. Max Müller") == (
        "Herr Dr. Max Müller"
    )


def test_anruf_faellig_nach_nachfass_mail_oder_portal(config):
    db = BewerbungsDB(config.db_path)
    mail_alt = bewerbung(db, "A", 25, vor(8, True), empfaenger="hr@a.de")
    bewerbung(db, "B", 20, vor(3, True), empfaenger="hr@b.de")  # zu frisch
    bewerbung(db, "C", 25, "nein", empfaenger="hr@c.de")  # „nie nachfassen“
    bewerbung(db, "D", 20, empfaenger="hr@d.de")  # erst nachfassen
    portal = bewerbung(db, "E", 15, kanal="portal")
    bewerbung(db, "F", 5, kanal="portal")
    db.add_rueckmeldung(bewerbung(db, "G", 30, kanal="portal"), "absage")

    assert ids(Anrufen(config, FakeDialog()).kandidaten()) == [mail_alt, portal]


def test_anrufen_ergebnis_wird_gespeichert(config):
    db = BewerbungsDB(config.db_path)
    einladung = bewerbung(db, "Einladung GmbH", 20, kanal="portal")
    notiz = bewerbung(db, "Notiz GmbH", 19, kanal="portal")
    spaeter = bewerbung(db, "Später GmbH", 18, kanal="portal")
    nie = bewerbung(db, "Nie GmbH", 17, kanal="portal")
    dialog = FakeDialog("j", "e", "j", "Rückruf nächste Woche", "s", "n")

    assert Anrufen(config, dialog).pruefe() == 2

    nach_id = {b["id"]: b for b in db.get_all_bewerbungen()}
    assert nach_id[einladung]["status"] == "einladung"
    assert nach_id[einladung]["anruf"] == date.today().isoformat()
    assert nach_id[notiz]["notiz"] == "Rückruf nächste Woche"
    assert nach_id[nie]["anruf"] == "nein"
    # „später“: erst in ein paar Tagen wieder, die anderen gar nicht mehr
    assert Anrufen(config, FakeDialog()).kandidaten() == []
    db.setze_feld(spaeter, "anruf_erinnert", vor(3, True))
    assert ids(Anrufen(config, FakeDialog()).kandidaten()) == [spaeter]


def test_spickzettel_mit_allem_fuers_telefonat(config):
    db = BewerbungsDB(config.db_path)
    bewerbung(
        db,
        "Muster GmbH",
        25,
        vor(8, True),
        empfaenger="hr@muster.de",
        telefon="030 1234567",
        ansprechpartner="Frau Anna Beispiel",
        link="https://jobs.example.org/1",
        projekte=["backend", "linux"],
        skills_gefordert=["Java", "Linux"],
    )
    b = Anrufen(config, FakeDialog()).kandidaten()[0]
    zettel = Anrufen(config, FakeDialog()).spickzettel(b)
    for erwartet in (
        "Frau Anna Beispiel",
        "030 1234567",
        "hr@muster.de",
        "nachgefragt am",
        "Java, Linux",
        "BackendApp, LinuxConfig",  # Namen statt IDs
        "https://jobs.example.org/1",
        "mein Name ist Max Muster",
    ):
        assert erwartet in zettel

    ohne = dict(b, telefon="", ansprechpartner="", link="")
    assert "Impressum" in Anrufen(config, FakeDialog()).spickzettel(ohne)


def test_uebersicht_nach_faelligkeit(config):
    db = BewerbungsDB(config.db_path)
    assert "Noch keine" in uebersicht(config)
    bewerbung(db, "Anruf GmbH", 20, kanal="portal", telefon="030 1234567")
    bewerbung(db, "Nachfass GmbH", 20, empfaenger="hr@n.de")
    bewerbung(db, "Warte GmbH", 2, empfaenger="hr@w.de")
    db.add_rueckmeldung(bewerbung(db, "Top GmbH", 9, kanal="portal"), "einladung")
    db.add_rueckmeldung(bewerbung(db, "Nein GmbH", 9, kanal="portal"), "absage")

    text = uebersicht(config)
    reihenfolge = ["Einladungen (1)", "Anrufen fällig (1)", "Nachfassen fällig (1)"]
    reihenfolge += ["Wartet auf Antwort (1)", "Absagen bisher: 1"]
    assert [text.index(t) for t in reihenfolge] == sorted(
        text.index(t) for t in reihenfolge
    )
    assert "☎️ 030 1234567" in text and "Warte GmbH" in text


def test_bot_offen_und_wochen_uebersicht(config, monkeypatch):
    monkeypatch.setattr("src.telegram_bot.erstelle_llm_client", lambda c: None)
    monkeypatch.setattr(
        "src.telegram_bot.ImapReader.check_postfach",
        lambda self: self.melde("📭 Keine neuen Rückmeldungen gefunden."),
    )
    api = FakeAPI()
    bot = BewerbungsBot(config, api, CHAT)
    bot._befehl("offen")
    assert "Deine Bewerbungen" in api.texte[-1]

    BewerbungsDB(config.db_path).insert_bewerbung("Firma", position="Admin")
    vorher = len(api.texte)
    bot._postfach_routine(TelegramDialog(api, CHAT), True)
    assert len(api.texte) == vorher + 1 and "Wartet auf Antwort" in api.texte[-1]
    bot._postfach_routine(TelegramDialog(api, CHAT), True)
    assert len(api.texte) == vorher + 1  # nur einmal pro Woche


def test_ohne_antwort_wird_abgeschlossen(config):
    from src.uebersicht import schliesse_ohne_antwort

    db = BewerbungsDB(config.db_path)
    alt = bewerbung(db, "Alt GmbH", 50, kanal="portal")
    nachgefasst = bewerbung(db, "Alt Mail GmbH", 50, vor(30, True), empfaenger="a@b.de")
    frisch = bewerbung(db, "Frisch GmbH", 30, kanal="portal")
    angerufen = bewerbung(db, "Angerufen GmbH", 50, kanal="portal")
    db.setze_feld(angerufen, "anruf", vor(5, True))  # gerade erst telefoniert
    db.add_rueckmeldung(bewerbung(db, "Absage GmbH", 60, kanal="portal"), "absage")

    meldung = schliesse_ohne_antwort(config)

    status = {b["id"]: b["status"] for b in db.get_all_bewerbungen()}
    assert [i for i, s in status.items() if s == "geghosted"] == [nachgefasst, alt]
    assert status[angerufen] == "gesendet"
    assert "Alt GmbH" in meldung and "Frisch GmbH" not in meldung
    assert schliesse_ohne_antwort(config) is None  # nur einmal melden
    text = uebersicht(config)
    assert "Ohne Antwort abgeschlossen: 2" in text and "Alt GmbH" not in text
    # Abgeschlossene bekommen keinen Anruf mehr, die frische schon
    assert ids(Anrufen(config, FakeDialog()).kandidaten()) == [frisch]


def test_spaete_antwort_oeffnet_wieder(config):
    from src.imap_reader import ImapReader
    from src.uebersicht import schliesse_ohne_antwort

    db = BewerbungsDB(config.db_path)
    bid = bewerbung(db, "Späte GmbH", 50, kanal="portal")
    schliesse_ohne_antwort(config)
    ImapReader(config).verarbeite_mail(
        "id-1", "Einladung zum Gespräch", "hr@spaete.de", "Späte GmbH lädt Sie ein"
    )
    assert db.get_all_bewerbungen()[0]["status"] == "einladung" and bid


def test_alle_bewerbungen(config):
    from src.uebersicht import alle_bewerbungen

    db = BewerbungsDB(config.db_path)
    assert "Noch keine" in alle_bewerbungen(config)
    bewerbung(db, "Alt GmbH", 60, kanal="portal")
    db.add_rueckmeldung(bewerbung(db, "Nein GmbH", 9, empfaenger="hr@n.de"), "absage")
    text = alle_bewerbungen(config)
    assert "(2)" in text
    assert text.index("Nein GmbH") < text.index("Alt GmbH")  # neueste zuerst
    assert "📭" in text and "Portal" in text and "Mail" in text
