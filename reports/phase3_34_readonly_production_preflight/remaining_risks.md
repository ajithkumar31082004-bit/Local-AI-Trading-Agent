# Phase 3.34 — Remaining Operational Risks & Environment Observations

This document details residual operational risks, environment observations, and infrastructural considerations following the Phase 3.34 read-only preflight inspection.

---

## 1. Environment & Branch Alignment

| Observation | Risk Assessment | Mitigation / Operational Action |
| :--- | :--- | :--- |
| **Branch Discrepancy** | The EC2 instance is currently on branch `feature/stage1-readonly-observer` at commit `a7d7c9a`. The approved candidate release is commit `2807757` on `feature/phase3-33-operational-readiness-audit`. | Any future deployment must explicitly fetch and check out the approved commit `2807757` without merging unreviewed branches. |
| **Untracked Runtime Logs on EC2** | `git status` on EC2 reports untracked runtime logs (`data/monitoring_api.log`, `data/paper_trading_service.log`, `.env.monitoring`). | These are standard production log and environment files. They must be preserved during any future checkout. |
| **Memory Constraints on t2/t3.micro** | The EC2 instance has 908MB physical RAM, with 421MB used and 486MB available. 4GB swap space is configured with 3.9GB free. | With Streamlit (~16MB) and Uvicorn (~159MB) running, the paper trading daemon (~100-120MB) fits comfortably within available RAM without swap pressure. |

---

## 2. Safety Invariants & Operational Boundaries

1. **Daemon State**: `delta-paper-trading.service` is inactive (dead) and has not executed since Fri 2026-11-06.
2. **Configuration Mode**: Both `.env` on EC2 and local settings strictly enforce `TRADING_MODE=PAPER` and `LIVE_TRADING_ENABLED=false`.
3. **Hard Sizing Barrier**: Central Position Size Guard enforces `APPROVED_MAX_PAPER_LOTS == 1`.
4. **Human Authorization Gate**: Zero automated deployment or daemon startup scripts have been executed. All operational actions remain strictly halted awaiting human review.
