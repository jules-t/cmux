#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import {
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
  SettingsManager,
  createAgentSession,
} from "@earendil-works/pi-coding-agent";

const PROVIDER = "deepseek";
const MODEL = "deepseek-v4-flash";
const THINKING_LEVEL = "max";
const PROFILES = {
  resolver: ["read", "bash", "edit", "write", "grep", "find", "ls"],
  reviewer: ["read", "bash", "grep", "find", "ls"],
  smoke: ["read", "grep", "find", "ls"],
};

function usage() {
  return `Usage:
  node run_agent.mjs --profile <resolver|reviewer|smoke> --prompt-file <path> [--output <path>]
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
    if (!["--profile", "--prompt-file", "--output"].includes(argument)) {
      throw new Error(`unknown argument: ${argument}\n${usage()}`);
    }
    const value = arguments_[index + 1];
    if (!value) {
      throw new Error(`${argument} requires a value\n${usage()}`);
    }
    const key = {
      "--profile": "profile",
      "--prompt-file": "promptFile",
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

function finalAssistantMessage(session) {
  return [...session.agent.state.messages]
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
  await session.prompt(prompt, {
    expandPromptTemplates: false,
    source: "rpc",
  });

  const message = finalAssistantMessage(session);
  if (!message) {
    throw new Error("Pi completed without an assistant response");
  }
  if (message.stopReason === "error" || message.stopReason === "aborted") {
    throw new Error(message.errorMessage || `Pi stopped with ${message.stopReason}`);
  }

  const response = textFromMessage(message).trim();
  if (response) {
    console.log(response);
  }
  if (arguments_.output) {
    if (!response) {
      throw new Error("Pi did not produce the required structured response");
    }
    await fs.writeFile(path.resolve(arguments_.output), `${response}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
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
