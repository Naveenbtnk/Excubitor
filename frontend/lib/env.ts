/**
 * Build-mode facts, in one place.
 *
 * Next substitutes `process.env.NODE_ENV` at build time rather than reading it at run time, so a
 * production bundle cannot be talked into revealing a development affordance by anything the
 * process happens to have in its environment later. The default argument is what keeps the
 * question testable: a test can ask what each kind of build would do without rebuilding one.
 */
export function isDevBuild(nodeEnv: string | undefined = process.env.NODE_ENV): boolean {
  return nodeEnv !== "production";
}
