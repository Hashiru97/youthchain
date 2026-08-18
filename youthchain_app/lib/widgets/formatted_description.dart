import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// Turns a scraped job description's raw text into a short list of typed
/// blocks so the widget below can give it real visual hierarchy (bold
/// section headers, proper bullet/numbered lists, bolded inline labels)
/// instead of dumping it as one plain [Text] with only whatever line
/// breaks happened to survive extraction.
///
/// Designed directly against real scraped descriptions, not a generic
/// Markdown-style guess -- Careers.sl/JobSearch SL listings come back in
/// three shapes, all handled here:
///  - clean "Header:" lines followed by "- bullet" lines (e.g. the
///    Enumerator posting's "Key Responsibilities:" section)
///  - a "Header:" line followed by loose pseudo-list lines, each its own
///    full paragraph starting with either a number ("1. Team Leadership -
///    ...") or a title-case label and a dash ("Academic Qualification -
///    Bachelor's degree...") -- both real, both single `\n`-separated,
///    seen in the same Senior M&E Officer posting
///  - one continuous unstructured paragraph with no headers or lists at
///    all (e.g. the Tender Notice posting) -- falls through to plain
///    paragraph rendering, which still benefits from the wider line
///    height/spacing the plain old Text() never had
sealed class DescriptionBlock {
  const DescriptionBlock();
}

class HeaderBlock extends DescriptionBlock {
  final String text;
  const HeaderBlock(this.text);
}

class ParagraphBlock extends DescriptionBlock {
  final String text;
  // Bold prefix pulled out of a "Label: rest" / "Label - rest" line, or
  // null when the paragraph is plain prose with nothing to highlight.
  final String? label;
  const ParagraphBlock(this.text, {this.label});
}

class ListItemLine {
  final String marker; // "•", "1.", or a pulled-out label like "Academic Qualification"
  final String text;
  final bool markerIsLabel; // true for the "Label - rest" shape -- rendered as a bold inline label, not a bullet/number
  const ListItemLine({required this.marker, required this.text, required this.markerIsLabel});
}

class ListBlock extends DescriptionBlock {
  final List<ListItemLine> items;
  const ListBlock(this.items);
}

final RegExp _headerLine = RegExp(r'^[A-Za-z][A-Za-z0-9 &,\/\x27-]{1,58}:$');
// ➢ (➢) confirmed for real: 25 occurrences across live scraped
// descriptions (a Globalite/job posting's own house style for bullets),
// second only to a plain "-" in how often source pages actually use it.
final RegExp _bulletLine = RegExp(r'^[-•➢]\s*(.*)$');
final RegExp _numberedLine = RegExp(r'^(\d{1,2}[.)])\s+(.*)$');
// Comma allowed in the label itself now, not just letters/space/&/apostrophe
// -- confirmed for real: sibling sub-headers in the same list can read
// "Strategy & Growth:" right next to "Sales, Partnerships & Revenue:".
// Without it, only some of a set of visually-parallel labels would get
// bolded and the rest would render as plain prose, which reads as
// broken/inconsistent formatting, not as a reasonable fallback.
final RegExp _labeledLine = RegExp(r"^([A-Z][A-Za-z0-9 &',\/]{1,40})\s[-–]\s(.+)$");
final RegExp _labeledParagraph = RegExp(r"^([A-Za-z][A-Za-z0-9 &',\/]{1,40}):\s+(.+)$");

_LineKind _classifyLine(String line) {
  final bullet = _bulletLine.firstMatch(line);
  if (bullet != null) return _LineKind(marker: "•", text: bullet.group(1)!, markerIsLabel: false);

  final numbered = _numberedLine.firstMatch(line);
  if (numbered != null) return _LineKind(marker: numbered.group(1)!, text: numbered.group(2)!, markerIsLabel: false);

  final labeled = _labeledLine.firstMatch(line);
  if (labeled != null) return _LineKind(marker: labeled.group(1)!, text: labeled.group(2)!, markerIsLabel: true);

  return _LineKind(marker: "", text: line, markerIsLabel: false);
}

class _LineKind {
  final String marker;
  final String text;
  final bool markerIsLabel;
  _LineKind({required this.marker, required this.text, required this.markerIsLabel});
  bool get isListItem => marker.isNotEmpty;
}

// Confirmed for real: not every section header in this data ends with a
// colon -- "Responsibilities", "Requirements", "Preferred Qualifications"
// and "TASKS" all appear as bare title-case lines directly above a run
// of bullet/numbered items, no colon at all. Without this, those lines
// were themselves swallowed as the list's first (nonsensical) item.
// Deliberately conservative: short, no sentence-ending punctuation,
// starts with a capital, isn't itself a list item -- and only actually
// used as a header when what follows really is a list (checked at the
// call site), so an ordinary short sentence fragment split across lines
// doesn't get mistaken for one.
bool _looksLikeBareHeader(String line) {
  if (line.isEmpty || line.length > 40) return false;
  if (RegExp(r'[.!?:]$').hasMatch(line)) return false;
  if (!RegExp(r'^[A-Z]').hasMatch(line)) return false;
  return !_classifyLine(line).isListItem;
}

List<DescriptionBlock> parseJobDescription(String raw) {
  final sections = raw
      .replaceAll('\r\n', '\n')
      .split(RegExp(r'\n{2,}'))
      .map((s) => s.trim())
      .where((s) => s.isNotEmpty);

  final blocks = <DescriptionBlock>[];

  for (final section in sections) {
    var lines = section.split('\n').map((l) => l.trim()).where((l) => l.isNotEmpty).toList();
    if (lines.isEmpty) continue;

    // A standalone "Header:" line introducing the rest of the section --
    // only when there's something below it; a lone "Header:" line with
    // nothing following is just short prose, handled by the paragraph
    // path below instead.
    if (lines.length > 1 && _headerLine.hasMatch(lines.first)) {
      blocks.add(HeaderBlock(lines.first.substring(0, lines.first.length - 1)));
      lines = lines.sublist(1);
    } else if (lines.length > 1 && _looksLikeBareHeader(lines.first)) {
      // Only promote a colon-less line to a header when what follows it
      // really is a list -- otherwise it's just a short sentence that
      // happened to be its own line, not an intro.
      final rest = lines.sublist(1);
      final restListCount = rest.map(_classifyLine).where((c) => c.isListItem).length;
      if (restListCount >= (rest.length / 2).ceil()) {
        blocks.add(HeaderBlock(lines.first));
        lines = rest;
      }
    }
    if (lines.isEmpty) continue;

    final classified = lines.map(_classifyLine).toList();
    final listItemCount = classified.where((c) => c.isListItem).length;

    // Section reads as a list when most of its lines classify as one --
    // real data is occasionally one stray sentence mixed into an
    // otherwise clean bullet/numbered/labeled run, so this doesn't
    // require every single line to match.
    if (lines.length > 1 && listItemCount >= (lines.length / 2).ceil()) {
      blocks.add(ListBlock([
        for (final c in classified)
          ListItemLine(
            marker: c.isListItem ? c.marker : "•",
            text: c.text,
            markerIsLabel: c.markerIsLabel,
          ),
      ]));
      continue;
    }

    // Otherwise: plain prose. Multiple raw lines here are almost always
    // soft-wrapped continuations of the same paragraph, not separate
    // ones -- join them back into one paragraph rather than rendering
    // suspicious mid-sentence gaps.
    final joined = lines.join(' ');
    final labeledParagraph = _labeledParagraph.firstMatch(joined);
    if (labeledParagraph != null) {
      blocks.add(ParagraphBlock(labeledParagraph.group(2)!, label: labeledParagraph.group(1)));
    } else {
      blocks.add(ParagraphBlock(joined));
    }
  }

  return blocks;
}

/// Renders [parseJobDescription]'s blocks with real typographic
/// hierarchy: bold section headers with breathing room above them,
/// proper indented bullet/numbered/labeled list rows, and comfortable
/// line height on prose paragraphs -- instead of one dense [Text] block
/// with the source's raw line breaks as the only structure.
class FormattedJobDescription extends StatelessWidget {
  final String text;
  const FormattedJobDescription({super.key, required this.text});

  @override
  Widget build(BuildContext context) {
    final blocks = parseJobDescription(text);
    final bodyStyle = Theme.of(context).textTheme.bodyMedium?.copyWith(height: 1.5);
    final labelStyle = bodyStyle?.copyWith(fontWeight: FontWeight.w700, color: context.colors.textPrimary);

    final children = <Widget>[];
    for (var i = 0; i < blocks.length; i++) {
      final block = blocks[i];
      final isFirst = i == 0;
      switch (block) {
        case HeaderBlock():
          children.add(Padding(
            padding: EdgeInsets.only(top: isFirst ? 0 : AppSpacing.md, bottom: AppSpacing.xs),
            child: Text(
              block.text,
              style: Theme.of(context).textTheme.titleSmall?.copyWith(color: context.colors.primary),
            ),
          ));
        case ParagraphBlock():
          children.add(Padding(
            padding: EdgeInsets.only(bottom: AppSpacing.sm),
            child: block.label != null
                ? Text.rich(
                    TextSpan(children: [
                      TextSpan(text: "${block.label}: ", style: labelStyle),
                      TextSpan(text: block.text, style: bodyStyle),
                    ]),
                  )
                : Text(block.text, style: bodyStyle),
          ));
        case ListBlock():
          children.add(Padding(
            padding: EdgeInsets.only(bottom: AppSpacing.sm),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final item in block.items)
                  Padding(
                    padding: const EdgeInsets.only(bottom: AppSpacing.xs),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SizedBox(
                          width: item.markerIsLabel ? 0 : 22,
                          child: item.markerIsLabel
                              ? null
                              : Text(item.marker, style: bodyStyle?.copyWith(color: context.colors.primary)),
                        ),
                        Expanded(
                          child: item.markerIsLabel
                              ? Text.rich(
                                  TextSpan(children: [
                                    TextSpan(text: "${item.marker} — ", style: labelStyle),
                                    TextSpan(text: item.text, style: bodyStyle),
                                  ]),
                                )
                              : Text(item.text, style: bodyStyle),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ));
      }
    }

    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: children);
  }
}
