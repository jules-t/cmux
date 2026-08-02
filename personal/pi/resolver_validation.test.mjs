import assert from "node:assert/strict";
import test from "node:test";

import { validateResolverOutcome } from "./resolver_validation.mjs";


const RESOLVED_REPORT = {
  ok: true,
  value:
    '{"decision":"resolved","confidence":0.9,"summary":"done","files":[]}\n',
};

test("does not verify a structurally invalid resolver report", async () => {
  let verificationCalls = 0;
  const reportValidation = { ok: false, error: "invalid resolver JSON" };

  const result = await validateResolverOutcome({
    reportValidation,
    verifyCandidate: async () => {
      verificationCalls += 1;
      return { ok: true };
    },
  });

  assert.equal(result, reportValidation);
  assert.equal(verificationCalls, 0);
});

test("preserves a valid stop decision without requesting a repair", async () => {
  let verificationCalls = 0;
  const reportValidation = {
    ok: true,
    value:
      '{"decision":"stop","confidence":0.7,"summary":"unsafe","files":[]}\n',
  };

  const result = await validateResolverOutcome({
    reportValidation,
    verifyCandidate: async () => {
      verificationCalls += 1;
      return { ok: true };
    },
  });

  assert.equal(result, reportValidation);
  assert.equal(verificationCalls, 0);
});

test("accepts a resolved report only after deterministic candidate verification", async () => {
  let verificationCalls = 0;

  const result = await validateResolverOutcome({
    reportValidation: RESOLVED_REPORT,
    verifyCandidate: async () => {
      verificationCalls += 1;
      return { ok: true };
    },
  });

  assert.equal(result, RESOLVED_REPORT);
  assert.equal(verificationCalls, 1);
});

test("turns deterministic candidate rejection into a semantic repair request", async () => {
  const result = await validateResolverOutcome({
    reportValidation: RESOLVED_REPORT,
    verifyCandidate: async () => ({
      ok: false,
      error: "candidate verification failed: personal commit graph changed",
    }),
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /personal commit graph changed/);
  assert.match(result.correctionScope, /candidate Git state/);
  assert.match(result.correctionInstruction, /rewrite.*resolver report/i);
});
