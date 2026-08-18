import 'package:flutter/material.dart';

import '../services/api_client.dart';
import '../theme/app_theme.dart';

/// The bookmark toggle used on every job card and detail screen (Home
/// and Discover both, see backend SavedJob's own docstring for why one
/// shared table/UI covers both). Owns its own optimistic toggle + revert-
/// on-failure logic in one place rather than duplicating it in four
/// screens.
class SaveJobButton extends StatefulWidget {
  final int jobId;
  final bool initiallySaved;
  final double size;
  final ValueChanged<bool>? onChanged;
  // Overrides both the saved and unsaved icon color with one fixed
  // color, for placements (like an AppBar) where the default
  // primary/textMuted pair isn't guaranteed to contrast with the
  // background behind it -- e.g. this app's global AppBarTheme uses
  // colors.primary as its own background, which would make a "saved"
  // icon in that default primary color invisible against it. Left null
  // on cards, where the surface background makes the default pair read
  // fine (confirmed live on both Home and Discover cards).
  final Color? color;

  const SaveJobButton({
    super.key,
    required this.jobId,
    required this.initiallySaved,
    this.size = 24,
    this.onChanged,
    this.color,
  });

  @override
  State<SaveJobButton> createState() => _SaveJobButtonState();
}

class _SaveJobButtonState extends State<SaveJobButton> {
  late bool _saved;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _saved = widget.initiallySaved;
  }

  @override
  void didUpdateWidget(covariant SaveJobButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    // A ListView.builder can reuse this element for a different job at
    // the same list position (e.g. the item above got removed) --
    // without this, the icon would briefly show the previous job's
    // saved state instead of this one's.
    if (oldWidget.jobId != widget.jobId) {
      _saved = widget.initiallySaved;
    }
  }

  Future<void> _toggle() async {
    if (_busy) return;
    final next = !_saved;
    setState(() {
      _saved = next;
      _busy = true;
    });
    widget.onChanged?.call(next);

    try {
      final path = "/api/jobs/${widget.jobId}/${next ? 'save' : 'unsave'}";
      final res = await ApiClient.instance.postJson(path, {});
      if (res.statusCode != 200) throw Exception("unexpected status ${res.statusCode}");
    } catch (_) {
      if (!mounted) return;
      setState(() => _saved = !next);
      widget.onChanged?.call(!next);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(next ? "Could not save this job — try again." : "Could not remove — try again.")),
      );
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return IconButton(
      onPressed: _toggle,
      tooltip: _saved ? "Remove from saved jobs" : "Save this job",
      icon: Icon(
        _saved ? Icons.bookmark_rounded : Icons.bookmark_border_rounded,
        color: widget.color ?? (_saved ? context.colors.primary : context.colors.textMuted),
        size: widget.size,
      ),
    );
  }
}
