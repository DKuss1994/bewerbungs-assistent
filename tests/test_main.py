import pytest

from src.database import BewerbungsDB
from src.dialog import TerminalDialog
from src.kompetenzen import KompetenzenDB
from src.main import BewerbungsAgent, ordnername, sammle_anlagen
from tests.conftest import FakeDialog


def test_ordnername_ohne_leerzeichen():
    assert ordnername("ABC GmbH") == "ABC_GmbH"
    assert ordnername("  Müller & Söhne  GmbH ") == "Müller_Söhne_GmbH"
    assert ordnername("A/B: C") == "A_B_C"


def test_anlagen_nur_einmal_auch_bei_gleichem_ordner(config):
    echt = config.base_dir / "anlagen"
    echt.mkdir()
    (echt / "Zeugnisse.pdf").write_bytes(b"%PDF")
    config.data_dir.joinpath("zeugnisse").symlink_to(echt)
    config.data_dir.joinpath("zertifikate").symlink_to(echt)
    assert [p.name for p in sammle_anlagen(config)] == ["Zeugnisse.pdf"]


@pytest.fixture
def agent(config, monkeypatch):
    pytest.importorskip("weasyprint")
    monkeypatch.setattr("src.main.erstelle_llm_client", lambda c: None)
    anlagen = config.data_dir / "zeugnisse"
    anlagen.mkdir()
    (anlagen / "Zeugnisse.pdf").write_bytes(b"%PDF-1.4")
    (config.data_dir / "zertifikate").mkdir()
    return BewerbungsAgent(config, dialog=FakeDialog())


def test_portal_bewerbung_ohne_versand_gespeichert(agent, config):
    agent.dialog.antworten = ["j"]
    agent.email.send_application = lambda *a: pytest.fail("darf nicht senden")

    bid = agent.bewerben("Java und Linux", "Firma GmbH", "Admin", None)

    ordner = config.bewerbungen_ordner / "Firma_GmbH"
    assert (ordner / "Zeugnisse.pdf").exists()  # Anlagen zum Hochladen kopiert
    bewerbung = BewerbungsDB(config.db_path).get_all_bewerbungen()[0]
    assert bewerbung["id"] == bid and bewerbung["kanal"] == "portal"


def test_email_bewerbung_nein_speichert_nichts(agent, config):
    agent.dialog.antworten = ["n"]
    assert agent.bewerben("Java", "Firma GmbH", "Admin", "hr@firma.de") is None
    assert BewerbungsDB(config.db_path).get_all_bewerbungen() == []


def test_ganzer_ablauf_ueber_dialog(agent, config):
    """Alles läuft über den Dialog – Grundlage für den Chat-Bot."""
    gesendet = []
    agent.email.send_application = lambda *a: gesendet.append(a) or True
    # Docker ist unbekannt → Rückfrage; dann PDFs öffnen, dann senden
    agent.dialog.antworten = ["ja", "-", "o", "j"]

    bid = agent.bewerben("Java und Docker", "Firma GmbH", "Admin", "hr@firma.de")

    assert bid and gesendet[0][0] == "hr@firma.de"
    assert KompetenzenDB(config.db_path).hat_kompetenz("Docker") == "ja"
    assert [p.name for p in agent.dialog.geoeffnet][0].startswith("Lebenslauf_")
    assert "KOMPETENZ-CHECK" in agent.dialog.text and "VORSCHAU" in agent.dialog.text
    assert "📊 Passung:" in agent.dialog.text
    assert agent.dialog.fragen[-1][1] == ["j", "n", "o"]  # Knöpfe für den Bot


def test_abbruch_im_dialog_speichert_nichts(agent, config):
    agent.dialog.antworten = []  # keine Antwort = Abbruch
    assert agent.bewerben("Java", "Firma GmbH", "Admin", "hr@firma.de") is None
    assert BewerbungsDB(config.db_path).get_all_bewerbungen() == []


def test_terminal_dialog_eof_ist_abbruch(monkeypatch):
    def eof(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert TerminalDialog().frage("?") is None


def test_portal_email_nachtragen_und_senden(agent, config):
    gesendet = []
    agent.email.send_application = lambda *a: gesendet.append(a) or True
    # statt Portal: E-Mail nachtragen, dann neue Vorschau und senden
    agent.dialog.antworten = ["m", "hr@firma.de", "j"]

    bid = agent.bewerben("Java und Linux", "Firma GmbH", "Admin", None)

    assert gesendet[0][0] == "hr@firma.de"
    bewerbung = BewerbungsDB(config.db_path).get_all_bewerbungen()[0]
    assert bewerbung["id"] == bid and bewerbung["kanal"] == "email"
    assert agent.dialog.fragen[0][1] == ["j", "n", "o", "m"]
    assert agent.dialog.fragen[-1][1] == ["j", "n", "o"]  # jetzt Senden-Frage


def test_nach_absage_im_cooldown_wird_nachgefragt(agent, config):
    db = BewerbungsDB(config.db_path)
    alt = db.insert_bewerbung("Firma GmbH", position="Admin")
    db.add_rueckmeldung(alt, "absage")
    agent.dialog.antworten = ["n"]  # trotzdem bewerben? nein

    assert agent.bewerben("Java", "Firma", "Support", "hr@firma.de") is None
    assert "Cooldown" in agent.dialog.fragen[0][0]
    assert len(db.get_all_bewerbungen()) == 1


def test_fruehere_bewerbung_ohne_absage_nur_hinweis(agent, config):
    db = BewerbungsDB(config.db_path)
    alt = db.insert_bewerbung("Firma GmbH", position="Admin")
    agent.email.send_application = lambda *a: True
    agent.dialog.antworten = ["j"]

    bid = agent.bewerben("Java", "Firma", "Support", "hr@firma.de")

    assert "schon beworben" in agent.dialog.text
    neu = [b for b in db.get_all_bewerbungen() if b["id"] == bid][0]
    assert neu["previous_id"] == alt and neu["is_reapply"] == 1


def test_kontakt_link_und_anzeige_werden_gespeichert(agent, config):
    anzeige = "Java\nAnsprechpartner: Herr Max Beispiel, Tel. 030 1234567"
    agent.email.send_application = lambda *a: True
    agent.dialog.antworten = ["j"]

    agent.bewerben(anzeige, "Firma", "Admin", "hr@firma.de", link="https://x.de/1")

    assert "Herr Max Beispiel · 030 1234567" in agent.dialog.text  # Vorschau
    b = BewerbungsDB(config.db_path).get_all_bewerbungen()[0]
    assert (b["telefon"], b["ansprechpartner"], b["link"]) == (
        "030 1234567",
        "Herr Max Beispiel",
        "https://x.de/1",
    )
    ordner = config.bewerbungen_ordner / "Firma"
    assert (ordner / "Stellenanzeige.txt").read_text(encoding="utf-8") == anzeige
