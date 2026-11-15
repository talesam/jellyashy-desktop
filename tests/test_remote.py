"""The TV side: DLNA descriptions, times and the relay's address."""

from jellyashy_desktop import remote

DESC = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
 <device>
  <friendlyName>[TV] Samsung 6 Series (49)</friendlyName>
  <UDN>uuid:abc</UDN>
  <serviceList>
   <service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
     <controlURL>/upnp/control/RenderingControl1</controlURL></service>
   <service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
     <controlURL>/upnp/control/AVTransport1</controlURL></service>
  </serviceList>
 </device>
</root>"""


def test_a_renderer_is_read_from_its_description():
    r = remote.parse_description(DESC, "http://192.168.68.107:9197/dmr")
    assert r.name == "[TV] Samsung 6 Series (49)"
    assert r.udn == "uuid:abc"
    assert r.control == "http://192.168.68.107:9197/upnp/control/AVTransport1"


def test_a_device_without_a_player_is_not_a_renderer():
    assert remote.parse_description(DESC.replace("AVTransport", "Other"), "http://x/") is None
    assert remote.parse_description("not xml", "http://x/") is None


def test_times_go_both_ways():
    assert remote.to_hms(3725) == "1:02:05"
    assert remote.from_hms("1:02:05") == 3725
    assert remote.from_hms("NOT_IMPLEMENTED") == 0


def test_the_type_comes_from_the_container():
    assert remote.mime_of("https://s/Videos/1/stream.mkv?static=true") == "video/x-matroska"
    assert remote.mime_of("https://s/Videos/1/stream.mp4?static=true") == "video/mp4"
    assert remote.mime_of("https://s/videos/1/master.m3u8?x") == "application/vnd.apple.mpegurl"


def test_the_item_description_escapes_the_address():
    d = remote.didl("http://h/v?a=1&b=2", "Tom & Jerry", "video/mp4")
    assert "a=1&amp;b=2" in d and "Tom &amp; Jerry" in d and "http-get:*:video/mp4:" in d
