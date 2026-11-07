"""Configure only PostgreSQL 18/corei_os_mini:5433; never reset existing credentials."""
from pathlib import Path
import hashlib
import os
import secrets
import subprocess
import sys
from urllib.parse import urlsplit

ENV_FILE = Path('/etc/corei-os-mini/workstation.env')
ROLE = 'corei_os_mini_app'
DATABASE = 'corei_os_mini'
PSQL = '/usr/lib/postgresql/18/bin/psql'
DATA_DIRECTORY = '/var/lib/postgresql/18/corei_os_mini'


def invoke(sql, password=None):
    # Do not inherit another project's PGHOST/PGPORT/PGSERVICE or .pgpass settings.
    env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    env.update(PGCONNECT_TIMEOUT='5', PGPASSFILE='/dev/null')
    args = [PSQL, '-X', '-w', '-v', 'ON_ERROR_STOP=1', '-A', '-t', '-p', '5433']
    if password is None:
        args = ['sudo', '-u', 'postgres', '--', *args, '-h', '/var/run/postgresql', '-d', 'postgres']
    else:
        env['PGPASSWORD'] = password
        args += ['-h', '127.0.0.1', '-U', ROLE, '-d', DATABASE]
    result = subprocess.run(args, input=sql, text=True, capture_output=True, cwd='/tmp', env=env)
    if result.returncode:
        # SQL errors can include input lines. Never forward credential SQL or URLs.
        raise RuntimeError('PostgreSQL operation failed. Check cluster availability, ownership and credentials; existing passwords were not changed.')
    return result.stdout.strip()


def credential_url(password):
    return f'postgresql://{ROLE}:{password}@127.0.0.1:5433/{DATABASE}'


def read_password(path):
    if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
        raise RuntimeError('The workstation environment file must be root-owned, non-symlink and mode 600.')
    lines = path.read_text().splitlines()
    values = [line.split('=', 1)[1] for line in lines if line.startswith('COREI_DATABASE_URL=')]
    if len(values) != 1:
        raise RuntimeError('Expected one project database URL in the workstation environment file.')
    url = urlsplit(values[0])
    if (url.scheme != 'postgresql' or url.hostname != '127.0.0.1' or url.port != 5433
            or url.username != ROLE or url.path != '/' + DATABASE or url.query or url.fragment
            or not url.password or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in url.password)):
        raise RuntimeError('Environment file does not match this dedicated project database; no change made.')
    return url.password


def migration_sql(files):
    statements = ["BEGIN;", "SELECT pg_advisory_xact_lock(187865433);", """
CREATE TABLE IF NOT EXISTS corei_schema_migrations (
    name TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);"""]
    for file in files:
        body = file.read_text()
        checksum = hashlib.sha256(file.read_bytes()).hexdigest()
        name = file.name.replace("'", "''")
        delimiter = '$migration_' + checksum[:16] + '$'
        if delimiter in body:
            raise RuntimeError('Migration delimiter collision')
        statements.append(f"""
DO {delimiter}
BEGIN
    IF EXISTS (SELECT 1 FROM corei_schema_migrations WHERE name = '{name}' AND checksum <> '{checksum}') THEN
        RAISE EXCEPTION 'Applied migration checksum changed: {name}';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM corei_schema_migrations WHERE name = '{name}') THEN
        {body}
        INSERT INTO corei_schema_migrations(name, checksum) VALUES ('{name}', '{checksum}');
    END IF;
END;
{delimiter};""")
    statements.append('COMMIT;')
    return '\n'.join(statements)


def main():
    if os.geteuid() != 0:
        sys.exit('Run bash scripts/configure-workstation-database.sh as your normal user.')
    if not Path(PSQL).is_file():
        sys.exit('PostgreSQL 18 client is missing; no database changes made.')
    identity = invoke("SELECT current_setting('server_version_num')::int / 10000; SHOW data_directory;")
    if identity.splitlines() != ['18', DATA_DIRECTORY]:
        sys.exit('Port 5433 is not the expected PostgreSQL 18/corei_os_mini cluster; stopped.')
    role_exists = invoke(f"SELECT count(*) FROM pg_roles WHERE rolname='{ROLE}';") == '1'
    owner = invoke(f"SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='{DATABASE}';")
    if owner and owner != ROLE:
        sys.exit('Existing database has another owner; no change made.')
    if ENV_FILE.exists():
        password = read_password(ENV_FILE)
    else:
        if role_exists or owner:
            sys.exit('Existing project role/database found without its environment file. Restore its credentials; this installer will not reset them.')
        password = secrets.token_urlsafe(36)
        ENV_FILE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            output.write('COREI_DATABASE_URL=' + credential_url(password) + '\n')
        # Save credentials before creation so interrupted setup can safely resume.
    if not role_exists:
        invoke(f"CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '{password}';")
    flags = invoke(f"SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication FROM pg_roles WHERE rolname='{ROLE}';")
    if flags != 'f':
        sys.exit('Existing project role has elevated permissions; stopped without changing it.')
    if not owner:
        invoke(f'CREATE DATABASE {DATABASE} OWNER {ROLE};')
    files = sorted((Path(__file__).resolve().parents[1] / 'backend/sql').glob('[0-9][0-9][0-9]_*.sql'))
    if not files:
        sys.exit('No project migrations found.')
    invoke(migration_sql(files), password)
    # Roll back the probe: no sample trading records are left behind.
    invoke("""BEGIN;
INSERT INTO corei_paper_runs(run_id,instrument_id,strategy_id,execution_mode,request_json,result_json)
VALUES('00000000-0000-0000-0000-000000000001','INSTALL_CHECK','INSTALL_CHECK','PAPER','{}','{}') ON CONFLICT DO NOTHING;
INSERT INTO corei_market_data_assessments(assessment_id,instrument_id,evaluated_at,request_json,result_json)
VALUES('00000000-0000-0000-0000-000000000001','INSTALL_CHECK',now(),'{}','{}') ON CONFLICT DO NOTHING;
SELECT run_id FROM corei_paper_runs LIMIT 1;
SELECT assessment_id FROM corei_market_data_assessments LIMIT 1;
ROLLBACK;""", password)
    print(f'Database ready: PostgreSQL 18 / {DATABASE} / 127.0.0.1:5433')
    print(f'Checked {len(files)} migrations; application read/write probe rolled back.')
    print('Credentials stored in /etc/corei-os-mini/workstation.env (root-only).')
    print('Deploy the workstation to load its database configuration.')


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        sys.exit(str(exc))
    except (OSError, ValueError):
        sys.exit('Database setup stopped. Verify the dedicated cluster and root-owned environment file. No existing passwords were reset; do not paste credentials into chat.')
