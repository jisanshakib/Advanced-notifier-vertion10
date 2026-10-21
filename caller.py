"""Places real Telegram voice calls from the userbot account.

Rules:
  * Call #1 rings the target.
  * If the target declines / hangs up  -> STOP.
  * If the target answers               -> treated as acknowledged, STOP.
  * If the call is ignored (no answer)  -> place call #2.
Audio is not streamed – the goal is to make the phone ring.
"""
import asyncio
import hashlib
import logging
import secrets

from telethon import events
from telethon.tl import types
from telethon.tl.functions.messages import GetDhConfigRequest
from telethon.tl.functions.phone import DiscardCallRequest, RequestCallRequest

log = logging.getLogger("caller")
RING_SECONDS = 45

_waiters: dict[int, asyncio.Future] = {}
_lock = asyncio.Lock()


def install(user_client):
    @user_client.on(events.Raw(types.UpdatePhoneCall))
    async def _on_call(update):
        call = update.phone_call
        fut = _waiters.get(call.id)
        if not fut or fut.done():
            return
        if isinstance(call, types.PhoneCallDiscarded):
            fut.set_result(("discarded", call.reason))
        elif isinstance(call, (types.PhoneCallAccepted, types.PhoneCall)):
            fut.set_result(("answered", call))


async def _one_call(client, target):
    dh = await client(GetDhConfigRequest(version=0, random_length=256))
    p = int.from_bytes(dh.p, "big")
    a = int.from_bytes(secrets.token_bytes(256), "big") % p
    g_a = pow(dh.g, a, p).to_bytes(256, "big")
    res = await client(RequestCallRequest(
        user_id=target,
        random_id=secrets.randbelow(2**31),
        g_a_hash=hashlib.sha256(g_a).digest(),
        protocol=types.PhoneCallProtocol(
            min_layer=65, max_layer=92, udp_p2p=True, udp_reflector=True,
            library_versions=["2.4.4", "3.0.0", "4.0.0"]),
        video=False,
    ))
    call = res.phone_call
    peer = types.InputPhoneCall(id=call.id, access_hash=call.access_hash)
    fut = asyncio.get_running_loop().create_future()
    _waiters[call.id] = fut

    async def discard(reason):
        try:
            await client(DiscardCallRequest(peer=peer, duration=0, reason=reason, connection_id=0))
        except Exception as e:  # already ended
            log.debug("discard: %s", e)

    try:
        kind, info = await asyncio.wait_for(fut, RING_SECONDS)
    except asyncio.TimeoutError:
        await discard(types.PhoneCallDiscardReasonMissed())
        return "ignored"
    finally:
        _waiters.pop(call.id, None)

    if kind == "answered":
        await discard(types.PhoneCallDiscardReasonHangup())
        return "answered"
    if isinstance(info, (types.PhoneCallDiscardReasonBusy, types.PhoneCallDiscardReasonHangup)):
        return "hung_up"
    return "ignored"  # Missed / Disconnect / unknown


async def alert_call(client, target_ref: str) -> str:
    """Returns a human-readable summary of what happened."""
    async with _lock:  # never ring twice in parallel
        target = await client.get_input_entity(int(target_ref) if target_ref.lstrip("-").isdigit() else target_ref)
        r1 = await _one_call(client, target)
        if r1 in ("hung_up", "answered"):
            return f"Call 1: {r1.replace('_', ' ')} → stopped."
        await asyncio.sleep(3)
        r2 = await _one_call(client, target)
        return f"Call 1: ignored → Call 2: {r2.replace('_', ' ')}."
