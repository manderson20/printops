import { fixupConfigRules } from "@eslint/compat";
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// fixupConfigRules wraps every rule in these shared configs so they keep
// working on ESLint 10.
//
// ESLint 10 removed the legacy rule-context accessors — context.getFilename(),
// getCwd(), getPhysicalFilename(), getSourceCode() — in favour of plain
// properties, with no fallback:
// https://eslint.org/docs/latest/use/migrate-to-10.0.0
//
// eslint-config-next pulls in three plugins that still call them and still cap
// their peer range at ESLint 9: eslint-plugin-react (7.37.5, no release since
// April 2025), eslint-plugin-jsx-a11y (6.10.2) and eslint-plugin-import
// (2.32.0). Without this the very first file fails the whole run with
// "contextOrFilename.getFilename is not a function" out of
// eslint-plugin-react's React-version detection.
//
// @eslint/compat 2.1.1 detects the running major and puts those four methods
// back on the context object for wrapped rules. It is the mechanism ESLint
// itself points at, not a private patch of somebody else's package, and it is a
// no-op on ESLint 9 — the same config works on both, so this is not a one-way
// door.
//
// Remove it once eslint-plugin-react, jsx-a11y and import all accept ESLint 10,
// or eslint-config-next stops depending on the ones that do not. Until then
// `pnpm install` prints unmet-peer warnings for those three; they are accurate
// about the declared ranges and wrong about whether it works.
const eslintConfig = defineConfig([
  ...fixupConfigRules(nextVitals),
  ...fixupConfigRules(nextTs),
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
