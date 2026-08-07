import fs from "node:fs";
import path from "node:path";

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

export function runTests() {
  const projectRoot = process.cwd();
  const source = fs.readFileSync(
    path.join(projectRoot, "components", "ops", "config", "ConfigPageClient.tsx"),
    "utf8",
  );

  assert(
    source.includes("data-testid=\"sensitive-session-input\"") &&
      source.includes("spellCheck={false}") &&
      source.includes("autoCapitalize=\"none\""),
    "ops sensitive session input must be optimized for exact sessionId entry",
  );
  assert(
    source.includes("Docker .env") &&
      source.includes("No need to write $$ as $$$$") &&
      source.includes("Real $$"),
    "ops config page must explain that UI-entered sessionIds keep literal $$ and do not need Docker escaping",
  );
  assert(
    source.includes("Last checked") &&
      source.includes("sensitiveCheckedAt") &&
      source.includes("setSensitiveCheckedAt"),
    "ops config page must show when the post-rotation sensitive health check was performed",
  );
}
