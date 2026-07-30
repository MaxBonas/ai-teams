import { describe, expect, it } from 'vitest';
import { invalidProjectStepControls } from './projectStepValidation';

const base = {
  mode: 'create' as const,
  name: 'Portal',
  path: '',
  goal: 'Entregar un portal',
  objectiveKind: 'software',
  languages: 'TypeScript',
  resourcesReady: true,
};

describe('projectStepValidation', () => {
  it('identifies every missing import identity field in focus order', () => {
    expect(invalidProjectStepControls({
      ...base,
      step: 0,
      mode: 'import',
      name: '',
    })).toEqual(['project-name', 'project-path']);
  });

  it('requires goal and stack only when the objective has software surface', () => {
    expect(invalidProjectStepControls({
      ...base,
      step: 1,
      goal: '',
      languages: '',
    })).toEqual(['project-goal', 'project-languages']);
    expect(invalidProjectStepControls({
      ...base,
      step: 1,
      objectiveKind: 'research',
      languages: '',
    })).toEqual([]);
  });

  it('routes missing prepared resources to the adjacent error', () => {
    expect(invalidProjectStepControls({
      ...base,
      step: 2,
      resourcesReady: false,
    })).toEqual(['resources-error']);
  });
});
