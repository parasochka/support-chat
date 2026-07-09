"""AI metadata generation for retention media: the prompt builder and the
strict-JSON parser/clamper behind POST /admin/retention/photos/generate-metadata."""
from __future__ import annotations

import pytest

import prompts
from api.retention import _clamp_photo_gate, _parse_photo_meta

_TIERS = ["none", "bronze", "silver", "gold"]


def test_build_photo_meta_messages_shape():
    msgs = prompts.build_photo_meta_messages("data:image/jpeg;base64,AAA",
                                             _TIERS, max_stage=4)
    assert msgs[0]["role"] == "system"
    assert "strict JSON" in msgs[0]["content"]
    user = msgs[1]
    assert user["role"] == "user"
    text_part, image_part = user["content"]
    # The ranges the model is given match the product's real gates.
    assert "1..4" in text_part["text"]
    assert "0..3" in text_part["text"]
    assert "3 = gold" in text_part["text"]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # Descriptions must be plain speech the persona can voice - not catalogue
    # jargon (haircut names, fashion terms) she would then parrot verbatim.
    assert "haircut names" in text_part["text"]
    # No library counts passed -> no balancing block.
    assert "BALANCE THE LIBRARY" not in text_part["text"]


def test_build_photo_meta_messages_balance_block():
    """With the library's current distribution passed, the task gains the
    balancing block that steers borderline ratings to under-filled levels."""
    msgs = prompts.build_photo_meta_messages(
        "data:image/jpeg;base64,AAA", _TIERS, max_stage=3,
        library_counts={"stage": {1: 7, 2: 0, 3: 1}, "level": {0: 8, 1: 0}})
    text = msgs[1]["content"][0]["text"]
    assert "BALANCE THE LIBRARY" in text
    assert "1: 7, 2: 0, 3: 1" in text
    # tier counts are labelled with the tier NAMES, not bare ordinals
    assert "0 (none): 8, 1 (bronze): 0" in text
    # what is visible still rules - balancing only breaks ties
    assert "plausible range" in text
    # the tier is a distribution choice, decoupled from the stage
    assert "independently of the stage" in text


def test_build_photo_meta_messages_live_config():
    """The exact live NikaBet ladder: stages 1-5, six tiers none..diamond
    (Level 0-5) - the prompt ranges must mirror the product settings."""
    tiers = ["none", "bronze", "silver", "gold", "platinum", "diamond"]
    msgs = prompts.build_photo_meta_messages(
        "data:image/jpeg;base64,AAA", tiers, max_stage=5,
        library_counts={"stage": {s: 0 for s in range(1, 6)},
                        "level": {lv: 0 for lv in range(6)}})
    text = msgs[1]["content"][0]["text"]
    assert "1..5" in text          # the stage ladder
    assert "0..5" in text          # the tier range (0 = none, open to everyone)
    assert "5 = diamond" in text
    assert "0 (none): 0" in text and "5 (diamond): 0" in text


def test_parse_photo_meta_happy_path():
    meta = _parse_photo_meta(
        '{"description": "A photo.", "tags": ["Beach", " sun ", ""],'
        ' "stage": 2, "level_min": 1}',
        vip_tiers=_TIERS, max_stage=4)
    assert meta == {"description": "A photo.", "tags": ["beach", "sun"],
                    "stage": 2, "level_min": 1}


def test_parse_photo_meta_tolerates_fences_and_prose():
    meta = _parse_photo_meta(
        'Sure! ```json\n{"description": "d", "tags": ["a"], "stage": 1,'
        ' "level_min": 0}\n```',
        vip_tiers=_TIERS, max_stage=4)
    assert meta["description"] == "d"


def test_parse_photo_meta_clamps_to_product_gates():
    # A hallucinated stage/level can never unlock beyond the real ladder.
    meta = _parse_photo_meta(
        '{"description": "d", "tags": [], "stage": 99, "level_min": 42}',
        vip_tiers=_TIERS, max_stage=3)
    assert meta["stage"] == 3
    assert meta["level_min"] == 3          # len(tiers) - 1
    meta = _parse_photo_meta(
        '{"description": "d", "tags": [], "stage": 0, "level_min": -5}',
        vip_tiers=_TIERS, max_stage=3)
    assert meta["stage"] == 1
    assert meta["level_min"] == 0


def test_clamp_photo_gate_bounds_hand_entered_values():
    # Above the ladder: stage caps at max_stage, level at the top tier ordinal.
    assert _clamp_photo_gate(stage=6, level_min=9, vip_tiers=_TIERS,
                             max_stage=5) == {"stage": 5, "level_min": 3}
    # Below the floor: stage never below 1, level never below 0.
    assert _clamp_photo_gate(stage=0, level_min=-2, vip_tiers=_TIERS,
                             max_stage=5) == {"stage": 1, "level_min": 0}
    # A field left unset is simply absent (partial update).
    assert _clamp_photo_gate(stage=None, level_min=2, vip_tiers=_TIERS,
                             max_stage=5) == {"level_min": 2}
    assert _clamp_photo_gate(stage=None, level_min=None, vip_tiers=_TIERS,
                             max_stage=5) == {}


def test_parse_photo_meta_rejects_garbage():
    with pytest.raises(ValueError):
        _parse_photo_meta("no json here", vip_tiers=_TIERS, max_stage=3)
    with pytest.raises(ValueError):
        _parse_photo_meta('{"tags": [], "stage": 1, "level_min": 0}',
                          vip_tiers=_TIERS, max_stage=3)   # empty description
    with pytest.raises(ValueError):
        _parse_photo_meta('{"description": "d", "tags": "not-a-list",'
                          ' "stage": 1, "level_min": 0}',
                          vip_tiers=_TIERS, max_stage=3)
    with pytest.raises(ValueError):
        _parse_photo_meta('{"description": "d", "tags": [], "stage": "x",'
                          ' "level_min": 0}',
                          vip_tiers=_TIERS, max_stage=3)
