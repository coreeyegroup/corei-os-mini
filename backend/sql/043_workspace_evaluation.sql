-- The latest evaluated bar is independent of whether a signal or order was created.
ALTER TABLE tr_runtime_state ADD COLUMN IF NOT EXISTS last_evaluation JSONB;
