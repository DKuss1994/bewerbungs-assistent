from unittest.mock import MagicMock, patch

import pytest
import requests

from src.database import BewerbungsDB
from src.imap_reader import ImapReader
from src.learning import LearningSystem
from src.llm_client import LLMClient, LLMError
from tests.conftest import FakeLLM

ABSAGE = "Leider müssen wir Ihnen mitteilen, dass wir Bewerber mit mehr Berufserfahrung suchen."


def test_mail_wird_zugeordnet_und_gelernt(config):
    db = BewerbungsDB(config.db_path)
    bid = db.insert_bewerbung("NORDLICHT GmbH", position="Support")
    reader = ImapReader(config, client=None)

    ergebnis = reader.verarbeite_mail(
        "<id1>", "Ihre Bewerbung", "hr@nordlicht.de", ABSAGE
    )
    assert ergebnis["bewerbung_id"] == bid
    assert (ergebnis["typ"], ergebnis["grund"]) == ("absage", "fehlende_erfahrung")
    assert db.get_all_bewerbungen()[0]["status"] == "absage"
    assert len(LearningSystem(config.db_path).get_active_learnings()) == 1

    # Gleiche Mail nochmal → wird ignoriert
    assert (
        reader.verarbeite_mail("<id1>", "Ihre Bewerbung", "hr@nordlicht.de", ABSAGE)
        is None
    )


def test_mail_ohne_passende_bewerbung_wird_ignoriert(config):
    reader = ImapReader(config, client=None)
    assert (
        reader.verarbeite_mail("<x>", "Newsletter", "shop@example.de", ABSAGE) is None
    )


def test_einladung_per_ki(config):
    db = BewerbungsDB(config.db_path)
    db.insert_bewerbung("Sirius", position="Dev")
    reader = ImapReader(config, client=FakeLLM("einladung"))
    ergebnis = reader.verarbeite_mail(
        "<y>", "Gespräch", "jobs@sirius.de", "Wir möchten Sie kennenlernen"
    )
    assert ergebnis["typ"] == "einladung"
    assert db.get_all_bewerbungen()[0]["status"] == "einladung"


def test_ki_ausfall_nutzt_keywords(config):
    reader = ImapReader(config, client=FakeLLM(fehler=LLMError("aus")))
    assert reader.erkenne_typ(ABSAGE) == "absage"
    assert reader.kategorisiere_absage("Gehaltsvorstellung zu hoch") == "gehalt"


# ---------- LLM-Client ----------
def _antwort(status=200, json_data=None):
    resp = MagicMock(status_code=status, ok=200 <= status < 300, text="")
    resp.json.return_value = json_data or {}
    return resp


def test_ollama_generate():
    client = LLMClient("ollama", ollama_url="http://localhost:11434/")
    with patch("src.llm_client.requests.post") as post:
        post.return_value = _antwort(json_data={"message": {"content": "Hallo"}})
        assert client.generate("Hi") == "Hallo"
        url = post.call_args.args[0]
        payload = post.call_args.kwargs["json"]
    assert url == "http://localhost:11434/api/chat"
    assert payload["model"] == "qwen2.5:3b-instruct" and payload["stream"] is False


def test_ollama_nicht_erreichbar():
    with patch(
        "src.llm_client.requests.post", side_effect=requests.exceptions.ConnectionError
    ):
        with pytest.raises(LLMError, match="ollama serve"):
            LLMClient("ollama").generate("Hi")


def test_ollama_modell_fehlt():
    with patch("src.llm_client.requests.post", return_value=_antwort(404)):
        with pytest.raises(LLMError, match="ollama pull"):
            LLMClient("ollama", model="gibtsnicht").generate("Hi")


def test_klassifiziere():
    client = LLMClient("ollama")
    with patch.object(client, "generate", return_value=" Team_Fit.\n"):
        assert client.klassifiziere("t", "?", ["gehalt", "team_fit"]) == "team_fit"
    with patch.object(client, "generate", return_value="keine Ahnung"):
        assert client.klassifiziere("t", "?", ["gehalt", "team_fit"]) is None


def test_mistral_braucht_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(ValueError):
        LLMClient("mistral")
