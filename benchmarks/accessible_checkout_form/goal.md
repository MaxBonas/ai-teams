# Checkout accesible sin frameworks

Implementa un formulario de checkout pequeño, usable con teclado y sin depender
de frameworks, red ni build step.

Entregables:

- `index.html`;
- `checkout.js`;
- `styles.css`;
- tests públicos propios.

Contrato funcional:

- el formulario `checkout-form` contiene `full_name`, `email`, `card_number`,
  `expiry` y `cvc`;
- cada campo tiene un `<label>` asociado, `autocomplete` apropiado y los campos
  de pago usan `inputmode` adecuado;
- `validateCheckout(data)` devuelve un objeto de errores sin modificar el input;
- nombre no vacío, email con forma válida, tarjeta válida mediante Luhn, expiry
  futura o del mes actual en formato `MM/YY`, y CVC de 3 o 4 dígitos;
- espacios y guiones de la tarjeta se aceptan y normalizan solo para validar;
- el orden estable de errores es nombre, email, tarjeta, expiry y CVC;
- `checkout.js` funciona tanto en navegador como mediante `require()` de Node y
  exporta al menos `validateCheckout` y `luhnValid`.

Contrato de interacción y accesibilidad:

- validación en submit con `preventDefault`, sin requests de red;
- cada campo inválido recibe `aria-invalid="true"`, un mensaje asociado mediante
  `aria-describedby` y una clase visual de error;
- existe un resumen `error-summary`, con `role="alert"`, `tabindex="-1"` y links
  que llevan a cada campo inválido; al fallar recibe foco;
- existe `order-status` con `role="status"` y `aria-live="polite"`; al validar
  correctamente anuncia confirmación y oculta/limpia errores anteriores;
- el HTML sigue siendo comprensible con JavaScript deshabilitado: títulos,
  agrupación de datos de pago mediante `fieldset`/`legend` y botón descriptivo;
- no usar `alert()` ni comunicar errores solo mediante color.

Contrato visual:

- foco `:focus-visible` claramente perceptible;
- estado de error con texto y borde, manteniendo contraste legible;
- layout de una columna en móvil y al menos dos columnas a partir de `768px`;
- respeta `prefers-reduced-motion` y no depende de animación para comprender el
  estado.

La tarea es reversible, pero cruza comportamiento, accesibilidad y diseño. La
verificación debe cubrir tanto las funciones puras como el contrato del DOM.
