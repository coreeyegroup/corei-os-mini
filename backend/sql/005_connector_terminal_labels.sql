-- Inventory labels only. No network endpoints or credentials.
ALTER TABLE corei_connector_profiles
 ADD COLUMN terminal_host TEXT NOT NULL DEFAULT '' CHECK(length(terminal_host) <= 120),
 ADD COLUMN terminal_instance TEXT NOT NULL DEFAULT '' CHECK(length(terminal_instance) <= 120);
