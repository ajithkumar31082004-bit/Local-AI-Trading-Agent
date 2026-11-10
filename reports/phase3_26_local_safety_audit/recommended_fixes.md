# Phase 3.26 — Recommended Safety Fixes & Surgical Code Diffs

This document specifies the exact, minimal, surgical code modifications required to remediate the vulnerabilities discovered during the Phase 3.26 local safety audits.

---

## 1. Remediation for Audit 1: Order Reservation TTL Expiration & Fencing

### Vulnerability Summary
1. `reserve_order_slot` does not store `ttl_seconds` in `self._pending_reservation`, causing callers to compare against incoming caller TTL rather than active lease TTL.
2. `commit_order_reservation` checks only token string equality, allowing uncontended expired reservations to commit.
3. `open_simulated_trade` assigns `self.open_position = pos` before calling `commit_order_reservation`, and ignores its return value.

### Proposed Code Diff for `services/delta_paper_broker.py`

```diff
--- a/services/delta_paper_broker.py
+++ b/services/delta_paper_broker.py
@@ -177,10 +177,11 @@ class DeltaPaperBroker:
             # Check existing in-flight reservation
             if self._pending_reservation is not None:
                 res_time = float(self._pending_reservation.get("timestamp_epoch", 0.0))
+                res_ttl = float(self._pending_reservation.get("ttl_seconds", 30.0))
                 now_epoch = datetime.now(timezone.utc).timestamp()
-                if (now_epoch - res_time) < ttl_seconds:
+                if (now_epoch - res_time) < res_ttl:
                     active_key = self._pending_reservation.get("idempotency_key")
                     return False, f"Order slot currently reserved by in-flight order '{active_key}'", None
                 else:
-                    logger.warning("Clearing expired order reservation (age: %.1fs > %.1fs).", now_epoch - res_time, ttl_seconds)
+                    logger.warning("Clearing expired order reservation (age: %.1fs > %.1fs).", now_epoch - res_time, res_ttl)
                     self._pending_reservation = None
@@ -193,6 +194,7 @@ class DeltaPaperBroker:
                 "direction": direction,
                 "symbol": symbol,
+                "ttl_seconds": float(ttl_seconds),
                 "timestamp_epoch": datetime.now(timezone.utc).timestamp(),
                 "timestamp_iso": datetime.now(timezone.utc).isoformat(),
             }
@@ -213,6 +215,16 @@ class DeltaPaperBroker:
         with self._lock:
             if token and self._pending_reservation and self._pending_reservation.get("token") == token:
+                # Verify reservation has not expired prior to commit
+                res_time = float(self._pending_reservation.get("timestamp_epoch", 0.0))
+                res_ttl = float(self._pending_reservation.get("ttl_seconds", 30.0))
+                now_epoch = datetime.now(timezone.utc).timestamp()
+                if (now_epoch - res_time) >= res_ttl:
+                    logger.error("Order reservation %s expired before commit (age: %.1fs >= %.1fs). Commit rejected.", token, now_epoch - res_time, res_ttl)
+                    self._pending_reservation = None
+                    return False
+
                 key = self._pending_reservation.get("idempotency_key")
                 self._processed_idempotency_keys[key] = {
@@ -512,8 +524,15 @@ class DeltaPaperBroker:
-                self.open_position = pos
-                self.commit_order_reservation(res_token, pos)
-                self._save_state()
+                commit_ok = self.commit_order_reservation(res_token, pos)
+                if not commit_ok:
+                    # Roll back in-memory cost counters on rejected commit
+                    self.total_fees -= entry_fee
+                    self.total_slippage -= slippage_cost
+                    logger.error("Failed to commit order reservation %s. Aborting trade entry.", res_token)
+                    return False, f"Order reservation commit failed: token {res_token} invalid or expired", None
+
+                self.open_position = pos
+                self._save_state()
```

---

## 2. Remediation for Audit 2: Idempotency Persistence & Crash Recovery

### Vulnerability Summary
`self._processed_idempotency_keys` is an in-memory dictionary omitted from `_save_state()` and `_load_state()`. Process crashes or restarts wipe this deduplication state, enabling replayed signals to execute duplicate trades once the position becomes flat.

### Proposed Code Diff for `services/delta_paper_broker.py`

```diff
--- a/services/delta_paper_broker.py
+++ b/services/delta_paper_broker.py
@@ -770,6 +770,7 @@ class DeltaPaperBroker:
             "closed_trades": self.closed_trades,
             "total_fees": round(self.total_fees, 4),
             "total_slippage": round(self.total_slippage, 4),
+            "processed_idempotency_keys": self._processed_idempotency_keys,
             "last_order": self.last_order,
             "updated_at": self._now_iso(),
         }
@@ -825,6 +826,8 @@ class DeltaPaperBroker:
             self.closed_trades = data.get("closed_trades", [])
             self.total_fees = float(data.get("total_fees", 0.0))
             self.total_slippage = float(data.get("total_slippage", 0.0))
+            self._processed_idempotency_keys = data.get("processed_idempotency_keys", {})
             self.last_order = data.get("last_order")
             logger.info("Loaded paper state from %s (equity=$%.2f, open_position=%s)",
```

### Pruning Policy
To prevent indefinite state file growth over months of continuous operation, add a pruning utility during `_save_state` that retains keys for 72 hours (or the most recent 1,000 keys), which is more than sufficient for order signal replay defense.

---

## 3. Remediation for Audit 3: Multi-Process Locking & Stream Feed Clocks

### Part A: Multi-Process OS-Level File Locking
`threading.Lock()` provides mutual exclusion only within a single Python runtime. For cross-process isolation on EC2:
1. Ensure the trading daemon runs strictly as a single designated systemd unit (`delta-paper-trading.service`) with `ExecStart` guarded against multiple instances.
2. Implement cross-platform OS file locking on the broker state file using `portalocker` or Python standard `fcntl.flock` (Linux) / `msvcrt.locking` (Windows):

```python
import portalocker

class DeltaPaperBroker:
    def _save_state(self) -> None:
        lock_file_path = f"{self.state_file}.lock"
        with portalocker.Lock(lock_file_path, timeout=5.0):
            # Atomic file write with os.replace
            ...
```

### Part B: Separate Public Feed Stream Monotonic Clocks
In `scripts/stage1_readonly_market_monitor.py`, separate the single `self.last_tick_ts` clock into independent channel tracking:

```diff
--- a/scripts/stage1_readonly_market_monitor.py
+++ b/scripts/stage1_readonly_market_monitor.py
@@ -244,7 +244,8 @@ class Stage1MarketObserver:
         self.duplicate_ticks: int = 0
         self.out_of_order_ticks: int = 0
-        self.last_tick_ts: Optional[int] = None
+        self.last_ticker_ts: Optional[int] = None
+        self.last_trade_ts: Optional[int] = None
         self.seen_tick_signatures: Set[str] = set()
@@ -355,8 +356,8 @@ class Stage1MarketObserver:
         ts = data.get("timestamp")
         if ts is not None:
             try:
                 ts_int = int(ts)
-                if self.last_tick_ts is not None and ts_int < self.last_tick_ts:
+                if self.last_ticker_ts is not None and ts_int < self.last_ticker_ts:
                     self.out_of_order_ticks += 1
-                self.last_tick_ts = ts_int
+                self.last_ticker_ts = ts_int
@@ -410,8 +411,8 @@ class Stage1MarketObserver:
         ts = data.get("timestamp")
         if ts is not None:
             try:
                 ts_int = int(ts)
-                if self.last_tick_ts is not None and ts_int < self.last_tick_ts:
+                if self.last_trade_ts is not None and ts_int < self.last_trade_ts:
                     self.out_of_order_ticks += 1
-                self.last_tick_ts = ts_int
+                self.last_trade_ts = ts_int
```

