# Vertex AI Setup (Auth + Models) — Petamind MCP

This doc is a practical checklist for getting **Petamind MCP** working with **Vertex AI**.

It focuses on the two common failure classes:

1) **Auth** (401/403)  
2) **Model endpoint / location / access** (404/permission errors)

---

## 0) Prereqs

- A Google Cloud project you control.
- The **Vertex AI API** enabled in that project.
- `gcloud` installed (recommended).

---

## 1) Enable required APIs (project)

Enable at least:

- Vertex AI API (`aiplatform.googleapis.com`)

You can do this in the GCP Console, or via CLI:

```bash
gcloud services enable aiplatform.googleapis.com
```

---

## 2) Pick an auth strategy (recommended: ADC)

Petamind MCP uses `google-auth` which fetches **short-lived access tokens**. This is expected.
You should NOT be rotating tokens manually every hour.

### Option A (recommended): ADC (Application Default Credentials)

This is the cleanest “permanent” setup for local development:

```bash
gcloud auth application-default login
```

Notes:
- This stores refresh credentials locally and `google-auth` refreshes tokens automatically.
- You may need to set the active project:
  ```bash
  gcloud config set project YOUR_GCP_PROJECT
  ```

### Option B: Service account JSON

Good for servers/CI.

1) Create a service account.
2) Grant it least-privilege IAM roles (see next section).
3) Download the JSON key.
4) Point `google-auth` at it:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/absolute/path/to/key.json"
```

**Do not commit the key**. Keep it outside the repo or in a secret manager.

---

## 3) IAM roles (minimum viable)

The exact role set varies by org policies, but the minimum usually includes:

- Vertex AI user permissions (for calling prediction endpoints)
- Service usage consumer permissions (to call enabled APIs)

If you’re using a service account, grant roles at the project level.

If you’re using ADC (your user), make sure your user has equivalent permissions.

---

## 4) Location / region vs global

Petamind MCP supports:

- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_REGION`

Many **MaaS / publisher models** on Vertex are served from `global`, while some org setups
require a region for policy reasons.

Start with:

```bash
export GOOGLE_CLOUD_REGION="global"
```

If your org requires a region, set a region (e.g. `us-central1`) but be aware:
- Some models may not be available in every region/location
- Some endpoints may return 404 when the location is wrong

---

## 5) Model access / entitlements

For **publisher models** (e.g., Anthropic Claude on Vertex), projects can require:

- Accepting terms in the console
- Allowlisting / entitlement approvals

If you see a 404 or permission error even with valid auth:
- Confirm the model is enabled/available for your project
- Confirm the location is correct (`global` vs region)

---

## 6) Quick “is auth working?” check

From inside the repo venv:

```bash
source .venv/bin/activate
python -c "import google.auth; print(google.auth.default()[0].quota_project_id)"
```

If that fails, fix auth before debugging the MCP server.

---

## 7) Recommended env vars (local dev)

Put these in your `.env` (copied from `.env.example`):

```bash
GOOGLE_CLOUD_PROJECT="YOUR_GCP_PROJECT"
GOOGLE_CLOUD_REGION="global"
TITAN_CONFIG_PATH="config/config.yaml"
```

---

## References

- Claude Code MCP docs: `https://code.claude.com/docs/claude-code/mcp`
- Vertex AI “partner models” (Claude on Vertex): `https://cloud.google.com/vertex-ai/generative-ai/docs/partner-models/use-claude`
