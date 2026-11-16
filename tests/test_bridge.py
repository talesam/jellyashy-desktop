import json

from jellyashy_desktop import bridge


def test_shim_defines_the_android_bridge_methods():
    js = bridge.shim({"model": "pc", "tv": False})
    for method in ("info", "exit", "playerLoad", "playerPlay", "playerPause", "playerStop", "playerSeek",
                   "playerRate", "playerAudio", "playerAspect", "playerStatus", "playerTracks"):
        assert method + ":" in js
    assert "window.JellyAshyLinux" in js
    assert 'messageHandlers.jellyashy' in js
    assert '"model": "pc"' in js


def test_parse_message_accepts_only_known_commands():
    assert bridge.parse_message('{"cmd":"seek","arg":12.5}') == bridge.Command("seek", 12.5)
    assert bridge.parse_message('{"cmd":"play"}') == bridge.Command("play", None)
    assert bridge.parse_message('{"cmd":"rm -rf"}') is None
    assert bridge.parse_message("not json") is None
    assert bridge.parse_message("[1,2]") is None


def test_parse_load_reads_url_start_and_user_agent():
    load = bridge.parse_load(json.dumps({"url": "https://s/v.mkv", "startAt": 61.5, "userAgent": "UA"}))
    assert (load.url, load.start, load.user_agent) == ("https://s/v.mkv", 61.5, "UA")
    assert bridge.parse_load(json.dumps({"url": "u", "startAt": -3})).start == 0
    assert bridge.parse_load(json.dumps({"url": "u", "startAt": "x"})).start == 0
    assert bridge.parse_load(json.dumps({"startAt": 3})) is None
    assert bridge.parse_load("{broken") is None
    assert bridge.parse_load(None) is None


def test_scripts_hand_data_to_the_page_as_json():
    assert bridge.event_script({"type": "state", "state": "playing"}) == (
        'window.__jellyashy&&window.__jellyashy.event({"type": "state", "state": "playing"})')
    s = bridge.status_script({"pos": 1, "dur": 2, "buf": 3}, [{"id": "1"}])
    assert "status({\"pos\": 1" in s and "tracks([{\"id\": \"1\"}])" in s
    assert "tracks(" not in bridge.status_script({"pos": 1, "dur": 2, "buf": 3})


def test_a_title_cannot_break_out_of_the_script():
    s = bridge.event_script({"type": "error", "message": "x\"); alert(1); (\"", "detail": "</script>"})
    payload = s[s.index("(") + 1:-1]
    assert json.loads(payload)["message"] == "x\"); alert(1); (\""


def test_fullscreen_is_a_command():
    # The F key (bridge shim) asks the window for full screen.
    assert bridge.parse_message('{"cmd": "fullscreen"}').cmd == "fullscreen"
    assert "fullscreen" in bridge.shim({})
