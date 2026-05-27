# OpenHands + Ollama + Qwen Local Setup (ThinkPad T14 Gen 4)

## Goal

Run OpenHands with local Qwen via Ollama.

---

## 1. Pull OpenHands

```bash
docker pull ghcr.io/all-hands-ai/openhands:main
```

---

## 2. Run OpenHands (Linux Networking Fix)

```bash
docker run -it --rm \
  --add-host=host.docker.internal:host-gateway \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -p 3000:3000 \
  ghcr.io/all-hands-ai/openhands:main
```

Open:

```text
http://localhost:3000
```

---

## 3. Verify Ollama

```bash
curl http://localhost:11434/api/tags
```

---

## 4. Fix Ollama Host Binding

### Problem

Docker could not reach Ollama.

Check:

```bash
ss -tulpn | grep 11434
```

If:

```text
127.0.0.1:11434
```

Fix:

```bash
sudo systemctl edit ollama
```

Add:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

Reload:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Verify:

```bash
ss -tulpn | grep 11434
```

Expected:

```text
0.0.0.0:11434
```

---

## 5. Verify Docker → Ollama Connectivity

Enter OpenHands container:

```bash
docker exec -it <openhands_container> bash
```

Test:

```bash
curl http://host.docker.internal:11434/api/tags
```

Success:

- Qwen models visible
- Docker ↔ Ollama networking working

---

## 6. OpenHands Configuration

Enable **Advanced**.

### Custom Model

```text
ollama/qwen2.5-coder:7b
```

### Base URL

```text
http://host.docker.internal:11434
```

### API Key

```text
ollama
```

### Agent

```text
CodeActAgent
```

### Important

Use native Ollama endpoint:

```text
http://host.docker.internal:11434
```

NOT:

```text
/v1
```

because LiteLLM already uses native Ollama provider when model starts with:

```text
ollama/
```

---

## 7. Runtime Image Fix

### Problem

```text
micromamba not found
```

### Cause

Custom sandbox image incompatible.

Problematic:

```bash
-e SANDBOX_RUNTIME_CONTAINER_IMAGE=...
```

### Fix

Remove sandbox override.

Use OpenHands default runtime.

---

## 8. Runtime Buildup Fix

### Problem

Multiple runtime containers accumulated.

Check:

```bash
docker ps
```

Example:

```text
openhands-runtime-xxx
openhands-runtime-yyy
openhands-runtime-zzz
```

Cleanup:

```bash
docker stop $(docker ps -q --filter name=openhands-runtime)
```

Healthy state:

```text
1 OpenHands app
1 runtime sandbox max
```

---

## 9. Performance Diagnosis

### Observed

OpenHands slow / stuck.

### Not a Networking Issue

Ollama logs showed:

- prompt ~10k tokens
- context limit 4096
- truncation
- timeout
- client abort

Cause:

```text
OpenHands
+
7B
+
CPU-only
+
4096 context
=
heavy agent prompt overload
```

---

## 10. Hardware State

Device:

```text
ThinkPad T14 Gen 4
Intel UHD Graphics (Raptor Lake)
15GB RAM
CPU-only inference
```

Check:

```bash
ollama ps
```

Observed:

```text
PROCESSOR = 100% CPU
```

Meaning:

```text
No GPU acceleration
Ollama runs entirely on CPU
```

---

## 11. Recommended Path

### OpenHands + 7B

Works, but heavy on ThinkPad T14 CPU-only setup.

Better options:

### Option A — Increase Context

8192+

Example:

```bash
export OLLAMA_CONTEXT_LENGTH=8192
```

### Option B — Use Smaller Model (Recommended)

```text
qwen2.5-coder:3b
```

### Option C — Move to OpenCode

Lighter agent workflow compared to OpenHands.

---

## Final Diagnosis

```text
Setup SUCCESS
Networking SUCCESS
Runtime SUCCESS

Main bottleneck:
OpenHands prompt size
+
CPU-only 7B inference
+
4096 context limit
```