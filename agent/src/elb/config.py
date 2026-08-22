"""Central configuration. Everything tunable lives here, read from env once."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Repo root = .../agent/src/elb/config.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GLOSSARY = REPO_ROOT / "shared" / "critical_terms.json"


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # --- LiveKit -----------------------------------------------------------
    livekit_url: str = field(default_factory=lambda: os.getenv("LIVEKIT_URL", ""))
    livekit_api_key: str = field(default_factory=lambda: os.getenv("LIVEKIT_API_KEY", ""))
    livekit_api_secret: str = field(default_factory=lambda: os.getenv("LIVEKIT_API_SECRET", ""))

    # --- Model providers ---------------------------------------------------
    deepgram_api_key: str = field(default_factory=lambda: os.getenv("DEEPGRAM_API_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    elevenlabs_api_key: str = field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY", ""))

    # Translation runs on Haiku for latency (explicit product decision).
    # Final report synthesis runs on Opus for judgement quality; it is off the
    # critical path (call is already over).
    translate_model: str = field(
        default_factory=lambda: os.getenv("ELB_TRANSLATE_MODEL", "claude-haiku-4-5")
    )
    extract_model: str = field(
        default_factory=lambda: os.getenv("ELB_EXTRACT_MODEL", "claude-haiku-4-5")
    )
    report_model: str = field(
        default_factory=lambda: os.getenv("ELB_REPORT_MODEL", "claude-opus-5")
    )

    stt_model: str = field(default_factory=lambda: os.getenv("ELB_STT_MODEL", "nova-3"))
    tts_model: str = field(default_factory=lambda: os.getenv("ELB_TTS_MODEL", "eleven_flash_v2_5"))
    tts_voice_operator: str = field(
        default_factory=lambda: os.getenv("ELB_TTS_VOICE_OPERATOR", "EXAVITQu4vr4xnSDxMaL")
    )
    tts_voice_caller: str = field(
        default_factory=lambda: os.getenv("ELB_TTS_VOICE_CALLER", "ODq5zmih8GrVes37Dizd")
    )

    # --- Latency / state machine ------------------------------------------
    # Budget from PROCESSING -> first audio byte. On expiry we fall back to
    # speaking the untranslated source rather than stalling the call.
    translate_timeout_s: float = field(default_factory=lambda: _f("ELB_TRANSLATE_TIMEOUT_S", 3.5))
    stt_silence_timeout_s: float = field(default_factory=lambda: _f("ELB_STT_TIMEOUT_S", 4.0))

    # --- Comprehension / early notification -------------------------------
    extract_interval_s: float = field(default_factory=lambda: _f("ELB_EXTRACT_INTERVAL_S", 6.0))
    # See docs/DECISIONS.md for the derivation of these thresholds.
    notify_min_location_conf: float = field(default_factory=lambda: _f("ELB_NOTIFY_LOC_CONF", 0.75))
    notify_min_type_conf: float = field(default_factory=lambda: _f("ELB_NOTIFY_TYPE_CONF", 0.70))
    notify_stable_passes: int = field(default_factory=lambda: _i("ELB_NOTIFY_STABLE_PASSES", 2))
    notify_min_elapsed_s: float = field(default_factory=lambda: _f("ELB_NOTIFY_MIN_ELAPSED_S", 12.0))

    # --- Storage / delivery ------------------------------------------------
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_service_key: str = field(
        default_factory=lambda: os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    )
    web_base_url: str = field(
        default_factory=lambda: os.getenv("ELB_WEB_BASE_URL", "http://localhost:3000").rstrip("/")
    )
    notify_endpoint: str = field(default_factory=lambda: os.getenv("ELB_NOTIFY_ENDPOINT", ""))
    report_signing_secret: str = field(
        default_factory=lambda: os.getenv("ELB_REPORT_SIGNING_SECRET", "")
    )
    internal_api_token: str = field(default_factory=lambda: os.getenv("ELB_INTERNAL_TOKEN", ""))
    link_ttl_early_s: int = field(default_factory=lambda: _i("ELB_LINK_TTL_EARLY_S", 6 * 3600))
    link_ttl_final_s: int = field(default_factory=lambda: _i("ELB_LINK_TTL_FINAL_S", 72 * 3600))

    # --- Misc --------------------------------------------------------------
    glossary_path: Path = field(
        default_factory=lambda: Path(os.getenv("ELB_GLOSSARY_PATH", str(DEFAULT_GLOSSARY)))
    )
    operator_language: str = field(default_factory=lambda: os.getenv("ELB_OPERATOR_LANG", "es"))
    offline_mode: bool = field(default_factory=lambda: _b("ELB_OFFLINE", False))
    reports_dir: Path = field(
        default_factory=lambda: Path(os.getenv("ELB_REPORTS_DIR", str(REPO_ROOT / "reports")))
    )

    def missing_required(self) -> list[str]:
        """Keys that must be present for a real (non-offline) run."""
        required = {
            "LIVEKIT_URL": self.livekit_url,
            "LIVEKIT_API_KEY": self.livekit_api_key,
            "LIVEKIT_API_SECRET": self.livekit_api_secret,
            "DEEPGRAM_API_KEY": self.deepgram_api_key,
            "ANTHROPIC_API_KEY": self.anthropic_api_key,
            "ELEVENLABS_API_KEY": self.elevenlabs_api_key,
        }
        return [k for k, v in required.items() if not v]


settings = Settings()

# Identities are the contract between the agent, the Android app and the web
# console. Selective subscription on both clients keys off these exact strings.
IDENTITY_CALLER = "caller"
IDENTITY_OPERATOR = "operator"
TRACK_TO_OPERATOR = "interpreter-to-operator"
TRACK_TO_CALLER = "interpreter-to-caller"
DATA_TOPIC = "elb"
