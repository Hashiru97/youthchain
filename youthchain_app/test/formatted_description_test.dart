// Coverage for FormattedJobDescription's parser -- designed directly
// against real scraped description text (see the widget's own docstring
// for the three real shapes this was built from: Careers.sl's Enumerator
// posting, its Senior M&E Officer posting, and its Tender Notice
// posting), not a generic guess at what "some markdown" might look like.

import 'package:flutter_test/flutter_test.dart';
import 'package:youthchain_app/widgets/formatted_description.dart';

void main() {
  test('a single unstructured paragraph stays one ParagraphBlock', () {
    const text = "Action Against Hunger is inviting qualified and reputable Contractors for the "
        "Construction of a two-room self-contained Agromet block.";
    final blocks = parseJobDescription(text);
    expect(blocks, hasLength(1));
    expect(blocks.single, isA<ParagraphBlock>());
    expect((blocks.single as ParagraphBlock).text, text);
    expect((blocks.single as ParagraphBlock).label, isNull);
  });

  test('a "Header:" line followed by "- bullet" lines becomes a header + bullet list', () {
    const text = "Key Responsibilities:\n"
        "- Conduct household and community interviews using structured questionnaires.\n"
        "- Record responses accurately on digital devices.\n"
        "- Ensure confidentiality and ethical handling of respondent information.";
    final blocks = parseJobDescription(text);
    expect(blocks, hasLength(2));
    expect(blocks[0], isA<HeaderBlock>());
    expect((blocks[0] as HeaderBlock).text, "Key Responsibilities");
    expect(blocks[1], isA<ListBlock>());
    final items = (blocks[1] as ListBlock).items;
    expect(items, hasLength(3));
    expect(items[0].marker, "•");
    expect(items[0].markerIsLabel, isFalse);
    expect(items[0].text, "Conduct household and community interviews using structured questionnaires.");
  });

  test('a "Header:" line followed by numbered pseudo-list paragraphs becomes a header + numbered list', () {
    const text = "Essential Duties and Responsibilities:\n"
        "1. Program Management - Contribute to the development of the overall M&E strategy.\n"
        "2. Team Leadership and Supervision - Directly supervise M&E Officers and Assistants.";
    final blocks = parseJobDescription(text);
    expect(blocks, hasLength(2));
    expect(blocks[0], isA<HeaderBlock>());
    expect((blocks[0] as HeaderBlock).text, "Essential Duties and Responsibilities");
    final items = (blocks[1] as ListBlock).items;
    expect(items, hasLength(2));
    expect(items[0].marker, "1.");
    expect(items[0].text, "Program Management - Contribute to the development of the overall M&E strategy.");
    expect(items[1].marker, "2.");
  });

  test('"Label - rest" pseudo-list lines (no bullet/number) become a labeled list', () {
    const text = "Education and Work Experience Requirements:\n"
        "Academic Qualification - Bachelor's degree in relevant field.\n"
        "Technical Competencies - Minimum of five years of progressive experience.";
    final blocks = parseJobDescription(text);
    expect(blocks[0], isA<HeaderBlock>());
    final items = (blocks[1] as ListBlock).items;
    expect(items, hasLength(2));
    expect(items[0].markerIsLabel, isTrue);
    expect(items[0].marker, "Academic Qualification");
    expect(items[0].text, "Bachelor's degree in relevant field.");
    expect(items[1].marker, "Technical Competencies");
  });

  test('a single "Label: rest" line becomes a labeled paragraph, not a list', () {
    const text = "Position Overview: We are seeking motivated and detail-oriented Field Enumerators.";
    final blocks = parseJobDescription(text);
    expect(blocks, hasLength(1));
    final p = blocks.single as ParagraphBlock;
    expect(p.label, "Position Overview");
    expect(p.text, "We are seeking motivated and detail-oriented Field Enumerators.");
  });

  test('a lone "Header:" line with nothing following it is treated as short prose, not a header', () {
    final blocks = parseJobDescription("Closing Date:");
    expect(blocks, hasLength(1));
    expect(blocks.single, isA<ParagraphBlock>());
  });

  test('soft-wrapped prose lines (single newlines, not a list) are rejoined into one paragraph', () {
    const text = "This role requires strong communication skills\nand the ability to work independently\nin field conditions.";
    final blocks = parseJobDescription(text);
    expect(blocks, hasLength(1));
    expect((blocks.single as ParagraphBlock).text,
        "This role requires strong communication skills and the ability to work independently in field conditions.");
  });

  test('a realistic multi-section description produces the expected block sequence', () {
    const text = "Job Vacancy: Enumerators\n\n"
        "Position Title: Enumerator\n\n"
        "Key Responsibilities:\n"
        "- Conduct household and community interviews.\n"
        "- Record responses accurately on digital devices.\n\n"
        "Deadline for Applications: 23 August 2026";
    final blocks = parseJobDescription(text);
    expect(blocks, hasLength(5));
    expect(blocks[0], isA<ParagraphBlock>()); // "Job Vacancy: Enumerators"
    expect((blocks[0] as ParagraphBlock).label, "Job Vacancy");
    expect(blocks[1], isA<ParagraphBlock>()); // "Position Title: Enumerator"
    expect((blocks[1] as ParagraphBlock).label, "Position Title");
    expect(blocks[2], isA<HeaderBlock>());
    expect(blocks[3], isA<ListBlock>());
    expect(blocks[4], isA<ParagraphBlock>()); // "Deadline for Applications: ..."
    expect((blocks[4] as ParagraphBlock).label, "Deadline for Applications");
  });

  test('empty or whitespace-only input produces no blocks and does not crash', () {
    expect(parseJobDescription(""), isEmpty);
    expect(parseJobDescription("   \n\n  "), isEmpty);
  });

  test('a bare title-case line with no colon, followed by bullets, is promoted to a header', () {
    // Real bug found reviewing actual scraped data: "Responsibilities" /
    // "Requirements" / "TASKS" appear as bare lines (no trailing colon)
    // directly above a bullet list -- without this, the header text
    // itself became the list's nonsensical first "bullet".
    const text = "Responsibilities\n"
        "- Routine primary health care to Peace Corps Trainees.\n"
        "- Individual and group counseling as needed.";
    final blocks = parseJobDescription(text);
    expect(blocks[0], isA<HeaderBlock>());
    expect((blocks[0] as HeaderBlock).text, "Responsibilities");
    expect((blocks[1] as ListBlock).items, hasLength(2));
  });

  test('a bare short line NOT followed by a list stays plain prose, not a header', () {
    const text = "Summary\nThis role requires strong communication skills and attention to detail.";
    final blocks = parseJobDescription(text);
    expect(blocks, hasLength(1));
    expect(blocks.single, isA<ParagraphBlock>());
  });

  test('the ➢ character is recognized as a bullet marker', () {
    const text = "TASKS\n"
        "➢ Assist in managing the financial reporting for the company.\n"
        "➢ Risk Management to ensure the business has a solid risk framework.";
    final blocks = parseJobDescription(text);
    expect(blocks[0], isA<HeaderBlock>());
    final items = (blocks[1] as ListBlock).items;
    expect(items, hasLength(2));
    expect(items[0].text, "Assist in managing the financial reporting for the company.");
  });

  test('a comma inside a "Label: text" label is still recognized, matching sibling labels without one', () {
    // Real bug: "Strategy & Growth:" matched but "Sales, Partnerships &
    // Revenue:" didn't, so visually-parallel sub-headers in the same
    // list rendered inconsistently -- some bold, some plain.
    const text = "Strategy & Growth: Develop business strategies.\n\n"
        "Sales, Partnerships & Revenue: Lead business development initiatives.";
    final blocks = parseJobDescription(text);
    expect(blocks, hasLength(2));
    expect((blocks[0] as ParagraphBlock).label, "Strategy & Growth");
    expect((blocks[1] as ParagraphBlock).label, "Sales, Partnerships & Revenue");
  });

  test('a section mixing a couple of stray sentences into mostly bullets still reads as a list', () {
    const text = "Notes:\n"
        "- First real bullet point here.\n"
        "- Second real bullet point here.\n"
        "One stray sentence with no marker at all.";
    final blocks = parseJobDescription(text);
    expect(blocks[0], isA<HeaderBlock>());
    final items = (blocks[1] as ListBlock).items;
    expect(items, hasLength(3));
    // The stray line still renders, just with a plain bullet marker instead of losing it entirely.
    expect(items[2].marker, "•");
    expect(items[2].text, "One stray sentence with no marker at all.");
  });
}
