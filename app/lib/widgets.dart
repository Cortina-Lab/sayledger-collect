import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import 'theme.dart';

class ErrorView extends StatelessWidget {
  const ErrorView({super.key, required this.message, required this.onRetry});
  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.cloud_off_rounded, size: 44, color: context.c.inkFaint),
            const SizedBox(height: 14),
            Text(message, textAlign: TextAlign.center, style: TextStyle(fontSize: 16, color: context.c.inkSoft)),
            const SizedBox(height: 18),
            OutlinedButton(onPressed: onRetry, child: const Text('Try again')),
          ]),
        ),
      );
}

/// Centered spinner with a short line, so long model calls don't feel frozen.
class Working extends StatelessWidget {
  const Working(this.label, {super.key});
  final String label;

  @override
  Widget build(BuildContext context) => Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          SizedBox(width: 34, height: 34, child: CircularProgressIndicator(strokeWidth: 2.6, color: context.c.accent)),
          const SizedBox(height: 18),
          Text(label, style: TextStyle(fontSize: 16, color: context.c.inkSoft)),
        ]),
      );
}

/// Light card used across screens.
class Panel extends StatelessWidget {
  const Panel({super.key, required this.child, this.padding = const EdgeInsets.all(18), this.color, this.onTap});
  final Widget child;
  final EdgeInsets padding;
  final Color? color;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final c = context.c;
    return Material(
      color: color ?? c.card,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(22), side: BorderSide(color: c.line)),
      clipBehavior: Clip.antiAlias,
      child: InkWell(onTap: onTap, child: Padding(padding: padding, child: child)),
    );
  }
}

/// Small uppercase label like "CREDIT GIVEN".
class Eyebrow extends StatelessWidget {
  const Eyebrow(this.text, {super.key});
  final String text;

  @override
  Widget build(BuildContext context) => Text(
        text.toUpperCase(),
        style: TextStyle(fontSize: 11.5, letterSpacing: 1.2, fontWeight: FontWeight.w600, color: context.c.inkFaint),
      );
}

/// Round back button used on inner screens.
class BackCircle extends StatelessWidget {
  const BackCircle({super.key});

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(left: 12),
        child: Center(
          child: Material(
            color: context.c.card,
            shape: CircleBorder(side: BorderSide(color: context.c.line)),
            child: InkWell(
              customBorder: const CircleBorder(),
              onTap: () => Navigator.maybePop(context),
              child: Padding(padding: const EdgeInsets.all(10), child: Icon(Icons.arrow_back_rounded, size: 20, color: context.c.ink)),
            ),
          ),
        ),
      );
}

AppBar innerBar(String title, {List<Widget>? actions}) => AppBar(
      leading: const BackCircle(),
      leadingWidth: 60,
      titleSpacing: 6,
      title: Text(title),
      actions: actions,
    );

void showSnack(BuildContext context, String text) =>
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));

IconData sourceIcon(String source) => switch (source) {
      'voice' => Icons.mic_none_rounded,
      'ledger_photo' => Icons.photo_camera_outlined,
      'paypal' => Icons.account_balance_wallet_outlined,
      _ => Icons.edit_outlined,
    };

class TagChip extends StatelessWidget {
  const TagChip(this.text, {super.key, required this.bg, required this.fg});
  final String text;
  final Color bg, fg;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(30), border: Border.all(color: context.c.line)),
        child: Text(text, style: TextStyle(color: fg, fontWeight: FontWeight.w600, fontSize: 13)),
      );
}

const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/// "3 Nov, 09:25" from a UTC ISO timestamp, in the phone's local time.
String whenLabel(String iso, {bool time = true}) {
  final d = DateTime.tryParse(iso)?.toLocal();
  if (d == null) return '';
  final day = '${d.day} ${_months[d.month - 1]}${d.year != DateTime.now().year ? ' ${d.year}' : ''}';
  return time ? '$day, ${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}' : day;
}

String dayLabel(DateTime d) => '${d.day} ${_months[d.month - 1]} ${d.year}';

/// YYYY-MM-DD for query strings.
String ymd(DateTime d) => '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

/// A date range the user picks on Day book / Reports.
class Period {
  const Period(this.label, this.start, this.end);
  final String label;
  final DateTime start, end;

  static List<Period> presets() {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    return [
      Period('Today', today, today),
      Period('7 days', today.subtract(const Duration(days: 6)), today),
      Period('This month', DateTime(now.year, now.month, 1), today),
      Period('Last month', DateTime(now.year, now.month - 1, 1), DateTime(now.year, now.month, 0)),
    ];
  }
}

Future<Period?> pickPeriod(BuildContext context, Period current) async {
  final r = await showDateRangePicker(
    context: context,
    firstDate: DateTime(2000),
    lastDate: DateTime.now(),
    initialDateRange: DateTimeRange(start: current.start, end: current.end),
  );
  if (r == null) return null;
  return Period('${dayLabel(r.start)} – ${dayLabel(r.end)}', r.start, r.end);
}

/// Horizontal chips for the period presets + a Custom… chip.
class PeriodBar extends StatelessWidget {
  const PeriodBar({super.key, required this.value, required this.onChanged});
  final Period value;
  final ValueChanged<Period> onChanged;

  @override
  Widget build(BuildContext context) {
    final c = context.c;
    final presets = Period.presets();
    final isPreset = presets.any((p) => p.label == value.label);
    Widget chip(String label, bool on, VoidCallback onTap) => Padding(
          padding: const EdgeInsets.only(right: 8),
          child: ChoiceChip(
            label: Text(label),
            selected: on,
            onSelected: (_) => onTap(),
            selectedColor: c.accent,
            backgroundColor: c.card,
            side: BorderSide(color: on ? c.accent : c.line),
            labelStyle: TextStyle(color: on ? Colors.white : c.ink, fontWeight: FontWeight.w600),
            showCheckmark: false,
            shape: const StadiumBorder(),
          ),
        );
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(children: [
        for (final p in presets) chip(p.label, p.label == value.label, () => onChanged(p)),
        chip(isPreset ? 'Custom…' : value.label, !isPreset, () async {
          final p = await pickPeriod(context, value);
          if (p != null) onChanged(p);
        }),
      ]),
    );
  }
}

/// Saves bytes to a temp file and opens the system share sheet.
Future<void> shareFile(BuildContext context, List<int> bytes, String filename, String mime, {String? text}) async {
  final box = context.findRenderObject() as RenderBox?;
  final dir = await getTemporaryDirectory();
  final f = File('${dir.path}/$filename');
  await f.writeAsBytes(bytes, flush: true);
  await SharePlus.instance.share(ShareParams(
    files: [XFile(f.path, mimeType: mime)],
    text: text,
    sharePositionOrigin: box == null ? null : box.localToGlobal(Offset.zero) & box.size,
  ));
}
