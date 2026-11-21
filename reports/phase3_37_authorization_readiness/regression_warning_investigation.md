# Phase 3.37 — Regression Warning Investigation

**Audit Date:** 2026-11-11  
**Target Instrument:** `XAUTUSD`  
**Base Commit:** `13e8680`  
**Audit Branch:** `feature/phase3-37-authorization-readiness`  
**Auditor:** Antigravity Independent Audit Agent  
**Scope:** Forensic identification, root-cause tracing, and safety assessment of the 4 warnings observed during regression test runs (`463 passed, 4 warnings in 140.98s`).

---

## 1. Executive Summary

During the Phase 3.36 independent audit, the full repository test suite completed with:
`463 passed, 4 warnings in 140.98s`

As mandated by Phase 3.37 instructions, these four warnings were independently investigated without suppression or dismissal. The investigation confirmed:
1. All four warnings are third-party library `DeprecationWarning`s originating from `supabase-py` initializing `SyncPostgrestClient` inside virtual environment site-packages (`.venv/Lib/site-packages/supabase/_sync/client.py:309`).
2. The warnings occur during mock SQLite repository testing in [`tests/test_paper_broker.py`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_paper_broker.py) when an optional cloud telemetry client is initialized.
3. The read-only shadow observer (`services/shadow_observer/`) has **zero imports or dependencies** on `supabase`, `database.repository.DatabaseRepository`, or `services.paper_broker.PaperBroker`.
4. The warnings do not compromise observer safety, do not indicate memory or resource leaks, and do not block public market data observation.
5. In accordance with audit standards, warnings are documented with full provenance rather than artificially masked.

---

## 2. Forensic Warning Inventory

### 2.1 Warning 1: `timeout` Parameter Deprecation in `test_buy_and_sell_cycle`
- **Exact Warning Text:**
  ```text
  DeprecationWarning: The 'timeout' parameter is deprecated. Please configure it in the http client instead.
    return SyncPostgrestClient(
  ```
- **Source File & Line:**
  `C:\Users\ajith\Downloads\Local_AI_Agent\.venv\Lib\site-packages\supabase\_sync\client.py:309`
- **Test That Generated It:**
  [`tests/test_paper_broker.py::test_buy_and_sell_cycle`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_paper_broker.py#L18-L57)
- **Warning Category:** `DeprecationWarning` (Python standard library warning class).
- **Trigger Chain:**
  1. `test_paper_broker.py` invokes fixture `test_repo` ([`tests/test_paper_broker.py#L10-L15`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_paper_broker.py#L10-L15)).
  2. `test_repo` constructs `DatabaseRepository(db_path=...)` ([`database/repository.py#L23-L27`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/database/repository.py#L23-L27)).
  3. `DatabaseRepository` instantiates `SupabaseClient()` ([`database/supabase_client.py#L39`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/database/supabase_client.py#L39)).
  4. `SupabaseClient._init_client()` calls `create_client(self.url, self.key)`.
  5. `create_client()` constructs `SyncClient`, which passes `timeout=self.options.postgrest_client_timeout` to `SyncPostgrestClient`.
  6. `postgrest` package emits `DeprecationWarning` because httpx client-level timeouts are preferred in newer versions of `postgrest-py`.

### 2.2 Warning 2: `verify` Parameter Deprecation in `test_buy_and_sell_cycle`
- **Exact Warning Text:**
  ```text
  DeprecationWarning: The 'verify' parameter is deprecated. Please configure it in the http client instead.
    return SyncPostgrestClient(
  ```
- **Source File & Line:**
  `C:\Users\ajith\Downloads\Local_AI_Agent\.venv\Lib\site-packages\supabase\_sync\client.py:309`
- **Test That Generated It:**
  [`tests/test_paper_broker.py::test_buy_and_sell_cycle`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_paper_broker.py#L18-L57)
- **Warning Category:** `DeprecationWarning`.
- **Trigger Chain:** Same as Warning 1; `SyncPostgrestClient` receives `verify=self.options.verify`.

### 2.3 Warning 3: `timeout` Parameter Deprecation in `test_account_reset`
- **Exact Warning Text:**
  ```text
  DeprecationWarning: The 'timeout' parameter is deprecated. Please configure it in the http client instead.
    return SyncPostgrestClient(
  ```
- **Source File & Line:**
  `C:\Users\ajith\Downloads\Local_AI_Agent\.venv\Lib\site-packages\supabase\_sync\client.py:309`
- **Test That Generated It:**
  [`tests/test_paper_broker.py::test_account_reset`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_paper_broker.py#L71-L79)
- **Warning Category:** `DeprecationWarning`.
- **Trigger Chain:** Same as Warning 1, triggered when `test_account_reset` instantiates `test_repo`.

### 2.4 Warning 4: `verify` Parameter Deprecation in `test_account_reset`
- **Exact Warning Text:**
  ```text
  DeprecationWarning: The 'verify' parameter is deprecated. Please configure it in the http client instead.
    return SyncPostgrestClient(
  ```
- **Source File & Line:**
  `C:\Users\ajith\Downloads\Local_AI_Agent\.venv\Lib\site-packages\supabase\_sync\client.py:309`
- **Test That Generated It:**
  [`tests/test_paper_broker.py::test_account_reset`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/tests/test_paper_broker.py#L71-L79)
- **Warning Category:** `DeprecationWarning`.
- **Trigger Chain:** Same as Warning 2, triggered when `test_account_reset` instantiates `test_repo`.

---

## 3. Impact Assessment on Shadow Observer Safety

| Audit Criteria | Observation | Assessment |
| :--- | :--- | :---: |
| **Import Coupling** | Does `services/shadow_observer` import `supabase` or `database.repository`? | **NO (0 occurrences)** |
| **Execution Path** | Does public market data ingestion invoke `test_paper_broker` code? | **NO** |
| **Runtime Crash Risk** | Can these warnings raise exceptions or crash the observer? | **NO (Warnings only)** |
| **Data Integrity Risk** | Do these warnings affect candle validation or strategy evaluation? | **NO** |
| **Security Risk** | Do these warnings leak credentials or alter isolation boundaries? | **NO** |

---

## 4. Remediation Decision

- **Required Action Before Public-Feed Test:** **NONE**.
- **Rationale:** These warnings are emitted exclusively by third-party upstream code in `supabase-py` during testing of legacy components. Suppressing them via `pytest.ini` filters (`filterwarnings = ignore::DeprecationWarning`) would mask the test environment rather than fix a real defect. Leaving them visible preserves test transparency without any impact on shadow observer execution.
