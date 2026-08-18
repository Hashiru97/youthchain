// Runs FormattedJobDescription's parser against every real scraped
// description currently in the live database (see
// test/fixtures_real_descriptions.json, exported directly from it) --
// not just the 3 hand-picked shapes the parser was designed against.
// The point isn't just "does it crash" -- it's whether the block
// sequence for each one is something a human would actually call
// well-organized, so failures print the full block breakdown for
// manual review, not just a pass/fail.

import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:youthchain_app/widgets/formatted_description.dart';

void main() {
  final fixturePath = 'test/fixtures_real_descriptions.json';
  final file = File(fixturePath);

  test('fixture file exists (export it via the backend before running this)', () {
    expect(file.existsSync(), isTrue, reason: '$fixturePath not found -- see the export command in git history');
  });

  if (!file.existsSync()) return;

  final fixtures = (jsonDecode(file.readAsStringSync()) as List).cast<Map<String, dynamic>>();

  test('parses every real description without crashing and accounts for all non-blank content', () {
    for (final fixture in fixtures) {
      final id = fixture['id'];
      final title = fixture['title'] as String;
      final description = fixture['description'] as String;

      final blocks = parseJobDescription(description);

      expect(blocks, isNotEmpty, reason: 'job $id ($title): produced zero blocks for non-empty input');

      // No block should ever be blank -- that would render as a mysterious
      // empty line/bullet on screen.
      for (final block in blocks) {
        final text = switch (block) {
          HeaderBlock() => block.text,
          ParagraphBlock() => block.text,
          ListBlock() => block.items.map((i) => i.text).join(),
        };
        expect(text.trim(), isNotEmpty, reason: 'job $id ($title): a ${block.runtimeType} rendered blank');
      }

      // Every list item's marker must itself be non-blank -- a blank
      // marker would render as an orphaned bullet dot or number.
      for (final block in blocks.whereType<ListBlock>()) {
        for (final item in block.items) {
          expect(item.marker.trim(), isNotEmpty, reason: 'job $id ($title): a list item had a blank marker');
        }
      }
    }
  });

  test('print the block breakdown for every real description for manual review', () {
    for (final fixture in fixtures) {
      final id = fixture['id'];
      final title = fixture['title'] as String;
      final description = fixture['description'] as String;
      final blocks = parseJobDescription(description);

      // ignore: avoid_print
      print('===== job $id: $title (${description.length} chars -> ${blocks.length} blocks) =====');
      for (final block in blocks) {
        switch (block) {
          case HeaderBlock():
            // ignore: avoid_print
            print('  [HEADER] ${block.text}');
          case ParagraphBlock():
            final preview = block.text.length > 70 ? '${block.text.substring(0, 70)}...' : block.text;
            // ignore: avoid_print
            print('  [PARA]${block.label != null ? ' (label: ${block.label})' : ''} $preview');
          case ListBlock():
            // ignore: avoid_print
            print('  [LIST] ${block.items.length} items, e.g. "${block.items.first.marker}" -> '
                '"${block.items.first.text.length > 50 ? '${block.items.first.text.substring(0, 50)}...' : block.items.first.text}"');
        }
      }
    }
  });
}
