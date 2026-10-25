from kdvops.privacy import TOKEN_PATTERN, PrivacyFilter


def test_masks_sensitive_data_without_retaining_a_reverse_map():
    original = (
        "email=kelvin@example.com phone=+1 809-555-1212 "
        "token=secret-value-123 ip=10.0.0.100 known=configured-secret"
    )
    privacy = PrivacyFilter(["configured-secret"])

    masked = privacy.mask_text(original)

    for value in ("kelvin@example.com", "+1 809-555-1212", "secret-value-123", "10.0.0.100", "configured-secret"):
        assert value not in masked
    assert len(TOKEN_PATTERN.findall(masked)) == 5
    assert not hasattr(privacy, "_token_to_value")


def test_mask_is_deterministic_within_session_but_not_between_sessions():
    first = PrivacyFilter()
    second = PrivacyFilter()
    text = "owner@example.com owner@example.com"

    masked = first.mask_text(text)

    assert len(set(TOKEN_PATTERN.findall(masked))) == 1
    assert masked != second.mask_text(text)


def test_user_supplied_marker_cannot_reference_a_future_mapping():
    privacy = PrivacyFilter()
    fake = "__KDVOPS_MASK_AAAAAAAAAAAAAAAAAAAAAAAA__"

    masked = privacy.mask_text(fake)

    assert masked != fake
    assert fake not in masked


def test_masks_short_explicit_sensitive_values():
    privacy = PrivacyFilter(["s3cr"])

    assert "s3cr" not in privacy.mask_text("password=s3cr")


def test_recursively_masks_json_compatible_messages():
    privacy = PrivacyFilter()
    source = [{"role": "tool", "content": "owner@example.com", "nested": ["10.0.0.1"]}]

    masked = privacy.mask(source)

    assert "owner@example.com" not in masked[0]["content"]
    assert "10.0.0.1" not in masked[0]["nested"][0]
    assert source[0]["content"] == "owner@example.com"
