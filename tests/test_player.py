import pytest

mpv = pytest.importorskip("mpv")  # libmpv is needed to import the player

from jellyashy_desktop import player  # noqa: E402


def test_aspect_modes():
    assert player.aspect_options("fit", 1280, 720) == {"video-aspect-override": "no", "panscan": "0"}
    assert player.aspect_options("fill", 1280, 720) == {"video-aspect-override": "1280:720", "panscan": "0"}
    assert player.aspect_options("zoom", 1280, 720) == {"video-aspect-override": "no", "panscan": "1"}
    assert player.aspect_options("fill", 0, 0) == player.aspect_options("fit", 0, 0)
    assert player.aspect_options("anything", 1, 1) == player.aspect_options("fit", 1, 1)


def test_audio_tracks_in_the_pages_shape():
    tracks = [
        {"type": "video", "id": 1},
        {"type": "audio", "id": 1, "lang": "por", "title": " Dublado ", "selected": True},
        {"type": "audio", "id": 2, "lang": None},
        {"type": "sub", "id": 1, "lang": "eng"},
    ]
    assert player.audio_tracks(tracks) == [
        {"id": "1", "language": "por", "label": "Dublado", "selected": True},
        {"id": "2", "language": "", "label": "", "selected": False},
    ]
    assert player.audio_tracks(None) == []


def test_error_text():
    assert player.error_text(-13) == "loading failed"
    assert player.error_text(0) == ""
