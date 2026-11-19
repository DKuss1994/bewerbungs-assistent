"""KI-Client: lokal über Ollama (Standard) oder Mistral-API (optional)."""

import json
import os
import re
import time
from typing import List, Optional

import requests

from src.config import AppConfig

SYSTEM_PROMPT = (
    "Du bist ein deutschsprachiger Assistent. "
    "Antworte AUSSCHLIESSLICH auf Deutsch. "
    "Verwende keine anderen Sprachen (kein Englisch, kein Chinesisch)."
)

# Schnelles Modell für kurze Aufgaben (passt komplett in 4 GB VRAM)
STANDARD_MODELLE = {
    "ollama": "qwen2.5:3b-instruct",
    "mistral": "mistral-small-latest",
}
# Besseres Modell für Fließtext (langsamer, wird nach Gebrauch entladen)
STANDARD_TEXT_MODELLE = {
    "ollama": "qwen2.5:7b-instruct",
    "mistral": "mistral-small-latest",
}


class LLMError(RuntimeError):
    """Die KI ist nicht erreichbar oder hat einen Fehler geliefert."""


class LLMClient:
    def __init__(
        self,
        provider: str = "ollama",
        model: str = "",
        ollama_url: str = "http://localhost:11434",
        api_key: Optional[str] = None,
        keep_alive: str = "10m",
    ):
        if provider not in STANDARD_MODELLE:
            raise ValueError(f"❌ Unbekannter LLM_PROVIDER: {provider}")
        self.provider = provider
        self.model = model or STANDARD_MODELLE[provider]
        self.ollama_url = ollama_url.rstrip("/")
        self.keep_alive = keep_alive
        self.api_key = api_key or os.getenv("MISTRAL_API_KEY", "")

        if provider == "mistral" and not self.api_key:
            raise ValueError("❌ MISTRAL_API_KEY fehlt in .env!")

    @classmethod
    def from_config(cls, config: AppConfig, text: bool = False) -> "LLMClient":
        """text=True: größeres Modell für Fließtext, wird schnell wieder entladen."""
        if text:
            return cls(
                provider=config.llm_provider,
                model=config.llm_model_text
                or STANDARD_TEXT_MODELLE.get(config.llm_provider, ""),
                ollama_url=config.ollama_url,
                keep_alive="1m",
            )
        return cls(
            provider=config.llm_provider,
            model=config.llm_model,
            ollama_url=config.ollama_url,
        )

    # ---------- ÖFFENTLICHE API ----------
    def generate(
        self,
        prompt: str,
        temperature: float = 0.3,
        timeout: int = 180,
    ) -> str:
        """Sendet einen Prompt und gibt die Antwort zurück. Wirft LLMError."""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        if self.provider == "ollama":
            return self._ollama(messages, temperature, timeout)
        return self._mistral(messages, temperature, timeout)

    def generate_json(self, prompt: str, temperature: float = 0.1) -> dict:
        """Generiert eine JSON-Antwort."""
        response = self.generate(
            prompt + "\n\nAntworte NUR im JSON-Format.", temperature
        )
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise LLMError(f"❌ Kein JSON gefunden: {response[:200]}")

    def klassifiziere(
        self, text: str, frage: str, optionen: List[str]
    ) -> Optional[str]:
        """Lässt die KI genau eine der Optionen wählen. None bei Fehler/ungültig."""
        prompt = (
            f"{frage}\n\nTEXT:\n{text[:3000]}\n\n"
            f"Antworte mit GENAU EINEM dieser Wörter: {', '.join(optionen)}\n"
            "Keine Erklärung."
        )
        try:
            antwort = self.generate(prompt, temperature=0.0, timeout=120)
        except LLMError:
            return None
        antwort = antwort.strip().lower()
        for option in optionen:
            if option.lower() in antwort:
                return option
        return None

    def ist_verfuegbar(self) -> bool:
        """Prüft schnell, ob die KI erreichbar ist."""
        if self.provider == "mistral":
            return bool(self.api_key)
        try:
            resp = requests.get(f"{self.ollama_url}/api/tags", timeout=3)
            return resp.ok
        except requests.exceptions.RequestException:
            return False

    # ---------- PROVIDER ----------
    def _ollama(self, messages: list, temperature: float, timeout: int) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature},
        }
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/chat", json=payload, timeout=timeout
            )
        except requests.exceptions.ConnectionError:
            raise LLMError("❌ Ollama läuft nicht! Starte es mit: ollama serve")
        except requests.exceptions.Timeout:
            raise LLMError(f"❌ Ollama hat nach {timeout}s nicht geantwortet!")

        if resp.status_code == 404:
            raise LLMError(
                f"❌ Modell '{self.model}' fehlt. Lade es mit: ollama pull {self.model}"
            )
        if not resp.ok:
            raise LLMError(f"❌ Ollama-Fehler {resp.status_code}: {resp.text[:200]}")
        return resp.json()["message"]["content"]

    def _mistral(
        self, messages: list, temperature: float, timeout: int, max_retries: int = 5
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        for versuch in range(max_retries):
            # Rate-Limit der kostenlosen API (1 Anfrage/Sekunde)
            time.sleep(2)
            try:
                resp = requests.post(
                    "https://api.mistral.ai/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )
            except requests.exceptions.ConnectionError:
                raise LLMError("❌ Keine Internetverbindung zur Mistral API!")
            except requests.exceptions.Timeout:
                raise LLMError(f"❌ Mistral hat nach {timeout}s nicht geantwortet!")

            if resp.status_code == 429 and versuch < max_retries - 1:
                wartezeit = 15 * (versuch + 1)
                print(f"   ⏳ Rate-Limit. Warte {wartezeit}s...")
                time.sleep(wartezeit)
                continue
            if resp.status_code == 401:
                raise LLMError("❌ API-Key ungültig! Prüfe MISTRAL_API_KEY in .env")
            if not resp.ok:
                raise LLMError(f"❌ Mistral-Fehler {resp.status_code}")
            return resp.json()["choices"][0]["message"]["content"]

        raise LLMError("❌ Rate-Limit endgültig erreicht!")
