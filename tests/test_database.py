import sqlite3

from src.database import BewerbungsDB, normalisiere_firma
from src.erfolg import ErfolgsTracker
from src.learning import LearningSystem


def test_migration_fuegt_neue_spalten_zu_alter_tabelle_hinzu(tmp_path):
    db_path = tmp_path / "alt.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE bewerbungen (
                id INTEGER PRIMARY KEY AUTOINCREMENT, datum_gesendet TEXT NOT NULL,
                unternehmen TEXT NOT NULL, branche TEXT, position TEXT, tech_stack TEXT,
                wochentag_gesendet TEXT, monat_gesendet INTEGER, agent_self_score INTEGER,
                status TEXT DEFAULT 'gesendet', is_reapply INTEGER DEFAULT 0,
                previous_id INTEGER NULL, cooldown_until TEXT)
        """)
        conn.execute(
            "INSERT INTO bewerbungen (datum_gesendet, unternehmen) VALUES ('2026-01-01', 'Alt')"
        )

    db = BewerbungsDB(db_path)
    alle = db.get_all_bewerbungen()
    assert alle[0]["unternehmen"] == "Alt"
    assert "projekte" in alle[0] and "berufsrichtung" in alle[0]


def test_duplikat_erkennung(tmp_path):
    db = BewerbungsDB(tmp_path / "t.db")
    db.insert_bewerbung("Firma", position="Dev")
    assert db.hat_doppelte_bewerbung("Firma", "Dev")
    assert not db.hat_doppelte_bewerbung("Firma", "Support")


def test_normalisiere_firma():
    assert normalisiere_firma("NORDLICHT GmbH") == "nordlicht"


def test_finde_bewerbung(tmp_path):
    db = BewerbungsDB(tmp_path / "t.db")
    db.insert_bewerbung("NORDLICHT GmbH", position="Support")
    db.insert_bewerbung("Sirius Systems AG", position="Dev")

    assert (
        db.finde_bewerbung("jobs@nordlicht.de", "Ihre Bewerbung")["unternehmen"]
        == "NORDLICHT GmbH"
    )
    assert (
        db.finde_bewerbung("Sirius Systems <hr@sirius.de>")["unternehmen"]
        == "Sirius Systems AG"
    )
    assert db.finde_bewerbung("newsletter@shop.de", "Angebot") is None


def test_rueckmeldung_setzt_status_und_dauer(tmp_path):
    db = BewerbungsDB(tmp_path / "t.db")
    bid = db.insert_bewerbung("Firma", position="Dev")
    db.add_rueckmeldung(bid, "absage", "tech_stack")

    assert db.get_all_bewerbungen()[0]["status"] == "absage"
    with sqlite3.connect(db.db_path) as conn:
        typ, grund, dauer = conn.execute(
            "SELECT typ, grund_kategorie, dauer_tage FROM rueckmeldungen"
        ).fetchone()
    assert (typ, grund, dauer) == ("absage", "tech_stack", 0)


def test_erfolgs_tracker_bonus(tmp_path):
    db = BewerbungsDB(tmp_path / "t.db")
    a = db.insert_bewerbung(
        "A", projekte=["backend", "linux"], berufsrichtung="itsupport"
    )
    b = db.insert_bewerbung("B", projekte=["linux"], berufsrichtung="itsupport")
    db.insert_bewerbung("C", projekte=["app"])  # noch offen → zählt nicht
    db.add_rueckmeldung(a, "einladung")
    db.add_rueckmeldung(b, "absage")

    tracker = ErfolgsTracker(db.db_path)
    assert tracker.projekt_bonus() == {"backend": 1.0, "linux": 0.75}
    assert tracker.statistik()["berufsrichtung"]["itsupport"] == {
        "einladung": 1,
        "absage": 1,
    }


def test_learning_keine_duplikate(tmp_path):
    ls = LearningSystem(tmp_path / "t.db")
    assert ls.add_learning("anschreiben", "x", "Regel A")
    assert not ls.add_learning("anschreiben", "y", "Regel A")
    assert ls.apply_learning_to_prompt("P", "anschreiben").endswith("- Regel A\n")
    assert ls.apply_learning_to_prompt("P", "skills") == "P"


def test_duplikat_ignoriert_rechtsform_und_schreibweise(tmp_path):
    db = BewerbungsDB(tmp_path / "t.db")
    db.insert_bewerbung("ABC GmbH", position="IT-Support (m/w/d)")
    assert db.hat_doppelte_bewerbung("ABC", "it-support  (m/w/d)")
    assert db.letzte_bewerbung_bei("abc gmbh")["unternehmen"] == "ABC GmbH"
    assert db.letzte_bewerbung_bei("Andere AG") is None


def test_cooldown_verschwindet_nach_neuer_bewerbung(config):
    from src.cooldown import CooldownManager

    db = BewerbungsDB(config.db_path)
    alt = db.insert_bewerbung("Firma", position="Dev")
    db.add_rueckmeldung(alt, "absage")
    with sqlite3.connect(config.db_path) as conn:  # Cooldown abgelaufen
        conn.execute("UPDATE bewerbungen SET cooldown_until = '2020-01-01'")
    cooldown = CooldownManager(config)
    assert [k["id"] for k in cooldown.get_rebewerbungs_kandidaten()] == [alt]

    neu = db.insert_bewerbung("Firma", position="Dev", previous_id=alt)
    assert cooldown.get_rebewerbungs_kandidaten() == []
    assert db.get_all_bewerbungen()[0]["is_reapply"] == 1
    # Die alte Absage bleibt eine Absage – sonst gingen Lerndaten verloren
    assert [b["status"] for b in db.get_all_bewerbungen() if b["id"] == alt] == [
        "absage"
    ]
    assert neu != alt


def test_verbindungen_werden_geschlossen(tmp_path):
    """Viele Abfragen dürfen keine Dateihandles offen lassen (sonst
    „unable to open database file“ mitten in der Stellensuche)."""
    import os

    import pytest

    from src.stellen import StellenDB

    if not os.path.isdir("/proc/self/fd"):
        pytest.skip("nur unter Linux messbar")
    db = StellenDB(tmp_path / "test.db")
    vorher = len(os.listdir("/proc/self/fd"))
    for i in range(200):
        db.bekannt("ba", str(i))
    assert len(os.listdir("/proc/self/fd")) <= vorher + 2
