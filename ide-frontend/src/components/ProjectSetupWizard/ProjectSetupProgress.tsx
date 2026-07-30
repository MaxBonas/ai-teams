import { Check } from 'lucide-react';
import { PROJECT_SETUP_STEPS } from './wizardConfig';

interface ProjectSetupProgressProps {
  step: number;
  busy: boolean;
  onNavigate: (step: number) => void;
}

export function ProjectSetupProgress({
  step,
  busy,
  onNavigate,
}: ProjectSetupProgressProps) {
  return (
    <nav className="project-setup-steps" aria-label="Progreso de configuración">
      {PROJECT_SETUP_STEPS.map((item, index) => {
        const Icon = item.icon;
        const complete = index < step;
        const current = index === step;
        const stateLabel = complete ? 'Completado' : current ? 'Actual' : 'Pendiente';
        return (
          <button
            key={item.label}
            type="button"
            className={`project-setup-step${current ? ' active' : ''}${complete ? ' complete' : ''}`}
            onClick={() => onNavigate(index)}
            disabled={index >= step || busy}
            aria-current={current ? 'step' : undefined}
            aria-label={`Paso ${index + 1} de ${PROJECT_SETUP_STEPS.length}: ${item.label}. ${stateLabel}`}
          >
            <span>{complete ? <Check size={15} /> : <Icon size={15} />}</span>
            <small>0{index + 1}</small>
            <strong>{item.label}<small> · {stateLabel}</small></strong>
          </button>
        );
      })}
    </nav>
  );
}
