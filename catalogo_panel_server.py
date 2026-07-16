
import os
import re
import gc
import uuid
import tempfile
from collections import Counter
from flask import Flask, request, render_template_string, send_file, jsonify
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import fitz  # PyMuPDF

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB max


@app.errorhandler(RequestEntityTooLarge)
def _archivo_muy_grande(_e):
    return ("El PDF supera el tamaño máximo permitido. "
            "Probá comprimirlo o dividirlo en partes."), 413


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
    del pix
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
            <a href="/local" class="text-xs bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-1.5 rounded-full font-medium ml-2">⚡ Versión local (sin subir)</a>
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

        // Lee los primeros bytes del archivo para confirmar que es un PDF real
        function leerCabecera(file) {
            return new Promise((resolve) => {
                const reader = new FileReader();
                reader.onload = () => {
                    const arr = new Uint8Array(reader.result || new ArrayBuffer(0));
                    let s = '';
                    for (let i = 0; i < arr.length; i++) s += String.fromCharCode(arr[i]);
                    resolve(s);
                };
                reader.onerror = () => resolve('');
                reader.readAsArrayBuffer(file.slice(0, 5));
            });
        }

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const file = fileInput.files[0];
            if (!file) { alert('Elegí un archivo PDF primero.'); return; }

            // Validación LOCAL antes de subir (evita subir archivos pesados en vano)
            if (!file.name.toLowerCase().endsWith('.pdf')) {
                alert('El archivo debe tener extensión .pdf');
                return;
            }
            const cabecera = await leerCabecera(file);
            if (!cabecera.startsWith('%PDF')) {
                alert('El archivo no parece un PDF válido (no empieza con "%PDF"). Puede estar dañado.');
                return;
            }

            const formData = new FormData();
            formData.append('file', file);
            formData.append('rate', document.getElementById('rate').value);

            statusContainer.classList.remove('hidden');
            submitBtn.disabled = true;
            submitBtn.classList.add('opacity-50', 'cursor-not-allowed');

            let processInterval = null;

            function resetStatus() {
                if (processInterval) clearInterval(processInterval);
                statusContainer.classList.add('hidden');
                progressBar.style.width = '0%';
                statusPercent.textContent = '0%';
                submitBtn.disabled = false;
                submitBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            }

            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/convert', true);
            xhr.responseType = 'blob';
            xhr.timeout = 600000; // 10 min de margen para catálogos pesados

            // Progreso REAL de subida (0% a 40%)
            xhr.upload.onprogress = function(ev) {
                if (ev.lengthComputable) {
                    const percent = Math.round((ev.loaded / ev.total) * 100);
                    progressBar.style.width = (percent * 0.4) + '%';
                    statusPercent.textContent = Math.round(percent * 0.4) + '%';
                    const mb = (ev.loaded / 1048576).toFixed(1);
                    const totalMb = (ev.total / 1048576).toFixed(1);
                    statusText.textContent = 'Subiendo catálogo... ' + mb + ' / ' + totalMb + ' MB';
                }
            };

            // Terminó la subida: ahora el servidor procesa (40% a 95% simulado)
            xhr.upload.onload = function() {
                progressBar.style.width = '45%';
                statusPercent.textContent = '45%';
                statusText.textContent = 'Procesando precios en el servidor...';
                let processPercent = 45;
                processInterval = setInterval(() => {
                    if (processPercent < 95) {
                        processPercent += 3;
                        progressBar.style.width = processPercent + '%';
                        statusPercent.textContent = processPercent + '%';
                    } else {
                        clearInterval(processInterval);
                    }
                }, 500);
            };

            xhr.onload = async function() {
                if (processInterval) clearInterval(processInterval);
                const ct = xhr.getResponseHeader('Content-Type') || '';
                if (xhr.status === 200 && ct.indexOf('application/pdf') !== -1) {
                    progressBar.style.width = '100%';
                    statusPercent.textContent = '100%';
                    statusText.textContent = '¡Listo! Descargando tu archivo...';
                    const url = window.URL.createObjectURL(xhr.response);
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = 'catalogo_guaranies.pdf';
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    setTimeout(() => window.URL.revokeObjectURL(url), 4000);
                    setTimeout(resetStatus, 3000);
                } else {
                    // Mostramos el mensaje REAL del servidor (no un genérico)
                    let msg = 'No se pudo procesar el archivo.';
                    try { msg = (await xhr.response.text()) || msg; } catch (_) {}
                    if (xhr.status === 413) {
                        msg = 'El PDF es demasiado grande para el servidor. Probá comprimirlo o dividirlo.';
                    } else if (xhr.status === 0) {
                        msg = 'Se cortó la conexión durante la subida. Probá con una conexión más estable.';
                    }
                    alert('Error (' + xhr.status + '): ' + msg);
                    resetStatus();
                }
            };

            xhr.onerror = function() {
                if (processInterval) clearInterval(processInterval);
                alert('Se interrumpió la conexión con el servidor. Revisá tu internet y volvé a intentar.');
                resetStatus();
            };

            xhr.ontimeout = function() {
                if (processInterval) clearInterval(processInterval);
                alert('La operación tardó demasiado y se canceló. Probá con un PDF más liviano o mejor conexión.');
                resetStatus();
            };

            statusText.textContent = 'Subiendo catálogo...';
            xhr.send(formData);
        });
    </script>
</body>
</html>
'''


LOCAL_HTML = r'''
<!DOCTYPE html>
<html lang="es" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Conversor de Catálogos (Local) ARS ➔ PYG</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf-lib/1.17.1/pdf-lib.min.js"></script>
    <style>
        body { background-color: #0f172a; color: #f8fafc; }
    </style>
</head>
<body class="min-h-screen flex flex-col font-sans">
    <header class="border-b border-slate-800 bg-slate-900/50 backdrop-blur py-4 px-6">
        <div class="max-w-3xl mx-auto flex justify-between items-center">
            <div class="flex items-center gap-3">
                <div class="p-2 bg-emerald-600/20 text-emerald-400 rounded-lg">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                </div>
                <div>
                    <h1 class="text-lg font-bold tracking-tight">Conversor Local (en tu navegador)</h1>
                    <p class="text-xs text-slate-400">Procesa el PDF en tu equipo · sin subir nada · sin límites de servidor</p>
                </div>
            </div>
            <a href="/" class="text-xs bg-slate-800 text-slate-300 px-3 py-1.5 rounded-full hover:bg-slate-700">Versión servidor</a>
        </div>
    </header>

    <main class="flex-grow max-w-3xl w-full mx-auto p-6">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
            <div>
                <h2 class="text-xl font-semibold mb-1">Convertí tu catálogo sin subirlo</h2>
                <p class="text-sm text-slate-400">Todo se procesa acá, en tu navegador. Ideal para PDFs pesados.</p>
            </div>

            <div>
                <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Tasa de cambio (1 $ = X Gs.)</label>
                <div class="relative rounded-lg">
                    <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none"><span class="text-slate-500">₲</span></div>
                    <input type="number" step="0.01" id="rate" value="7.80" class="block w-full pl-8 pr-3 py-2.5 bg-slate-950 border border-slate-800 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 text-white font-mono">
                </div>
            </div>

            <div>
                <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Archivo PDF del catálogo</label>
                <div id="dropZone" class="border-2 border-dashed border-slate-800 hover:border-slate-700 bg-slate-950/50 rounded-xl p-8 text-center cursor-pointer transition-colors group">
                    <input type="file" id="fileInput" accept="application/pdf" class="hidden">
                    <div class="space-y-3">
                        <svg class="mx-auto h-10 w-10 text-slate-500 group-hover:text-emerald-400 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"></path></svg>
                        <div class="text-sm text-slate-300"><span class="font-medium text-emerald-500 group-hover:underline">Hacé clic para elegir</span> o arrastrá tu PDF acá</div>
                        <p class="text-xs text-slate-500" id="fileNameDisplay">Solo archivos PDF</p>
                    </div>
                </div>
            </div>

            <button id="submitBtn" class="w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed text-white py-3 px-4 rounded-xl font-medium transition-all flex justify-center items-center gap-2">
                <span id="btnText">Convertir Catálogo</span>
            </button>

            <div id="statusContainer" class="hidden space-y-3">
                <div class="flex justify-between text-xs font-medium">
                    <span id="statusText" class="text-slate-400">Procesando...</span>
                    <span id="statusPercent" class="text-emerald-500 font-mono">0%</span>
                </div>
                <div class="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                    <div id="progressBar" class="bg-emerald-500 h-full w-0 transition-all duration-200"></div>
                </div>
            </div>

            <div id="resultBox" class="hidden bg-emerald-950/40 border border-emerald-700/50 rounded-xl p-4">
                <p id="resultText" class="text-sm text-emerald-300 font-medium mb-3"></p>
                <a id="downloadLink" class="inline-flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded-lg text-sm font-medium cursor-pointer">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
                    Descargar PDF convertido
                </a>
            </div>

            <div id="errorBox" class="hidden bg-red-950/40 border border-red-700/50 rounded-xl p-4 text-sm text-red-300"></div>
        </div>
    </main>

    <script>
        pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
        const { PDFDocument, StandardFonts, rgb } = PDFLib;

        const dropZone = document.getElementById('dropZone');
        const fileInput = document.getElementById('fileInput');
        const fileNameDisplay = document.getElementById('fileNameDisplay');
        const submitBtn = document.getElementById('submitBtn');
        const btnText = document.getElementById('btnText');
        const statusContainer = document.getElementById('statusContainer');
        const statusText = document.getElementById('statusText');
        const statusPercent = document.getElementById('statusPercent');
        const progressBar = document.getElementById('progressBar');
        const resultBox = document.getElementById('resultBox');
        const resultText = document.getElementById('resultText');
        const downloadLink = document.getElementById('downloadLink');
        const errorBox = document.getElementById('errorBox');

        let selectedFile = null;

        ['dragenter','dragover'].forEach(ev => dropZone.addEventListener(ev, e => { e.preventDefault(); dropZone.classList.add('border-emerald-500'); }));
        ['dragleave','drop'].forEach(ev => dropZone.addEventListener(ev, e => { e.preventDefault(); dropZone.classList.remove('border-emerald-500'); }));
        dropZone.addEventListener('drop', e => { if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]); });
        dropZone.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', () => { if (fileInput.files.length) setFile(fileInput.files[0]); });

        function setFile(f) {
            selectedFile = f;
            fileNameDisplay.textContent = f.name;
            fileNameDisplay.classList.add('text-emerald-400');
            resultBox.classList.add('hidden');
            errorBox.classList.add('hidden');
        }

        function setProgress(p, txt) {
            statusContainer.classList.remove('hidden');
            const pct = Math.round(p * 100);
            progressBar.style.width = pct + '%';
            statusPercent.textContent = pct + '%';
            if (txt) statusText.textContent = txt;
        }

        submitBtn.addEventListener('click', async () => {
            if (!selectedFile) { alert('Elegí un archivo PDF primero.'); return; }
            const rate = parseFloat(String(document.getElementById('rate').value).replace(',', '.'));
            if (!rate || rate <= 0) { alert('Ingresá una tasa de cambio válida.'); return; }

            submitBtn.disabled = true;
            btnText.textContent = 'Procesando...';
            resultBox.classList.add('hidden');
            errorBox.classList.add('hidden');
            setProgress(0.01, 'Leyendo el catálogo...');

            try {
                const bytes = new Uint8Array(await selectedFile.arrayBuffer());
                const { outBytes, convertidos } = await convertirLocal(bytes, rate, (p, t) => setProgress(p, t));

                setProgress(1, '¡Listo!');
                const blob = new Blob([outBytes], { type: 'application/pdf' });
                const url = URL.createObjectURL(blob);
                downloadLink.href = url;
                downloadLink.download = (selectedFile.name.replace(/\.pdf$/i, '')) + '_guaranies.pdf';
                resultText.textContent = '¡Listo! ' + convertidos + ' precios convertidos.';
                resultBox.classList.remove('hidden');
            } catch (err) {
                console.error(err);
                errorBox.textContent = 'Error: ' + (err && err.message ? err.message : err);
                errorBox.classList.remove('hidden');
            } finally {
                submitBtn.disabled = false;
                btnText.textContent = 'Convertir Catálogo';
                setTimeout(() => statusContainer.classList.add('hidden'), 1500);
            }
        });

        // ---- Motor de conversión (equivalente al de Python, pero en el navegador) ----

        const PRECIO_RE = /\$\s*([\d.,]+)/g;

        function formatoMiles(n) {
            return n.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
        }

        function agruparLineas(items) {
            const arr = items.filter(it => it.str && it.str.length)
                .sort((a, b) => {
                    const dy = b.transform[5] - a.transform[5];
                    if (Math.abs(dy) > 2) return dy;
                    return a.transform[4] - b.transform[4];
                });
            const lineas = [];
            for (const it of arr) {
                const y = it.transform[5];
                const tol = Math.max(2, (it.height || 10) * 0.5);
                let linea = lineas.find(l => Math.abs(l.y - y) <= tol);
                if (!linea) { linea = { y: y, items: [] }; lineas.push(linea); }
                linea.items.push(it);
            }
            for (const l of lineas) l.items.sort((a, b) => a.transform[4] - b.transform[4]);
            return lineas;
        }

        function rectAcanvas(viewport, x0, y0, x1, y1) {
            const p1 = viewport.convertToViewportPoint(x0, y0);
            const p2 = viewport.convertToViewportPoint(x1, y1);
            return {
                cx0: Math.min(p1[0], p2[0]), cy0: Math.min(p1[1], p2[1]),
                cx1: Math.max(p1[0], p2[0]), cy1: Math.max(p1[1], p2[1]),
            };
        }

        function colorDominante(data, w, h, cx0, cy0, cx1, cy1, ignore) {
            const counts = new Map();
            const x0 = Math.max(0, Math.floor(cx0)), x1 = Math.min(w - 1, Math.ceil(cx1));
            const y0 = Math.max(0, Math.floor(cy0)), y1 = Math.min(h - 1, Math.ceil(cy1));
            for (let y = y0; y <= y1; y++) {
                for (let x = x0; x <= x1; x++) {
                    const i = (y * w + x) * 4;
                    const r = data[i], g = data[i + 1], b = data[i + 2];
                    if (ignore) {
                        const d = Math.abs(r - ignore.r) + Math.abs(g - ignore.g) + Math.abs(b - ignore.b);
                        if (d < 100) continue;
                    }
                    const key = ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3);
                    counts.set(key, (counts.get(key) || 0) + 1);
                }
            }
            if (!counts.size) return null;
            let bestKey = 0, bestCount = -1;
            for (const [k, c] of counts) { if (c > bestCount) { bestCount = c; bestKey = k; } }
            return {
                r: ((bestKey >> 10) & 31) << 3,
                g: ((bestKey >> 5) & 31) << 3,
                b: (bestKey & 31) << 3,
            };
        }

        function asegurarContraste(text, bg) {
            const d = Math.abs(text.r - bg.r) + Math.abs(text.g - bg.g) + Math.abs(text.b - bg.b);
            if (d >= 120) return text;
            const lum = 0.299 * bg.r + 0.587 * bg.g + 0.114 * bg.b;
            return lum > 140 ? { r: 0, g: 0, b: 0 } : { r: 255, g: 255, b: 255 };
        }

        async function convertirLocal(fileBytes, rate, onProgress) {
            // pdf.js "transfiere" (vacía) el buffer al procesarlo, así que le damos
            // una copia a cada librería para que pdf-lib no reciba bytes vacíos.
            const bytesParaLib = fileBytes.slice(0);
            const tarea = pdfjsLib.getDocument({ data: fileBytes.slice(0) });
            const pdfjsDoc = await tarea.promise;
            const pdfDoc = await PDFDocument.load(bytesParaLib, { ignoreEncryption: true });
            const helv = await pdfDoc.embedFont(StandardFonts.Helvetica);
            const helvBold = await pdfDoc.embedFont(StandardFonts.HelveticaBold);
            const paginasLib = pdfDoc.getPages();

            const total = pdfjsDoc.numPages;
            let convertidos = 0;

            for (let n = 1; n <= total; n++) {
                const page = await pdfjsDoc.getPage(n);
                const contenido = await page.getTextContent();
                onProgress(n / total, 'Procesando página ' + n + ' de ' + total + '...');

                if (!contenido.items.some(it => it.str && it.str.includes('$'))) continue;

                // Render de la página para muestrear colores reales.
                const escala = 1.5;
                const viewport = page.getViewport({ scale: escala });
                const canvas = document.createElement('canvas');
                canvas.width = Math.ceil(viewport.width);
                canvas.height = Math.ceil(viewport.height);
                const ctx = canvas.getContext('2d', { willReadFrequently: true });
                // Fondo blanco (como PyMuPDF alpha=False): evita que las zonas sin
                // relleno se muestreen como negro.
                ctx.fillStyle = '#FFFFFF';
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                await page.render({ canvasContext: ctx, viewport: viewport, background: 'rgb(255,255,255)' }).promise;
                const img = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                const W = canvas.width, H = canvas.height;

                const pl = paginasLib[n - 1];
                const lineas = agruparLineas(contenido.items);
                const ediciones = [];

                for (const linea of lineas) {
                    const texto = linea.items.map(i => i.str).join('');
                    if (!texto.includes('$')) continue;

                    const rangos = [];
                    let pos = 0;
                    for (const it of linea.items) { rangos.push([pos, pos + it.str.length, it]); pos += it.str.length; }
                    const esTachado = /P\.?REG/i.test(texto);

                    PRECIO_RE.lastIndex = 0;
                    let m;
                    while ((m = PRECIO_RE.exec(texto)) !== null) {
                        const nums = (m[1].match(/\d+/g) || []).join('');
                        if (!nums) continue;
                        const mStart = m.index, mEnd = m.index + m[0].length;

                        // Calculamos el rectángulo EXACTO del precio dentro de cada
                        // bloque de texto (por proporción de caracteres), así no
                        // tapamos rótulos como "P.REG." u "OFERTA" que estén pegados.
                        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
                        let ref = null;
                        for (const r of rangos) {
                            const a = r[0], b = r[1], it = r[2];
                            if (!(a < mEnd && b > mStart)) continue;
                            const len = Math.max(1, b - a);
                            const localIni = Math.max(a, mStart) - a;
                            const localFin = Math.min(b, mEnd) - a;
                            const iw = it.width;
                            const left = it.transform[4] + (localIni / len) * iw;
                            const right = it.transform[4] + (localFin / len) * iw;
                            const iy = it.transform[5];
                            const fh = Math.hypot(it.transform[2], it.transform[3]) || it.height || 10;
                            x0 = Math.min(x0, left); x1 = Math.max(x1, right);
                            y0 = Math.min(y0, iy - fh * 0.22); y1 = Math.max(y1, iy + fh * 0.82);
                            if (!ref || it.str.includes('$')) ref = it;
                        }
                        if (!ref || !(x1 - x0 > 0.5 && y1 - y0 > 0.5)) continue;

                        const precioArs = parseFloat(nums);
                        const precioPyg = Math.round(precioArs * rate / 100) * 100;
                        const nuevoTexto = 'Gs. ' + formatoMiles(precioPyg);

                        const fh = Math.hypot(ref.transform[2], ref.transform[3]) || ref.height || 10;
                        const esNegrita = /bold|black|heavy/i.test(ref.fontName || '');
                        let font = esNegrita ? helvBold : helv;
                        let fontSize = fh;
                        const anchoOrig = x1 - x0;
                        let ancho = font.widthOfTextAtSize(nuevoTexto, fontSize);
                        if (ancho > anchoOrig && ancho > 0) { fontSize *= anchoOrig / ancho; ancho = font.widthOfTextAtSize(nuevoTexto, fontSize); }

                        // Colores desde el canvas.
                        const c = rectAcanvas(viewport, x0, y0, x1, y1);
                        const pad = Math.round(4 * escala);
                        const bg = colorDominante(img, W, H, c.cx0 - pad, c.cy0 - pad, c.cx1 + pad, c.cy1 + pad, null) || { r: 255, g: 255, b: 255 };
                        let textColor = colorDominante(img, W, H, c.cx0, c.cy0, c.cx1, c.cy1, bg) || { r: 20, g: 20, b: 20 };
                        textColor = asegurarContraste(textColor, bg);

                        ediciones.push({
                            x0: x0, y0: y0, x1: x1, y1: y1,
                            bg: bg, textColor: textColor,
                            nuevoTexto: nuevoTexto, font: font, fontSize: fontSize,
                            baseline: ref.transform[5], ancho: ancho, esTachado: esTachado,
                        });
                        convertidos++;
                    }
                }

                // Paso 1: tapamos TODOS los precios originales primero (pdf-lib usa
                // origen abajo-izquierda, Y hacia arriba).
                for (const e of ediciones) {
                    pl.drawRectangle({
                        x: e.x0 - 1, y: e.y0, width: (e.x1 - e.x0) + 2, height: (e.y1 - e.y0),
                        color: rgb(e.bg.r / 255, e.bg.g / 255, e.bg.b / 255),
                    });
                }
                // Paso 2: dibujamos los precios nuevos encima (y el tachado).
                for (const e of ediciones) {
                    pl.drawText(e.nuevoTexto, {
                        x: e.x0, y: e.baseline, size: e.fontSize, font: e.font,
                        color: rgb(e.textColor.r / 255, e.textColor.g / 255, e.textColor.b / 255),
                    });
                    if (e.esTachado) {
                        const yStrike = e.baseline + e.fontSize * 0.28;
                        pl.drawLine({
                            start: { x: e.x0, y: yStrike }, end: { x: e.x0 + e.ancho, y: yStrike },
                            thickness: Math.max(0.6, e.fontSize * 0.06),
                            color: rgb(e.textColor.r / 255, e.textColor.g / 255, e.textColor.b / 255),
                        });
                    }
                }

                canvas.width = 0; canvas.height = 0;
                page.cleanup();
                await new Promise(r => setTimeout(r, 0));
            }

            const outBytes = await pdfDoc.save();
            return { outBytes: outBytes, convertidos: convertidos };
        }
    </script>
</body>
</html>
'''




@app.route('/local')
def local():
    return render_template_string(LOCAL_HTML)

@app.route('/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return "No se recibió ningún archivo.", 400

    file = request.files['file']
    if not file or file.filename == '':
        return "No se seleccionó ningún archivo.", 400

    # Nombre seguro (evita path traversal) y validación de extensión.
    nombre_seguro = secure_filename(file.filename) or "catalogo.pdf"
    if not nombre_seguro.lower().endswith('.pdf'):
        return "El archivo debe ser un PDF (.pdf).", 400

    # Tasa de cambio robusta (acepta coma o punto).
    try:
        rate_val = float(str(request.form.get('rate', '7.80')).replace(',', '.'))
        if rate_val <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return "La tasa de cambio ingresada no es válida.", 400

    # Nombres únicos: evita colisiones si entran dos subidas a la vez.
    unico = uuid.uuid4().hex
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], f"in_{unico}.pdf")
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"out_{unico}.pdf")

    file.save(input_path)

    # Validamos que el archivo recibido sea realmente un PDF. Si la subida se
    # cortó a mitad (conexión inestable), el archivo llega incompleto/corrupto
    # y lo detectamos acá con un mensaje claro en lugar de un error genérico.
    try:
        tam = os.path.getsize(input_path)
        with open(input_path, 'rb') as fh:
            cabecera = fh.read(5)
        if tam == 0 or not cabecera.startswith(b'%PDF'):
            os.remove(input_path)
            return ("La subida se cortó o el archivo no es un PDF válido. "
                    "Volvé a intentarlo con una conexión estable."), 400
    except OSError:
        return "No se pudo leer el archivo subido. Intentá de nuevo.", 400

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

            # Liberamos la memoria de la página antes de pasar a la siguiente
            # (clave en el plan free de 512 MB con catálogos pesados).
            data = None
            ediciones = None
            gc.collect()

        doc.save(output_path, garbage=1, deflate=True)

        
        return send_file(output_path, as_attachment=True, download_name="catalogo_guaranies.pdf")
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error procesando PDF: {e}")
        return f"Error procesando PDF: {e}", 500
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
