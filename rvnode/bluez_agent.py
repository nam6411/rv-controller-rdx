"""BlueZ 페어링 에이전트 — MACH처럼 PIN(passkey) 입력을 요구하는 장치용 (ESP MachBle PinCallbacks와 같은 일).

KeyboardOnly 능력으로 등록하고 BlueZ가 묻는 passkey에 설정한 PIN을 답한다. 한 번 본딩되면 BlueZ가 키를 저장해
다음부터는 묻지 않는다. Linux(BlueZ + D-Bus)에서만 쓴다 — bleak가 이미 dbus-fast를 끌어온다.
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
