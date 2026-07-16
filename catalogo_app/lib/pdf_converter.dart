import 'dart:isolate';
import 'dart:typed_data';
import 'dart:ui';

import 'package:image/image.dart' as img;
import 'package:pdfx/pdfx.dart' as pdfx;
import 'package:syncfusion_flutter_pdf/pdf.dart';

/// Resultado de una conversión de catálogo.
class ConversionResult {
  final Uint8List bytes;
  final int preciosConvertidos;
  const ConversionResult(this.bytes, this.preciosConvertidos);
}

/// Convierte los precios en "$" (ARS) de un catálogo PDF a Guaraníes (PYG),
/// tapando el precio original con el color de fondo real y redibujando el
/// nuevo precio con el mismo estilo. Todo se procesa localmente.
///
/// El trabajo pesado (decodificar imágenes, muestrear colores, dibujar y
/// guardar el PDF) se ejecuta en un *isolate* en segundo plano para que la
/// interfaz nunca se congele. Solo la rasterización de páginas (nativa) se
/// hace en el isolate principal.
class PriceConverter {
  PriceConverter(this.rate);

  /// Tasa de cambio: 1 ARS = [rate] PYG.
  final double rate;

  /// DPI al que se rasteriza cada página para muestrear colores.
  static const double _dpi = 96.0;

  Future<ConversionResult> convert(
    Uint8List inputBytes, {
    void Function(double progress)? onProgress,
  }) async {
    onProgress?.call(0.02);

    // 1. Detectamos qué páginas tienen precios (extracción liviana en main,
    //    cediendo el hilo entre páginas para que el loader siga girando).
    final PdfDocument scanDoc = PdfDocument(inputBytes: inputBytes);
    final PdfTextExtractor scanner = PdfTextExtractor(scanDoc);
    final int pageCount = scanDoc.pages.count;
    final List<int> pricePages = <int>[];
    for (int p = 0; p < pageCount; p++) {
      final List<TextLine> lines =
          scanner.extractTextLines(startPageIndex: p, endPageIndex: p);
      if (lines.any((l) => l.text.contains(r'$'))) pricePages.add(p);
      if (p % 2 == 0) await Future<void>.delayed(Duration.zero);
    }
    scanDoc.dispose();

    if (pricePages.isEmpty) {
      onProgress?.call(1.0);
      return ConversionResult(inputBytes, 0);
    }

    // 2. Rasterizamos solo las páginas con precios (nativo, isolate principal).
    final pdfx.PdfDocument rasterDoc =
        await pdfx.PdfDocument.openData(inputBytes);
    final Map<int, Uint8List> rasters = <int, Uint8List>{};
    try {
      for (int i = 0; i < pricePages.length; i++) {
        final int p = pricePages[i];
        final Uint8List? png = await _renderPagePng(rasterDoc, p + 1);
        if (png != null) rasters[p] = png;
        onProgress?.call(0.05 + (i + 1) / pricePages.length * 0.6);
        await Future<void>.delayed(Duration.zero);
      }
    } finally {
      await rasterDoc.close();
    }

    onProgress?.call(0.7);

    // 3. Procesamiento pesado en un isolate en segundo plano.
    final double rate = this.rate;
    final _ProcessResult result = await Isolate.run(
      () => _processCatalog(inputBytes, rate, rasters, _dpi),
    );

    onProgress?.call(1.0);
    return ConversionResult(result.bytes, result.convertidos);
  }

  Future<Uint8List?> _renderPagePng(
      pdfx.PdfDocument doc, int pageNumber) async {
    final pdfx.PdfPage page = await doc.getPage(pageNumber);
    try {
      final double scale = _dpi / 72.0;
      final pdfx.PdfPageImage? image = await page.render(
        width: page.width * scale,
        height: page.height * scale,
        format: pdfx.PdfPageImageFormat.png,
        backgroundColor: '#FFFFFF',
      );
      return image?.bytes;
    } finally {
      await page.close();
    }
  }
}

/// Resultado interno que devuelve el isolate.
class _ProcessResult {
  final Uint8List bytes;
  final int convertidos;
  const _ProcessResult(this.bytes, this.convertidos);
}

// ---------------------------------------------------------------------------
// Todo lo que sigue es Dart puro y se ejecuta dentro del isolate en segundo
// plano (o es invocado desde él). No debe tocar canales de plataforma.
// ---------------------------------------------------------------------------

final RegExp _precioRegExp = RegExp(r'\$\s*([\d.,]+)');
final RegExp _digitosRegExp = RegExp(r'\d+');
int _debugCount = 0;

Future<_ProcessResult> _processCatalog(
  Uint8List inputBytes,
  double rate,
  Map<int, Uint8List> rastersPng,
  double dpi,
) async {
  final double scale = dpi / 72.0;
  final PdfDocument document = PdfDocument(inputBytes: inputBytes);
  final PdfTextExtractor extractor = PdfTextExtractor(document);

  // Decodificamos los rasters una sola vez.
  final Map<int, img.Image> rasters = <int, img.Image>{};
  rastersPng.forEach((page, png) {
    final img.Image? decoded = img.decodePng(png);
    if (decoded != null) rasters[page] = decoded;
  });

  int convertidos = 0;
  try {
    for (final int p in rastersPng.keys) {
      final PdfPage page = document.pages[p];
      final img.Image? raster = rasters[p];
      final List<TextLine> lines =
          extractor.extractTextLines(startPageIndex: p, endPageIndex: p);
      for (final TextLine line in lines) {
        if (!line.text.contains(r'$')) continue;
        convertidos += _procesarLinea(page, line, raster, rate, scale);
      }
    }

    final List<int> saved = await document.save();
    return _ProcessResult(Uint8List.fromList(saved), convertidos);
  } finally {
    document.dispose();
  }
}

/// Procesa una línea de texto y convierte cada precio que encuentre.
/// Devuelve la cantidad de precios convertidos.
int _procesarLinea(
  PdfPage page,
  TextLine line,
  img.Image? raster,
  double rate,
  double scale,
) {
  final String texto = line.text;
  if (!texto.contains(r'$')) return 0;

  // Mapeamos cada palabra a su rango de caracteres dentro de la línea, para
  // poder ubicar el precio en sus palabras exactas y usar sus bounds reales.
  final List<_WordSpan> wordSpans = <_WordSpan>[];
  int cursor = 0;
  for (final TextWord word in line.wordCollection) {
    final String wt = word.text;
    if (wt.isEmpty) continue;
    int idx = texto.indexOf(wt, cursor);
    if (idx < 0) idx = cursor;
    wordSpans.add(_WordSpan(idx, idx + wt.length, word));
    cursor = idx + wt.length;
  }

  final Rect lineBounds = line.bounds;
  final bool esTachado = texto.toUpperCase().contains('P.REG') ||
      texto.toUpperCase().contains('PREG');

  int convertidos = 0;

  for (final Match match in _precioRegExp.allMatches(texto)) {
    final String numeros = _digitosRegExp
        .allMatches(match.group(1) ?? '')
        .map((m) => m.group(0))
        .join();
    if (numeros.isEmpty) continue;

    final int mStart = match.start;
    final int mEnd = match.end;

    final List<_WordSpan> priceWords =
        wordSpans.where((w) => w.start < mEnd && w.end > mStart).toList();

    final double precioArs = double.parse(numeros);
    final double precioPyg = _redondearCentena(precioArs * rate);
    final String nuevoTexto = 'Gs. ${_formatoMiles(precioPyg)}';

    // Rectángulo del precio original: unión de los bounds de sus palabras.
    double x0, y0, x1, y1;
    double fontSizeOrig;
    bool bold;

    if (priceWords.isNotEmpty &&
        priceWords.first.word.bounds.width > 0.5 &&
        priceWords.first.word.bounds.height > 0.5) {
      Rect r = priceWords.first.word.bounds;
      for (final w in priceWords) {
        if (w.word.bounds.width > 0 && w.word.bounds.height > 0) {
          r = r.expandToInclude(w.word.bounds);
        }
      }
      x0 = r.left;
      y0 = r.top;
      x1 = r.right;
      y1 = r.bottom;
      final TextWord fw = priceWords.first.word;
      fontSizeOrig = fw.fontSize > 0 ? fw.fontSize : line.fontSize;
      bold = _esNegrita(fw);
    } else {
      // Fallback: los bounds por palabra no son fiables en este PDF. Estimamos
      // la posición del precio de forma proporcional dentro de la línea.
      final int len = texto.isEmpty ? 1 : texto.length;
      final double frStart = mStart / len;
      final double frEnd = mEnd / len;
      x0 = lineBounds.left + frStart * lineBounds.width;
      x1 = lineBounds.left + frEnd * lineBounds.width;
      y0 = lineBounds.top;
      y1 = lineBounds.bottom;
      fontSizeOrig = line.fontSize > 0 ? line.fontSize : (y1 - y0);
      bold = priceWords.isNotEmpty && _esNegrita(priceWords.first.word);
    }

    if (x1 - x0 < 1 || y1 - y0 < 1) continue;

    if (_debugCount < 5) {
      _debugCount++;
      final Size ps = page.size;
      final bool usoFallback = !(priceWords.isNotEmpty &&
          priceWords.first.word.bounds.width > 0.5 &&
          priceWords.first.word.bounds.height > 0.5);
      // ignore: avoid_print
      print('[CONV] precio="${match.group(0)}" fallback=$usoFallback '
          'rect=(${x0.toStringAsFixed(1)},${y0.toStringAsFixed(1)})-'
          '(${x1.toStringAsFixed(1)},${y1.toStringAsFixed(1)}) '
          'pageSize=${ps.width.toStringAsFixed(0)}x${ps.height.toStringAsFixed(0)}');
    }

    // Muestreamos colores desde el raster (si está disponible).
    final _Rgb bg = _muestrearFondo(raster, x0, y0, x1, y1, scale);
    _Rgb textColor = _muestrearTexto(raster, x0, y0, x1, y1, bg, scale);
    // Garantizamos que el texto nuevo SIEMPRE contraste con el fondo, para
    // que nunca quede invisible (texto claro sobre fondo claro).
    textColor = _asegurarContraste(textColor, bg);

    // Tapamos el precio original con el color de fondo.
    final double anchoOriginal = x1 - x0;
    page.graphics.drawRectangle(
      brush: PdfSolidBrush(PdfColor(bg.r, bg.g, bg.b)),
      bounds: Rect.fromLTRB(x0 - 1, y0 - 1, x1 + 1, y1 + 1),
    );

    // Ajustamos el tamaño de fuente para que entre en el ancho original.
    double fontSize = fontSizeOrig > 0 ? fontSizeOrig : (y1 - y0);
    PdfStandardFont font = _crearFuente(fontSize, bold);
    double ancho = font.measureString(nuevoTexto).width;
    if (ancho > anchoOriginal && ancho > 0) {
      fontSize = fontSize * (anchoOriginal / ancho);
      font = _crearFuente(fontSize, bold);
      ancho = font.measureString(nuevoTexto).width;
    }

    // Dibujamos el nuevo precio alineado al tope del original.
    page.graphics.drawString(
      nuevoTexto,
      font,
      brush: PdfSolidBrush(PdfColor(textColor.r, textColor.g, textColor.b)),
      bounds: Rect.fromLTWH(x0, y0, anchoOriginal + 2, (y1 - y0) + 4),
      format: PdfStringFormat(
        alignment: PdfTextAlignment.left,
        lineAlignment: PdfVerticalAlignment.middle,
      ),
    );

    // Si el precio original estaba tachado (P.REG.), redibujamos la línea.
    if (esTachado) {
      final double yMid = (y0 + y1) / 2;
      page.graphics.drawLine(
        PdfPen(
          PdfColor(textColor.r, textColor.g, textColor.b),
          width: (fontSize * 0.06).clamp(0.6, 4.0),
        ),
        Offset(x0, yMid),
        Offset(x0 + ancho, yMid),
      );
    }

    convertidos++;
  }

  return convertidos;
}

PdfStandardFont _crearFuente(double size, bool bold) {
  return PdfStandardFont(
    PdfFontFamily.helvetica,
    size <= 0 ? 8 : size,
    style: bold ? PdfFontStyle.bold : PdfFontStyle.regular,
  );
}

bool _esNegrita(TextWord word) {
  final String name = word.fontName.toLowerCase();
  if (name.contains('bold')) return true;
  return word.fontStyle.contains(PdfFontStyle.bold);
}

double _redondearCentena(double valor) {
  return (valor / 100).round() * 100.0;
}

String _formatoMiles(double valor) {
  final String entero = valor.toStringAsFixed(0);
  final StringBuffer sb = StringBuffer();
  final int len = entero.length;
  for (int i = 0; i < len; i++) {
    if (i > 0 && (len - i) % 3 == 0) sb.write('.');
    sb.write(entero[i]);
  }
  return sb.toString();
}

/// Si el color del texto quedó demasiado parecido al fondo, lo forzamos a
/// negro o blanco según la luminancia del fondo para garantizar visibilidad.
_Rgb _asegurarContraste(_Rgb text, _Rgb bg) {
  final int dist =
      (text.r - bg.r).abs() + (text.g - bg.g).abs() + (text.b - bg.b).abs();
  if (dist >= 120) return text;
  final double lum = 0.299 * bg.r + 0.587 * bg.g + 0.114 * bg.b;
  return lum > 140 ? const _Rgb(0, 0, 0) : const _Rgb(255, 255, 255);
}

/// Muestrea el color de fondo en las bandas por encima y por debajo del
/// precio (zonas limpias, sin dígitos ni tachado).
_Rgb _muestrearFondo(img.Image? raster, double x0, double y0, double x1,
    double y1, double scale) {
  if (raster == null) return const _Rgb(255, 255, 255);

  final int ix0 = (x0 * scale).floor().clamp(0, raster.width - 1);
  final int ix1 = (x1 * scale).ceil().clamp(0, raster.width - 1);
  final int iy0 = (y0 * scale).floor().clamp(0, raster.height - 1);
  final int iy1 = (y1 * scale).ceil().clamp(0, raster.height - 1);
  final int pad = (5 * scale).round();

  final Map<int, int> counts = <int, int>{};

  void band(int yStart, int yEnd) {
    for (int y = yStart; y <= yEnd; y++) {
      if (y < 0 || y >= raster.height) continue;
      for (int x = ix0; x <= ix1; x++) {
        final img.Pixel px = raster.getPixel(x, y);
        final int key = _quant(px.r.toInt(), px.g.toInt(), px.b.toInt());
        counts[key] = (counts[key] ?? 0) + 1;
      }
    }
  }

  band(iy0 - pad, iy0 - 1); // banda superior
  band(iy1 + 1, iy1 + pad); // banda inferior

  if (counts.isEmpty) return const _Rgb(255, 255, 255);
  return _rgbFromKey(_modeKey(counts));
}

/// Muestrea el color del texto: el color más frecuente dentro del precio
/// que sea distinto del fondo.
_Rgb _muestrearTexto(img.Image? raster, double x0, double y0, double x1,
    double y1, _Rgb bg, double scale) {
  if (raster == null) return const _Rgb(20, 20, 20);

  final int ix0 = (x0 * scale).floor().clamp(0, raster.width - 1);
  final int ix1 = (x1 * scale).ceil().clamp(0, raster.width - 1);
  final int iy0 = (y0 * scale).floor().clamp(0, raster.height - 1);
  final int iy1 = (y1 * scale).ceil().clamp(0, raster.height - 1);

  final Map<int, int> counts = <int, int>{};
  for (int y = iy0; y <= iy1; y++) {
    for (int x = ix0; x <= ix1; x++) {
      final img.Pixel px = raster.getPixel(x, y);
      final int r = px.r.toInt(), g = px.g.toInt(), b = px.b.toInt();
      final int dist = (r - bg.r).abs() + (g - bg.g).abs() + (b - bg.b).abs();
      if (dist < 90) continue; // parecido al fondo -> ignorar
      counts[_quant(r, g, b)] = (counts[_quant(r, g, b)] ?? 0) + 1;
    }
  }

  if (counts.isEmpty) return const _Rgb(20, 20, 20);
  return _rgbFromKey(_modeKey(counts));
}

int _quant(int r, int g, int b) {
  // Reducimos ruido de anti-aliasing agrupando a múltiplos de 8.
  final int qr = (r ~/ 8) * 8;
  final int qg = (g ~/ 8) * 8;
  final int qb = (b ~/ 8) * 8;
  return (qr << 16) | (qg << 8) | qb;
}

int _modeKey(Map<int, int> counts) {
  int bestKey = counts.keys.first;
  int bestCount = -1;
  counts.forEach((k, v) {
    if (v > bestCount) {
      bestCount = v;
      bestKey = k;
    }
  });
  return bestKey;
}

_Rgb _rgbFromKey(int key) {
  return _Rgb((key >> 16) & 0xFF, (key >> 8) & 0xFF, key & 0xFF);
}

class _WordSpan {
  const _WordSpan(this.start, this.end, this.word);
  final int start;
  final int end;
  final TextWord word;
}

class _Rgb {
  const _Rgb(this.r, this.g, this.b);
  final int r;
  final int g;
  final int b;
}
