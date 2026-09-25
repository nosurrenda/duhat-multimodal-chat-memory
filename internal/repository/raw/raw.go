// Package raw contains persistence statements only. Authorization belongs to repository.
package raw

// Statement is a database operation described without importing a driver.
// scope supplies the PostgreSQL executor and owns all transaction boundaries.
type Statement struct {
	SQL  string
	Args []any
}
