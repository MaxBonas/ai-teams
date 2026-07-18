# Release notes indexer

Implementa una utilidad Python que convierta un `CHANGELOG.md` en un índice JSON estable.

Entregables:

- módulo `release_notes_indexer.py`;
- función `index_release_notes(markdown: str) -> list[dict]`;
- CLI `python release_notes_indexer.py CHANGELOG.md index.json`;
- tests públicos propios.

Contrato:

- reconoce releases solo en headings H2 con formato `## [VERSION] - YYYY-MM-DD`;
- cada release contiene `version`, `date` y `sections`;
- las secciones son headings H3 y su valor es una lista de bullets `- texto`;
- conserva el orden original de releases, secciones y bullets;
- ignora preámbulo, headings mal formados y contenido fuera de una release válida;
- headings y bullets dentro de fences Markdown no cuentan;
- un H2 no válido termina la release activa para evitar atribuir contenido ambiguo;
- versiones duplicadas producen `ValueError`;
- la CLI escribe UTF-8, JSON con indentación de dos espacios y termina con newline;
- argumentos inválidos o errores de entrada deben devolver exit code distinto de cero sin crear una salida parcial.

La tarea es acotada, reversible y de una sola rama. No necesita dependencias externas.
