#!/usr/bin/python3
"""Installed, root-owned preview controller. Application code runs unprivileged.

No imports from slot checkouts. Fixed paths and validated configuration bound all
privileged operations to preview resources. See README.md for bootstrap/trust model.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.request import urlopen

INSTALL = Path("/opt/anylist-preview")
ROOT = Path("/srv/anylist-preview")
ETC = Path("/etc/anylist-preview")
STATE = Path("/var/lib/anylist-preview")
REPOSITORY = "https://github.com/antipixelhd/AnyList.git"
OPERATIONS = {"status", "restart", "redeploy", "reset-db-from-beta", "recreate-preview", "provision", "sync-config"}
DEPENDENCIES = {"backend": ["backend/pyproject.toml", "backend/uv.lock"],
                "frontend": ["frontend/package.json", "frontend/package-lock.json"]}
BACKUP_KEEP = 20


class Refused(RuntimeError):
    pass


def load_slots(path=INSTALL / "slots.json"):
    slots = json.loads(path.read_text())
    databases, ports = set(), set()
    for name, slot in slots.items():
        if name != "beta" and not re.fullmatch(r"development-[1-9][0-9]*", name):
            raise Refused("Invalid configured branch")
        if slot["persistent"] is not (name == "beta"):
            raise Refused("Only beta may be persistent; beta must be persistent")
        expected = "anylist_preview_" + name.replace("-", "_")
        if slot["database"] != expected or expected in databases:
            raise Refused("Invalid preview database mapping")
        databases.add(expected)
        for field in ("frontend_port", "backend_port"):
            port = slot[field]
            if type(port) is not int or not 1024 <= port <= 65535 or port in ports:
                raise Refused("Invalid or duplicate port")
            ports.add(port)
    if "beta" not in slots:
        raise Refused("Missing beta")
    return slots


def validate(slots, branch, operation, sha=None, forced="false"):
    if branch not in slots:
        raise Refused("Unknown preview branch")
    if operation not in OPERATIONS | {"deploy"}:
        raise Refused("Unknown operation")
    if operation == "reset-db-from-beta" and slots[branch]["persistent"]:
        raise Refused("REFUSED: beta database reset is forbidden")
    if operation == "sync-config" and branch != "beta":
        raise Refused("Configuration additions must come from beta")
    if operation == "deploy" and not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        raise Refused("An exact 40-character commit SHA is required")
    if forced not in {"true", "false"}:
        raise Refused("Invalid forced-push flag")


def reset_required(persistent, forced, non_fast_forward, compatible, recreate=False):
    return not persistent and (forced or non_fast_forward or not compatible or recreate)


def dependency_digest(checkout, component):
    digest = hashlib.sha256()
    for name in DEPENDENCIES[component]:
        digest.update(name.encode())
        digest.update((checkout / name).read_bytes())
    return digest.hexdigest()


def run(args, *, capture=False, check=True, **kwargs):
    result = subprocess.run([str(arg) for arg in args], check=check, text=True,
                            stdout=subprocess.PIPE if capture else None, **kwargs)
    return result.stdout.strip() if capture else result.returncode


@contextlib.contextmanager
def lock(name):
    import fcntl
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / (name + ".lock")).open("a") as stream:
        print(f"Waiting for {name} lock", flush=True)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


class Preview:
    def __init__(self, branch, slots):
        self.branch, self.slots = branch, slots
        self.slot = slots[branch]
        self.user = "anylist-" + branch
        self.role = self.slot["database"]
        self.home = ROOT / branch
        self.checkout = self.home / "checkout"
        self.state = STATE / branch
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)

    def env(self):
        values = {}
        # Generated files use simple KEY=value, no shell evaluation/interpolation.
        for line in (ETC / (self.branch + ".env")).read_text().splitlines():
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key] = value
        return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.home),
                "UV_PROJECT_ENVIRONMENT": str(self.home / ".venv"),
                "UV_PYTHON": "3.13", "GIT_TERMINAL_PROMPT": "0", **values}

    def app(self, args, *, directory="backend", capture=False, check=True, env=None, **kwargs):
        # root never executes branch-controlled packages/scripts or sources .env.
        return run(["/usr/sbin/runuser", "-u", self.user, "--", *args],
                   cwd=self.checkout / directory, env=env or self.env(),
                   capture=capture, check=check, **kwargs)

    def git(self, *args, capture=True, check=True):
        return self.app(["git", "-c", "core.hooksPath=/dev/null", *args],
                        directory=".", capture=capture, check=check,
                        env={"PATH": "/usr/bin:/bin", "HOME": str(self.home),
                             "GIT_TERMINAL_PROMPT": "0"})

    def fetch(self):
        self.git("fetch", "--no-tags", REPOSITORY,
                 f"+refs/heads/{self.branch}:refs/remotes/origin/{self.branch}")
        return self.git("rev-parse", f"refs/remotes/origin/{self.branch}")

    def services(self, action):
        return run(["systemctl", action, *[f"anylist-preview-{self.branch}-{part}.service"
                                          for part in ("backend", "frontend")]])

    def http_health(self, part):
        port, path = (self.slot["backend_port"], "/health") if part == "backend" else (self.slot["frontend_port"], "/login")
        try:
            with urlopen(f"http://127.0.0.1:{port}{path}", timeout=4) as response:
                return response.status == 200
        except Exception:
            return False

    def healthy(self, attempts=45):
        for _ in range(attempts):
            if self.http_health("backend") and self.http_health("frontend"):
                return True
            if attempts > 1:
                time.sleep(2)
        return False

    def restart(self):
        self.services("restart")
        if not self.healthy():
            raise Refused("Health check failed; inspect Preview Operations status / service logs")

    def dependencies(self, force=False):
        for component in DEPENDENCIES:
            stamp = self.state / (component + ".dependencies")
            digest = dependency_digest(self.checkout, component)
            if component == "backend":
                probe = [self.home / ".venv/bin/python", "-c",
                         "import fastapi, uvicorn, alembic, asyncpg"]
            else:
                probe = ["node", "--input-type=module", "-e", "import('astro')"]
            broken = self.app(probe, directory=component, check=False) != 0
            if force or broken or not stamp.exists() or stamp.read_text() != digest:
                print(f"Synchronizing {component} dependencies", flush=True)
                command = (["uv", "sync", "--frozen", "--no-dev"] if component == "backend"
                           else ["npm", "ci"])
                self.app(command, directory=component)
                stamp.write_text(digest)

    def migration_state(self):
        # Root-owned helper, imported app modules run only with slot privileges.
        output = self.app([self.home / ".venv/bin/python", "-c",
                           "import sys; sys.path.insert(0, '.'); "
                           "exec(open('/opt/anylist-preview/migration_state.py').read())"],
                          capture=True)
        return json.loads(output.splitlines()[-1])

    def migrate(self):
        state = self.migration_state()
        print("Migration state: " + json.dumps(state), flush=True)
        if not state["compatible"]:
            raise Refused("Database revision absent from branch graph; resolve migrations in code")
        # heads handles valid parallel heads and diamonds; no automatic merge/downgrade.
        self.app([self.home / ".venv/bin/python", "-m", "alembic", "upgrade", "heads"])

    def snapshot(self, permanent=False):
        folder = STATE / ("backups" if permanent else "snapshots")
        folder.mkdir(mode=0o700, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="beta-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"),
                                    suffix=".dump", dir=folder)
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                subprocess.run(["/usr/sbin/runuser", "-u", "postgres", "--", "pg_dump", "-Fc",
                                "--no-owner", "--no-acl", self.slots["beta"]["database"]],
                               stdout=stream, check=True)
            print(f"Beta snapshot: {path}", flush=True)
            if permanent:
                # Only completed, controller-owned snapshots in the private folder.
                backups = sorted(folder.glob("beta-*.dump"),
                                 key=lambda item: item.stat().st_mtime_ns, reverse=True)
                for old in backups[BACKUP_KEEP:]:
                    if old != path and old.is_file() and not old.is_symlink():
                        old.unlink()
            return path
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def reset_database(self):
        if self.slot["persistent"] or self.branch == "beta":
            raise Refused("REFUSED: beta database reset is forbidden")
        # Same lock as beta forward migrations. pg_dump is a consistent live snapshot.
        with lock("beta-database"):
            snapshot = self.snapshot()
        try:
            self.services("stop")
            run(["/usr/sbin/runuser", "-u", "postgres", "--", "dropdb", "--if-exists", "--force", self.role])
            run(["/usr/sbin/runuser", "-u", "postgres", "--", "createdb", "--owner", self.role, self.role])
            run(["/usr/sbin/runuser", "-u", "postgres", "--", "psql", "-v", "ON_ERROR_STOP=1"],
                input=f"REVOKE ALL ON DATABASE {self.role} FROM PUBLIC;\n")
            # Restore as postgres SET ROLE target: all restored objects belong to target.
            with snapshot.open("rb") as stream:
                run(["/usr/sbin/runuser", "-u", "postgres", "--", "pg_restore", "--exit-on-error",
                     "--no-owner", "--no-acl", "--role", self.role, "--dbname", self.role], stdin=stream)
            self.migrate()
        finally:
            snapshot.unlink(missing_ok=True)

    def deploy(self, sha=None, forced=False, recreate=False):
        remote = self.fetch()
        sha = sha or remote
        if remote != sha:
            print(f"SUPERSEDED: requested {sha}; remote {self.branch} is {remote}", flush=True)
            return "superseded"
        marker = self.state / "deployed.sha"
        old = marker.read_text().strip() if marker.exists() else None
        old_exists = bool(old and self.git("cat-file", "-e", old + "^{commit}",
                                          capture=False, check=False) == 0)
        non_ff = bool(old and self.git("merge-base", "--is-ancestor", old, sha,
                                      capture=False, check=False) != 0)
        changed_migrations = not old_exists or bool(self.git("diff", "--name-only", old, sha,
                                                         "--", "backend/migrations", "backend/alembic.ini"))
        self.services("stop")
        self.git("checkout", "-B", self.branch, sha)
        self.git("reset", "--hard", sha)
        # Remove deleted source left untracked without touching venv/node_modules/data.
        self.git("clean", "-fd")
        self.dependencies(force=recreate)
        if self.slot["persistent"]:
            with lock("beta-database"):
                state = self.migration_state()
                if not state["compatible"]:
                    raise Refused("Beta migration state incompatible; database preserved, no recovery attempted")
                if changed_migrations or state["pending"]:
                    self.snapshot(permanent=True)
                self.migrate()
        else:
            state = self.migration_state()
            if reset_required(False, forced, non_ff, state["compatible"], recreate or old is None):
                print("Rebuilding disposable development DB from beta", flush=True)
                self.reset_database()
            else:
                self.migrate()
        self.restart()
        marker.write_text(sha + "\n")
        print(f"DEPLOYED {self.branch} {sha}; forced={forced}; non-fast-forward={non_ff}", flush=True)
        return "deployed"

    def status(self):
        failed = False
        def report(label, callback):
            nonlocal failed
            try:
                print(f"{label}: {callback()}", flush=True)
            except Exception as error:
                failed = True
                print(f"{label}: ERROR {error}", flush=True)
        report("branch", lambda: self.git("branch", "--show-current"))
        report("checkout SHA", lambda: self.git("rev-parse", "HEAD"))
        report("remote SHA", self.fetch)
        for part in ("frontend", "backend"):
            unit = f"anylist-preview-{self.branch}-{part}.service"
            report(part + " service", lambda unit=unit: run(["systemctl", "show", unit,
                   "--property=ActiveState,SubState,Result", "--no-pager"], capture=True))
            report(part + " recent logs", lambda unit=unit: run(["journalctl", "-u", unit,
                   "-n", "30", "--no-pager"], capture=True))
        report("DB connectivity / Alembic", self.migration_state)
        report("preview URL", lambda: self.env()["SERVER_URL"])
        health = True
        for part in ("frontend", "backend"):
            good = self.http_health(part)
            print(f"{part} health: {good}", flush=True)
            health = health and good
        return not failed and health

    def sync_config(self):
        self.fetch()
        candidate = self.git("show", "refs/remotes/origin/beta:ops/preview/slots.json")
        temporary = STATE / "slots.candidate.json"
        try:
            temporary.write_text(candidate)
            new_slots = load_slots(temporary)
            for name, slot in self.slots.items():
                if new_slots.get(name) != slot:
                    raise Refused("Configuration sync may only add slots; existing mappings are immutable")
            temporary.replace(INSTALL / "slots.json")
            (INSTALL / "slots.json").chmod(0o644)
            print("Installed validated additive slot mapping from beta", flush=True)
        finally:
            temporary.unlink(missing_ok=True)

    def provision(self, recreate=False):
        host = (ETC / "hostname").read_text().strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.ts\.net", host):
            raise Refused("Configure a valid Tailscale machine FQDN first")
        if run(["id", "-u", self.user], check=False, capture=False) != 0:
            run(["useradd", "--system", "--user-group", "--create-home", "--home-dir", self.home,
                 "--shell", "/usr/sbin/nologin", self.user])
        environment = ETC / (self.branch + ".env")
        if not environment.exists():
            password = secrets.token_hex(32)
            # Never print credentials or put them in process arguments.
            sql = f"CREATE ROLE {self.role} LOGIN PASSWORD '{password}';"
            run(["/usr/sbin/runuser", "-u", "postgres", "--", "psql", "-v", "ON_ERROR_STOP=1"], input=sql)
            run(["/usr/sbin/runuser", "-u", "postgres", "--", "createdb", "--owner", self.role, self.role])
            run(["/usr/sbin/runuser", "-u", "postgres", "--", "psql", "-v", "ON_ERROR_STOP=1"],
                input=f"REVOKE ALL ON DATABASE {self.role} FROM PUBLIC;\n")
            values = {
                "SECRET_KEY": secrets.token_hex(32),
                "DATABASE_URL": f"postgresql+asyncpg://{self.role}:{password}@127.0.0.1:5432/{self.role}",
                "SERVER_URL": f"https://{host}:{self.slot['frontend_port']}",
                "PREVIEW_HOSTNAME": host, "PREVIEW_SLOT": self.branch,
                "BACKEND_PORT": str(self.slot["backend_port"]),
                "OIDC_REDIRECT_URL": f"https://{host}:{self.slot['frontend_port']}/oidc-callback",
                "DATA_DIR": str(self.home / "data"),
            }
            environment.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
            import grp
            os.chown(environment, 0, grp.getgrnam(self.user).gr_gid)
            environment.chmod(0o640)
        if recreate and self.checkout.exists():
            self.services("stop")
            # Rename only as the runtime user. Preserve data/env/DB and retain old
            # runtime files for diagnosis; no root traversal of branch-owned trees.
            retired = "retired-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            code = ("import os, pathlib; home=pathlib.Path(os.environ['HOME']); "
                    f"retired=home/'{retired}'; retired.mkdir(); "
                    "[(home/name).rename(retired/name) for name in ('checkout', '.venv') "
                    "if (home/name).exists()]")
            run(["/usr/sbin/runuser", "-u", self.user, "--", "python3", "-c", code], env=self.env())
        if not self.checkout.exists():
            run(["/usr/sbin/runuser", "-u", self.user, "--", "git", "clone", "--no-checkout", REPOSITORY,
                 self.checkout], env={"PATH": "/usr/bin:/bin", "HOME": str(self.home)})
        run(["/usr/sbin/runuser", "-u", self.user, "--", "mkdir", "-p", self.home / "data"])
        for part in ("frontend", "backend"):
            template = (INSTALL / (part + ".service.in")).read_text()
            substitutions = {"@BRANCH@": self.branch, "@USER@": self.user,
                             "@FRONTEND_PORT@": str(self.slot["frontend_port"]),
                             "@BACKEND_PORT@": str(self.slot["backend_port"])}
            for key, value in substitutions.items():
                template = template.replace(key, value)
            (Path("/etc/systemd/system") / f"anylist-preview-{self.branch}-{part}.service").write_text(template)
        run(["systemctl", "daemon-reload"])
        self.deploy(recreate=recreate)
        run(["tailscale", "serve", "--bg", f"--https={self.slot['frontend_port']}",
             f"http://127.0.0.1:{self.slot['frontend_port']}"])
        self.services("enable")


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if len(args) not in (2, 4):
        raise Refused("Usage: controller operation branch [exact-sha forced]")
    operation, branch = args[:2]
    sha, forced = args[2:] if len(args) == 4 else (None, "false")
    slots = load_slots()
    validate(slots, branch, operation, sha, forced)
    if operation != "deploy" and len(args) != 2:
        raise Refused("Unexpected arguments")
    if os.geteuid() != 0:
        raise Refused("Use installed restricted CI entrypoint")
    os.umask(0o077)
    with lock(branch):
        preview = Preview(branch, slots)
        if operation == "status":
            if not preview.status():
                raise Refused("Status detected errors")
        elif operation == "restart":
            preview.restart()
        elif operation == "reset-db-from-beta":
            preview.reset_database()
            preview.restart()
        elif operation == "provision":
            preview.provision()
        elif operation == "sync-config":
            preview.sync_config()
        elif operation == "recreate-preview":
            preview.provision(recreate=True)
        else:
            preview.deploy(sha, forced == "true")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FAILED SAFELY: {error}. No beta restore/drop/downgrade attempted.", file=sys.stderr)
        sys.exit(1)
