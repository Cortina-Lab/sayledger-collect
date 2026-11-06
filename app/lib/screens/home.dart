import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../api.dart';
import '../money.dart';
import '../recorder.dart';
import '../theme.dart';
import '../widgets.dart';
import 'ask.dart';
import 'collect_agent.dart';
import 'collections.dart';
import 'confirm.dart';
import 'customers.dart';
import 'daybook.dart';
import 'inbox.dart';
import 'onboarding.dart';
import 'reports.dart';
import 'settings.dart';
import 'stock.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  Map<String, dynamic>? _shop;
  Map<String, dynamic>? _summary;
  String? _error;
  bool _links = false;
  int _inbox = 0;
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    _load();
    // Foreground polling is enough for the demo: new PayPal payments show up within 30 s.
    _poll = Timer.periodic(const Duration(seconds: 30), (_) => _checkInbox());
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  /// PayPal Collect is optional: INR books or an older server simply hide it.
  Future<void> _checkInbox() async {
    if (!mounted || !Api.instance.hasBook) return;
    try {
      final st = await Api.instance.collectStatus();
      final links = st['links_available'] == true && st['paypal_configured'] == true;
      final n = links ? (await Api.instance.inbox()).length : 0;
      if (mounted && (links != _links || n != _inbox)) setState(() => (_links = links, _inbox = n));
    } on ApiException {
      // keep the last known state
    }
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final shop = await Api.instance.shop();
      final summary = await Api.instance.summary();
      if (mounted) setState(() => (_shop = shop, _summary = summary));
      _checkInbox();
    } on UnlinkedException {
      await Api.instance.forgetBook();
      if (Api.instance.hasBook) return _load();
      if (mounted) {
        Navigator.of(context).pushAndRemoveUntil(MaterialPageRoute(builder: (_) => const OnboardingScreen()), (_) => false);
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _open(Widget screen) async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => screen));
    _load();
  }

  Future<void> _switchBook() async {
    final picked = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: context.c.card,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(26))),
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Padding(padding: const EdgeInsets.fromLTRB(20, 20, 20, 8), child: Text('Your books', style: display(ctx, 22))),
          for (final b in Api.instance.books)
            ListTile(
              leading: CircleAvatar(
                backgroundColor: ctx.c.accentSoft,
                foregroundColor: ctx.c.accent,
                child: Text(b.name.characters.first.toUpperCase(), style: const TextStyle(fontWeight: FontWeight.w700)),
              ),
              title: Text(b.name, style: const TextStyle(fontWeight: FontWeight.w600)),
              subtitle: Text(b.currency),
              trailing: b.token == Api.instance.token ? Icon(Icons.check_rounded, color: ctx.c.accent) : null,
              onTap: () => Navigator.pop(ctx, b.token),
            ),
          ListTile(
            leading: Icon(Icons.add_rounded, color: ctx.c.accent),
            title: Text('New book', style: TextStyle(color: ctx.c.accent, fontWeight: FontWeight.w700)),
            subtitle: const Text('Another shop, or another region / currency'),
            onTap: () => Navigator.pop(ctx, '+'),
          ),
          const SizedBox(height: 8),
        ]),
      ),
    );
    if (picked == null || !mounted) return;
    if (picked == '+') {
      await Navigator.push(context, MaterialPageRoute(builder: (_) => const OnboardingScreen(another: true)));
      return;
    }
    if (picked == Api.instance.token) return;
    await Api.instance.switchTo(picked);
    setState(() => (_shop = null, _summary = null));
    _load();
  }

  Future<void> _voice(Uint8List wav) => _open(ConfirmScreen.fromVoice(wav));

  Future<void> _typeEntry() async {
    final text = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      backgroundColor: context.c.card,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(26))),
      builder: (_) => _TypeSheet(currency: (_summary?['currency'] ?? 'INR') as String),
    );
    if (text == null || text.trim().isEmpty || !mounted) return;
    await _open(ConfirmScreen.fromText(text.trim()));
  }

  Future<void> _scan() async {
    final source = await showModalBottomSheet<ImageSource>(
      context: context,
      backgroundColor: context.c.card,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(26))),
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 4),
            child: Text('Scan a ledger page', style: display(ctx, 22)),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
            child: Text('Flat page, good light, whole page in view.', style: TextStyle(color: ctx.c.inkFaint)),
          ),
          ListTile(leading: const Icon(Icons.photo_camera_outlined), title: const Text('Take a photo'), onTap: () => Navigator.pop(ctx, ImageSource.camera)),
          ListTile(leading: const Icon(Icons.photo_library_outlined), title: const Text('Choose from gallery'), onTap: () => Navigator.pop(ctx, ImageSource.gallery)),
          const SizedBox(height: 8),
        ]),
      ),
    );
    if (source == null) return;
    final shot = await ImagePicker().pickImage(source: source, maxWidth: 2200, imageQuality: 85);
    if (shot == null || !mounted) return;
    final bytes = await shot.readAsBytes();
    final mime = shot.mimeType ?? (shot.path.toLowerCase().endsWith('.png') ? 'image/png' : 'image/jpeg');
    await _open(ConfirmScreen.fromPhoto(bytes, mime));
  }

  @override
  Widget build(BuildContext context) {
    final c = context.c;
    final s = _summary;
    final cur = (s?['currency'] ?? 'INR') as String;
    return Scaffold(
      body: SafeArea(
        child: _error != null
            ? ErrorView(message: _error!, onRetry: _load)
            : s == null
                ? const Working('Opening your book…')
                : RefreshIndicator(
                    color: c.accent,
                    onRefresh: _load,
                    child: ListView(padding: const EdgeInsets.fromLTRB(20, 14, 20, 28), children: [
                      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Expanded(
                          child: InkWell(
                            borderRadius: BorderRadius.circular(12),
                            onTap: _switchBook,
                            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                              const Eyebrow('SayLedger'),
                              const SizedBox(height: 3),
                              Row(children: [
                                Flexible(child: Text(_shop?['name'] ?? 'Your shop', style: display(context, 21), overflow: TextOverflow.ellipsis)),
                                Icon(Icons.expand_more_rounded, color: c.inkFaint),
                              ]),
                            ]),
                          ),
                        ),
                        Padding(
                          padding: const EdgeInsets.only(top: 4, right: 10),
                          child: Text('by Cortina', style: TextStyle(fontSize: 12.5, color: c.inkFaint)),
                        ),
                        Material(
                          color: c.card,
                          shape: CircleBorder(side: BorderSide(color: c.line)),
                          child: IconButton(
                            icon: Icon(Icons.settings_outlined, color: c.ink, size: 21),
                            onPressed: () => _open(const SettingsScreen()),
                          ),
                        ),
                      ]),
                      const SizedBox(height: 24),
                      InkWell(
                        borderRadius: BorderRadius.circular(16),
                        onTap: () => _open(const CollectionsScreen()),
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Row(children: [
                            Text('Customers owe you', style: TextStyle(fontSize: 15, color: c.inkSoft, fontWeight: FontWeight.w500)),
                            const SizedBox(width: 4),
                            Icon(Icons.chevron_right_rounded, size: 18, color: c.inkFaint),
                          ]),
                          const SizedBox(height: 4),
                          FittedBox(
                            alignment: Alignment.centerLeft,
                            fit: BoxFit.scaleDown,
                            child: Text(formatMoney(s['outstanding_total_minor'] as int, cur), style: display(context, 50)),
                          ),
                        ]),
                      ),
                      if (_inbox > 0) ...[
                        const SizedBox(height: 12),
                        Panel(
                          color: c.accentSoft,
                          padding: const EdgeInsets.all(14),
                          onTap: () => _open(const InboxScreen()),
                          child: Row(children: [
                            Icon(Icons.account_balance_wallet_outlined, color: c.accent),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Text('$_inbox new PayPal payment${_inbox == 1 ? '' : 's'} to confirm',
                                  style: TextStyle(color: c.ink, fontWeight: FontWeight.w600, fontSize: 15.5)),
                            ),
                            Text('Review', style: TextStyle(color: c.accent, fontWeight: FontWeight.w700)),
                          ]),
                        ),
                      ],
                      const SizedBox(height: 16),
                      Panel(
                        onTap: () => _open(const DayBookScreen()),
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Row(children: [
                            Expanded(child: Text('Today', style: display(context, 20))),
                            Text('${s['entries']} ${s['entries'] == 1 ? 'entry' : 'entries'}',
                                style: TextStyle(color: c.inkFaint, fontSize: 14)),
                          ]),
                          const SizedBox(height: 16),
                          Row(children: [
                            Expanded(child: _stat('Credit given', s['credit_given_minor'], cur)),
                            Expanded(child: _stat('Payments', s['payment_received_minor'], cur)),
                          ]),
                          const SizedBox(height: 14),
                          Row(children: [
                            Expanded(child: _stat('Cash sales', s['cash_sale_minor'], cur)),
                            Expanded(child: _stat('Expenses', s['expense_minor'], cur)),
                          ]),
                        ]),
                      ),
                      const SizedBox(height: 14),
                      Center(child: HoldToSpeak(onAudio: _voice)),
                      Center(child: Text('Hold to speak', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700, color: c.ink))),
                      const SizedBox(height: 4),
                      Center(
                        child: Text('Any language · release to send', style: TextStyle(fontSize: 14, color: c.inkFaint)),
                      ),
                      const SizedBox(height: 8),
                      Center(
                        child: TextButton.icon(
                          onPressed: _typeEntry,
                          icon: const Icon(Icons.edit_outlined, size: 18),
                          label: const Text('Type an entry'),
                        ),
                      ),
                      const SizedBox(height: 14),
                      _grid([
                        if (_links) (Icons.send_to_mobile_outlined, 'Get paid', () => _open(const CollectAgentScreen())),
                        if (_links)
                          (Icons.account_balance_wallet_outlined, _inbox > 0 ? 'Payment inbox · $_inbox' : 'Payment inbox', () => _open(const InboxScreen())),
                        (Icons.people_outline_rounded, 'Customers', () => _open(const CustomersScreen())),
                        (Icons.savings_outlined, 'Collections', () => _open(const CollectionsScreen())),
                        (Icons.chat_bubble_outline_rounded, 'Ask', () => _open(const AskScreen())),
                        (Icons.document_scanner_outlined, 'Scan ledger page', _scan),
                        (Icons.receipt_long_outlined, 'Day book', () => _open(const DayBookScreen())),
                        (Icons.insert_chart_outlined_rounded, 'Reports', () => _open(const ReportsScreen())),
                        (Icons.inventory_2_outlined, 'Count stock', () => _open(const StockScreen())),
                      ]),
                    ]),
                  ),
      ),
    );
  }

  Widget _grid(List<(IconData, String, VoidCallback)> items) => Column(children: [
        for (var i = 0; i < items.length; i += 2)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Row(children: [
              Expanded(child: _tile(items[i])),
              const SizedBox(width: 12),
              Expanded(child: i + 1 < items.length ? _tile(items[i + 1]) : const SizedBox()),
            ]),
          ),
      ]);

  Widget _stat(String label, dynamic minor, String cur) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Eyebrow(label),
        const SizedBox(height: 4),
        Text(formatMoney(minor as int, cur), style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, color: context.c.ink)),
      ]);

  Widget _tile((IconData, String, VoidCallback) t) => Panel(
        onTap: t.$3,
        padding: const EdgeInsets.fromLTRB(16, 16, 12, 16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(t.$1, color: context.c.accent, size: 24),
          const SizedBox(height: 16),
          Text(t.$2, style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5, color: context.c.ink)),
        ]),
      );
}

class _TypeSheet extends StatefulWidget {
  const _TypeSheet({required this.currency});
  final String currency;

  @override
  State<_TypeSheet> createState() => _TypeSheetState();
}

class _TypeSheetState extends State<_TypeSheet> {
  final _c = TextEditingController();

  static String _example(String cur) => switch (cur) {
        'INR' => '"Ramesh took 3 kg rice, 450 on credit"',
        'USD' => '"Maria took two gallons of milk, \$12.50 on her tab"',
        _ => '"Ana took bread, 8 on credit"',
      };

  @override
  Widget build(BuildContext context) => Padding(
        padding: EdgeInsets.fromLTRB(20, 22, 20, 20 + MediaQuery.of(context).viewInsets.bottom),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text('Type an entry', style: display(context, 22)),
          const SizedBox(height: 6),
          Text('Any language. e.g. ${_example(widget.currency)}', style: TextStyle(color: context.c.inkFaint)),
          const SizedBox(height: 14),
          TextField(controller: _c, autofocus: true, minLines: 2, maxLines: 4, textInputAction: TextInputAction.done),
          const SizedBox(height: 14),
          FilledButton(onPressed: () => Navigator.pop(context, _c.text), child: const Text('Continue')),
        ]),
      );
}
