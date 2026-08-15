# Clinicore runbook

**For whoever is looking after the server.** No programming knowledge assumed.
Every command can be copied exactly as written.

You are probably reading this at night with the clinic waiting. Start at
[Is it running?](#is-it-running) and work down. Nothing in this file can lose
data except the section marked **DESTRUCTIVE**.

First, two facts that save time:

- The clinic's records are in a database on this server, and the photographs are
  in a separate store beside it. A backup of both runs every night at 02:15.
- **You cannot break anything by looking.** Reading status, reading logs and
  restarting are all safe. Only restoring a backup destroys anything.

Log in to the server with:

```bash
ssh clinicore@<the server address>
cd /opt/clinicore
```

---

## Is it running?

```bash
/opt/clinicore/deploy/status.sh
```

That prints five things: the containers, whether the app answers, when the last
backup succeeded, when a backup was last test-restored, and free disk.

**What good looks like:**

```
  CONTAINERS
    db: Up 6 days (healthy)
    web: Up 6 days (healthy)

  THE APP ANSWERS?
    yes — http://127.0.0.1:8000/healthz returned OK
```

Both containers `Up` and `(healthy)`, and the app answering. Anything else, keep
reading.

---

## Restart it

Safe. Takes about thirty seconds. Patients being entered right now may need to
press Save again, so if the clinic is busy, tell them first.

```bash
cd /opt/clinicore
docker compose -f docker-compose.prod.yml restart
```

Wait a minute, then run `deploy/status.sh` again.

If that does not fix it, a fuller stop and start:

```bash
cd /opt/clinicore
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d
```

`down` sounds frightening and is not. **It does not delete any data.** It stops
the containers; the records and photographs live in Docker volumes that survive.
(The one command that *would* delete them is `down -v`. Never type `-v`.)

---

## The site will not load

Work through these in order. Stop as soon as it works.

### 1. Is it the server or the internet?

On your phone, turn Wi-Fi off and try the site on mobile data. If it loads, the
clinic's internet or router is the problem, not this server. Restart the router.

### 2. Is the server switched on?

```bash
ssh clinicore@<the server address>
```

If that does not connect, the server is off or has no network. After a power
cut, check it powered back on — some machines need the power button pressed.

### 3. Look at the status

```bash
/opt/clinicore/deploy/status.sh
```

| What you see | What it means | What to do |
|---|---|---|
| Both `(healthy)`, app answers | The app is fine | The problem is the network or the browser. Try another device. |
| A container is `Restarting` | It is crashing on startup | Go to step 4 |
| A container is `(unhealthy)` | Running but not working | Go to step 4 |
| A container is missing or `Exited` | It stopped | `docker compose -f docker-compose.prod.yml up -d` |
| `NO — the app is not answering` | Web is down | Restart it (above), then step 4 |

### 4. Read the last few lines of the log

```bash
cd /opt/clinicore
docker compose -f docker-compose.prod.yml logs --tail=50 web
docker compose -f docker-compose.prod.yml logs --tail=50 db
```

You are looking for the last few lines before it stopped working. You do not
need to understand them — **copy them and send them on.** Two you can act on:

- `no space left on device` → see [The disk is full](#the-disk-is-full).
- `password authentication failed` → the `.env` file has been changed. Call for
  help; do not edit it.

### 5. Restart, then wait two minutes

```bash
cd /opt/clinicore
docker compose -f docker-compose.prod.yml restart
```

There is also a watchdog that checks every two minutes and restarts anything
Docker has marked unhealthy, so a transient fault often clears itself. To see
whether it has been doing that:

```bash
journalctl -u clinicore-heal --since "1 day ago" | tail -20
```

Repeated restarts in that log mean something is genuinely wrong underneath.
Send that output on rather than leaving it to restart forever.

### 6. Still down after all that

Call. See [When to call](#what-not-to-do-and-when-to-call).

---

## What to tell the clinic while it is down

Say it early. A doctor who knows what is happening can keep working on paper; a
doctor who is guessing cannot.

**When you have just started looking (first five minutes):**

> "The computer system is down. I am looking at it now. Please write today's
> visits and payments on paper — nothing already saved is lost, and we will type
> them in once it is back. I will tell you in ten minutes where we stand."

**If it is a restart and it is coming back (under fifteen minutes):**

> "It is restarting. It will be back in about ten minutes. Keep taking patients
> on paper; nothing is lost."

**If you are restoring from a backup (an hour or more):**

> "The system has a fault and I am putting last night's backup back. That will
> take about an hour. Everything up to last night is safe. **Anything typed in
> today will need entering again from the paper notes** — so please keep every
> slip." 

**If you do not know yet (be honest, do not guess):**

> "I do not know the cause yet and I would rather not guess at a time. Please
> work on paper and I will come back to you within thirty minutes either way."

Three rules for these conversations:

1. **Never promise a time you are not sure of.** "I will update you in thirty
   minutes" is always safe; "it will be up in ten" is not.
2. **Say what is safe, not only what is broken.** "Nothing already saved is
   lost" is the sentence that lets the clinic carry on.
3. **Tell them to keep the paper.** After a restore, those slips are the only
   record of the gap.

---

## Backups

They run at 02:15 every night and cover **both** the database and the
photographs — a database on its own restores a clinic whose visits are all there
and whose every photograph is missing.

Check them:

```bash
/opt/clinicore/deploy/status.sh          # bottom two sections
journalctl -u clinicore-backup --since "3 days ago"
```

The administrator's dashboard in the app also shows a banner when the last
backup is more than 36 hours old, and a red one past 72 hours. **That banner is
the main way you find out**, because this server cannot send email.

### Backups stopped running

```bash
# Is the nightly job still scheduled?
systemctl list-timers clinicore-backup

# Why did the last one fail?
journalctl -u clinicore-backup -n 50

# Run one right now, by hand, and watch it
sudo /opt/clinicore/deploy/backup.sh
```

Most common causes, in order: the disk is full; the Google Drive credentials
expired (`rclone` errors in the log); the database container was down at 02:15.

### The disk is full

```bash
df -h /
du -sh /var/backups/clinicore/*
docker system df
```

Safe to delete: old Docker images, with `docker image prune -a`. **Never delete
anything under `/var/backups/clinicore` by hand** — the rotation does that, and
deleting the wrong file removes the only copy of a month.

---

## Restoring from a backup

> ### ⚠️ READ THIS FIRST
>
> **The backup private key is the single point of total data loss. If that key is
> lost, every backup ever made is permanently unreadable and the clinic's entire
> history is gone.** No password can be reset, no service can recover it, and
> nobody — not the developer, not Google — can decrypt those files without it.
> The backups themselves are useless without it.
>
> There must be **two copies of the key from day one**, both in place *before*
> the first backup ever runs:
>
> 1. In the password manager, under **"Clinicore backup key"**.
> 2. Printed on paper, in the **clinic safe**.
>
> If you are reading this and cannot confirm both exist, stop and fix that now.
> It is more urgent than whatever brought you here.
>
> There *is* a copy on the server, at `/etc/clinicore/backup-identity.key`, used
> only by the monthly restore check — see
> [Why the private key is on the server](#why-the-private-key-is-on-the-server--a-deliberate-trade).
> **Do not rely on it.** If you are restoring, the reason is usually that this
> machine is gone or broken, and that copy went with it. Fetch the key from the
> password manager or the safe; if the server happens to still have it, that is
> luck, not the plan.

**DESTRUCTIVE.** This replaces the live records with the backup's. Anything
entered since that backup was taken is gone — which is why the clinic must be
told to keep the paper slips.

### Step 1 — get the key onto the server

Copy it out of the password manager (or type it from the printed copy) into a
file, then delete that file afterwards.

```bash
nano /tmp/clinicore-backup.key
# paste the whole key, including the AGE-SECRET-KEY-1... line
chmod 600 /tmp/clinicore-backup.key
```

**If this is a brand-new or rebuilt server**, do
[First-time setup](#first-time-setup-on-a-new-server) first, up to and including
`docker compose ... up -d`. The restore needs the stack running with an empty
database to put the records into. Check with:

```bash
cd /opt/clinicore
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps      # db must say (healthy)
```

You do **not** need to run `migrate` first. The backup carries the structure with
it, and `restore.sh` applies any later updates itself at the end.

### Step 2 — find the backup you want

```bash
ls -lh /var/backups/clinicore/daily/
```

You need **two files with the same timestamp** — one `db-`, one `media-`:

```
db-2026-08-15_021501.dump.age
media-2026-08-15_021501.tar.gz.age
```

If this server's disk is the thing that died, pull them from Google Drive
instead:

```bash
rclone copy gdrive:clinicore-backups/daily /var/backups/clinicore/daily --max-age 3d
```

### Step 3 — restore

```bash
cd /opt/clinicore
./deploy/restore.sh \
  /var/backups/clinicore/daily/db-2026-08-15_021501.dump.age \
  /var/backups/clinicore/daily/media-2026-08-15_021501.tar.gz.age \
  /tmp/clinicore-backup.key
```

It shows what it is about to replace and waits. Type `RESTORE` to go ahead, or
anything else to cancel. It takes a few minutes: it stops the app, puts the
records back, puts the photographs back, applies any updates the backup predates,
and starts the app again.

### Step 4 — check, then destroy the key copy

```bash
/opt/clinicore/deploy/status.sh
rm /tmp/clinicore-backup.key
```

Then open the site, sign in, and confirm a patient you expect to see is there
**and that a visit with a photograph still shows the photograph.** A restore
that brought back the records but not the pictures is a half restore, and the
photographs are the part you would not notice for weeks.

Finally, tell the clinic which day's data they need to re-enter from paper.

---

## Deploying an update

In this order. The order matters: the database must be updated before the new
code runs against it.

```bash
cd /opt/clinicore

# 1. Take a backup first, so there is a way back
sudo ./deploy/backup.sh

# 2. Get the new code
git pull

# 3. Build it
docker compose -f docker-compose.prod.yml build

# 4. Update the database structure
docker compose -f docker-compose.prod.yml run --rm web python manage.py migrate

# 5. Start the new version
docker compose -f docker-compose.prod.yml up -d

# 6. Check
./deploy/status.sh
```

**Step 4 is never skipped and never automatic.** It is deliberately not run when
the app starts: if a database change fails at three in the morning you want it to
stop and wait for a person, not leave the app restarting in a loop. If step 4
prints an error, **stop there and call** — do not run step 5.

Do updates in the morning, never on a Thursday evening, and never while the
clinic is full.

---

## First-time setup on a new server

Once, in this order.

```bash
# Docker must start itself after a power cut. Without this the clinic
# stays down until somebody logs in, which is the whole point of the
# restart policies.
sudo systemctl enable docker
sudo systemctl enable --now docker

sudo apt update && sudo apt install -y age rclone

# 1. The key — BEFORE the first backup. See the warning above.
#    Generate it on your OWN machine, never on the server:
#      age-keygen -o clinicore-backup.key
#    Put the file in the password manager AND print it for the safe.
#    Only the "Public key: age1..." line goes on the server.

sudo mkdir -p /etc/clinicore
sudo cp /opt/clinicore/deploy/clinicore.env.example /etc/clinicore/clinicore.env
sudo nano /etc/clinicore/clinicore.env        # set AGE_RECIPIENT and the paths

# 2. A copy of the PRIVATE key, for the monthly restore check only.
#    Read the note under this block before doing it — this is a deliberate
#    security trade, not a convenience.
sudo nano /etc/clinicore/backup-identity.key   # paste the whole key file
sudo chown root:root /etc/clinicore/backup-identity.key
sudo chmod 600 /etc/clinicore/backup-identity.key

# 3. Google Drive, as the user the timers run as
rclone config                                  # name the remote "gdrive"

# 4. The scheduled jobs
sudo cp /opt/clinicore/deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clinicore-backup.timer
sudo systemctl enable --now clinicore-verify-restore.timer
sudo systemctl enable --now clinicore-heal.timer
systemctl list-timers 'clinicore-*'

# 5. Prove it works, now, rather than finding out in six months
sudo /opt/clinicore/deploy/backup.sh
```

### Why the private key is on the server — a deliberate trade

Step 2 puts a copy of the backup private key on this machine, at
`/etc/clinicore/backup-identity.key`, readable only by root. **This weakens the
main reason for encrypting to a keypair: somebody who steals this server can now
decrypt the backups it made.** That is accepted, knowingly, and here is why.

The monthly restore check is the only thing that turns a backup from a guess
into a fact, and it cannot run without the key. The alternative was a person
running it by hand each month with the key on a USB stick. That works in
January, slips in February, and has stopped by March — and the failure is
invisible, because unverified backups look exactly like verified ones right up
until the night somebody needs one.

**A year of backups nobody has ever proven can be read is a larger risk than a
stolen server.** So the key stays, and the check runs on its own.

What this does *not* change:

- The off-site copies on Google Drive are still useless to anyone without the
  key, so a compromised Drive account is still not a data breach.
- The key is still not in the code repository, not in the Docker image, and not
  inside any backup.
- Losing this server loses **one copy** of the key, not the key. The password
  manager and the printed copy in the clinic safe remain the two that matter.

If the server is ever stolen, lost, or decommissioned, treat the key as exposed:
generate a new pair, put the new public key in
`/etc/clinicore/clinicore.env`, and take a fresh backup that same day. Old
backups stay readable with the old key, so keep it — do not destroy the old key
just because it was rotated.

Then do a full restore drill onto a spare machine before the clinic depends on
this. A backup nobody has restored is a guess.

### The second copy

Google Drive is one off-site copy and it is one account away from being lost.
Once a month, from a machine at home:

```bash
rclone copy gdrive:clinicore-backups/monthly ~/clinicore-backups/monthly
```

Keep that machine's copy encrypted as it arrives — the files are already
encrypted, so simply keeping them is enough. Do not decrypt them to check;
`deploy/verify-restore.sh` already proves monthly that they load.

---

## What not to do, and when to call

**Never:**

- `docker compose ... down -v` — the `-v` deletes the records and photographs.
  There is no undo.
- `docker volume rm` anything.
- Edit `.env` or `/etc/clinicore/clinicore.env` to "fix" a password. A wrong
  value here stops the app and can stop the backups silently.
- Delete files under `/var/backups/clinicore`.
- Restore a backup because the site is slow or a page looks wrong. Restoring
  throws away everything since last night. It is the last resort, not the first.
- Run a database update (`migrate`) while the clinic is working.
- Copy `/etc/clinicore/backup-identity.key` anywhere — not to your laptop, not
  to Drive, not into a chat message. It is the key to every backup the clinic
  has. It stays on the server and in the two places named above, and nowhere
  else. If it has been copied somewhere by accident, say so; rotating the pair
  is easy and staying quiet is not.

**Call, do not continue, when:**

- The log says anything about corruption, or Postgres will not start.
- A restore fails part way through — stop, change nothing, call.
- The backup key cannot be found in either place.
- The disk is full and you cannot see what is filling it.
- You have restarted twice and it is still down.
- You are about to type a command you do not understand.

**When you call, have these ready** — it turns an hour into ten minutes:

```bash
/opt/clinicore/deploy/status.sh
cd /opt/clinicore && docker compose -f docker-compose.prod.yml logs --tail=100
journalctl -u clinicore-backup -n 30
df -h /
```

Copy the output and send it with a one-line description of what the clinic sees.
