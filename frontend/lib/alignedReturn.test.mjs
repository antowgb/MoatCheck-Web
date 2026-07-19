// Cross-checks alignedReturn() against app.backtest.engine._aligned_return
// (backend/app/backtest/engine.py) using the shared fixture at
// fixtures/aligned_return_case.json, so the two implementations can't
// silently diverge. No test framework: run directly with
//   node lib/alignedReturn.test.mjs
// (compiles lib/alignedReturn.ts on the fly via the project's own
// TypeScript devDependency, then executes it).

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const outDir = mkdtempSync(path.join(tmpdir(), "aligned-return-test-"));

execFileSync(
  path.join(__dirname, "..", "node_modules", ".bin", "tsc"),
  [
    path.join(__dirname, "alignedReturn.ts"),
    "--outDir", outDir,
    "--target", "es2020",
    "--module", "es2020",
    "--moduleResolution", "node",
    "--skipLibCheck",
  ],
  { stdio: "inherit" }
);

const { alignedReturn } = await import(path.join(outDir, "alignedReturn.js"));

const fixture = JSON.parse(
  readFileSync(path.join(__dirname, "..", "..", "fixtures", "aligned_return_case.json"), "utf-8")
);

const stockPoints = fixture.stock_dates.map((date) => ({ date, close: 0 }));
const result = alignedReturn(stockPoints, fixture.benchmark_prices, fixture.start_date);
const rounded = Math.round(result * 1e4) / 1e4;

if (rounded !== fixture.expected_aligned_return) {
  console.error(
    `alignedReturn returned ${rounded}, fixture expects ${fixture.expected_aligned_return} — ` +
    `check it still matches the Python port in backend/app/backtest/engine.py`
  );
  process.exit(1);
}

console.log("OK: alignedReturn matches fixtures/aligned_return_case.json");
