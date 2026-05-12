# Nervos Brain systemd user timers

These files are deployment templates for local/server use. They are not required for tests.

## Talk forum incremental ingest

The Talk forum database is already fully crawled; the timer only keeps it fresh by crawling latest pages and skipping existing anchors.

Default cadence: every 24 hours. Default command:

```bash
mamba run -n nervos-brain python scripts/run_talk_forum_ingest.py --latest-pages 3 --incremental
```

Install for the current Linux user:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/nervos-talk-forum-ingest.service ~/.config/systemd/user/
cp deploy/systemd/nervos-talk-forum-ingest.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nervos-talk-forum-ingest.timer
```

Edit these service variables before installing if your checkout or mamba path differs:

```ini
Environment=PROJECT_ROOT=%h/path/to/Nervos-Brain
Environment=MAMBA_BIN=%h/miniforge3/bin/mamba
Environment=MAMBA_ENV=nervos-brain
Environment=TALK_LATEST_PAGES=3
```

Check status and logs:

```bash
systemctl --user status nervos-talk-forum-ingest.timer
systemctl --user list-timers nervos-talk-forum-ingest.timer
journalctl --user -u nervos-talk-forum-ingest.service -n 100
```


## GitHub docs/code incremental ingest

The GitHub docs/code corpora can be refreshed weekly. Runtime state stays local under `data/ingest_state/` because it is a machine-specific cursor. Public manifests are written under `data/manifests/` as commit/version evidence for the published corpus.

Default command:

```bash
mamba run -n nervos-brain python scripts/run_github_docs_ingest.py --incremental
mamba run -n nervos-brain python scripts/run_github_code_ingest.py --incremental
```

Install the user timer:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/nervos-github-ingest.service ~/.config/systemd/user/
cp deploy/systemd/nervos-github-ingest.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nervos-github-ingest.timer
```

Optional GitHub token file to reduce rate limits:

```bash
mkdir -p ~/.config/nervos-brain
cat > ~/.config/nervos-brain/github-ingest.env <<'ENV'
GITHUB_TOKEN=<GITHUB_TOKEN>
ENV
chmod 600 ~/.config/nervos-brain/github-ingest.env
```

Check status and logs:

```bash
systemctl --user status nervos-github-ingest.timer
systemctl --user list-timers nervos-github-ingest.timer
journalctl --user -u nervos-github-ingest.service -n 100
```
