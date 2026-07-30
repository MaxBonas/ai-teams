export interface ProjectStepValidationInput {
  step: number;
  mode: 'create' | 'import';
  name: string;
  path: string;
  goal: string;
  objectiveKind: string;
  languages: string;
  resourcesReady: boolean;
}

export function invalidProjectStepControls(
  input: ProjectStepValidationInput,
): string[] {
  if (input.step === 0) {
    return [
      ...(!input.name.trim() ? ['project-name'] : []),
      ...(input.mode === 'import' && !input.path.trim() ? ['project-path'] : []),
    ];
  }
  if (input.step === 1) {
    const software = ['software', 'mixed', 'unknown'].includes(input.objectiveKind);
    return [
      ...(input.goal.trim().length < 3 ? ['project-goal'] : []),
      ...(software && !input.languages.split(',').some((item) => item.trim())
        ? ['project-languages']
        : []),
    ];
  }
  return input.step === 2 && !input.resourcesReady ? ['resources-error'] : [];
}
