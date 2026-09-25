// Package violatinggopostgres intentionally violates Z4f for its positive control.
package violatinggopostgres

import "database/sql"

var _ *sql.DB
