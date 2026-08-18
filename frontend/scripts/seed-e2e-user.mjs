/**
 * Seeds a throwaway admin for the Playwright suite and prints the credentials
 * as shell exports. Idempotent: reuses the account if it already exists.
 *
 * Requires the backend container to be running (docker compose dev stack).
 * Skipped automatically when E2E_EMAIL/E2E_PASSWORD are already set.
 */
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";

const EMAIL = process.env.E2E_EMAIL || "e2e@stencil.test";

if (process.env.E2E_EMAIL && process.env.E2E_PASSWORD) {
  console.error("E2E credentials already set in the environment — skipping seed.");
  process.exit(0);
}

const password = process.env.E2E_PASSWORD || `E2e-${randomBytes(12).toString("base64url")}`;

const PY = `
import sys
from stencil.db.session import SessionLocal
from stencil.db.models import User
from stencil import auth

email, password = sys.argv[1], sys.argv[2]
db = SessionLocal()
user = db.query(User).filter(User.email == email).first()
if user is None:
    user = User(email=email, username="E2E", role="admin", is_active=True,
                password_hash=auth.hash_password(password))
    db.add(user)
else:
    user.password_hash = auth.hash_password(password)
    user.is_active = True
    user.deleted_at = None
    user.role = "admin"
db.commit()
print("seeded", email)
`;

const compose = [
  "compose",
  "-f",
  "../docker-compose.yml",
  "-f",
  "../docker-compose.dev.yml",
  "exec",
  "-T",
  "backend",
  "python",
  "-c",
  PY,
  EMAIL,
  password,
];

const result = spawnSync("docker", compose, { encoding: "utf8" });
if (result.status !== 0) {
  console.error("Failed to seed the E2E user. Is the dev stack up?");
  console.error(result.stderr || result.stdout);
  process.exit(1);
}

// Consumed by `npm run test:e2e` via cross-env-style shell export.
process.stdout.write(`${EMAIL}\n${password}\n`);
