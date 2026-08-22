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

### When step 4 says "no-op"

Some updates change the code without changing the database. Step 4 still runs —
it is what proves the database is where the new code expects it — and it may
report that it applied a migration that does nothing. That is normal and is not
an error.

The **Developer role** update (August 2026) is one of these. It adds a fourth
role for somebody who administers the system without treating patients, so that
they stop appearing in the practitioner list on the visit form and the
appointment screen. Its migration **only widens a list of permitted values and
alters no data** — no patient record, visit, bill or team member is touched by
it, and nothing needs correcting afterwards. Detail in
`docs/adr/0019-read-clinical-and-may-be-booked-are-two-facts.md`.

After deploying it, set your own role on **Team → your own row**. The dropdown
on your own row offers only the roles that can still administer the clinic —
that is deliberate, so that nobody can accidentally leave the clinic with no
administrator.

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

## Setting up a new clinic

The server is running but there is no clinic in it yet. Three commands, in this
order. Run them from `/opt/clinicore`.

```bash
# 1. The clinic itself: the organization, one branch, one administrator.
#    Nothing else — no patients, no medicines, no demo anything.
docker compose -f docker-compose.prod.yml exec web \
  python manage.py bootstrap_clinic \
  --name "Karim Homeo Hall" \
  --timezone "Asia/Dhaka" \
  --branch "Main Chamber" \
  --admin-phone 01712345678 \
  --admin-name "Dr Ayesha Karim"
```

All five are required — the command invents nothing about the clinic, so there
is no default to be quietly wrong about.

It prints a temporary password. **Write it down before you close the window —
it is not stored anywhere and cannot be shown again.** Read it out to the
administrator; the application forces them to change it the first time they
sign in.

Get the time zone right at this step. It decides which day a visit saved late at
night belongs to, and correcting it afterwards does not correct the visits
already filed under the wrong date.

The command prints the clinic's **slug** (`karim-homeo-hall` above). The next
command needs it.

```bash
# 2. The clinic's own medicine list.
docker compose -f docker-compose.prod.yml exec web \
  python manage.py import_remedies karim-homeo-hall
```

It reports how many it created and how many it skipped. Running it twice is
safe: the second run creates nothing and skips everything. To load a different
list, put the file somewhere the container can read and add
`--file /path/to/list.txt` — one medicine per entry, separated by commas or
newlines.

```bash
# 3. Take a backup, so there is a restore point before any patient data exists.
sudo /opt/clinicore/deploy/backup.sh
```

Then sign in as the administrator and finish the setup on screen:

- **Settings → Features** — turn on *Record how strong each medicine is* if this
  clinic prescribes potencies, set what it calls the field ("Potency"), and list
  the usual values one per line.
- **Settings → Billing** — the currency and the consultation fee.
- **Team** — add the receptionist and any other practitioners. Each gets a
  temporary password the same way, read out and changed on first sign-in.

### If a clinic ends up with medicines it does not want

Nothing can delete a medicine once it has been prescribed, billed or stocked —
prescriptions, bills and stock movements point at it, so the delete either
refuses or leaves records pointing at nothing. There is no `--replace` on the
import for the same reason. The fix is to deactivate each unwanted medicine
(**Medicines → Deactivate**), which takes it out of every search box and leaves
the history that mentions it readable.

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

## Loading the clinic's existing patient list

The clinic sends a CSV of the patients it already has. This is real patient
data: it goes onto the server, into the application, and then off the server
again. Work through the steps in order and do not skip step 1 or step 7.

The file must have a header line naming the columns, then one patient per line:

```
full_name,date_of_birth,sex,phone
Rahima Begum,1981-04-17,Female,01712345678
```

`docs/sample-patient-import.csv` in the repository is a working example with
invented names — send it to whoever is preparing the file. What the four columns
accept:

| column | accepts | if it is blank |
|---|---|---|
| `full_name` | any text; a name with a comma must be in "quotes" | the row is not imported |
| `date_of_birth` | `YYYY-MM-DD` only, e.g. `1981-04-17` | fine — left unknown |
| `sex` | `Male`, `Female`, `Other`, or `M`, `F`, `O` | fine — recorded as unknown |
| `phone` | anything the clinic writes | fine — left empty |

Extra columns the clinic added — an address, a note — are ignored, and the
command says which it ignored. A **missing** column is an error.

### Step 1 — take a backup first

```bash
sudo /opt/clinicore/deploy/backup.sh
```

**Do not skip this.** The import has no undo. If it puts several hundred wrong
patients into the clinic, the only way back is restoring the backup you took
here.

### Step 2 — put the file on the server

```bash
# From your own computer:
scp patients.csv you@the-server:/tmp/patients.csv
```

### Step 3 — put the file inside the application container

The application cannot see `/tmp` on the server — it runs in its own container
with its own filesystem. Copy it in:

```bash
cd /opt/clinicore
docker compose -f docker-compose.prod.yml cp /tmp/patients.csv web:/tmp/patients.csv
```

### Step 4 — dry run, and read it

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py import_patients karim-homeo-hall \
  --file /tmp/patients.csv --dry-run
```

This writes nothing at all. Read the whole output before going on:

- **The clinic name at the top must be the right clinic.** This is the moment a
  mistyped name is free to fix.
- **Would be created** is how many new patients you are about to add. Check it
  against how many the clinic says it sent.
- **Failed** lists a row number and a reason for each row that cannot be
  imported. Row numbers are the line numbers Excel shows, so the clinic can find
  and fix them. Send the list back, get a corrected file, start again at step 2.
- **Sex unrecognised** lists rows where the sex column held something the
  application did not understand. Those patients *are* imported, with the sex
  left as unknown, and can be corrected on the patient screen afterwards.

### Step 5 — run it

The same command without `--dry-run`:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py import_patients karim-homeo-hall \
  --file /tmp/patients.csv
```

Do it when nobody is using the application. While it runs, nobody can register a
new patient — it holds the counter that hands out patient codes. It takes
seconds.

If the clinic has more than one chamber the command will stop and ask which one
these patients belong to. Add `--branch` and the chamber's code, which it lists
for you.

### Step 6 — check in the application

Sign in and open **Patients**. The count should have gone up by the number the
dry run said. Open two or three records and check the name, date of birth and
phone against the clinic's own list.

### Step 7 — destroy both copies of the file

```bash
docker compose -f docker-compose.prod.yml exec web rm /tmp/patients.csv
shred -u /tmp/patients.csv
```

**There is a third copy** — in whatever email or WhatsApp message the clinic sent
it by. Delete that too, on your own machine and in the sent folder.

### If you have to run it a second time

Running it again with the same file is safe: it recognises the patients it
already imported and creates nothing. The dry run will say **would be created:
0**. If it says anything else, the file has changed since last time — find out
what changed before running it.

The one thing it cannot see is a **corrected spelling**. If someone fixed
`Rahima Begum` to `Rahima Begam` and you re-run, that is a new patient as far as
the application is concerned, and you will have both. When the clinic sends a
corrected file, ask which rows changed.

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
