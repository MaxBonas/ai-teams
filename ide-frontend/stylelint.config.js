export default {
  // El gate bloquea CSS inválido, duplicado o incompatible. El formato visual
  // se mantiene estable hasta dividir la hoja monolítica por componentes.
  extends: ['stylelint-config-recommended'],
  rules: {
    // El producto usa nombres BEM, utilidades cortas y selectores heredados.
    'selector-class-pattern': null,
    // La hoja aún es monolítica; comparar especificidad entre componentes no
    // relacionados produce falsos positivos. Se activará al modularizarla.
    'no-descending-specificity': null,
    // `clip` está deprecado y suele ocultar contenido a lectores de pantalla.
    'property-no-deprecated': true,
  },
  overrides: [
    {
      files: [
        'src/components/ModelRoleSelector/*.css',
        'src/components/QuorumStepper/*.css',
        'src/components/InboxPanel/*.css',
        'src/components/ChatPanel/*.css',
        'src/components/IssuePanel/*.css',
        'src/components/RunsPanel/*.css',
        'src/components/ProfileBadge.css',
      ],
      rules: {
        'no-descending-specificity': true,
      },
    },
  ],
};
