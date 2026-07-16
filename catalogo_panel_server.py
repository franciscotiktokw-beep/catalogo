
import os
import re
import tempfile
from collections import Counter
from flask import Flask, request, render_template_string, send_file, jsonify
import fitz  # PyMuPDF

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB max


def color_int_to_rgb(color_int):
    """Convierte el color entero (sRGB) de PyMuPDF a una tupla (r, g, b) 0-1."""
    return (
        ((color_int >> 16) & 255) / 255,
        ((color_int >> 8) & 255) / 255,
        (color_int & 255) / 255,
    )


def sample_background_color(pagina, rect, ignore_color=None):
    """Muestrea el color de fondo real de una zona del catálogo.

    Renderiza un recorte un poco más grande que el precio y toma el color
    MÁS FRECUENTE de toda la zona (el fondo domina en superficie frente al
    texto). Si se indica ``ignore_color`` (el color del texto/tachado del
    precio), se descartan esos píxeles para que nunca "gane" el color del
    texto y el parche quede negro/blanco fuera de lugar.
    """
    pad = 5
    clip = fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)
    clip = clip & pagina.rect
    if clip.is_empty:
        return (1, 1, 1)
    try:
        pix = pagina.get_pixmap(clip=clip, alpha=False)
    except Exception:
        return (1, 1, 1)
    if pix.width < 2 or pix.height < 2 or pix.n < 3:
        return (1, 1, 1)

    ignore_rgb = None
    if ignore_color is not None:
        ignore_rgb = tuple(int(round(c * 255)) for c in ignore_color)

    colores = Counter()
    w, h = pix.width, pix.height
    for y in range(h):
        for x in range(w):
            px = pix.pixel(x, y)[:3]
            if ignore_rgb is not None:
                # Descartamos píxeles parecidos al color del texto/tachado
                dist = abs(px[0] - ignore_rgb[0]) + abs(px[1] - ignore_rgb[1]) + abs(px[2] - ignore_rgb[2])
                if dist < 100:
                    continue
            colores[px] += 1

    if not colores:
        return (1, 1, 1)
    r, g, b = colores.most_common(1)[0][0]
    return (r / 255, g / 255, b / 255)



HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="es" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Conversor de Catálogos ARS ➔ PYG</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        darkBg: '#0f172a',
                        cardBg: '#1e293b',
                        accent: '#3b82f6',
                        accentHover: '#2563eb'
                    }
                }
            }
        }
    </script>
    <style>
        body {
            background-color: #0f172a;
            color: #f8fafc;
        }
    </style>
</head>
<body class="min-h-screen flex flex-col justify-between font-sans">
    <header class="border-b border-slate-800 bg-slate-900/50 backdrop-blur py-4 px-6">
        <div class="max-w-5xl mx-auto flex justify-between items-center">
            <div class="flex items-center gap-3">
                <div class="p-2 bg-blue-600/20 text-blue-400 rounded-lg">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                </div>
                <div>
                    <h1 class="text-lg font-bold tracking-tight">PDF Price Converter</h1>
                    <p class="text-xs text-slate-400">ARS ➔ PYG para catálogos de cosméticos</p>
                </div>
            </div>
            <span class="text-xs bg-slate-800 text-slate-300 px-2.5 py-1 rounded-full font-mono">v1.0.0</span>
        </div>
    </header>

    <main class="flex-grow max-w-5xl w-full mx-auto p-6 flex flex-col justify-center">
        <div class="grid md:grid-cols-5 gap-8 items-start">
            
            <!-- Panel Izquierdo: Configuración y Carga -->
            <div class="md:col-span-3 bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
                <div>
                    <h2 class="text-xl font-semibold mb-1">Configura tu conversión</h2>
                    <p class="text-sm text-slate-400">El motor detectará los precios con "$" automáticamente y reemplazará su valor en el mismo espacio físico.</p>
                </div>

                <form id="convertForm" class="space-y-5">
                    <!-- Tasa de Cambio -->
                    <div>
                        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Tasa de cambio (1 ARS = X PYG)</label>
                        <div class="relative rounded-lg shadow-sm">
                            <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                                <span class="text-slate-500 sm:text-sm">₲</span>
                            </div>
                            <input type="number" step="0.01" name="rate" id="rate" value="7.80" class="block w-full pl-8 pr-3 py-2.5 bg-slate-950 border border-slate-800 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-white font-mono" required>
                        </div>
                        <p class="text-[11px] text-slate-500 mt-1">Sugerido hoy: 7.80. Redondeo automático a la centena (ej: Gs. 131.800).</p>
                    </div>

                    <!-- Input PDF -->
                    <div>
                        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Archivo PDF del catálogo</label>
                        <div id="dropZone" class="border-2 border-dashed border-slate-800 hover:border-slate-700 bg-slate-950/50 rounded-xl p-8 text-center cursor-pointer transition-colors group">
                            <input type="file" name="file" id="fileInput" accept="application/pdf" class="hidden" required>
                            <div class="space-y-3">
                                <svg class="mx-auto h-10 w-10 text-slate-500 group-hover:text-blue-400 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"></path></svg>
                                <div class="text-sm text-slate-300">
                                    <span class="font-medium text-blue-500 group-hover:underline">Haz clic para subir</span> o arrastra tu PDF aquí
                                </div>
                                <p class="text-xs text-slate-500" id="fileNameDisplay">Solo archivos PDF (máx. 100MB)</p>
                            </div>
                        </div>
                    </div>

                    <!-- Botón Acción -->
                    <button type="submit" id="submitBtn" class="w-full bg-blue-600 hover:bg-blue-700 text-white py-3 px-4 rounded-xl font-medium transition-all shadow-lg shadow-blue-500/10 flex justify-center items-center gap-2">
                        <span>Procesar Catálogo</span>
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>
                    </button>
                </form>

                <!-- Barra de Progreso / Estado -->
                <div id="statusContainer" class="hidden space-y-3">
                    <div class="flex justify-between text-xs font-medium">
                        <span id="statusText" class="text-slate-400">Subiendo archivo...</span>
                        <span id="statusPercent" class="text-blue-500 font-mono">0%</span>
                    </div>
                    <div class="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                        <div id="progressBar" class="bg-blue-500 h-full w-0 transition-all duration-300"></div>
                    </div>
                </div>
            </div>

            <!-- Panel Derecho: Explicación del Motor Inteligente -->
            <div class="md:col-span-2 space-y-6">
                <div class="bg-slate-900/50 border border-slate-800/80 rounded-2xl p-6">
                    <h3 class="text-md font-semibold text-slate-200 mb-3 flex items-center gap-2">
                        <span class="flex h-2 w-2 rounded-full bg-green-500"></span>
                        Motor Inteligente de Detección
                    </h3>
                    <ul class="space-y-3 text-sm text-slate-400">
                        <li class="flex items-start gap-2.5">
                            <svg class="w-4 h-4 text-green-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                            <span><strong>Precios Grandes y Chicos:</strong> El script rastrea el símbolo "$" y lee horizontalmente de acuerdo a la escala del texto original.</span>
                        </li>
                        <li class="flex items-start gap-2.5">
                            <svg class="w-4 h-4 text-green-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                            <span><strong>Tachados (P.REG.):</strong> Detecta automáticamente bloques como "P.REG.: $..." y respeta la estructura reemplazando solo el valor numérico.</span>
                        </li>
                        <li class="flex items-start gap-2.5">
                            <svg class="w-4 h-4 text-green-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                            <span><strong>Redondeo Comercial:</strong> Los Guaraníes no usan decimales. El sistema redondea automáticamente al múltiplo de 100 más cercano para un acabado comercial perfecto.</span>
                        </li>
                    </ul>
                </div>

                <div class="border border-slate-800 rounded-xl p-4 bg-slate-950/40 text-xs text-slate-500 flex items-center gap-3">
                    <svg class="w-8 h-8 text-slate-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                    <span>Como eres desarrollador, puedes ejecutar y extender este panel localmente en tu puerto preferido. El backend está basado en Flask y PyMuPDF.</span>
                </div>
            </div>

        </div>
    </main>

    <footer class="border-t border-slate-800 bg-slate-950 py-4 px-6 text-center text-xs text-slate-500">
        <div class="max-w-5xl mx-auto flex flex-col md:flex-row justify-between items-center gap-2">
            <span>Conversor Estético de Catálogos — Arbell, Millanel, Tsu, etc.</span>
            <span>Desarrollado para automatización ágil de PDFs</span>
        </div>
    </footer>

    <script>
        const dropZone = document.getElementById('dropZone');
        const fileInput = document.getElementById('fileInput');
        const fileNameDisplay = document.getElementById('fileNameDisplay');
        const form = document.getElementById('convertForm');
        const statusContainer = document.getElementById('statusContainer');
        const statusText = document.getElementById('statusText');
        const statusPercent = document.getElementById('statusPercent');
        const progressBar = document.getElementById('progressBar');
        const submitBtn = document.getElementById('submitBtn');

        // Drag and Drop events
        ['dragenter', 'dragover'].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                dropZone.classList.add('border-blue-500', 'bg-blue-950/10');
            }, false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                dropZone.classList.remove('border-blue-500', 'bg-blue-950/10');
            }, false);
        });

        dropZone.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            const files = dt.files;
            if(files.length > 0) {
                fileInput.files = files;
                updateFileName(files[0].name);
            }
        });

        dropZone.addEventListener('click', () => fileInput.click());

        fileInput.addEventListener('change', () => {
            if(fileInput.files.length > 0) {
                updateFileName(fileInput.files[0].name);
            }
        });

        function updateFileName(name) {
            fileNameDisplay.textContent = name;
            fileNameDisplay.classList.remove('text-slate-500');
            fileNameDisplay.classList.add('text-blue-400', 'font-medium');
        }

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            if(fileInput.files.length === 0) return alert('Por favor, selecciona un archivo PDF primero.');

            const formData = new FormData();
            formData.append('file', fileInput.files[0]);
            formData.append('rate', document.getElementById('rate').value);

            statusContainer.classList.remove('hidden');
            submitBtn.disabled = true;
            submitBtn.classList.add('opacity-50', 'cursor-not-allowed');

            try {
                // Configurar XHR para poder ver el progreso real de subida
                const xhr = new XMLHttpRequest();
                xhr.open('POST', '/convert', true);
                
                xhr.upload.onprogress = function(e) {
                    if (e.lengthComputable) {
                        const percent = Math.round((e.loaded / e.total) * 100);
                        progressBar.style.width = (percent * 0.4) + '%'; // 40% reservado para subida
                        statusPercent.textContent = Math.round(percent * 0.4) + '%';
                        statusText.textContent = 'Subiendo catálogo al servidor...';
                    }
                };

                xhr.onload = function() {
                    if (xhr.status === 200) {
                        progressBar.style.width = '100%';
                        statusPercent.textContent = '100%';
                        statusText.textContent = '¡Listo! Descargando tu archivo...';
                        
                        // Crear enlace de descarga con el binario recibido
                        const blob = new Blob([xhr.response], { type: 'application/pdf' });
                        const link = document.createElement('a');
                        link.href = window.URL.createObjectURL(blob);
                        link.download = 'catalogo_pyg.pdf';
                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);
                        
                        setTimeout(resetStatus, 3000);
                    } else {
                        alert('Error al procesar el PDF. Por favor verifica el formato del archivo.');
                        resetStatus();
                    }
                };

                xhr.onerror = function() {
                    alert('Ocurrió un error en la conexión.');
                    resetStatus();
                };

                xhr.responseType = 'blob';
                
                // Simular el backend procesando (incrementando barra del 40% al 90% mientras esperamos)
                let processPercent = 40;
                const interval = setInterval(() => {
                    if (processPercent < 95) {
                        processPercent += 5;
                        progressBar.style.width = processPercent + '%';
                        statusPercent.textContent = processPercent + '%';
                        statusText.textContent = 'Procesando coordenadas de precios y sobreescribiendo...';
                    } else {
                        clearInterval(interval);
                    }
                }, 400);

                xhr.send(formData);

                function resetStatus() {
                    clearInterval(interval);
                    statusContainer.classList.add('hidden');
                    progressBar.style.width = '0%';
                    statusPercent.textContent = '0%';
                    submitBtn.disabled = false;
                    submitBtn.classList.remove('opacity-50', 'cursor-not-allowed');
                }

            } catch (err) {
                console.error(err);
                alert('Ocurrió un error en el envío.');
                submitBtn.disabled = false;
                submitBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            }
        });
    </script>
</body>
</html>
'''

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return "No file uploaded", 400
    
    file = request.files['file']
    rate_val = float(request.form.get('rate', 7.80))
    
    if file.filename == '':
        return "No file selected", 400

    input_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], "converted_" + file.filename)
    
    file.save(input_path)
    
    doc = None
    try:
        # Abrimos el PDF original
        doc = fitz.open(input_path)
        
        for pagina in doc:
            # Extraemos el texto con detalle (fuente, tamaño, color, posición)
            data = pagina.get_text("dict")

            # Recolectamos todas las ediciones de precios de la página. Muestreamos
            # el color de fondo AHORA (antes de tapar nada), para que el color sea el
            # original y no el de un parche ya dibujado.
            ediciones = []

            for block in data.get("blocks", []):
                if block.get("type", 0) != 0:  # solo bloques de texto
                    continue

                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue

                    line_text = "".join(s["text"] for s in spans)
                    if "$" not in line_text:
                        continue

                    # Calculamos el rango de caracteres que ocupa cada span dentro
                    # de la línea, para poder mapear el precio a sus spans exactos.
                    spans_ranges = []
                    pos = 0
                    for s in spans:
                        spans_ranges.append((pos, pos + len(s["text"]), s))
                        pos += len(s["text"])

                    # Procesamos cada precio ("$" + cifras) de la línea por separado.
                    for match in re.finditer(r'\$\s*([\d.,]+)', line_text):
                        numeros = "".join(re.findall(r'\d+', match.group(1)))
                        if not numeros:
                            continue

                        m_start, m_end = match.start(), match.end()

                        # Solo los spans que se solapan con el precio (excluye texto
                        # extra de la misma línea como "cada uno" / "cada una").
                        price_spans = [
                            s for (a, b, s) in spans_ranges
                            if a < m_end and b > m_start
                        ]
                        if not price_spans:
                            continue

                        precio_ars = float(numeros)
                        # Convertimos y redondeamos a la centena
                        precio_pyg = round(precio_ars * rate_val, -2)
                        nuevo_texto = f"Gs. {precio_pyg:,.0f}".replace(",", ".")

                        # Span de referencia: el que contiene el "$" (estilo y baseline)
                        ref = next(
                            (s for s in price_spans if "$" in s["text"]),
                            price_spans[0],
                        )

                        # Rectángulo exacto que ocupa el precio original
                        x0 = min(s["bbox"][0] for s in price_spans)
                        y0 = min(s["bbox"][1] for s in price_spans)
                        x1 = max(s["bbox"][2] for s in price_spans)
                        y1 = max(s["bbox"][3] for s in price_spans)
                        price_rect = fitz.Rect(x0, y0, x1, y1)

                        # Estilo original: tamaño, color y negrita del precio original
                        fontsize = ref.get("size", 10.0)
                        color_orig = color_int_to_rgb(ref.get("color", 0))
                        es_negrita = bool(ref.get("flags", 0) & 16)
                        fontname = "hebo" if es_negrita else "helv"

                        # Mantenemos el precio DENTRO del área exacta que ocupaba el
                        # original: así nunca se sale del margen ni pisa lo de al lado.
                        ancho_original = x1 - x0
                        ancho_texto = fitz.get_text_length(nuevo_texto, fontname=fontname, fontsize=fontsize)
                        if ancho_texto > ancho_original and ancho_texto > 0:
                            fontsize *= ancho_original / ancho_texto
                            ancho_texto = ancho_original

                        # Detectamos si es un precio regular tachado (P.REG.)
                        es_tachado = "P.REG" in line_text.upper() or "PREG" in line_text.upper()

                        # Muestreamos el color de fondo real (con la página aún
                        # intacta), ignorando el color del texto/tachado del precio.
                        bg = sample_background_color(pagina, price_rect, ignore_color=color_orig)
                        origin = ref.get("origin", (x0, y1))
                        baseline = origin[1]

                        # El recuadro que tapa el precio llega SOLO hasta la línea
                        # base (no invade la línea de abajo, ej. "cada uno") y se
                        # recorta un poco arriba (el espacio de ascenso vacío de la
                        # fuente) para no tapar el texto de la línea de arriba.
                        top_trim = (y1 - y0) * 0.13
                        cover_rect = fitz.Rect(x0 - 1, y0 + top_trim, x1 + 1, baseline + 1) & pagina.rect

                        ediciones.append({
                            "cover_rect": cover_rect,
                            "bg": bg,
                            "punto": fitz.Point(x0, baseline),
                            "texto": nuevo_texto,
                            "fontsize": fontsize,
                            "fontname": fontname,
                            "color": color_orig,
                            "ancho": ancho_texto,
                            "tachado": es_tachado,
                        })

            # Paso 1: tapamos TODOS los precios originales primero.
            for e in ediciones:
                pagina.draw_rect(e["cover_rect"], color=e["bg"], fill=e["bg"], width=0)

            # Paso 2: dibujamos los precios nuevos encima, así ningún parche
            # posterior puede cortar un precio ya dibujado.
            for e in ediciones:
                pagina.insert_text(
                    e["punto"],
                    e["texto"],
                    fontsize=e["fontsize"],
                    fontname=e["fontname"],
                    color=e["color"],
                )
                # Si el precio original estaba tachado (P.REG.), redibujamos la
                # línea de tachado sobre el nuevo precio convertido.
                if e["tachado"]:
                    y_strike = e["punto"].y - e["fontsize"] * 0.28
                    x_ini = e["punto"].x
                    x_fin = x_ini + e["ancho"]
                    pagina.draw_line(
                        fitz.Point(x_ini, y_strike),
                        fitz.Point(x_fin, y_strike),
                        color=e["color"],
                        width=max(0.6, e["fontsize"] * 0.06),
                    )

        doc.save(output_path, garbage=4, deflate=True)

        
        return send_file(output_path, as_attachment=True, download_name="catalogo_guaranies.pdf")
        
    except Exception as e:
        print(f"Error procesando PDF: {e}")
        return "Internal server error during conversion", 500
    finally:
        # Cerramos el documento para liberar el archivo antes de borrarlo
        if doc is not None:
            doc.close()
        # Limpieza de archivos temporales
        if os.path.exists(input_path):
            os.remove(input_path)

if __name__ == '__main__':
    # En local: python catalogo_panel_server.py
    # En producción usá gunicorn/Passenger (ver instrucciones de deploy).
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
