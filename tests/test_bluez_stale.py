from rvnode.bluez_agent import stale_paths

FF00 = "0000ff00-0000-1000-8000-00805f9b34fb"
OBJ = {
    "/org/bluez/hci0": {"org.bluez.Adapter1": {"Address": "00:00:00:00:00:01"}},
    "/org/bluez/hci0/dev_70_3E": {"org.bluez.Device1": {"Address": "70:3E:97:07:B4:D3", "Name": "JBD-SP04S034-L4S-1",
                                                       "Alias": "JBD-SP04S034-L4S-1", "Connected": True, "UUIDs": [FF00]}},
    "/org/bluez/hci0/dev_FD_B3": {"org.bluez.Device1": {"Address": "FD:B3:28:41:63:16", "Name": "MACH-BASIC",
                                                       "Alias": "MACH-BASIC", "Connected": False, "UUIDs": []}},
}


def test_by_address_case_insensitive():
    assert stale_paths(OBJ, address="70:3e:97:07:b4:d3") == ["/org/bluez/hci0/dev_70_3E"]


def test_not_connected_is_left_alone():
    assert stale_paths(OBJ, address="FD:B3:28:41:63:16") == []
    assert stale_paths(OBJ, name="MACH-BASIC") == []


def test_by_name_then_by_service():
    assert stale_paths(OBJ, name="JBD-SP04S034-L4S-1") == ["/org/bluez/hci0/dev_70_3E"]
    assert stale_paths(OBJ, uuid=FF00) == ["/org/bluez/hci0/dev_70_3E"]


def test_address_wins_over_name():
    assert stale_paths(OBJ, address="11:22:33:44:55:66", name="JBD-SP04S034-L4S-1") == []


def test_nothing_to_match_drops_nothing():
    assert stale_paths(OBJ) == []
