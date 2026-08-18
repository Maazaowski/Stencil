/**
 * `npm run test:e2e` entry point.
 *
 * Seeds a throwaway admin against the running dev stack (unless credentials are
 * already in the environment), then hands off to Playwright with those creds
 * injected. Keeps passwords out of the repo and out of playwright.config.ts.
 */
import { spawnSync } from "node:child_process";

let email = process.env.E2E_EMAIL;
let password = process.env.E2E_PASSWORD;

if (!email || !password) {
  const seed = spawnSync(process.execPath, ["scripts/seed-e2e-user.mjs"], { encoding: "utf8" });
  if (seed.status !== 0) {
    process.stderr.write(seed.stderr || "");
    process.stderr.write(
      "\nCould not seed an E2E user. Start the stack first:\n" +
        "  docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d\n" +
        "or export E2E_EMAIL / E2E_PASSWORD for an existing account.\n",
    );
    process.exit(1);
  }
  [email, password] = seed.stdout.trim().split("\n");
}

const args = process.argv.slice(2);
const run = spawnSync("npx", ["playwright", "test", ...args], {
  stdio: "inherit",
  shell: process.platform === "win32",
  env: { ...process.env, E2E_EMAIL: email, E2E_PASSWORD: password },
});
process.exit(run.status ?? 1);
