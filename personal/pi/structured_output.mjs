export const DEFAULT_MAX_CORRECTIONS = 2;

export class StructuredOutputValidationError extends Error {
  constructor(label, attempts, validatorError) {
    super(
      `${label} remained invalid after ${attempts} attempts: ${validatorError}`,
    );
    this.name = "StructuredOutputValidationError";
    this.attempts = attempts;
    this.validatorError = validatorError;
  }
}

function compactValidatorError(error) {
  const compact =
    typeof error === "string" ? error.replace(/\s+/g, " ").trim() : "";
  return (compact || "validator rejected the output").slice(0, 1000);
}

function correctionPrompt({
  label,
  validatorError,
  correctionNumber,
  maxCorrections,
  correctionInstruction,
}) {
  return [
    `The trusted deterministic validator rejected your previous ${label}.`,
    `Validator error: ${compactValidatorError(validatorError)}`,
    `This is correction ${correctionNumber} of ${maxCorrections}.`,
    "Correct only the machine-readable handoff. Preserve your substantive findings and do not change a stop or failure conclusion merely to satisfy validation.",
    correctionInstruction,
    "Do not add Markdown fences or explanatory prose to the machine-readable output.",
  ].join("\n");
}

export async function collectValidatedStructuredOutput({
  initialPrompt,
  runTurn,
  validate,
  label,
  correctionInstruction,
  maxCorrections = DEFAULT_MAX_CORRECTIONS,
  onRejected = () => {},
}) {
  if (!Number.isInteger(maxCorrections) || maxCorrections < 0) {
    throw new Error("maxCorrections must be a non-negative integer");
  }

  let prompt = initialPrompt;
  let lastValidatorError = "validator rejected the output";
  const totalAttempts = maxCorrections + 1;

  for (let attempt = 1; attempt <= totalAttempts; attempt += 1) {
    const candidate = await runTurn(prompt);
    const validation = await validate(candidate);
    if (!validation || typeof validation.ok !== "boolean") {
      throw new Error("structured output validator returned an invalid result");
    }
    if (validation.ok) {
      if (typeof validation.value !== "string") {
        throw new Error("structured output validator did not return canonical text");
      }
      return {
        attempts: attempt,
        value: validation.value,
      };
    }

    lastValidatorError = compactValidatorError(validation.error);
    await onRejected({ attempt, error: lastValidatorError });
    if (attempt === totalAttempts) {
      throw new StructuredOutputValidationError(
        label,
        totalAttempts,
        lastValidatorError,
      );
    }

    prompt = correctionPrompt({
      label,
      validatorError: lastValidatorError,
      correctionNumber: attempt,
      maxCorrections,
      correctionInstruction,
    });
  }

  throw new StructuredOutputValidationError(
    label,
    totalAttempts,
    lastValidatorError,
  );
}
