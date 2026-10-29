from aiohttp.test_utils import TestClient, TestServer

from rvnode import web, wifi

from .test_wifi import FakeNm


class SavingNm(FakeNm):
    async def save_network(self, ssid, password):
        self.calls.append(f"save:{ssid}:{bool(password)}")
        self.saved_list.append({"name": ssid, "ssid": ssid})
        return ssid

    async def forget(self, ssid):
        n = len(self.saved_list)
        self.saved_list = [c for c in self.saved_list if c["ssid"] != ssid]
        return n - len(self.saved_list)


async def test_wifi_api(node):
    nm = SavingNm(mode="ap")
    node.wifi = wifi.Wifi(node.cfg.fallback, nm=nm)
    await node.wifi.tick()
    async with TestClient(TestServer(web.make_app(node))) as c:
        page = await (await c.get("/")).text()
        assert 'id="wificard"' in page and "fallback.password" in page
        j = await (await c.get("/api/wifi")).json()
        assert j["mode"] == "ap" and j["networks"] == ["Home"] and j["ssid"] == "RV-FALLBACK"
        r = await c.post("/api/wifi/networks", json={"ssid": "Camp", "password": "short"})
        assert r.status == 400
        r = await c.post("/api/wifi/networks", json={"ssid": "Camp", "password": "12345678"})
        assert (await r.json())["ok"] and "save:Camp:True" in nm.calls
        r = await c.delete("/api/wifi/networks/Camp")
        assert (await r.json())["removed"] == 1
        assert (await c.post("/api/wifi/mode", json={"to": "x"})).status == 400
        assert (await (await c.post("/api/wifi/mode", json={"to": "sta"})).json())["ok"]
