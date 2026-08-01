#!/usr/bin/env node

import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";

import {
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
  SettingsManager,
  createAgentSession,
} from "@earendil-works/pi-coding-agent";

import { collectValidatedStructuredOutput } from "./structured_output.mjs";

const PROVIDER = "deepseek";
const MODEL = "deepseek-v4-flash";
const THINKING_LEVEL = "max";
const execFileAsync = promisify(execFile);
const PROFILES = {
  resolver: ["read", "bash", "edit", "write", "grep", "find", "ls"],
  reviewer: ["read", "bash", "grep", "find", "ls"],
  smoke: ["read", "grep", "find", "ls"],
};
const OUTPUT_CONTRACTS = {
  resolver: {
    label: "resolver report",
    correctionInstruction:
      "Rewrite `.cmux-resolver-output.json` with the corrected JSON object. Do not make any other filesystem or Git changes during this correction; your conversational response is not the handoff.",
  },
  reviewer: {
    label: "reviewer response",
    correctionInstruction:
      "Return the corrected JSON object as your entire final response.",
  },
  smoke: {
    label: "smoke response",
    correctionInstruction:
      "Return the corrected JSON object as your entire final response.",
  },
};

function usage() {
  return `Usage:
  node run_agent.mjs --profile <resolver|reviewer|smoke> --prompt-file <path> --control-root <path> [--output <path>]
  node run_agent.mjs --self-check`;
}

function parseArguments(arguments_) {
  if (arguments_.length === 1 && arguments_[0] === "--self-check") {
    return { selfCheck: true };
  }

  const result = { selfCheck: false };
  for (let index = 0; index < arguments_.length; index += 1) {
    const argument = arguments_[index];
    if (argument === "--help" || argument === "-h") {
      console.log(usage());
      process.exit(0);
    }
    if (
      !["--profile", "--prompt-file", "--control-root", "--output"].includes(
        argument,
      )
    ) {
      throw new Error(`unknown argument: ${argument}\n${usage()}`);
    }
    const value = arguments_[index + 1];
    if (!value) {
      throw new Error(`${argument} requires a value\n${usage()}`);
    }
    const key = {
      "--profile": "profile",
      "--prompt-file": "promptFile",
      "--control-root": "controlRoot",
      "--output": "output",
    }[argument];
    result[key] = value;
    index += 1;
  }

  if (!Object.hasOwn(PROFILES, result.profile)) {
    throw new Error(`invalid or missing --profile\n${usage()}`);
  }
  if (!result.promptFile) {
    throw new Error(`missing --prompt-file\n${usage()}`);
  }
  if (!result.controlRoot) {
    throw new Error(`missing --control-root\n${usage()}`);
  }
  return result;
}

async function readApiKey() {
  let value = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    value += chunk;
  }
  if (!value) {
    throw new Error("DeepSeek API key was not provided on standard input");
  }
  return value;
}

function finalAssistantMessage(session, startIndex) {
  return session.agent.state.messages
    .slice(startIndex)
    .reverse()
    .find((message) => message.role === "assistant");
}

function textFromMessage(message) {
  if (!message) {
    return "";
  }
  if (typeof message.content === "string") {
    return message.content;
  }
  return message.content
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("");
}

async function createModelRuntime(apiKey) {
  const modelRuntime = await ModelRuntime.create({
    allowModelNetwork: false,
    modelsPath: null,
  });
  if (apiKey) {
    await modelRuntime.setRuntimeApiKey(PROVIDER, apiKey, {
      allowNetwork: false,
    });
  }
  const model = modelRuntime.getModel(PROVIDER, MODEL);
  if (!model) {
    throw new Error(`Pi does not provide the required model: ${PROVIDER}/${MODEL}`);
  }
  return { modelRuntime, model };
}

async function selfCheck() {
  const { model } = await createModelRuntime();
  if (!model.reasoning || !PROFILES.resolver.includes("edit")) {
    throw new Error("Pi runner profiles are invalid");
  }
  if (PROFILES.reviewer.includes("edit") || PROFILES.smoke.includes("bash")) {
    throw new Error("read-only Pi profiles expose mutation tools");
  }
  console.log(`Pi runner ready: ${model.provider}/${model.id} (${THINKING_LEVEL})`);
}

function validatorError(error) {
  for (const value of [error.stderr, error.stdout, error.message]) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "validator rejected the output";
}

async function runPythonValidator(script, arguments_) {
  try {
    await execFileAsync("python3", [script, ...arguments_], {
      encoding: "utf8",
      maxBuffer: 1024 * 1024,
    });
    return { ok: true };
  } catch (error) {
    if (typeof error?.code !== "number") {
      throw error;
    }
    return { ok: false, error: validatorError(error) };
  }
}

async function validateAssistantResponse({
  profile,
  response,
  controlRoot,
  temporaryRoot,
}) {
  const candidate = path.join(temporaryRoot, `${profile}-candidate.json`);
  await fs.writeFile(candidate, `${response}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  const validation = await runPythonValidator(
    path.join(controlRoot, "personal", "agent_output.py"),
    [profile, candidate],
  );
  if (!validation.ok) {
    return validation;
  }
  return {
    ok: true,
    value: await fs.readFile(candidate, "utf8"),
  };
}

async function validateResolverReport({ cwd, controlRoot, temporaryRoot }) {
  const source = path.join(cwd, ".cmux-resolver-output.json");
  const destination = path.join(temporaryRoot, "resolver-canonical.json");
  await fs.rm(destination, { force: true });
  const validation = await runPythonValidator(
    path.join(controlRoot, "personal", "resolver_report.py"),
    ["collect", "--source", source, "--destination", destination],
  );
  if (!validation.ok) {
    return validation;
  }

  const value = await fs.readFile(destination, "utf8");
  await fs.writeFile(source, value, { encoding: "utf8", mode: 0o600 });
  return { ok: true, value };
}

async function runAgentTurn(session, prompt) {
  const startIndex = session.agent.state.messages.length;
  await session.prompt(prompt, {
    expandPromptTemplates: false,
    source: "rpc",
  });

  const message = finalAssistantMessage(session, startIndex);
  if (!message) {
    throw new Error("Pi completed without an assistant response");
  }
  if (message.stopReason === "error" || message.stopReason === "aborted") {
    throw new Error(message.errorMessage || `Pi stopped with ${message.stopReason}`);
  }
  return textFromMessage(message).trim();
}

async function run(arguments_) {
  const apiKey = await readApiKey();
  const { modelRuntime, model } = await createModelRuntime(apiKey);
  const cwd = process.cwd();
  const settingsManager = SettingsManager.inMemory({
    enableAnalytics: false,
    enableInstallTelemetry: false,
  });
  const resourceLoader = new DefaultResourceLoader({
    cwd,
    agentDir: "/tmp/pi-agent-config",
    settingsManager,
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
  });
  await resourceLoader.reload();

  const { session } = await createAgentSession({
    cwd,
    modelRuntime,
    model,
    thinkingLevel: THINKING_LEVEL,
    tools: PROFILES[arguments_.profile],
    resourceLoader,
    settingsManager,
    sessionManager: SessionManager.inMemory(cwd),
  });

  const prompt = await fs.readFile(arguments_.promptFile, "utf8");
  console.log(`Starting Pi with ${PROVIDER}/${MODEL} (${THINKING_LEVEL})`);
  const temporaryRoot = await fs.mkdtemp(
    path.join(os.tmpdir(), "pi-structured-output-"),
  );
  try {
    const contract = OUTPUT_CONTRACTS[arguments_.profile];
    const result = await collectValidatedStructuredOutput({
      initialPrompt: prompt,
      label: contract.label,
      correctionInstruction: contract.correctionInstruction,
      runTurn: (turnPrompt) => runAgentTurn(session, turnPrompt),
      validate: (response) =>
        arguments_.profile === "resolver"
          ? validateResolverReport({
              cwd,
              controlRoot: arguments_.controlRoot,
              temporaryRoot,
            })
          : validateAssistantResponse({
              profile: arguments_.profile,
              response,
              controlRoot: arguments_.controlRoot,
              temporaryRoot,
            }),
      onRejected: ({ attempt, error }) => {
        console.error(
          `Structured output attempt ${attempt} rejected: ${error}`,
        );
      },
    });

    console.log(result.value.trim());
    if (result.attempts > 1) {
      console.log(`Structured output accepted on attempt ${result.attempts}`);
    }
    if (arguments_.output) {
      await fs.writeFile(path.resolve(arguments_.output), result.value, {
        encoding: "utf8",
        mode: 0o600,
      });
    }
  } finally {
    await fs.rm(temporaryRoot, { recursive: true, force: true });
  }
}

async function main() {
  const arguments_ = parseArguments(process.argv.slice(2));
  if (arguments_.selfCheck) {
    await selfCheck();
    return;
  }
  await run(arguments_);
}

main().catch((error) => {
  console.error(`Pi runner failed: ${error instanceof Error ? error.message : error}`);
  process.exitCode = 1;
});
