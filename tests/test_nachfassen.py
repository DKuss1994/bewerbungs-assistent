import sqlite3
from datetime import datetime, timedelta

from src.database import BewerbungsDB
from src.imap_reader import ImapReader
from src.nachfassen import Nachfassen
from tests.conftest import FakeDialog


class FakeEmail:
    def __init__(self, ok=True):
        self.ok = ok
        self.gesendet = []

    def send_application(self, empfaenger, betreff, text, anhaenge):
        self.gesendet.append((empfaenger, betreff, text))
        return self.ok


def bewerbung(db, firma, tage_her, **kwargs):
    bid = db.insert_bewerbung(firma, position="Admin", **kwargs)
    datum = (datetime.now() - timedelta(days=tage_her)).isoformat()
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE bewerbungen SET datum_gesendet = ? WHERE id = ?", (datum, bid)
        )
    return bid


def test_nur_alte_email_bewerbungen_ohne_antwort(config):
    db = BewerbungsDB(config.db_path)
    alt = bewerbung(db, "Alt GmbH", 20, empfaenger="hr@alt.de")
    bewerbung(db, "Neu GmbH", 3, empfaenger="hr@neu.de")
    bewerbung(db, "Portal GmbH", 20, kanal="portal")
    db.add_rueckmeldung(bewerbung(db, "Absage GmbH", 20, empfaenger="a@b.de"), "absage")

    assert [b["id"] for b in db.nachfass_kandidaten(14)] == [alt]


def test_nachfassen_ja_nein_spaeter(config):
    db = BewerbungsDB(config.db_path)
    ja = bewerbung(db, "Ja GmbH", 20, empfaenger="hr@ja.de")
    nein = bewerbung(db, "Nein GmbH", 20, empfaenger="hr@nein.de")
    spaeter = bewerbung(db, "Später GmbH", 20, empfaenger="hr@spaeter.de")
    email = FakeEmail()

    gesendet = Nachfassen(config, FakeDialog("j", "n", "s"), email).pruefe()

    assert gesendet == 1 and email.gesendet[0][0] == "hr@ja.de"
    assert "Admin" in email.gesendet[0][1] and "Max Muster" in email.gesendet[0][2]
    # "später" wird beim nächsten Mal wieder gefragt, die anderen nicht
    assert [b["id"] for b in db.nachfass_kandidaten(14)] == [spaeter]
    assert {ja, nein}.isdisjoint(b["id"] for b in db.nachfass_kandidaten(14))


def test_nachfassen_fehlgeschlagen_bleibt_offen(config):
    db = BewerbungsDB(config.db_path)
    bewerbung(db, "Firma GmbH", 20, empfaenger="hr@firma.de")
    dialog = FakeDialog("j")
    Nachfassen(config, dialog, FakeEmail(ok=False)).pruefe()
    assert len(db.nachfass_kandidaten(14)) == 1 and "fehlgeschlagen" in dialog.text


def test_imap_meldungen_umleitbar(config, monkeypatch):
    for name in ("IMAP_USER", "IMAP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    meldungen = []
    ImapReader(config, melde=meldungen.append).check_postfach()
    assert "IMAP_USER" in meldungen[0]
