import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('setup_database', Path(__file__).parents[1] / 'configure-workstation-database.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class DatabaseSetupTests(unittest.TestCase):
    def test_migrations_are_transactional_and_checksum_guarded(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / '001_example.sql'
            file.write_text('CREATE TABLE example(id INT);')
            sql = setup.migration_sql([file])
            self.assertTrue(sql.startswith('BEGIN;'))
            self.assertTrue(sql.endswith('COMMIT;'))
            self.assertIn('pg_advisory_xact_lock', sql)
            self.assertIn('Applied migration checksum changed', sql)
            self.assertIn('IF NOT EXISTS', sql)
            self.assertIn('END;\n$migration_', sql)

    def test_application_connection_is_explicit_and_secret_is_not_in_arguments(self):
        from subprocess import CompletedProcess
        with patch.object(setup.subprocess, 'run', return_value=CompletedProcess([], 0, 'ok\n', '')) as run:
            self.assertEqual(setup.invoke('SELECT 1', 'private_password'), 'ok')
            args = run.call_args.args[0]
            self.assertNotIn('private_password', ' '.join(args))
            self.assertIn('5433', args)
            self.assertIn('127.0.0.1', args)
            self.assertEqual(run.call_args.kwargs['env']['PGPASSWORD'], 'private_password')

    def test_postgres_errors_do_not_disclose_sql(self):
        from subprocess import CompletedProcess
        with patch.object(setup.subprocess, 'run', return_value=CompletedProcess([], 1, '', 'PASSWORD secret_value')):
            with self.assertRaises(RuntimeError) as error:
                setup.invoke("CREATE ROLE x PASSWORD 'secret_value'")
            self.assertNotIn('secret_value', str(error.exception))

    def test_wrong_cluster_stops_before_credentials_or_mutation(self):
        with patch.object(setup.os, 'geteuid', return_value=0), patch.object(Path, 'is_file', return_value=True), patch.object(setup, 'invoke', return_value='14\n/var/lib/postgresql/14/main') as invoke:
            with self.assertRaises(SystemExit):
                setup.main()
            self.assertEqual(invoke.call_count, 1)

    def test_existing_role_without_credentials_is_not_reset(self):
        with patch.object(setup.os, 'geteuid', return_value=0), patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'exists', return_value=False), patch.object(setup, 'invoke', side_effect=['18\n'+setup.DATA_DIRECTORY, '1', setup.ROLE]) as invoke:
            with self.assertRaises(SystemExit):
                setup.main()
            self.assertEqual(invoke.call_count, 3)

    def test_foreign_database_owner_is_not_changed(self):
        with patch.object(setup.os, 'geteuid', return_value=0), patch.object(Path, 'is_file', return_value=True), patch.object(setup, 'invoke', side_effect=['18\n'+setup.DATA_DIRECTORY, '1', 'other_project']) as invoke:
            with self.assertRaises(SystemExit):
                setup.main()
            self.assertEqual(invoke.call_count, 3)


if __name__ == '__main__':
    unittest.main()
