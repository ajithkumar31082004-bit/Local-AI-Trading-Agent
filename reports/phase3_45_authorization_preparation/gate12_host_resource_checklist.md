# Phase 3.45 — Gate 12: Host Resource Headroom Checklist

**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Current Branch:** `feature/phase3-45-dual-human-authorization-prep`  
**Target Instrument:** `XAUTUSD` | **Exchange:** Delta Exchange India  
**Document Status:** OPERATIONAL CHECKLIST ONLY — NO LIVE HOST/EC2 PROBING IN PHASE 3.45  

---

## 1. Objective of Preflight Gate 12

**Preflight Gate 12** ensures that the execution host (whether a local development workstation or a remote EC2 cloud server) possesses adequate CPU, memory, storage headroom, proper process privileges, time synchronization, and daemon inactivity before the shadow observer starts.

> [!CAUTION]
> **OFFLINE AUDIT RESTRICTION:**  
> This checklist defines the exact inspection protocol for a future authorized execution. **No EC2 connections, SSH sessions, or live machine probes are executed in Phase 3.45.**

---

## 2. Host Headroom Inspection Matrix

The following table distinguishes verification commands and passing criteria between **Local Workstation (Windows)** and **EC2 Cloud Instance (Ubuntu Linux)**:

| Headroom Dimension | Passing Threshold | Local Workstation Command (PowerShell) | EC2 Instance Command (Bash / Linux) | Gate Failure Impact |
|---|---|---|---|---|
| **Operating System & Runtime** | Python $\ge 3.10.0$<br>pytest available | `python --version`<br>`Get-CimInstance Win32_OperatingSystem \| Select Caption, Version` | `python3 --version`<br>`cat /etc/os-release` | Fails Gate 12 if Python version $<3.10$. Startup aborted. |
| **Available Host RAM** | $\ge 500.0\text{ MB}$ free host RAM<br>(Observer budget: 150 MB) | `(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024` (in MB) | `free -m \| awk '/^Mem:/{print $4}'` | Fails Gate 12 if free host memory $<500\text{ MB}$. Startup aborted. |
| **Available Disk Headroom** | $\ge 1.0\text{ GB}$ free disk on drive<br>(Observer budget: 25 MB) | `(Get-PSDrive -Name C).Free / 1GB` | `df -h /home/ubuntu \| awk 'NR==2{print $4}'` | Fails Gate 12 if free disk space $<1.0\text{ GB}$. Startup aborted. |
| **Host CPU Utilization** | Overall CPU usage $<80\%$ | `(Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples.CookedValue` | `top -bn1 \| grep "Cpu(s)" \| awk '{print 100 - $8}'` | Fails Gate 12 if CPU saturated ($>80\%$). Startup aborted. |
| **Log Directory Isolation** | `data/shadow_observer/`<br>writable & isolated | `Test-Path data/shadow_observer`<br>`[System.IO.File]::WriteAllText("data/shadow_observer/.probe", "ok")` | `test -w /home/ubuntu/Local-AI-Trading-Agent/data/shadow_observer`<br>`touch data/shadow_observer/.probe` | Fails Gate 10 & 12 if directory unisolated or read-only. |
| **Process Ownership** | Non-root execution | Standard user session (Not elevated administrator) | `whoami` returns `ubuntu` (UID $\ne 0$). Running as `root` is prohibited. | Fails Gate 12 if executed as `root` on Linux/EC2. |
| **Clock Synchronization** | Clock within $\pm 1.0\text{s}$ of UTC/NTP | `w32tm /query /status` | `timedatectl status \| grep "synchronized: yes"` | Fails Gate 12 if time drift $>1.0\text{s}$. |
| **Trading Daemon State** | STOPPED (`delta_paper_bot.py` not running) | `Get-Process python* \| Where-Object {$_.CommandLine -like "*delta_paper_bot*"}` (Must be empty) | `pgrep -fa "delta_paper_bot"` (Must return zero processes) | Fails Gate 2 & 12 if trading daemon is active. Startup aborted. |

---

## 3. Code-Level Headroom Logic (`dry_run_runner.py`)

In [`services/shadow_observer/dry_run_runner.py#L201-L214`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/services/shadow_observer/dry_run_runner.py#L201-L214), Gate 12 is automatically evaluated by the runner:

```python
# Gate 12: Host resource headroom
if host_headroom_check_fn:
    headroom_ok, headroom_err = host_headroom_check_fn()
    if not headroom_ok:
        failures.append(f"Gate 12 Failed: Host resource headroom check failed: {headroom_err}")
else:
    try:
        total, used, free = shutil.disk_usage(config.output_dir.resolve().anchor or str(config.output_dir.resolve()))
        free_gb = free / (1024 ** 3)
        if free_gb < 1.0:
            failures.append(f"Gate 12 Failed: Insufficient free disk space ({free_gb:.2f} GB free, minimum 1.0 GB required).")
    except Exception as exc:
        logger.debug("Host disk check error: %s", exc)
```

---

## 4. Operator Verification Sign-Off Table

When executing an authorized preflight check, the Lead Operator must complete the following checklist:

| Check Item | Verified Metric | Result | Operator Initials |
|---|---|:---:|:---:|
| Host Operating System | Recorded: ____________________________________ | [ ] PASS | _________ |
| Python Runtime Version | Recorded: ____________________________________ | [ ] PASS | _________ |
| Available Free RAM | Recorded: __________ MB ($\ge 500\text{ MB}$ required) | [ ] PASS | _________ |
| Available Free Disk | Recorded: __________ GB ($\ge 1.0\text{ GB}$ required) | [ ] PASS | _________ |
| CPU Utilization | Recorded: __________ % ($<80\%$ required) | [ ] PASS | _________ |
| Output Directory Writable | `data/shadow_observer/` verified isolated | [ ] PASS | _________ |
| Process User Privilege | Executing as non-root standard user | [ ] PASS | _________ |
| Time Sync Verification | Clock drift $<1.0\text{s}$ verified | [ ] PASS | _________ |
| Daemon Inactivity Check | `delta_paper_bot.py` confirmed STOPPED | [ ] PASS | _________ |
