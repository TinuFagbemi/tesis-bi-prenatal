# Terceros incluidos en `frontend/gestante/vendor/`

Copias sin modificar de la distribución de **pdfmake 0.2.23**
(paquete npm `pdfmake`, carpeta `build/`). Las sirve el propio portal
(`/vendor/...`) para generar el PDF de «Guardar mi resumen» en el dispositivo,
sin internet y sin enviar datos a ningún servicio.

| Archivo | Origen | SHA-256 |
|---|---|---|
| `pdfmake.min.js` | `pdfmake@0.2.23/build/pdfmake.min.js` | `4410187c36ed6dd8cbfbaeea5b6fab6244238b3479c6de1cfe125d2c61fc2ba0` |
| `vfs_fonts.js` | `pdfmake@0.2.23/build/vfs_fonts.js` (fuentes Roboto) | `23e7c970fa9e75d296ea565939502a69ab2184cf40756c7d4670b6dabf470b58` |

`pdfmake.min.js` incluye SVG-to-PDFKit. `vfs_fonts.js` contiene la familia
Roboto 3.014 (Roboto Classic, Google; Copyright 2011 The Roboto Project
Authors). Su tabla de nombres declara la **SIL Open Font License 1.1**
(https://openfontlicense.org), que permite incrustarla y redistribuirla con
el software.

Para actualizar: sustituir ambos archivos por los de la misma versión de
`build/`, actualizar esta tabla y repetir las pruebas.

## pdfmake

```
The MIT License (MIT)

Copyright (c) 2014-2015 bpampuch
              2016-2026 liborm85

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## SVG-to-PDFKit

```
The MIT License (MIT)

Copyright (c) 2019 SVG-to-PDFKit contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```
