from app.core.security import generate_key, hash_key


def test_generated_keys_are_unique_and_carry_their_prefix() -> None:
    keys = {generate_key() for _ in range(50)}
    assert len(keys) == 50
    for key, prefix in keys:
        assert key.startswith(f"voc_{prefix}_")
        assert len(key) > 40


def test_hash_is_stable_and_not_the_key() -> None:
    key, _ = generate_key()
    assert hash_key(key) == hash_key(key)
    assert len(hash_key(key)) == 64
    assert key not in hash_key(key)
