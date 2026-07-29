# Sentinel API Deployment on Render

## Quick Health Check

After deploying to Render, verify the service is healthy:

```bash
curl https://your-render-service.onrender.com/health
```

Should return:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "neo4j_available": true,
  "llm_available": false,
  "embeddings_available": false,
  "prompt_modifier_available": false,
  "jira_available": false
}
```

## Required Environment Variables

Set these in your Render dashboard under **Environment**:

### Critical (Service will fail without these)

| Variable | Example | Source |
|----------|---------|--------|
| `NEO4J_URI` | `neo4j+s://xxxxx.databases.neo4j.io` | Neo4j Aura console |
| `NEO4J_USER` | `neo4j` | Neo4j Aura console |
| `NEO4J_PASSWORD` | `xxxxx` | Neo4j Aura console |
| `GROQ_API_KEY` | `gsk_xxxxx` | [Groq Console](https://console.groq.com) |
| `GITHUB_TOKEN` | `ghp_xxxxx` | [GitHub Settings → Developer settings](https://github.com/settings/tokens) |
| `GITHUB_REPO` | `owner/repo-name` | Your policy repo |

### Optional but Recommended

| Variable | Example | Purpose |
|----------|---------|---------|
| `HF_ROUTER_URL` | `https://huggingface.co/spaces/user/model/call/predict` | Prompt enhancement (Hugging Face Spaces) |
| `GITHUB_POLICY_MANIFEST_REPO` | `owner/policy-repo` | If different from `GITHUB_REPO` |
| `GITHUB_POLICY_CONTENTS_REPO` | `owner/policy-repo` | If different from `GITHUB_REPO` |
| `GITHUB_DOCS_ROOT` | `hackathon-docs` | Subdirectory path in repo |
| `ENABLE_FOLLOWUP_SUGGESTIONS` | `true` | Enable AI follow-up questions |

## Troubleshooting "Service Unavailable"
### `/enhance` Returns 503 (Prompt Modifier)

This is **NOT a critical error** – your chat still works! It just means prompt enhancement is disabled.

**Logs will show:**
```
[ERROR] prompt_modifier import failed: ...
[ERROR] HF_ROUTER_URL env var: <not set>
```

**Causes & Fixes:**

1. **HF_ROUTER_URL not set** (Most Common)
  - Add `HF_ROUTER_URL` to Render environment variables
  - Get it from your HuggingFace Spaces deployment URL (ends with `/call/predict`)
  - Example: `https://huggingface.co/spaces/username/model-name/call/predict`

2. **HuggingFace Spaces endpoint is down**
  - Verify the HF Spaces deployment is running
  - Test it directly: `curl -X POST "https://your-hf-space/call/predict" -H "Content-Type: application/json" -d '{"prompt":"test"}'`
  - If unreachable, restart the HF Spaces app

3. **Import error** (e.g., missing `requests` module)
  - Check Render logs for full error message
  - Ensure `requirements.txt` includes `requests>=2.31.0`
  - Trigger a redeploy after updating requirements.txt

**Workaround:** The chat endpoint doesn't require prompt enhancement. Users can still get answers, just without the optimized query rewriting.

---

Check Render logs for these scenarios:

### 1. **Missing Critical Environment Variables**
```
[WARNING] NEO4J_URI not set. Graph queries will fail on first use.
[WARNING] GROQ_API_KEY not set. LLM generation will fail on first use.
```
**Fix:** Add the missing variables to Render environment.

### 2. **Neo4j Connection Failure**
On first request after deployment:
```
Neo4j unavailable: Connection refused / Authentication failed
```
**Fix:**
- Verify `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` are correct
- Ensure Neo4j Aura database is running
- Check firewall: Render IPs must be whitelisted in Neo4j Aura settings

### 3. **HuggingFace Router Unreachable**
```
HuggingFace router request failed (URL: ...): Connection timeout
```
**Fix:**
- Verify HF Spaces deployment is running
- Check `HF_ROUTER_URL` is correct and publicly accessible
- Prompt enhancement will fail, but chat still works

### 4. **GitHub Policy Manifest BOM Error**
```
Invalid policy manifest JSON: Unexpected UTF-8 BOM (decode using utf-8-sig)
```
**Fix:** Already fixed in code, but ensure `GITHUB_TOKEN` has repo access.

## Health Check Endpoints

### `/` (Root)
Basic status check:
```bash
curl https://your-render-service.onrender.com/
```

### `/health` (Recommended)
Full component status (safe for frequent polling):
```bash
curl https://your-render-service.onrender.com/health
```

### `/docs` (Swagger UI)
Interactive API documentation:
```
https://your-render-service.onrender.com/docs
```

## Startup Sequence

Render will log the initialization on every deploy:

```
[INFO] Sentinel API startup sequence initiated...
[INFO] ✓ GitHub policy proxy configured
[WARNING] HF_ROUTER_URL not set. Prompt enhancement will be unavailable.
[INFO] ✓ GROQ_API_KEY configured
[INFO] ✓ NEO4J_URI configured
[INFO] Startup validation complete. Service ready to accept requests.
```

## Performance Notes

- **First Request Delay:** Neo4j driver, LLM client, and embeddings models are initialized lazily on first use (~5-10s)
- **Subsequent Requests:** Fast (<500ms) after initialization
- **Memory:** Keep Render plan at **2GB RAM** minimum for model loading

## Monitoring

Add these commands to your Render monitoring:

1. **Health check URL:** `https://your-service.onrender.com/health`
2. **Expected HTTP status:** 200
3. **Check interval:** 60 seconds
