from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_round_trip():
    hashed = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", hashed)
    assert not verify_password("incorrect", hashed)


def test_access_token_round_trip():
    token = create_access_token("admin@cloudops.local")
    assert decode_access_token(token) == "admin@cloudops.local"
    assert decode_access_token("invalid") is None
