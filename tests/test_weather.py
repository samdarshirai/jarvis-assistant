from jarvis.weather import advice


def test_advice():
    assert "umbrella" in advice(20, 70, 6)
    assert "jacket" in advice(12, 0, 7) and "summer" in advice(12, 0, 7)
    assert "an autumn" in advice(5, 0, 10)
    assert advice(22, 10, 6) == ""
    assert "jacket" in advice(12, 0, 1, southern=True)  # January is summer down south


def test_coords_roundtrip():
    from jarvis.weather import load_coords, save_coords

    class S(dict):
        get_state = dict.get

        def set_state(self, k, v):
            self[k] = v

    s = S()
    assert load_coords(s) is None
    save_coords(s, 52.52, 13.405)
    assert load_coords(s) == (52.52, 13.405)
