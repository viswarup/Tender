"""
LLM Router — Centralized fallback routing chain for Gemini API, OpenRouter, and Ollama.
Handles client initialization lazily, robust balanced-brace JSON parsing, and tracks
which backend answered.
"""

import os
import json
import logging
import time
import re
import threading
from pathlib import Path
from typing import Optional, Any
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Resolve workspace directory dynamically relative to this file
WORKSPACE = Path(__file__).parent.parent
load_dotenv(dotenv_path=WORKSPACE / ".env")

# Thread-local state to track the last used backend for auditing/logging
_local_state = threading.local()

_google_client = None
_openrouter_client = None
_initialized = False
_init_lock = threading.Lock()


def get_last_used_backend() -> str:
    """Retrieve the name of the backend that successfully answered the last request."""
    return getattr(_local_state, "last_used_backend", "Ollama")


def _set_last_used_backend(backend: str):
    """Set the name of the backend that successfully answered the last request."""
    _local_state.last_used_backend = backend


def init_clients():
    """Lazily initialize Google GenAI and OpenRouter clients under a thread lock."""
    global _google_client, _openrouter_client, _initialized
    if _initialized:
        return

    with _init_lock:
        if _initialized:
            return

        gemini_key = os.environ.get("GEMINI_API_KEY")
        openrouter_key = os.environ.get("OPENROUTER_API_KEY")

        if gemini_key:
            try:
                from google import genai
                from google.genai import types
                _google_client = genai.Client(
                    api_key=gemini_key,
                    http_options=types.HttpOptions(timeout=60_000)
                )
                logger.info("Successfully initialized Google GenAI Client.")
            except Exception as e:
                logger.error(f"Failed to initialize Google GenAI SDK: {e}")
        else:
            logger.warning("GEMINI_API_KEY not found in environment.")

        if openrouter_key:
            try:
                from openai import OpenAI
                _openrouter_client = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=openrouter_key,
                    timeout=15.0,
                    max_retries=0
                )
                logger.info("Successfully initialized OpenAI client for OpenRouter.")
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI client for OpenRouter: {e}")
        else:
            logger.warning("OPENROUTER_API_KEY not found in environment.")

        _initialized = True


def get_google_client():
    init_clients()
    return _google_client


def get_openrouter_client():
    init_clients()
    return _openrouter_client


def parse_json_response(response: str) -> dict:
    """
    Robustly parse JSON from an LLM response using balanced-brace matching.
    Handles raw JSON, markdown-fenced JSON blocks, and leading/trailing junk.
    """
    text = response.strip()
    if not text:
        raise json.JSONDecodeError("Empty response", "", 0)

    # 1. Direct parsing attempt
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Markdown fence parsing attempt
    if '```' in text:
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

    # 3. Balanced brace parsing attempt
    brace_start = text.find('{')
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start:i+1])
                    except json.JSONDecodeError:
                        break

    raise json.JSONDecodeError(f"No JSON block could be extracted from: {text[:200]}...", text, 0)


def _query_llm(prompt: str, local_model: str = "llama3:8b", system: str = "", response_format: str = "text") -> str:
    """
    Unified query fallback chain: Google Gemini -> OpenRouter -> Local Ollama.
    Returns raw string response.
    """
    # 1. Google Gemini
    g_client = get_google_client()
    if g_client:
        models_to_try = ['gemini-2.5-flash', 'gemini-1.5-flash']
        for model_name in models_to_try:
            max_retries = 3
            backoff_factor = 2
            for attempt in range(max_retries):
                logger.info(f"Querying Google Gemini API ({model_name}) [{response_format}] - Attempt {attempt + 1}...")
                try:
                    from google.genai import types
                    config = types.GenerateContentConfig(
                        response_mime_type="application/json" if response_format == "json" else "text/plain",
                        temperature=0.1 if response_format == "json" else 0.3,
                        system_instruction=system or None
                    )
                    response = g_client.models.generate_content(
                        model=model_name,
                        contents=[prompt],
                        config=config
                    )
                    if response.text:
                        _set_last_used_backend(f"Gemini ({model_name})")
                        return response.text.strip()
                except Exception as e:
                    logger.warning(f"Google Gemini API ({model_name}) attempt {attempt + 1} failed: {e}")
                    if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                        logger.warning("Gemini quota exhausted (429). Failing fast to next service.")
                        break
                    if attempt < max_retries - 1:
                        sleep_time = backoff_factor ** attempt
                        time.sleep(sleep_time)
            logger.warning(f"Google Gemini API ({model_name}) query failed. Trying next model/service...")

    # 2. OpenRouter API
    or_client = get_openrouter_client()
    if or_client:
        models_to_try = [
            "google/gemini-2.5-flash",
            "meta-llama/llama-3.3-70b-instruct:free"
        ]
        for model_name in models_to_try:
            logger.info(f"Querying OpenRouter API ({model_name}) [{response_format}]...")
            try:
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": prompt})

                completion = or_client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=0.1 if response_format == "json" else 0.3
                )
                res_text = completion.choices[0].message.content
                if res_text:
                    _set_last_used_backend(f"OpenRouter ({model_name})")
                    return res_text.strip()
            except Exception as e:
                logger.warning(f"OpenRouter query for model {model_name} failed: {e}")
        logger.warning("OpenRouter queries failed. Trying local Ollama fallback...")

    # 3. Local Ollama
    from pipeline.ollama_client import ollama
    logger.info(f"Calling local Ollama fallback ({local_model}) [{response_format}]...")
    try:
        response = ollama.generate(
            model=local_model,
            prompt=prompt,
            system=system,
            temperature=0.1 if response_format == "json" else 0.3,
            format_json=(response_format == "json")
        )
        if response:
            _set_last_used_backend(f"Ollama ({local_model})")
            return response.strip()
    except Exception as e:
        logger.error(f"Local Ollama fallback failed: {e}")

    return ""


def query_llm_json(prompt: str, local_model: str = "llama3:8b", system: str = "") -> dict:
    """
    Unified JSON query fallback chain: Google Gemini -> OpenRouter -> Local Ollama.
    Returns a dictionary parsed from the LLM's response.
    """
    res = _query_llm(prompt, local_model, system, "json")
    if res:
        try:
            return parse_json_response(res)
        except Exception as e:
            logger.error(f"Failed to parse JSON response: {e}")
    return {}


def query_llm_text(prompt: str, local_model: str = "llama3:8b", system: str = "") -> str:
    """
    Unified text query fallback chain: Google Gemini -> OpenRouter -> Local Ollama.
    Returns the raw string response from the LLM.
    """
    return _query_llm(prompt, local_model, system, "text")

