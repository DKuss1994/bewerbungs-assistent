from src.cover_letter import CoverLetterGenerator
from src.database import BewerbungsDB
from src.email_handler import EmailHandler
from src.imap_reader import ImapReader
from src.learning import LERN_REGELN, LearningSystem
from src.resume_builder import ResumeBuilder
from tests.conftest import FakeLLM

STANDARD_ABSAGE = (
    "Vielen Dank für Ihr Interesse und Ihr Profil. Nach sorgfältiger Prüfung "
    "haben wir uns leider für andere Kandidaten mit passender Erfahrung "
    "entschieden.\n\nIhr Recruiting-Team der Muster GmbH"
)


def _absage(config, body):
    BewerbungsDB(config.db_path).insert_bewerbung("Muster GmbH", position="Dev")
    return ImapReader(config).verarbeite_mail("id-1", "Ihre Bewerbung", "x@y.de", body)


def test_standard_absage_lernt_nichts(config):
    ergebnis = _absage(config, STANDARD_ABSAGE)
    assert ergebnis["typ"] == "absage"
    assert ergebnis["grund"] == "sonstiges"
    assert LearningSystem(config.db_path).get_active_learnings() == []


def test_ausdruecklicher_grund_wird_gelernt(config):
    body = (
        "Leider müssen wir Ihnen absagen, da wir Kandidaten mit mehr\n"
        "Berufserfahrung gesucht haben. Muster GmbH"
    )
    assert _absage(config, body)["grund"] == "fehlende_erfahrung"
    regeln = LearningSystem(config.db_path).get_active_learnings()
    assert [r["aenderung"] for r in regeln] == [LERN_REGELN["fehlende_erfahrung"]]


def test_veraltete_regeln_werden_ersetzt(config):
    alt = LearningSystem(config.db_path)
    alt.add_learning(
        "anschreiben",
        "nicht_qualifiziert",
        "Stelle konkrete, nachprüfbare Ergebnisse der Projekte heraus "
        "(z.B. Anzahl Tests, gesparte Zeit).",
    )
    alt.add_learning(
        "anschreiben",
        "team_fit",
        "Betone Zusammenarbeit mit Technikern, Kollegen und Kunden.",
    )

    regeln = LearningSystem(config.db_path).get_active_learnings()
    assert [r["aenderung"] for r in regeln] == [LERN_REGELN["nicht_qualifiziert"]]


def test_ki_absatz_mit_erfundener_zahl_unbrauchbar(config):
    projekte = ResumeBuilder(config).waehle_projekte("Java")
    antwort = (
        "Um meine Kenntnisse praktisch zu vertiefen, habe ich BackendApp gebaut "
        "und damit 40 % Zeit beim Testen gespart."
    )
    text = CoverLetterGenerator(config, FakeLLM(antwort)).generate(
        "Java", "F", "D", projekte
    )["text"]
    assert "40 %" not in text
    assert "REST-API mit Tests." in text  # Standardtext statt KI


def test_mail_hat_datum_message_id_und_pdf_anhang(tmp_path):
    anhang = tmp_path / "Zeugnis_Müller.pdf"
    anhang.write_bytes(b"%PDF-1.4 test")
    handler = EmailHandler()
    handler.sender = "ich@example.org"

    msg = handler.baue_nachricht("hr@firma.de", "Bewerbung", "Hallo", [anhang])

    assert msg["Date"]
    assert msg["Message-ID"].endswith("@example.org>")
    teil = msg.get_payload()[1]
    assert teil.get_content_type() == "application/pdf"
    assert teil.get_filename() == "Zeugnis_Müller.pdf"
    assert teil.get_payload(decode=True) == b"%PDF-1.4 test"
