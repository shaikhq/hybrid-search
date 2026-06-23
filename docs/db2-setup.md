# Installing and Setting Up IBM Db2 12.1

A minimal, beginner-friendly guide to installing Db2 12.1 on Linux, creating an
instance, and verifying it works with a sample database and a test query.

## Setup workflow at a glance

![Db2 installation workflow: create db2inst1 user, install Db2, create instance, start & verify, create sample database, run a test query](Db2%20installation%20steps.jpg)

The commands behind each step follow.

## 1. Install Db2 and start the instance

**Goal:** a running Db2 engine owned by a dedicated `db2inst1` user.
**You need:** an account with `sudo` and the Db2 install tarball. **Time:** ~15 min.

Db2 runs under its own user (`db2inst1`), not root. In the steps below, `$`
is the prompt of the sudo-capable account; once you switch users it becomes
`db2inst1$`. Watch the prompt so you always know which user you are.

**Step 1 — Install the prerequisite package.** Db2's prereq check needs the
legacy `libcrypt.so.1` library; install it first so the installer doesn't abort.

```bash
$ sudo dnf install -y libxcrypt-compat
```

`libxcrypt-compat` provides `libcrypt.so.1`, which RHEL 10 no longer ships in the
default `glibc` — this clears the hard prereq error (`DBT3507E`).

> Db2's check may also warn about 32-bit libraries (`libstdc++.i686`, `pam.i686`).
> These are **warnings only** — they cover 32-bit non-SQL routines you almost
> certainly won't use — and RHEL 10 dropped 32-bit (i686) packages, so they
> aren't installable anyway. Safe to ignore. If the installer still refuses to
> proceed over them, append `-f sysreq` to the install command in Step 3 to skip
> the remaining warning-level checks.

**Step 2 — Create the `db2inst1` user.**

```bash
$ sudo groupadd db2iadm1
$ sudo useradd -m -d /home/db2inst1 -g db2iadm1 -s /bin/bash db2inst1
$ sudo passwd db2inst1                 # set a password
```

**Step 3 — Unpack the tarball and install.** Binaries go to `/opt/ibm/db2/V12.1`.

```bash
$ cd ~ && tar xzf db2vnext_aese_linux64_june4.tar.gz
$ sudo ./server/db2_install -b /opt/ibm/db2/V12.1 -p SERVER -y -n
```

**Step 4 — Create the instance**, owned by `db2inst1`.

```bash
$ sudo /opt/ibm/db2/V12.1/instance/db2icrt -s ese -a SERVER -p 50000 -u db2inst1 -nosharedgroup db2inst1
```

**Step 5 — Switch to `db2inst1`, then start the engine.**

```bash
$ sudo su - db2inst1                    # the prompt changes to db2inst1$
db2inst1$ db2set DB2COMM=TCPIP          # allow TCP/IP connections
db2inst1$ db2start
db2inst1$ db2level                      # success looks like: DB2 v12.1.x.x
```

## 2. Create a sample database and run a test query

Run these as `db2inst1` (the prompt from Step 5). `db2sampl` builds a ready-made
database called `SAMPLE`, full of example tables and data.

```bash
db2inst1$ db2sampl
db2inst1$ db2 connect to sample
db2inst1$ db2 "SELECT * FROM employee FETCH FIRST 5 ROWS ONLY"   # returns 5 rows
```

If five employee rows come back, Db2 is installed and working.
