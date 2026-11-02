from rvnode import wifiwatch as ww


class FakeSys:
    def __init__(self, state="ok", uptime=10_000.0, last_reboot=0.0):
        self.st, self.up, self.last = state, uptime, last_reboot
        self.calls: list[str] = []

    async def state(self):
        return self.st

    async def reload_driver(self):
        self.calls.append("reload")

    def uptime(self):
        return self.up

    def last_reboot(self):
        return self.last

    async def reboot(self):
        self.calls.append("reboot")


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(sys):
    clock, wall = Clock(), Clock(1_000_000.0)
    return ww.Watch(sys, clock=clock, wall=wall), clock, wall


async def test_stuck_reloads_then_reboots():
    sys = FakeSys("stuck")
    w, clock, wall = make(sys)
    await w.tick()
    clock.t += ww.STUCK_S - 1
    await w.tick()
    assert sys.calls == []
    clock.t += 1
    await w.tick()
    assert sys.calls == ["reload"]
    clock.t += ww.STUCK_S
    await w.tick()
    assert sys.calls == ["reload", "reboot"]


async def test_router_absent_never_acts():
    sys = FakeSys("absent")
    w, clock, _ = make(sys)
    for _ in range(10):
        await w.tick()
        clock.t += ww.STUCK_S
    assert sys.calls == []


async def test_recovery_resets():
    sys = FakeSys("stuck")
    w, clock, _ = make(sys)
    await w.tick()
    clock.t += ww.STUCK_S
    await w.tick()
    assert sys.calls == ["reload"]
    sys.st = "ok"
    await w.tick()
    sys.st = "stuck"
    await w.tick()
    clock.t += ww.STUCK_S
    await w.tick()
    assert sys.calls == ["reload", "reload"]      # 붙었다 다시 막히면 드라이버부터 다시


async def test_no_reboot_soon_after_boot_or_last_reboot():
    sys = FakeSys("stuck", uptime=ww.MIN_UPTIME_S - 1)
    w, clock, wall = make(sys)
    await w.tick()
    clock.t += ww.STUCK_S
    await w.tick()
    clock.t += ww.STUCK_S
    await w.tick()
    assert sys.calls == ["reload"]
    sys.up = ww.MIN_UPTIME_S
    sys.last = wall.t - ww.REBOOT_GAP_S + 1
    await w.tick()
    assert sys.calls == ["reload"]
    wall.t += 1
    await w.tick()
    assert sys.calls == ["reload", "reboot"]
