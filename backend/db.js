import Database from "better-sqlite3";
const db = new Database("auth.db");

// USERS TABLE
db.prepare(`
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  refreshToken TEXT,
  createdAt DATETIME DEFAULT CURRENT_TIMESTAMP
)
`).run();

// OTP TABLE
db.prepare(`
CREATE TABLE IF NOT EXISTS otp (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  otp TEXT NOT NULL,
  expiresAt INTEGER NOT NULL,
  attempts INTEGER DEFAULT 0
)
`).run();

// Add indexes for faster queries
db.prepare("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)").run();
db.prepare("CREATE INDEX IF NOT EXISTS idx_otp_email ON otp(email)").run();

export default db;