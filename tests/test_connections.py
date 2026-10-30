import asyncio
import json

import aiohttp
import pytest


def test_browser_refuses_web_origins_and_wrong_pairing():
    from browser_bridge import BrowserBridge, pairing_token
    async def scenario():
        bridge=BrowserBridge();await bridge.start(port=0)
        url=f'http://127.0.0.1:{bridge.port}/bridge'
        try:
            async with aiohttp.ClientSession() as client:
                with pytest.raises(aiohttp.WSServerHandshakeError):
                    await client.ws_connect(url,headers={'Origin':'https://untrusted.example'})
                socket=await client.ws_connect(url,headers={'Origin':'chrome-extension://'+'a'*32})
                await socket.send_json({'token':'wrong'})
                assert (await socket.receive()).type in {aiohttp.WSMsgType.CLOSE,aiohttp.WSMsgType.CLOSED}
                socket=await client.ws_connect(url,headers={'Origin':'chrome-extension://'+'a'*32})
                await socket.send_json({'token':pairing_token()})
                assert (await socket.receive_json())['connected']
                task=asyncio.create_task(bridge.command('snapshot'))
                request=await socket.receive_json()
                await socket.send_json({'id':request['id'],'result':{'title':'owned test page'}})
                assert (await task)['title']=='owned test page'
                await socket.close()
        finally:await bridge.close()
    asyncio.run(scenario())


def test_shared_session_round_trip_and_no_control_messages():
    from tutor_features.collab import CollabSession,checked_message
    async def scenario():
        host,peer=CollabSession(),CollabSession()
        seen=asyncio.Event()
        values=[]
        host.on_message(lambda value:(values.append(value),seen.set()))
        try:
            link=await host.start_host()
            await peer.join(link)
            await peer.send({'type':'qa','q':'Question','a':'Answer'})
            await asyncio.wait_for(seen.wait(),3)
            assert values[0]['a']=='Answer'
            with pytest.raises(ValueError):checked_message({'type':'drive','actions':[]})
        finally:
            await peer.stop();await host.stop()
    asyncio.run(scenario())


def test_mcp_sse_parser():
    import httpx
    from connectors import _decode_response
    response=httpx.Response(200,headers={'content-type':'text/event-stream'},content='event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n',request=httpx.Request('POST','http://localhost/mcp'))
    assert _decode_response(response)['result']['ok'] is True


def test_update_version_and_remote_connector_transport():
    from updates import version_key
    from connectors import save_connector
    assert version_key('v1.6.0')>version_key('1.6.0-preview')>version_key('1.5.1')
    with pytest.raises(ValueError):save_connector('unsafe','http://remote.example/mcp')
    with pytest.raises(ValueError):save_connector('unsafe','https://user:secret@example.com/mcp')


def test_velopack_install_updates_in_place(monkeypatch):
    import updates
    from types import SimpleNamespace
    calls=[]
    info=SimpleNamespace(TargetFullRelease=SimpleNamespace(Version='1.7.0'))
    manager=SimpleNamespace(check_for_updates=lambda:info,
                            download_updates=lambda value:calls.append(('download',value)),
                            apply_updates_and_restart=lambda value:calls.append(('apply',value)))
    monkeypatch.setattr(updates,'velopack_manager',lambda:manager)
    found=asyncio.run(updates.latest())
    assert found['version']=='1.7.0' and found['asset'] is None
    asyncio.run(updates.apply_in_place(found))
    assert calls==[('download',info),('apply',info)]
    manager.check_for_updates=lambda:None
    assert asyncio.run(updates.latest()) is None
