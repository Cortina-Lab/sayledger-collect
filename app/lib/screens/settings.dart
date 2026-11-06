import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import '../widgets.dart';
import 'activity.dart';
import 'home.dart';
import 'onboarding.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _name = TextEditingController();
  final _currency = TextEditingController();
  final _server = TextEditingController(text: Api.instance.base);
  bool _busy = false;
  String _region = 'IN';
  int _entries = 0;
  Map<String, dynamic>? _paypal;

  static const _regions = {'IN': ('India', 'INR'), 'US': ('USA', 'USD'), 'XX': ('Other', '')};

  String _regionFor(String cur) => cur == 'INR' ? 'IN' : cur == 'USD' ? 'US' : 'XX';

  void _pickRegion(String id) => setState(() {
        _region = id;
        final cur = _regions[id]!.$2;
        if (cur.isNotEmpty) _currency.text = cur;
      });

  @override
  void initState() {
    super.initState();
    Api.instance.shop().then((s) {
      if (!mounted) return;
      _name.text = s['name'] as String;
      _currency.text = s['currency'] as String;
      setState(() => (_region = _regionFor(_currency.text), _entries = (s['entries'] ?? 0) as int));
    }).catchError((_) {});
    Api.instance.collectStatus().then((st) {
      if (mounted) setState(() => _paypal = st);
    }).catchError((_) {});
  }

  Future<void> _run(Future<void> Function() job) async {
    setState(() => _busy = true);
    try {
      await job();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _save() => _run(() async {
        await Api.instance.setBase(_server.text);
        if (_name.text.trim().isNotEmpty) {
          await Api.instance.saveShop(_name.text.trim(), _currency.text.trim().toUpperCase());
        }
        if (mounted) {
          showSnack(context, 'Saved');
          Navigator.pop(context);
        }
      });

  Future<void> _backup() => _run(() async {
        final bytes = await Api.instance.backup();
        if (mounted) {
          await shareFile(context, bytes, 'sayledger-backup-${ymd(DateTime.now())}.sayledger', 'application/octet-stream',
              text: 'SayLedger backup');
        }
      });

  Future<void> _restore() async {
    final ok = await _confirm('Restore a backup?', 'Your current book will be replaced by the backup file you choose.', 'Choose file');
    if (!ok) return;
    final picked = await FilePicker.pickFile();
    if (picked == null) return;
    final bytes = await picked.readAsBytes();
    await _run(() async {
      final r = await Api.instance.restore(bytes);
      if (mounted) showSnack(context, 'Restored ${r['entries']} entries');
    });
  }

  Future<void> _erase() async {
    final ok = await _confirm(
      'Erase this book?',
      'All customers and entries in this book are permanently deleted from the server. Make a backup first if you may need it.',
      'Erase everything',
    );
    if (!ok) return;
    await _run(() async {
      await Api.instance.deleteBook();
      if (!mounted) return;
      Navigator.of(context).pushAndRemoveUntil(
          MaterialPageRoute(builder: (_) => Api.instance.hasBook ? const HomeScreen() : const OnboardingScreen()), (_) => false);
    });
  }

  Future<bool> _confirm(String title, String body, String action) async =>
      await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(title),
          content: Text(body),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Not now')),
            FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(action)),
          ],
        ),
      ) ??
      false;

  Widget _action(IconData icon, String title, String sub, VoidCallback onTap, {Color? tone}) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Panel(
          padding: EdgeInsets.zero,
          onTap: _busy ? null : onTap,
          child: ListTile(
            leading: Icon(icon, color: tone ?? context.c.accent),
            title: Text(title, style: TextStyle(fontWeight: FontWeight.w600, color: tone)),
            subtitle: Text(sub, style: TextStyle(color: context.c.inkFaint)),
          ),
        ),
      );

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: innerBar('Settings'),
        body: ListView(padding: const EdgeInsets.fromLTRB(20, 8, 20, 24), children: [
          TextField(controller: _name, decoration: const InputDecoration(labelText: 'Shop name')),
          const SizedBox(height: 22),
          const Eyebrow('Region'),
          const SizedBox(height: 10),
          SegmentedButton<String>(
            segments: [for (final e in _regions.entries) ButtonSegment(value: e.key, label: Text(e.value.$1))],
            selected: {_region},
            showSelectedIcon: false,
            onSelectionChanged: _entries > 0 ? null : (v) => _pickRegion(v.first),
          ),
          const SizedBox(height: 14),
          if (_entries > 0) ...[
            Text('This book has $_entries entries in ${_currency.text}, so its currency is fixed.',
                style: TextStyle(color: context.c.inkSoft)),
            TextButton.icon(
              style: TextButton.styleFrom(padding: EdgeInsets.zero, alignment: Alignment.centerLeft),
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const OnboardingScreen(another: true))),
              icon: const Icon(Icons.add_rounded, size: 18),
              label: const Text('Start a new book for another region'),
            ),
          ] else if (_region == 'XX')
            TextField(
              controller: _currency,
              textCapitalization: TextCapitalization.characters,
              maxLength: 3,
              decoration: const InputDecoration(labelText: 'Currency code (e.g. EUR, GBP, NGN, IDR)'),
            )
          else
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Text('Amounts in ${_currency.text}. Speak or type in any language.', style: TextStyle(color: context.c.inkSoft)),
            ),
          if (_entries == 0)
            Text('Currency is fixed once your book has entries.', style: TextStyle(color: context.c.inkFaint, fontSize: 13)),
          const SizedBox(height: 18),
          TextField(controller: _server, decoration: const InputDecoration(labelText: 'Server')),
          const SizedBox(height: 16),
          FilledButton(onPressed: _busy ? null : _save, child: const Text('Save')),
          if (_paypal != null) ...[
            const SizedBox(height: 28),
            Text('PayPal payment links', style: display(context, 20)),
            const SizedBox(height: 10),
            Panel(
              padding: EdgeInsets.zero,
              child: ListTile(
                leading: Icon(Icons.account_balance_wallet_outlined,
                    color: _paypal!['paypal_configured'] == true && _paypal!['links_available'] == true ? context.c.advance : context.c.inkFaint),
                title: Text(
                  _paypal!['paypal_configured'] != true
                      ? 'PayPal not set up on this server'
                      : _paypal!['links_available'] == true
                          ? 'PayPal (${_paypal!['environment']}) connected'
                          : 'Not available for ${_paypal!['currency']} books',
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
                subtitle: Text(
                  'Links work for ${(_paypal!['supported'] as List).join(', ')} books. Keys live on the server only — '
                  'this phone never signs in to PayPal.',
                  style: TextStyle(color: context.c.inkFaint),
                ),
              ),
            ),
          ],
          const SizedBox(height: 28),
          Text('Your data', style: display(context, 20)),
          const SizedBox(height: 10),
          _action(Icons.history_rounded, 'Activity log', 'Every add, edit and cancel',
              () => Navigator.push(context, MaterialPageRoute(builder: (_) => ActivityScreen(currency: _currency.text)))),
          _action(Icons.cloud_download_outlined, 'Back up book', 'Save a copy of your whole book as a file', _backup),
          _action(Icons.restore_rounded, 'Restore backup', 'Replace this book with a backup file', _restore),
          _action(Icons.delete_forever_outlined, 'Erase book', 'Permanently delete all data from the server', _erase,
              tone: context.c.owes),
          const SizedBox(height: 24),
          Text('About', style: display(context, 20)),
          const SizedBox(height: 6),
          Text(
            'SayLedger by Cortina. You check every entry before it is saved. '
            'Readings from voice and photos are approximate. All totals are calculated from your saved entries, not by AI. '
            'SayLedger is a credit book, not accounting or tax software.',
            style: TextStyle(color: context.c.inkSoft, height: 1.45),
          ),
        ]),
      );
}
