# PHASE 3.41 — AUTOMATIC STOP CONDITIONS & TEARDOWN PROTOCOL

**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Target Instrument:** `XAUTUSD`  
**Mode:** STRICT READ-ONLY SHADOW / OFFLINE PLANNING ONLY.

---

## 1. Ten Mandatory Automatic Stop Conditions

During any future 60-minute public market-data observation, the runner ([`ShadowDryRunRunner.evaluate_stop_conditions()`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/dry_run_runner.py)) continuously evaluates ten automatic tripwire conditions on every iteration:

| Condition # | Trigger Event | Threshold / Policy | Immediate Action | Test Verification |
| :---: | :--- | :--- | :--- | :---: |
| **Stop 1** | **60-Minute Max Duration** | Elapsed runtime $\ge 3,600.0\text{s}$. | Orderly completion shutdown. | `test_05` |
| **Stop 2** | **Stale Market Feed** | No tick received for $>60.0\text{s}$. | Halt observation; mark feed stale. | `test_06` |
| **Stop 3** | **Queue Overflow Detected** | `queue_overflow_count > 0`. | Halt observation; prevent silent loss. | `test_07` |
| **Stop 4** | **Product Mismatch** | Symbol $\ne \text{XAUTUSD}$. | Halt; drop frame; flag violation. | `test_03` |
| **Stop 5** | **Private Endpoint Access** | URL contains forbidden keywords. | Halt; raise `SafetyViolation`. | `test_04` |
| **Stop 6** | **Broker Dependency Infiltration** | Broker module or method detected. | Halt; raise `SafetyViolation`. | `test_11` (3.39) |
| **Stop 7** | **Memory Budget Overrun** | Process RSS $>150.0\text{ MB}$. | Halt; prevent memory exhaustion. | `test_08` |
| **Stop 8** | **Disk Budget Overrun** | Shadow directory $>25.0\text{ MB}$. | Halt; prevent disk saturation. | `test_08` |
| **Stop 9** | **Human Operator Stop** | SIGINT (Ctrl+C) or stop signal. | Immediate graceful shutdown. | `test_09` |
| **Stop 10** | **Unexpected Exception** | Socket error or fatal crash. | Halt; clean sockets; re-raise. | `test_02` (3.40) |

---

## 2. Orderly Teardown Protocol

When any stop condition is triggered, the runner executes the following sequential teardown protocol in `ShadowDryRunRunner.stop()`:

```
              [Stop Condition Triggered]
                           │
                           ▼
             1. Set is_running = False
             2. Set has_terminated = True
             3. Record Exact Shutdown Reason
                           │
                           ▼
          [Stop TransportSupervisor]
          - Send WSS unsubscription
          - Close active socket (_socket.close())
          - Set _socket = None
          - Set ws_client.state = STOPPED
          - Set rest_client.state = STOPPED
          - Set supervisor.state = STOPPED
                           │
                           ▼
             4. Flush In-Memory Telemetry
             5. Write Summary to data/shadow_observer/
             6. Assert Transport State is STOPPED
                           │
                           ▼
         [TERMINAL STATE: RESTART PREVENTED]
```

---

## 3. Guarantees During Shutdown

1. **Zero Order Execution:** The shutdown sequence touches only in-memory flags, sockets, and shadow JSON reports. It has zero code pathways to `DeltaPaperBroker` or order endpoints.
2. **Zero Dangling Sockets:** All socket descriptors are explicitly closed and zeroed in `finally` blocks, preventing kernel socket leaks.
3. **No Automatic Restart:** Once `has_terminated` is set to `True`, any subsequent call to `start()` immediately raises `ShadowObserverSafetyViolation("SECURITY VIOLATION: Restart after shutdown is prohibited.")`.

---

## 4. Audit Verdict

**PASS**. All ten stop conditions are implemented, tested offline, and guarantee deterministic teardown without order execution or resource leakage.