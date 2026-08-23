"""Tests for the parts that must be right regardless of any API being up.

These run with zero credentials - that is the point. If the glossary, the
signing scheme, the report renderer or the notification gate regress, this
catches it offline in under a second.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from elb.comprehension import Assessed, ComprehensionState, Extraction, Victim
from elb.config import DEFAULT_GLOSSARY, Settings
from elb.glossary import Glossary, normalize
from elb.report import Scope, ReportData, TranscriptLine, build_report, percentile
from elb.signing import BadToken, SCOPE_FAMILY, SCOPE_OPERATOR, mint, verify

SECRET = "test-secret-do-not-use-in-production"


@pytest.fixture(scope="module")
def gl() -> Glossary:
    return Glossary(DEFAULT_GLOSSARY)


# ------------------------------------------------------------------ glossary
def test_normalize_strips_diacritics_and_case():
    assert normalize("No ESTÁ Respirando") == "no esta respirando"
    assert normalize("n’arrive pas à respirer") == "n'arrive pas a respirer"


def test_detects_accented_input_against_ascii_patterns(gl: Glossary):
    hits = gl.detect("Mi hijo no está respirando", "es")
    assert "not_breathing" in {h.term_id for h in hits}


def test_negation_wins_over_affirmative(gl: Glossary):
    """The whole point of the glossary: 'no tiene pulso' is not 'pulso'."""
    ids = {h.term_id for h in gl.detect("el hombre no tiene pulso", "es")}
    assert "no_pulse" in ids
    assert "has_pulse" not in ids

    ids = {h.term_id for h in gl.detect("she is not breathing", "en")}
    assert "not_breathing" in ids
    assert "breathing" not in ids


def test_affirmative_still_detected(gl: Glossary):
    ids = {h.term_id for h in gl.detect("he is breathing normally", "en")}
    assert "breathing" in ids
    assert "not_breathing" not in ids


def test_fixed_rendering_is_language_specific(gl: Glossary):
    fixed = {h.term_id: h.fixed_target for h in gl.detect_for("my son is not breathing", "en", "es")}
    assert fixed["not_breathing"] == "NO RESPIRA"
    assert fixed["child"] == "MENOR DE EDAD"
    fixed = {h.term_id: h.fixed_target for h in gl.detect_for("mi hijo no respira", "es", "en")}
    assert fixed["not_breathing"] == "NOT BREATHING"


def test_locked_block_is_injected_per_direction(gl: Glossary):
    block = gl.locked_block("en", "es")
    assert "NO RESPIRA" in block
    assert "!!" in block  # critical terms are flagged for the model
    assert "NOT BREATHING" not in block  # that is the source side, not the target


def test_enforce_repairs_a_dropped_critical_term(gl: Glossary):
    """The model silently dropped the negation. We must put it back."""
    src = "my son is not breathing"
    bad = "mi hijo esta bien"
    fixed, repaired = gl.enforce(src, bad, "en", "es")
    assert "not_breathing" in repaired
    assert "NO RESPIRA" in fixed


def test_enforce_is_a_noop_when_the_model_got_it_right(gl: Glossary):
    src = "my son is not breathing"
    good = "Mi hijo NO RESPIRA"
    fixed, repaired = gl.enforce(src, good, "en", "es")
    assert repaired == []
    assert fixed == good


def test_contradiction_detection_catches_polarity_flip(gl: Glossary):
    src = "she is not breathing"
    flipped = "ella SI RESPIRA"
    assert "not_breathing" in gl.contradicts(src, flipped, "en", "es")


def test_every_term_round_trips_in_every_language(gl: Glossary):
    """Each term must be detectable from its own match phrases in each lang."""
    for term in gl.terms.values():
        for lang, phrases in term.match.items():
            for phrase in phrases:
                ids = {h.term_id for h in gl.detect(phrase, lang)}
                assert ids, f"{term.id}/{lang}: {phrase!r} matched nothing"


# ------------------------------------------------------------------- signing
def test_mint_and_verify_roundtrip():
    token, claims = mint(
        SECRET, report_id="elb_1", contact_id="c1", scope=SCOPE_FAMILY, ttl_s=60
    )
    out = verify(SECRET, token)
    assert out.report_id == "elb_1"
    assert out.contact_id == "c1"
    assert out.scope == SCOPE_FAMILY
    assert not out.expired


def test_tampered_payload_is_rejected():
    token, _ = mint(SECRET, report_id="elb_1", contact_id="c1", scope=SCOPE_FAMILY, ttl_s=60)
    version, body, sig = token.split(".")
    # Try to promote a family link to an operator link.
    forged = f"{version}.{body[:-2]}XY.{sig}"
    with pytest.raises(BadToken):
        verify(SECRET, forged)


def test_wrong_secret_is_rejected():
    token, _ = mint(SECRET, report_id="r", contact_id="c", scope=SCOPE_FAMILY, ttl_s=60)
    with pytest.raises(BadToken):
        verify("another-secret", token)


def test_expired_token_is_rejected():
    token, _ = mint(SECRET, report_id="r", contact_id="c", scope=SCOPE_OPERATOR, ttl_s=-1)
    with pytest.raises(BadToken, match="expired"):
        verify(SECRET, token)


def test_links_are_unique_per_contact():
    a, _ = mint(SECRET, report_id="r", contact_id="c1", scope=SCOPE_FAMILY, ttl_s=60)
    b, _ = mint(SECRET, report_id="r", contact_id="c2", scope=SCOPE_FAMILY, ttl_s=60)
    assert a != b


def test_unknown_scope_rejected():
    with pytest.raises(ValueError):
        mint(SECRET, report_id="r", contact_id="c", scope="admin", ttl_s=60)


# -------------------------------------------------------------------- report
def _sample_report() -> ReportData:
    start = datetime(2026, 8, 22, 19, 3, 0, tzinfo=timezone.utc)
    return ReportData(
        report_id="elb_abc123def456",
        call_id="call-1",
        started_at=start,
        ended_at=start + timedelta(minutes=7, seconds=12),
        caller_lang="en",
        operator_lang="es",
        caller_name="Amara Okafor",
        extraction={
            "emergency_type": {"value": "medical", "confidence": 0.94, "evidence": "not breathing"},
            "emergency_detail": {"value": "Nino de 4 anos no respira", "confidence": 0.9, "evidence": ""},
            "location": {"value": "Calle 72 #10-34, Bogota", "confidence": 0.88, "evidence": ""},
            "location_detail": {"value": "Apartamento 502, torre B", "confidence": 0.8, "evidence": ""},
            "victim_count": {"value": "1", "confidence": 0.95, "evidence": ""},
            "victims": [
                {"who": "my son", "condition": "no respira, inconsciente", "is_child": True, "conscious": "no"}
            ],
            "hazards": ["escalera bloqueada"],
            "caller_relationship": {"value": "madre", "confidence": 0.9, "evidence": ""},
            "severity": "critical",
            "summary_operator": "Nino de 4 anos no respira en Calle 72 #10-34, apto 502.",
            "summary_family": "Hay una emergencia medica en Calle 72 #10-34, apto 502. Los servicios de emergencia ya tienen los datos.",
        },
        critical_flags=["not_breathing", "unconscious"],
        transcript=[
            TranscriptLine(3.2, "caller", "en", "My son is not breathing",
                           "Mi hijo NO RESPIRA", "es", False, ["not_breathing"]),
            TranscriptLine(9.8, "operator", "es", "Cual es la direccion",
                           "What is the address", "en", False, []),
        ],
        latencies_ms=[1180, 1420, 2600, 990],
        fallback_turns=1,
        repaired_terms=["not_breathing"],
        gps=(4.6533, -74.0836),
        is_final=True,
    )


def test_operator_report_contains_everything():
    txt = build_report(_sample_report(), Scope.OPERATOR, "es")
    assert "TRANSCRIPCION COMPLETA" in txt
    assert "My son is not breathing" in txt
    assert "NO RESPIRA" in txt
    assert "confianza" in txt              # confidence scores shown
    assert "CALIDAD DE LA INTERPRETACION" in txt
    assert "no respira, inconsciente" in txt  # clinical condition
    assert "google.com/maps" in txt


def test_family_report_withholds_clinical_detail_and_transcript():
    """Data minimisation is a hard requirement, not a nicety."""
    txt = build_report(_sample_report(), Scope.FAMILY, "es")

    # Present: what a relative needs to act on.
    assert "Calle 72 #10-34" in txt
    assert "google.com/maps" in txt
    assert "escalera bloqueada" in txt        # hazard: they might drive there
    assert "1 persona(s) mencionada(s)" in txt

    # Absent: everything that belongs to the professionals.
    assert "TRANSCRIPCION COMPLETA" not in txt
    assert "My son is not breathing" not in txt
    assert "no respira, inconsciente" not in txt
    assert "[confianza " not in txt
    # The clinical operator summary must not leak in through section 1.
    assert "Nino de 4 anos no respira en Calle" not in txt
    assert "Hay una emergencia medica" in txt
    assert "CALIDAD DE LA INTERPRETACION" not in txt


def test_both_scopes_carry_the_ethical_notice():
    for scope in (Scope.OPERATOR, Scope.FAMILY):
        txt = build_report(_sample_report(), scope, "es")
        assert "INFORMA, no INSTRUYE" in txt
        assert "no reemplaza" in txt.lower() or "No reemplaza" in txt
        assert "123" in txt


def test_report_lines_stay_within_width():
    txt = build_report(_sample_report(), Scope.OPERATOR, "es")
    too_long = [ln for ln in txt.splitlines() if len(ln) > 82]
    assert not too_long, f"lines exceed the fixed width: {too_long[:3]}"


def test_partial_report_is_labelled_as_such():
    data = _sample_report()
    data.is_final = False
    txt = build_report(data, Scope.FAMILY, "es")
    assert "PARCIAL" in txt


def test_percentile():
    assert percentile([], 0.5) == 0
    assert percentile([5], 0.9) == 5
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3


# -------------------------------------------------- early notification gate
def _extraction(loc_conf: float, type_conf: float, loc="Calle 72 #10-34", typ="medical"):
    blank = Assessed(value="", confidence=0.0, evidence="")
    return Extraction(
        emergency_type=Assessed(value=typ, confidence=type_conf, evidence=""),
        emergency_detail=blank,
        location=Assessed(value=loc, confidence=loc_conf, evidence=""),
        location_detail=blank,
        victim_count=blank,
        victims=[],
        hazards=[],
        caller_relationship=blank,
        severity="critical",
        summary_operator="x",
        summary_family="y",
    )


def _engine(**over):
    from elb.comprehension import ComprehensionEngine

    s = Settings()
    eng = ComprehensionEngine(s, bus=None, operator_lang="es")  # type: ignore[arg-type]
    eng.state = ComprehensionState()
    eng.state.started_at = time.monotonic() - over.pop("elapsed", 60.0)
    return eng


def test_no_notification_without_location():
    eng = _engine()
    assert not eng._evaluate(_extraction(0.9, 0.9, loc="")).should_notify


def test_no_notification_below_confidence_threshold():
    eng = _engine()
    assert not eng._evaluate(_extraction(0.5, 0.9)).should_notify
    eng = _engine()
    assert not eng._evaluate(_extraction(0.9, 0.4)).should_notify


def test_requires_two_stable_passes():
    eng = _engine()
    first = eng._evaluate(_extraction(0.9, 0.9))
    assert not first.should_notify and "stable" in first.reason
    second = eng._evaluate(_extraction(0.9, 0.9))
    assert second.should_notify


def test_changing_location_resets_stability():
    eng = _engine()
    eng._evaluate(_extraction(0.9, 0.9, loc="Calle 72"))
    d = eng._evaluate(_extraction(0.9, 0.9, loc="Calle 80"))  # caller corrected
    assert not d.should_notify


def test_too_early_in_the_call_is_blocked():
    eng = _engine(elapsed=3.0)
    eng._evaluate(_extraction(0.9, 0.9))
    d = eng._evaluate(_extraction(0.9, 0.9))
    assert not d.should_notify and "elapsed" in d.reason


def test_critical_flag_takes_the_fast_path():
    """not_breathing: one pass, lower bar, no minimum elapsed time."""
    eng = _engine(elapsed=2.0)
    eng.note_critical(["not_breathing"])
    d = eng._evaluate(_extraction(0.65, 0.65))
    assert d.should_notify and d.fast_path


def test_never_notifies_twice():
    eng = _engine()
    eng._evaluate(_extraction(0.9, 0.9))
    assert eng._evaluate(_extraction(0.9, 0.9)).should_notify
    eng.state.early_sent = True
    assert not eng._evaluate(_extraction(0.9, 0.9)).should_notify


def test_enforce_leaves_non_critical_terms_alone(gl: Glossary):
    """"my son" -> "mi hijo" is a good translation; do not staple MENOR onto it."""
    fixed, repaired = gl.enforce("my son is here", "mi hijo esta aqui", "en", "es")
    assert repaired == []
    assert fixed == "mi hijo esta aqui"


def test_enforce_overrides_a_polarity_flip_even_when_not_critical(gl: Glossary):
    """`breathing` is severity=high, but a flip to NO RESPIRA must be corrected."""
    fixed, repaired = gl.enforce("he is breathing", "el NO RESPIRA", "en", "es")
    assert "breathing" in repaired
    assert "SI RESPIRA" in fixed or "SÍ RESPIRA" in fixed
