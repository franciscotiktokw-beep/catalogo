// Test básico de humo: verifica que la app arranca y muestra su UI.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:catalogo_guarani/main.dart';

void main() {
  testWidgets('La app arranca correctamente', (WidgetTester tester) async {
    await tester.pumpWidget(const CatalogoApp());
    await tester.pump();

    // La app debe construirse sin errores y mostrar un MaterialApp.
    expect(find.byType(MaterialApp), findsOneWidget);
  });
}
