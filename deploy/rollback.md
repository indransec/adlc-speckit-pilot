# Rollback

1. Set `kill_switch: true` (stops straight-through and payment instructions immediately).
2. Route all new claims to the manual queue.
3. Redeploy previous release by bundle hash.
4. In-flight claims: hold at current step; adjusters complete manually.
Owner: on-call platform engineer + claims operations lead.
