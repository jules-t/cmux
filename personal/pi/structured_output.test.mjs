import assert from "node:assert/strict";
import test from "node:test";

import {
  StructuredOutputValidationError,
  collectValidatedStructuredOutput,
} from "./structured_output.mjs";

const OPTIONS = {
  initialPrompt: "Perform the review.",
  label: "reviewer response",
  correctionInstruction: "Return only the corrected JSON object.",
};

test("retries invalid output in the same conversation", async () => {
  const responses = ["not JSON", '{"decision":"stop"}'];
  const prompts = [];
  const rejections = [];

  const result = await collectValidatedStructuredOutput({
    ...OPTIONS,
    runTurn: async (prompt) => {
      prompts.push(prompt);
      return responses[prompts.length - 1];
    },
    validate: async (candidate) =>
      candidate.startsWith("{")
        ? { ok: true, value: `${candidate}\n` }
        : { ok: false, error: "expected a JSON object at line 1" },
    onRejected: async (rejection) => rejections.push(rejection),
  });

  assert.deepEqual(result, {
    attempts: 2,
    value: '{"decision":"stop"}\n',
  });
  assert.equal(prompts[0], OPTIONS.initialPrompt);
  assert.match(prompts[1], /expected a JSON object at line 1/);
  assert.match(prompts[1], /correction 1 of 2/);
  assert.doesNotMatch(prompts[1], /not JSON/);
  assert.deepEqual(rejections, [
    { attempt: 1, error: "expected a JSON object at line 1" },
  ]);
});

test("does not retry a structurally valid stop decision", async () => {
  let turns = 0;

  const result = await collectValidatedStructuredOutput({
    ...OPTIONS,
    runTurn: async () => {
      turns += 1;
      return '{"decision":"stop"}';
    },
    validate: async (candidate) => ({ ok: true, value: `${candidate}\n` }),
  });

  assert.equal(turns, 1);
  assert.equal(result.attempts, 1);
});

test("fails closed after the bounded correction attempts", async () => {
  const prompts = [];

  await assert.rejects(
    collectValidatedStructuredOutput({
      ...OPTIONS,
      runTurn: async (prompt) => {
        prompts.push(prompt);
        return "still invalid";
      },
      validate: async () => ({ ok: false, error: "invalid reviewer JSON" }),
    }),
    (error) => {
      assert.ok(error instanceof StructuredOutputValidationError);
      assert.equal(error.attempts, 3);
      assert.match(error.message, /after 3 attempts/);
      return true;
    },
  );

  assert.equal(prompts.length, 3);
  assert.match(prompts[2], /correction 2 of 2/);
});

test("does not turn validator infrastructure errors into model retries", async () => {
  let turns = 0;

  await assert.rejects(
    collectValidatedStructuredOutput({
      ...OPTIONS,
      runTurn: async () => {
        turns += 1;
        return "candidate";
      },
      validate: async () => {
        throw new Error("validator process is unavailable");
      },
    }),
    /validator process is unavailable/,
  );

  assert.equal(turns, 1);
});
