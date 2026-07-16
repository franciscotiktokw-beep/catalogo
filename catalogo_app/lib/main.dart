import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:path_provider/path_provider.dart';
import 'package:pdfx/pdfx.dart';
import 'package:share_plus/share_plus.dart';

import 'pdf_converter.dart';

void main() {
  runApp(const CatalogoApp());
}

class CatalogoApp extends StatelessWidget {
  const CatalogoApp({super.key});

  @override
  Widget build(BuildContext context) {
    const Color primary = Color(0xFF3B82F6);
    final ColorScheme scheme = ColorScheme.fromSeed(
      seedColor: primary,
      brightness: Brightness.dark,
    ).copyWith(surface: const Color(0xFF0F172A));

    return MaterialApp(
      title: 'Conversor de Catálogos',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: scheme,
        scaffoldBackgroundColor: const Color(0xFF020617),
        textTheme: GoogleFonts.interTextTheme(ThemeData.dark().textTheme),
      ),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

enum _Estado { inicial, procesando, listo, error }

class _HomePageState extends State<HomePage> {
  final TextEditingController _rateController =
      TextEditingController(text: '7.80');

  String? _fileName;
  String? _filePath;

  _Estado _estado = _Estado.inicial;
  double _progress = 0;
  String _mensaje = '';
  Uint8List? _resultBytes;
  int _preciosConvertidos = 0;

  @override
  void dispose() {
    _rateController.dispose();
    super.dispose();
  }

  Future<void> _elegirArchivo() async {
    final FilePickerResult? result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['pdf'],
    );
    if (result == null || result.files.isEmpty) return;
    setState(() {
      _fileName = result.files.single.name;
      _filePath = result.files.single.path;
      _estado = _Estado.inicial;
      _resultBytes = null;
    });
  }

  Future<void> _convertir() async {
    if (_filePath == null) return;
    final double? rate =
        double.tryParse(_rateController.text.replaceAll(',', '.'));
    if (rate == null || rate <= 0) {
      _mostrarError('Ingresá una tasa de cambio válida.');
      return;
    }

    setState(() {
      _estado = _Estado.procesando;
      _progress = 0;
      _mensaje = 'Leyendo el catálogo...';
    });

    try {
      final Uint8List bytes = await File(_filePath!).readAsBytes();

      if (mounted) setState(() => _mensaje = 'Convirtiendo precios...');

      final PriceConverter converter = PriceConverter(rate);
      final ConversionResult result = await converter.convert(
        bytes,
        onProgress: (p) {
          if (mounted) setState(() => _progress = p);
        },
      );

      if (!mounted) return;
      setState(() {
        _estado = _Estado.listo;
        _resultBytes = result.bytes;
        _preciosConvertidos = result.preciosConvertidos;
        _mensaje = '';
      });
      _abrirVistaPrevia();
    } catch (e) {
      _mostrarError('Error al convertir: $e');
    }
  }

  void _abrirVistaPrevia() {
    if (_resultBytes == null) return;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => PreviewPage(
          bytes: _resultBytes!,
          fileName: _fileName ?? 'catalogo.pdf',
          precios: _preciosConvertidos,
        ),
      ),
    );
  }

  void _mostrarError(String msg) {
    if (!mounted) return;
    setState(() {
      _estado = _Estado.error;
      _mensaje = msg;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(20),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 480),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const SizedBox(height: 12),
                  _header(),
                  const SizedBox(height: 28),
                  _card(),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _header() {
    return Column(
      children: [
        Container(
          width: 64,
          height: 64,
          decoration: BoxDecoration(
            gradient: const LinearGradient(
              colors: [Color(0xFF3B82F6), Color(0xFF8B5CF6)],
            ),
            borderRadius: BorderRadius.circular(18),
            boxShadow: [
              BoxShadow(
                color: const Color(0xFF3B82F6).withValues(alpha: 0.4),
                blurRadius: 24,
                offset: const Offset(0, 8),
              ),
            ],
          ),
          child: const Icon(Icons.currency_exchange,
              color: Colors.white, size: 32),
        ),
        const SizedBox(height: 16),
        Text(
          'Conversor de Catálogos',
          textAlign: TextAlign.center,
          style: GoogleFonts.inter(
            fontSize: 24,
            fontWeight: FontWeight.w800,
            color: Colors.white,
          ),
        ),
        const SizedBox(height: 6),
        Text(
          'Convertí los precios en pesos (\$) a Guaraníes\ndirecto en tu teléfono, sin subir nada.',
          textAlign: TextAlign.center,
          style: GoogleFonts.inter(
            fontSize: 13,
            color: Colors.white.withValues(alpha: 0.55),
            height: 1.4,
          ),
        ),
      ],
    );
  }

  Widget _card() {
    return Container(
      padding: const EdgeInsets.all(22),
      decoration: BoxDecoration(
        color: const Color(0xFF0F172A),
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: Colors.white.withValues(alpha: 0.06)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _label('TASA DE CAMBIO (1 \$ = X Gs.)'),
          const SizedBox(height: 8),
          _rateField(),
          const SizedBox(height: 20),
          _label('ARCHIVO PDF DEL CATÁLOGO'),
          const SizedBox(height: 8),
          _dropZone(),
          const SizedBox(height: 22),
          _accionPrincipal(),
          if (_estado == _Estado.procesando) ...[
            const SizedBox(height: 18),
            _progresoWidget(),
          ],
          if (_estado == _Estado.listo) ...[
            const SizedBox(height: 18),
            _resultadoWidget(),
          ],
          if (_estado == _Estado.error) ...[
            const SizedBox(height: 16),
            _errorWidget(),
          ],
        ],
      ),
    );
  }

  Widget _label(String text) {
    return Text(
      text,
      style: GoogleFonts.inter(
        fontSize: 11,
        fontWeight: FontWeight.w600,
        letterSpacing: 0.5,
        color: Colors.white.withValues(alpha: 0.5),
      ),
    );
  }

  Widget _rateField() {
    return TextField(
      controller: _rateController,
      keyboardType: const TextInputType.numberWithOptions(decimal: true),
      style: GoogleFonts.robotoMono(
        fontSize: 18,
        fontWeight: FontWeight.w600,
        color: Colors.white,
      ),
      decoration: InputDecoration(
        prefixIcon: const Icon(Icons.attach_money, color: Color(0xFF64748B)),
        filled: true,
        fillColor: const Color(0xFF020617),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: Colors.white.withValues(alpha: 0.08)),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: Color(0xFF3B82F6), width: 2),
        ),
      ),
    );
  }

  Widget _dropZone() {
    final bool hasFile = _fileName != null;
    return InkWell(
      onTap: _estado == _Estado.procesando ? null : _elegirArchivo,
      borderRadius: BorderRadius.circular(14),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 26, horizontal: 16),
        decoration: BoxDecoration(
          color: const Color(0xFF020617),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(
            color: hasFile
                ? const Color(0xFF3B82F6).withValues(alpha: 0.6)
                : Colors.white.withValues(alpha: 0.12),
            width: 1.5,
          ),
        ),
        child: Column(
          children: [
            Icon(
              hasFile ? Icons.picture_as_pdf : Icons.cloud_upload_outlined,
              color: hasFile ? const Color(0xFF3B82F6) : const Color(0xFF64748B),
              size: 34,
            ),
            const SizedBox(height: 10),
            Text(
              hasFile ? _fileName! : 'Tocá para elegir tu PDF',
              textAlign: TextAlign.center,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: GoogleFonts.inter(
                fontSize: 13,
                fontWeight: hasFile ? FontWeight.w600 : FontWeight.w400,
                color: hasFile
                    ? const Color(0xFF60A5FA)
                    : Colors.white.withValues(alpha: 0.6),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _accionPrincipal() {
    final bool habilitado = _fileName != null && _estado != _Estado.procesando;
    return SizedBox(
      height: 52,
      child: ElevatedButton(
        onPressed: habilitado ? _convertir : null,
        style: ElevatedButton.styleFrom(
          backgroundColor: const Color(0xFF3B82F6),
          disabledBackgroundColor: const Color(0xFF1E293B),
          foregroundColor: Colors.white,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
          ),
          elevation: 0,
        ),
        child: _estado == _Estado.procesando
            ? const SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(
                    strokeWidth: 2.5, color: Colors.white),
              )
            : Text(
                'Procesar Catálogo',
                style: GoogleFonts.inter(
                    fontSize: 15, fontWeight: FontWeight.w700),
              ),
      ),
    );
  }

  Widget _progresoWidget() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          _mensaje,
          style: GoogleFonts.inter(
              fontSize: 12, color: Colors.white.withValues(alpha: 0.6)),
        ),
        const SizedBox(height: 8),
        ClipRRect(
          borderRadius: BorderRadius.circular(8),
          child: LinearProgressIndicator(
            value: _progress == 0 ? null : _progress,
            minHeight: 8,
            backgroundColor: const Color(0xFF1E293B),
            valueColor:
                const AlwaysStoppedAnimation<Color>(Color(0xFF3B82F6)),
          ),
        ),
        const SizedBox(height: 6),
        Text(
          '${(_progress * 100).toStringAsFixed(0)}%',
          textAlign: TextAlign.right,
          style: GoogleFonts.robotoMono(
              fontSize: 11, color: const Color(0xFF60A5FA)),
        ),
      ],
    );
  }

  Widget _resultadoWidget() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFF052E1B),
        borderRadius: BorderRadius.circular(14),
        border:
            Border.all(color: const Color(0xFF10B981).withValues(alpha: 0.4)),
      ),
      child: Column(
        children: [
          Row(
            children: [
              const Icon(Icons.check_circle, color: Color(0xFF10B981)),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  '¡Listo! $_preciosConvertidos precios convertidos.',
                  style: GoogleFonts.inter(
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                      color: Colors.white),
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          SizedBox(
            height: 48,
            width: double.infinity,
            child: ElevatedButton.icon(
              onPressed: _abrirVistaPrevia,
              style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFF10B981),
                foregroundColor: Colors.white,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
                elevation: 0,
              ),
              icon: const Icon(Icons.visibility, size: 18),
              label: Text(
                'Ver PDF convertido',
                style: GoogleFonts.inter(
                    fontSize: 14, fontWeight: FontWeight.w700),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _errorWidget() {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF450A0A),
        borderRadius: BorderRadius.circular(12),
        border:
            Border.all(color: const Color(0xFFEF4444).withValues(alpha: 0.4)),
      ),
      child: Row(
        children: [
          const Icon(Icons.error_outline, color: Color(0xFFEF4444), size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              _mensaje,
              style: GoogleFonts.inter(
                  fontSize: 12, color: Colors.white.withValues(alpha: 0.85)),
            ),
          ),
        ],
      ),
    );
  }
}

/// Pantalla que muestra el PDF convertido y permite compartirlo o guardarlo.
class PreviewPage extends StatefulWidget {
  const PreviewPage({
    super.key,
    required this.bytes,
    required this.fileName,
    required this.precios,
  });

  final Uint8List bytes;
  final String fileName;
  final int precios;

  @override
  State<PreviewPage> createState() => _PreviewPageState();
}

class _PreviewPageState extends State<PreviewPage> {
  late final PdfControllerPinch _controller;
  bool _ocupado = false;

  String get _nombreSalida {
    final String base = widget.fileName.replaceAll(
        RegExp(r'\.pdf$', caseSensitive: false), '');
    return '${base}_guaranies.pdf';
  }

  @override
  void initState() {
    super.initState();
    _controller = PdfControllerPinch(
      document: PdfDocument.openData(widget.bytes),
    );
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<File> _escribirTemporal() async {
    final Directory dir = await getTemporaryDirectory();
    final File out = File('${dir.path}/$_nombreSalida');
    await out.writeAsBytes(widget.bytes, flush: true);
    return out;
  }

  Future<void> _compartir() async {
    if (_ocupado) return;
    setState(() => _ocupado = true);
    try {
      final File out = await _escribirTemporal();
      await Share.shareXFiles(
        [XFile(out.path)],
        text: 'Catálogo convertido a Guaraníes',
      );
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Future<void> _guardar() async {
    if (_ocupado) return;
    setState(() => _ocupado = true);
    try {
      final String? path = await FilePicker.platform.saveFile(
        dialogTitle: 'Guardar catálogo convertido',
        fileName: _nombreSalida,
        type: FileType.custom,
        allowedExtensions: const ['pdf'],
        bytes: widget.bytes,
      );
      if (!mounted) return;
      if (path != null) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('PDF guardado correctamente.')),
        );
      }
    } catch (_) {
      // Si el diálogo de guardado no está disponible, ofrecemos compartir.
      await _compartir();
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF020617),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0F172A),
        foregroundColor: Colors.white,
        title: Text(
          'Vista previa',
          style: GoogleFonts.inter(fontSize: 16, fontWeight: FontWeight.w700),
        ),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(28),
          child: Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(
              '${widget.precios} precios convertidos',
              style: GoogleFonts.inter(
                fontSize: 12,
                color: const Color(0xFF34D399),
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ),
      ),
      body: Container(
        color: const Color(0xFF0B1220),
        child: PdfViewPinch(
          controller: _controller,
          padding: 12,
        ),
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            children: [
              Expanded(
                child: SizedBox(
                  height: 50,
                  child: OutlinedButton.icon(
                    onPressed: _ocupado ? null : _compartir,
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Colors.white,
                      side: BorderSide(
                          color: Colors.white.withValues(alpha: 0.2)),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12),
                      ),
                    ),
                    icon: const Icon(Icons.ios_share, size: 18),
                    label: Text(
                      'Compartir',
                      style: GoogleFonts.inter(
                          fontSize: 14, fontWeight: FontWeight.w700),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: SizedBox(
                  height: 50,
                  child: ElevatedButton.icon(
                    onPressed: _ocupado ? null : _guardar,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFF10B981),
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12),
                      ),
                      elevation: 0,
                    ),
                    icon: const Icon(Icons.download, size: 18),
                    label: Text(
                      'Guardar',
                      style: GoogleFonts.inter(
                          fontSize: 14, fontWeight: FontWeight.w700),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
