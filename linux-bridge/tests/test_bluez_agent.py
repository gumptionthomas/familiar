from familiar import bluez_agent


def test_device_path_is_built_the_way_bluez_names_objects():
    assert bluez_agent.device_path("/org/bluez/hci0", "F0:16:1D:03:4C:FA") == \
        "/org/bluez/hci0/dev_F0_16_1D_03_4C_FA"


def test_device_path_upcases_a_lowercase_mac():
    # bluetoothctl prints lowercase in some places; BlueZ object paths are upper.
    assert bluez_agent.device_path("/org/bluez/hci0", "f0:16:1d:03:4c:fa") == \
        "/org/bluez/hci0/dev_F0_16_1D_03_4C_FA"
