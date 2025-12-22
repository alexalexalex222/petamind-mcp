# Quick Reference

## Run Factory
```bash
source .venv/bin/activate
titan-factory run --max-tasks 1
```

## Check Latest Output
```bash
ls -lt out/ | head -3
```

## Query Candidates
```bash
sqlite3 out/run_*/manifest.db "SELECT id, status FROM candidates"
```

## Extract Candidate Code
```bash
sqlite3 out/run_*/manifest.db "SELECT code FROM candidates WHERE id=1"
```

## Preview Locally
```bash
cp -r templates/nextjs_app_router_tailwind /tmp/preview
cd /tmp/preview && npm ci
# paste candidate code into app/page.tsx
npm run dev
```

## Check for Emojis
```bash
sqlite3 out/run_*/manifest.db "SELECT id, code FROM candidates" | grep -P '[\x{1F300}-\x{1F9FF}]'
# Empty output = no emojis
```

## Key Files
| File | Purpose |
|------|---------|
| `src/titan_factory/uigen.py` | UI generation prompt |
| `src/titan_factory/judge.py` | Creative Director prompt |
| `src/titan_factory/refiner.py` | Refinement loop |
| `config/config.yaml` | Model config |
