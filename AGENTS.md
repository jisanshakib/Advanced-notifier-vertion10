# Architecture
- Keep hosting probes in hosting.py with native asynchronous HTTP I/O; probes must not access Telegram, audio processing, or the database.
- Keep database queries as single autocommit statements; reconnect stale connections before writes and never replay writes with ambiguous outcomes.
- Treat terminal Telegram disconnects as process failures and run cleanup for startup failures; intentional termination signals remain clean exits.