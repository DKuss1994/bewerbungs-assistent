from src.llm_client import LLMError
from src.main import frage_stellendaten
from src.stellen_info import StellenInfo, bereinige_position, erkenne_stelle
from tests.conftest import FakeDialog, FakeLLM

# Aufbau wie bei der Jobbörse der Arbeitsagentur
JOBBOERSE = """Support 2nd Level (m/w/d)
bei
Muster IT GmbH
Arbeitsort
Bielefeld
Fragen zum Datenschutz: datenschutz@muster-it.de
Bewerbungen bitte an: bewerbung@muster-it.de oder info@muster-it.de."""

# Firmenname nur im Fließtext, Titel ohne (m/w/d)
FLIESSTEXT = """Technischer Kundensupport für Energiemanagement

Über uns
Wir bauen Speicher. Schick uns deine Unterlagen an jobs@beispiel.de.

Dein Team der BEISPIEL GmbH"""


def test_erkennt_jobboersen_anzeige():
    info = erkenne_stelle(JOBBOERSE)
    assert info == StellenInfo(
        "Muster IT GmbH", "Support 2nd Level", "bewerbung@muster-it.de"
    )


def test_arbeitgeber_zeile_mit_co_kg():
    text = "Linux Administrator (w/m/d)\nArbeitgeber: Sirius GmbH & Co.KG\nText"
    info = erkenne_stelle(text)
    assert info.unternehmen == "Sirius GmbH & Co.KG"
    assert info.position == "Linux Administrator"


def test_firma_aus_fliesstext_ohne_ki_bleibt_position_offen():
    info = erkenne_stelle(FLIESSTEXT)
    assert info.unternehmen == "BEISPIEL GmbH"
    assert info.email == "jobs@beispiel.de"
    assert info.position is None


def test_ki_fuellt_nur_luecken_und_nur_mit_text_aus_anzeige():
    ki = FakeLLM(
        '{"position": "Technischer Kundensupport", "unternehmen": "Erfunden AG"}'
    )
    info = erkenne_stelle(FLIESSTEXT, ki)
    assert info.position == "Technischer Kundensupport"
    assert info.unternehmen == "BEISPIEL GmbH"  # Regel-Ergebnis bleibt


def test_ki_erfindet_position_wird_verworfen():
    info = erkenne_stelle(FLIESSTEXT, FakeLLM('{"position": "Senior Architekt"}'))
    assert info.position is None


def test_ki_fehler_oder_kein_json():
    assert erkenne_stelle(FLIESSTEXT, FakeLLM(fehler=LLMError("weg"))).position is None
    assert erkenne_stelle(FLIESSTEXT, FakeLLM("keine Ahnung")).position is None


def test_bereinige_position():
    assert (
        bereinige_position("Software Developer (w/d/m) Technical Support [befristet]")
        == "Software Developer Technical Support"
    )
    assert bereinige_position("Admin (all genders)") == "Admin"


def test_frage_stellendaten_enter_uebernimmt_eingabe_ueberschreibt(monkeypatch):
    eingaben = iter(["", "Junior Admin", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(eingaben))
    info = frage_stellendaten(StellenInfo("Firma GmbH", "Admin", "a@b.de"))
    assert info == StellenInfo("Firma GmbH", "Junior Admin", "a@b.de")


def test_frage_stellendaten_fehlender_wert_bricht_ab(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert frage_stellendaten(StellenInfo("Firma GmbH", None, "a@b.de")) is None


def test_frage_stellendaten_leere_email_heisst_portal(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    info = frage_stellendaten(StellenInfo("Firma GmbH", "Admin", None))
    assert info == StellenInfo("Firma GmbH", "Admin", None)


def test_ki_verschachtelte_antwort_wird_gelesen():
    antwort = (
        '{"position": {"unternehmen": "X", "position": "Technischer Kundensupport"}}'
    )
    assert erkenne_stelle(FLIESSTEXT, FakeLLM(antwort)).position == (
        "Technischer Kundensupport"
    )


def test_frage_stellendaten_email_nachtragen_mit_pruefung():
    dialog = FakeDialog("", "", "hr@firma", "hr@firma.de")
    info = frage_stellendaten(StellenInfo("Firma GmbH", "Admin", None), dialog=dialog)
    assert info.email == "hr@firma.de"
    assert "keine gültige E-Mail-Adresse" in dialog.text  # Tippfehler fällt auf
