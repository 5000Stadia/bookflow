from bookflow.core.ids import is_ulid, new_id, normalize_ulid


def test_new_id_shape():
    a, b = new_id(), new_id()
    assert len(a) == 26 and is_ulid(a) and a != b
    assert is_ulid(a.lower()) and normalize_ulid(a.lower()) == a


def test_not_ulid():
    assert not is_ulid("Acme Plumbing")
    assert not is_ulid("01ARZ3NDEKTSV4RRFFQ69G5FA")  # 25 chars
    assert not is_ulid("01ARZ3NDEKTSV4RRFFQ69G5FAVI")  # I is not in Crockford
