const SEMANTIC_CORRECTION_SCOPE =
  "Repair the candidate Git state as well as its machine-readable handoff. Preserve upstream and personal behavior, and keep a stop decision when no safe repair exists.";
const SEMANTIC_CORRECTION_INSTRUCTION =
  "Reinspect the original baseline, conflict result, and policy. Repair the candidate only within the original conflict-resolution mandate, rerun focused checks, and rewrite the resolver report `.cmux-resolver-output.json` with the resulting decision. Do not claim resolved unless the deterministic verifier passes.";


export async function validateResolverOutcome({
  reportValidation,
  verifyCandidate,
}) {
  if (!reportValidation?.ok) {
    return reportValidation;
  }
  const report = JSON.parse(reportValidation.value);
  if (report.decision !== "resolved") {
    return reportValidation;
  }

  const candidateValidation = await verifyCandidate();
  if (!candidateValidation || typeof candidateValidation.ok !== "boolean") {
    throw new Error("candidate verifier returned an invalid result");
  }
  if (candidateValidation.ok) {
    return reportValidation;
  }
  return {
    ok: false,
    error: candidateValidation.error,
    correctionScope: SEMANTIC_CORRECTION_SCOPE,
    correctionInstruction: SEMANTIC_CORRECTION_INSTRUCTION,
  };
}
