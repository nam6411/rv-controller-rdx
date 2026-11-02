"""BlueZ 페어링 에이전트 — MACH처럼 PIN(passkey) 입력을 요구하는 장치용 (ESP MachBle PinCallbacks와 같은 일).

KeyboardOnly 능력으로 등록하고 BlueZ가 묻는 passkey에 설정한 PIN을 답한다. 한 번 본딩되면 BlueZ가 키를 저장해
다음부터는 묻지 않는다. Linux(BlueZ + D-Bus)에서만 쓴다 — bleak가 이미 dbus-fast를 끌어온다.

disconnect_stale: 연결을 끊지 않고 끝난 프로세스의 링크를 BlueZ가 쥐고 있으면 장치가 광고를 멈춰 다시 찾을 수 없다.
"""

import logging  # from __future__ annotations를 쓰면 dbus-fast가 시그니처를 못 읽는다

log = logging.getLogger("rvnode.bluez")

AGENT_PATH = "/org/rvnode/agent"
_registered: dict[int, object] = {}


async def ensure_agent(pin: int) -> None:
    """프로세스당 한 번 등록한다 (PIN이 다르면 마지막 것을 쓴다)."""
    if pin in _registered:
        return
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method

    class Agent(ServiceInterface):
        def __init__(self, passkey: int):
            super().__init__("org.bluez.Agent1")
            self.passkey = passkey

        @method()
        def Release(self):  # noqa: N802
            pass

        @method()
        def RequestPinCode(self, device: "o") -> "s":  # noqa: F821, N802
            log.info("PIN requested by %s", device)
            return f"{self.passkey:06d}"

        @method()
        def RequestPasskey(self, device: "o") -> "u":  # noqa: F821, N802
            log.info("passkey requested by %s", device)
            return self.passkey

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: F821, N802
            pass

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821, N802
            pass

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821, N802
            pass

        @method()
        def RequestAuthorization(self, device: "o"):  # noqa: F821, N802
            pass

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821, N802
            pass

        @method()
        def Cancel(self):  # noqa: N802
            pass

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    bus.export(AGENT_PATH, Agent(pin))
    intro = await bus.introspect("org.bluez", "/org/bluez")
    mgr = bus.get_proxy_object("org.bluez", "/org/bluez", intro).get_interface("org.bluez.AgentManager1")
    await mgr.call_register_agent(AGENT_PATH, "KeyboardOnly")
    await mgr.call_request_default_agent(AGENT_PATH)
    _registered[pin] = bus
    log.info("BlueZ agent registered (KeyboardOnly)")


def stale_paths(objects: dict, address: str = "", name: str = "", uuid: str = "") -> list[str]:
    """GetManagedObjects 결과(값은 풀어 둔 것)에서 연결된 채 남은 장치 경로. 찾는 조건은 scanner.find와 같은 순서."""
    out = []
    for path, ifaces in objects.items():
        d = ifaces.get("org.bluez.Device1")
        if not d or not d.get("Connected"):
            continue
        if address:
            hit = str(d.get("Address", "")).upper() == address.upper()
        elif name:
            hit = name in (d.get("Name"), d.get("Alias"))
        else:
            hit = bool(uuid) and uuid in (d.get("UUIDs") or [])
        if hit:
            out.append(path)
    return out


async def disconnect_stale(address: str = "", name: str = "", uuid: str = "") -> list[str]:
    """찾는 장치가 광고하지 않을 때 부른다. BlueZ에 연결된 채 남아 있으면 끊는다 — 끊은 장치 경로를 돌려준다.

    2026-11-03: rvnode를 다시 띄우자 BMS가 BlueZ에 Connected로 남아 'not found'만 반복했다.
    """
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        intro = await bus.introspect("org.bluez", "/")
        om = bus.get_proxy_object("org.bluez", "/", intro).get_interface("org.freedesktop.DBus.ObjectManager")
        raw = await om.call_get_managed_objects()
        objects = {p: {i: {k: v.value for k, v in props.items()} for i, props in ifs.items()} for p, ifs in raw.items()}
        paths = stale_paths(objects, address, name, uuid)
        for p in paths:
            dev = bus.get_proxy_object("org.bluez", p, await bus.introspect("org.bluez", p)).get_interface("org.bluez.Device1")
            await dev.call_disconnect()
            log.warning("disconnected stale BlueZ link %s", p)
        return paths
    finally:
        bus.disconnect()
