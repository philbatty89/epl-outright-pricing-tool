# Updating the EPL Outright Pricing Tool

This guide covers two things:
- **Part A** — for testers: how to get the latest version
- **Part B** — for the maintainer: how to release a new version

---

## Part A — Getting the latest version (testers)

### How you know an update is available

When you open the app (http://localhost:8080), look at the **top-right corner**:

- **`v1.0.0`** in grey = you're up to date
- **`v1.0.0 → v1.1.0`** in green = a newer version is available
- A **green banner** also appears across the top saying a new version is available

### Step-by-step to update

1. **Stop the app** if it's running.
   - Go to the Terminal window where the app is running
   - Press `Ctrl` + `C` (hold Control, press C). The app stops.

2. **Go into the app folder** (if you're not already there):
   ```bash
   cd epl-outright-pricing-tool
   ```
   (If you cloned it somewhere specific, `cd` into that path — e.g. `cd ~/Downloads/epl-outright-pricing-tool`)

3. **Download the latest version:**
   ```bash
   git pull
   ```
   You'll see a summary of what changed. Your `config.py` (with the hostnames)
   is NOT touched — it stays exactly as you set it.

4. **Start the app again:**
   ```bash
   python3 server.py
   ```

5. **Refresh your browser** at http://localhost:8080 — the version badge should
   now show the new version in grey (up to date).

### If `git pull` gives an error

Occasionally git may complain about local changes blocking the pull. To fix it,
run these two commands (they reset the app files to the latest version — your
`config.py` is safe because it's ignored by git):

```bash
git checkout -- .
git pull
```

Then start the app again with `python3 server.py`.

---

## Part B — Releasing a new version (maintainer)

### Step-by-step to publish an update

1. **Make your changes** to the app files (`server.py`, `index.html`, etc.)

2. **Test locally** to make sure it works:
   ```bash
   python3 server.py
   ```
   Check http://localhost:8080, then stop it with `Ctrl+C`.

3. **Bump the version number.** Open the `VERSION` file and increase it:
   - Small fix → `1.0.0` becomes `1.0.1`
   - New feature → `1.0.0` becomes `1.1.0`
   - Big change → `1.0.0` becomes `2.0.0`

   You can do this in an editor, or from the terminal:
   ```bash
   echo "1.1.0" > VERSION
   ```

4. **Commit and push** the changes:
   ```bash
   git add -A
   git commit -m "Short description of what changed"
   git push
   ```

5. **Done.** Within a few minutes, every tester's app will detect the new
   version (it checks the VERSION file on GitHub on startup) and show them the
   green update banner. They then follow Part A to pull it.

### Important rules

- **Always bump `VERSION`** when you push a change testers should get — that's
  what triggers the update banner for them.
- **Never commit `config.py`** — it's gitignored on purpose (keeps internal
  hostnames out of the public repo). If you ever see it in `git status`, do not
  add it.
- Write a clear commit message — testers don't see it, but it's your changelog.

---

## Quick reference

| Task | Command |
|------|---------|
| Tester: get latest | `git pull` then restart |
| Tester: force clean update | `git checkout -- .` then `git pull` |
| Maintainer: release | edit `VERSION`, then `git add -A && git commit -m "..." && git push` |
