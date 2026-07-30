import { useEffect, useRef } from 'react';

export function useWizardStageFocus(step: number) {
  const stageRef = useRef<HTMLDivElement>(null);
  const previousStep = useRef(step);

  useEffect(() => {
    if (previousStep.current === step) return;
    previousStep.current = step;
    stageRef.current?.focus();
  }, [step]);

  return stageRef;
}
