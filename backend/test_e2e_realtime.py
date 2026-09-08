import asyncio
import json
import time
import httpx
import websockets

API_BASE = "http://127.0.0.1:8000"
WS_BASE = "ws://127.0.0.1:8000/ws"

async def _run_realtime_flow():
    ts = int(time.time())
    user_a_name = f"alice_{ts}"
    user_b_name = f"bob_{ts}"
    password = "ChatPassword!2026"

    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # 1. Register User A
        res_a = await client.post("/api/auth/register", json={"username": user_a_name, "password": password})
        assert res_a.status_code == 200, f"Register A failed: {res_a.text}"
        token_a = res_a.json()["access_token"]
        user_a_id = res_a.json()["user"]["id"]

        # 2. Register User B
        res_b = await client.post("/api/auth/register", json={"username": user_b_name, "password": password})
        assert res_b.status_code == 200, f"Register B failed: {res_b.text}"
        token_b = res_b.json()["access_token"]
        user_b_id = res_b.json()["user"]["id"]

        # 3. Connect User B to WebSocket
        ws_url_b = f"{WS_BASE}?token={token_b}"
        async with websockets.connect(ws_url_b) as ws_b:
            # 4. User A initiates conversation with User B
            headers_a = {"Authorization": f"Bearer {token_a}"}
            conv_res = await client.post("/api/conversations", json={"target_user_id": user_b_id}, headers=headers_a)
            assert conv_res.status_code == 200, f"Create conversation failed: {conv_res.text}"
            conv = conv_res.json()
            conv_id = conv["id"]

            # 5. User A drops a message to User B
            test_msg_content = "Hello Bob! Testing real-time direct delivery."
            send_res = await client.post(
                f"/api/conversations/{conv_id}/messages",
                json={"content": test_msg_content},
                headers=headers_a
            )
            assert send_res.status_code == 200, f"Send message failed: {send_res.text}"

            # 6. User B receives real-time WebSocket broadcast
            raw_ws_msg = await asyncio.wait_for(ws_b.recv(), timeout=5.0)
            ws_data = json.loads(raw_ws_msg)
            assert ws_data.get("type") == "new_message", "Expected new_message event"
            received_msg = ws_data.get("message", {})
            assert received_msg.get("content") == test_msg_content
            assert received_msg.get("sender_id") == user_a_id

            # 7. User B replies via REST API
            headers_b = {"Authorization": f"Bearer {token_b}"}
            reply_content = "Hey Alice! I received your message instantly via WebSocket!"
            reply_res = await client.post(
                f"/api/conversations/{conv_id}/messages",
                json={"content": reply_content},
                headers=headers_b
            )
            assert reply_res.status_code == 200

            # 8. User A fetches full message history
            history_res = await client.get(f"/api/conversations/{conv_id}/messages", headers=headers_a)
            assert history_res.status_code == 200
            history = history_res.json()
            assert len(history) == 2

def test_realtime_e2e():
    """Synchronous test wrapper for pytest execution against running server."""
    asyncio.run(_run_realtime_flow())

if __name__ == "__main__":
    asyncio.run(_run_realtime_flow())
    print("ALL REAL-TIME & ENDPOINT CHECKS PASSED PERFECTLY!")
