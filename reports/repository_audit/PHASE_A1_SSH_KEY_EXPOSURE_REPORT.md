# Phase A1 — SSH Private Key Exposure Investigation Report

**Document Identifier:** `REP-AUD-A1-SSH-EXP`  
**Repository:** `ajithkumar31082004-bit/Local-AI-Trading-Agent`  
**Local Working Path:** `C:\Users\ajith\Downloads\Local_AI_Agent`  
**Investigated Target:** `Super_Market.pem`  
**Investigation Date:** 2026-11-11  
**Auditor Roles:** Senior Git Security Engineer & AWS Infrastructure Security Auditor  
**Audit Mode:** Offline, Read-Only Security Inspection  

---

## 1. Executive Summary & Verdict

### Final Verdict: **CONDITIONAL PASS**
- **Findings Summary:**
  - **Zero Private Key Blob Exposure in Git:** The investigated file `Super_Market.pem` is **NOT tracked** by Git. An exhaustive audit of the entire Git object database across all commits, branches, and reflogs confirms that the private key file and its RSA payload have **never been committed** to the repository.
  - **Active `.gitignore` Protection:** The pattern `*.pem` was explicitly committed to `.gitignore` on Nov 4, 2026 (Commit `90e6089`). The key file has remained continuously ignored since that commit.
  - **Operational Exposure & Host Metadata Leak:** The filename `Super_Market.pem`, the associated AWS EC2 public IP address (`16.4.36.144`), and administrative SSH commands are committed in five Markdown documentation and preflight report files. If the remote GitHub repository is public, this provides threat actors with target reconnaissance data.
  - **Local Storage Misplacement:** The genuine 2048-bit RSA private key is located in the local software workspace root rather than a dedicated user SSH directory (`~/.ssh/`).
- **Verdict Rationale:** Because remote GitHub repository visibility (public vs. private) cannot be verified offline, and because host reconnaissance metadata is committed to Git, exposure cannot be completely ruled out. A definitive `PASS` cannot be granted without remote repository verification.

---

## 2. File Metadata & Cryptographic Type Inspection

Inspection was performed strictly using non-revealing metadata and structural parsers. No secret payload or base64 key data was output or displayed.

| Property | Inspected Value | Security Assessment |
|---|---|---|
| **File Path** | `C:\Users\ajith\Downloads\Local_AI_Agent\Super_Market.pem` | Stored in repository root directory (**Improper Location**) |
| **File Size** | 1,674 bytes | Standard 2048-bit RSA PKCS#1 PEM encoding |
| **Creation Time** | 2026-11-04 22:02:56 IST | Placed into repository directory |
| **Last Write Time** | 2026-08-16 18:41:09 IST | Original file generation timestamp |
| **Header Marker** | `-----BEGIN RSA PRIVATE KEY-----` | Genuine OpenSSL / AWS EC2 RSA Private Key |
| **Footer Marker** | `-----END RSA PRIVATE KEY-----` | Cryptographically complete PEM block |
| **Line Count** | 27 lines | Consistent with standard OpenSSH/AWS key pairs |
| **Windows Permissions (ACL)** | `AJITHKUMAR\ajith Allow Read, Synchronize` | Restricted to current user; inheritance not active |

---

## 3. Git Tracking & Historical Repository Audit

### 3.1 Git Tracking Status (`git ls-files`)
- Command: `git ls-files --error-unmatch Super_Market.pem`
- Result: Exited with code 1 (`pathspec 'Super_Market.pem' did not match any file(s) known to git`).
- Status: **NOT TRACKED**.

### 3.2 Commit History & Blob Audit
- **Full History Path Search:**
  ```powershell
  git log --all --full-history -- "**/Super_Market.pem" "Super_Market.pem"
  ```
  *Result:* **Zero commits.** The file path was never committed in any commit or tree.
- **Content String Search (`git log -S`):**
  Searches for `"BEGIN RSA PRIVATE KEY"`, `"BEGIN OPENSSH PRIVATE KEY"`, and `"BEGIN PRIVATE KEY"` across all historical commits returned **zero matches**.
- **Reflog Audit:**
  `git reflog` contains zero references to `Super_Market.pem` being staged or committed.

### 3.3 `.gitignore` Rule Chronology
- **Existing Rule:** Lines 30–35 of [`.gitignore`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/.gitignore) contain:
  ```gitignore
  # Environments & secrets
  .env
  .env.local
  *.pem
  *.key
  ```
- **Commit History of Rule:** Added in commit [`90e6089f45`](file:///C:/Users/ajith/Downloads/Local_AI_Agent):
  - Commit Date: `Wed Nov 4 22:08:33 2026 +0530`
  - Message: `chore(security): add *.pem and *.key to gitignore`
- **Window of Risk Analysis:**
  - `Super_Market.pem` was copied to the workspace directory at `22:02:56 IST`.
  - `.gitignore` was updated to exclude `*.pem` at `22:08:33 IST` (5 minutes 37 seconds later).
  - An audit of commit logs confirmed that **zero commits were authored or staged** during this ~6-minute interval. Commit `408d63a` preceded it at `21:45:26 IST`. Therefore, the key was never staged during the untracked window.

---

## 4. Associated Host, Infrastructure & Usage Identification

### 4.1 Committed Text References
A `git grep` across tracked files identified five Markdown reports that document commands referencing `Super_Market.pem`:
1. [`reports/PHASE_3_19_MONITORING_SECURITY_READINESS.md`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/reports/PHASE_3_19_MONITORING_SECURITY_READINESS.md)
2. [`reports/PHASE_3_20_FINAL_DEPLOYMENT_PREFLIGHT.md`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/reports/PHASE_3_20_FINAL_DEPLOYMENT_PREFLIGHT.md)
3. [`reports/PHASE_3_21_FINAL_DEPLOYMENT_COMMAND_REVIEW.md`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/reports/PHASE_3_21_FINAL_DEPLOYMENT_COMMAND_REVIEW.md)
4. [`reports/phase3_34_readonly_production_preflight/PHASE_3_34_PREFLIGHT_REPORT.md`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/reports/phase3_34_readonly_production_preflight/PHASE_3_34_PREFLIGHT_REPORT.md)
5. [`reports/phase3_34_readonly_production_preflight/ec2_readonly_inspection.json`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/reports/phase3_34_readonly_production_preflight/ec2_readonly_inspection.json)

### 4.2 Associated EC2 Host Details
From the audited reports, the associated infrastructure is confirmed as:
- **Cloud Provider:** Amazon Web Services (AWS)
- **Target Instance:** EC2 Ubuntu Instance
- **Public IP Address:** `16.4.36.144`
- **SSH Username:** `ubuntu`
- **SSH Port:** `22` (default)
- **Operational Purpose:**
  - Administrative SSH terminal access.
  - SSH port-forwarding for read-only FastAPI monitoring (`ssh -N -L 8088:127.0.0.1:8088 -i Super_Market.pem ubuntu@16.4.36.144`).
  - Secure copy (`scp`) deployment of monitoring scripts and systemd unit files.
  - Systemd service inspection (`delta-paper-trading.service`, `delta-monitoring-api.service`).
- **Last Verified Connectivity:** Documented in `PHASE_3_34_PREFLIGHT_REPORT.md` as having established an active SSH session on 2026-11-06.

---

## 5. Remote Exposure Analysis: Confirmed vs. Unknown

| Dimension | Classification | Evidentiary Basis |
|---|:---:|---|
| **Private Key Payload in Git** | **CONFIRMED NOT EXPOSED** | 0 commits contain key strings; 0 blobs match file; `.gitignore` actively ignores file. |
| **Key Filename in Git** | **CONFIRMED EXPOSED** | Committed in 5 report files alongside SSH commands. |
| **EC2 Public IP (`16.4.36.144`)** | **CONFIRMED EXPOSED** | Committed in documentation across multiple commits since Phase 3.19. |
| **Remote Repository Visibility** | **UNKNOWN** | Cannot determine if GitHub repository is Public or Private without network access. |
| **Third-Party / Out-of-Band Exposure** | **UNKNOWN** | Cannot verify whether key was copied, backed up, or shared via channels outside Git. |

---

## 6. Security Risk & Severity Assessment

### Overall Risk Level: **MEDIUM-HIGH**

1. **Why not CRITICAL?**  
   The actual private key payload (`-----BEGIN RSA...`) was never committed to Git. A clone of the repository does not yield the private key.
2. **Why MEDIUM-HIGH?**  
   - **Target Reconnaissance Leaked:** If the GitHub repository is public, an adversary has the exact EC2 public IP (`16.4.36.144`), username (`ubuntu`), running ports (`8088`, `22`), and the key pair name (`Super_Market`).
   - **Local Workspace Vulnerability:** Keeping private keys in a software project workspace poses severe operational risks: accidental future `git add -f`, inclusion in project ZIP/TAR backups, or exposure via IDE plugins.

---

## 7. Key Rotation Necessity & Recommendations

### Is Key Rotation Necessary?
**YES (Strongly Recommended).**  
Because the key pair has been stored in a development workspace and referenced in documentation alongside the server's public IP address, rotating the key is the only action that guarantees long-term infrastructure security.

---

## 8. Safe Recovery & Rotation Runbook

> [!CAUTION]
> **DO NOT delete or move `Super_Market.pem` immediately!**  
> Deleting the old key before testing the new key risks permanently locking the operator out of the EC2 instance. Follow the step-by-step recovery plan below:

### Step 1: Generate a New Dedicated Keypair (Local Workstation)
Generate a modern Ed25519 or 4096-bit RSA keypair directly in the user's secure `.ssh` directory:
```powershell
ssh-keygen -t ed25519 -f "$HOME\.ssh\delta_trading_ec2" -C "delta-trading-ec2-key"
```

### Step 2: Install the New Public Key on EC2 (Using Existing Key)
Using the existing `Super_Market.pem` key, append the new public key to the EC2 authorized keys:
```powershell
Get-Content "$HOME\.ssh\delta_trading_ec2.pub" | ssh.exe -i Super_Market.pem ubuntu@16.4.36.144 "cat >> ~/.ssh/authorized_keys"
```

### Step 3: Verify Independent Connectivity with the New Key
Open an independent terminal window and test logging in with the new key without specifying `Super_Market.pem`:
```powershell
ssh.exe -i "$HOME\.ssh\delta_trading_ec2" ubuntu@16.4.36.144 "echo 'NEW_KEY_VERIFIED'"
```
*Do not proceed to Step 4 until `NEW_KEY_VERIFIED` is confirmed.*

### Step 4: Deauthorize Old Key on EC2
Remove the old public key corresponding to `Super_Market` from `/home/ubuntu/.ssh/authorized_keys` on the EC2 instance.

### Step 5: Securely Relocate Local `Super_Market.pem`
Once the new key is functional:
1. Move `Super_Market.pem` out of `C:\Users\ajith\Downloads\Local_AI_Agent` to a secure offline storage directory or shred it.
2. Ensure no `.pem` files remain in the repository workspace.

### Step 6: Long-Term Recommendation (AWS SSM Session Manager)
For enterprise-grade security, configure **AWS Systems Manager (SSM) Session Manager** on the EC2 instance and close inbound SSH Port 22 in the AWS Security Group entirely. This provides IAM-governed, keyless terminal access with complete CloudTrail session logging.

---

## 9. Exact Files Modified in this Phase

Only the newly generated investigation report was created:
- [`reports/repository_audit/PHASE_A1_SSH_KEY_EXPOSURE_REPORT.md`](file:///C:/Users/ajith/Downloads/Local_AI_Agent/reports/repository_audit/PHASE_A1_SSH_KEY_EXPOSURE_REPORT.md)

Zero source code files, configuration files, git histories, or `.pem` keys were modified, renamed, moved, or deleted.

