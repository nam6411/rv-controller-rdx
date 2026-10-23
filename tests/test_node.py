import asyncio
import json


async def test_availability_after_three_errors(node):
    dev = node.devices[0]
    await dev.ok()
    assert node.bus.last("rv/mach/availability") == ("online", True)
    await dev.error("x")
    await dev.error("x")
    assert node.bus.last("rv/mach/availability") == ("online", True)
    await dev.error("x")
    assert node.bus.last("rv/mach/availability") == ("offline", True)


async def test_commands_routed_by_prefix(node):
    mach, tpms = node.devices
    await node._on_message("rv/mach/tpms/switch/led/set", "ON")
    await node._on_message("rv/mach/control/level/set", "7")
    assert tpms.commands.get_nowait() == ("switch", "led", "ON")
    assert mach.commands.get_nowait() == ("control", "level", "7")
    assert mach.commands.empty() and tpms.commands.empty()


async def test_command_handling_publishes_state(node):
    dev = node.devices[0]
    task = asyncio.create_task(dev.handle_commands())
    dev.commands.put_nowait(("switch", "led", "on"))
    dev.commands.put_nowait(("control", "level", "4"))
    dev.commands.put_nowait(("control", "ping", "PRESS"))
    await asyncio.sleep(0.01)
    task.cancel()
    assert node.bus.last("rv/mach/switch/led/state") == ("ON", True)
    state, _ = node.bus.last("rv/mach/state")
    assert json.loads(state)["level"] == 4
    ev, retain = node.bus.last("rv/mach/event/pinged")
    assert not retain and json.loads(ev)["event_type"] == "ping"


async def test_ha_birth_and_sys_cmd(node):
    await node._on_message("homeassistant/status", "online")
    assert node.bus.last("homeassistant/switch/9FCEE4_tpms/led/config")
    await node._on_message("rv/mach/sys/cmd", "restart")
    assert node.exit_code == 1
