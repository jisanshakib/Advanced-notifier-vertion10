# v16 — Reliability audit

- **No more missed posts:** new posts are put in a queue the moment they arrive and handled by 3 workers (ALERT_WORKERS). A burst from many channels at once is never dropped.
- **Safety-net poller:** every 20 s (POLL_INTERVAL) the bot checks each watched channel for posts Telegram never pushed (reconnects, DC moves, big channels). Recovered posts are never sent twice.
- **Reconnects:** clients retry forever, catch up on missed updates after a reconnect, and load the channel list on start.
- **FloodWait / network errors:** every send, upload and download waits out FloodWaits (up to 10 min) and retries temporary errors.
- **Albums:** posts with 2+ photos/videos are forwarded as ONE grouped album with the alert caption (up to 10 items).
- **Strict calls:** calls (and PRIORITY labels) happen ONLY for Priority Channel + Priority Keyword — voice messages included. A priority keyword heard on a normal channel is still forwarded as a normal alert.
- **Speed:** on priority posts the call starts before the media upload.
- **Memory:** media bytes are released right after upload; one stuck post times out after 10 min instead of blocking.
