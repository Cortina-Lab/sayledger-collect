import 'package:flutter/material.dart';
import 'package:share_plus/share_plus.dart';

import '../api.dart';
import '../money.dart';
import '../pdf_docs.dart';
import '../theme.dart';
import '../widgets.dart';
import 'daybook.dart';

class CustomerDetailScreen extends StatefulWidget {
  const CustomerDetailScreen({super.key, required this.partyId});
  final int partyId;

  @override
  State<CustomerDetailScreen> createState() => _CustomerDetailScreenState();
}

class _CustomerDetailScreenState extends State<CustomerDetailScreen> {
  Map<String, dynamic>? _p;
  String? _error;
  bool _links = false;
  List<Map<String, dynamic>> _requests = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final p = await Api.instance.party(widget.partyId);
      if (mounted) setState(() => _p = p);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
      return;
    }
    // PayPal Collect is optional: an older server or an INR book simply hides it.
    try {
      final st = await Api.instance.collectStatus();
      final links = st['links_available'] == true && st['paypal_configured'] == true;
      final reqs = links ? await Api.instance.paymentRequests(partyId: widget.partyId) : <Map<String, dynamic>>[];
      if (mounted) setState(() => (_links = links, _requests = reqs));
    } on ApiException {
      if (mounted) setState(() => _links = false);
    }
  }

  Future<void> _editCustomer() async {
    final p = _p!;
    final name = TextEditingController(text: p['name'] as String);
    final phone = TextEditingController(text: (p['phone'] ?? '') as String);
    final lang = TextEditingController(text: (p['language'] ?? '') as String);
    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: context.c.card,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(26))),
      builder: (ctx) => Padding(
        padding: EdgeInsets.fromLTRB(20, 22, 20, 20 + MediaQuery.of(ctx).viewInsets.bottom),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text('Customer details', style: display(ctx, 22)),
          const SizedBox(height: 14),
          TextField(controller: name, decoration: const InputDecoration(labelText: 'Name')),
          const SizedBox(height: 10),
          TextField(controller: phone, keyboardType: TextInputType.phone, decoration: const InputDecoration(labelText: 'Phone (optional)')),
          const SizedBox(height: 10),
          TextField(controller: lang, decoration: const InputDecoration(labelText: 'Preferred language for reminders')),
          const SizedBox(height: 14),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Save')),
        ]),
      ),
    );
    if (ok != true) return;
    try {
      await Api.instance.editParty(widget.partyId, {'name': name.text, 'phone': phone.text, 'language': lang.text});
      _load();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message);
    }
  }

  Future<void> _statement() async {
    final period = await showModalBottomSheet<Period?>(
      context: context,
      backgroundColor: context.c.card,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(26))),
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Padding(padding: const EdgeInsets.fromLTRB(20, 20, 20, 8), child: Text('Statement period', style: display(ctx, 22))),
          ListTile(title: const Text('All entries'), onTap: () => Navigator.pop(ctx, Period('All', DateTime(2000), DateTime.now()))),
          for (final p in Period.presets().skip(1)) ListTile(title: Text(p.label), onTap: () => Navigator.pop(ctx, p)),
          ListTile(
            title: const Text('Custom range…'),
            onTap: () async {
              final p = await pickPeriod(ctx, Period.presets()[2]);
              if (ctx.mounted) Navigator.pop(ctx, p);
            },
          ),
          const SizedBox(height: 8),
        ]),
      ),
    );
    if (period == null || !mounted) return;
    final all = period.label == 'All';
    final action = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: context.c.card,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(26))),
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const SizedBox(height: 12),
          ListTile(leading: const Icon(Icons.share_outlined), title: const Text('Share PDF'), onTap: () => Navigator.pop(ctx, 'share')),
          ListTile(leading: const Icon(Icons.print_outlined), title: const Text('Print'), onTap: () => Navigator.pop(ctx, 'print')),
          const SizedBox(height: 8),
        ]),
      ),
    );
    if (action == null || !mounted) return;
    showSnack(context, 'Preparing statement…');
    try {
      final st = await Api.instance.statement(widget.partyId, start: all ? null : ymd(period.start), end: all ? null : ymd(period.end));
      final bytes = await statementPdf(st);
      final file = 'statement-${(_p!['name'] as String).replaceAll(RegExp(r'[^A-Za-z0-9]+'), '-')}-${ymd(DateTime.now())}';
      if (action == 'print') {
        await printPdf(bytes, file);
      } else {
        await sharePdf(bytes, '$file.pdf');
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message);
    } catch (_) {
      if (mounted) showSnack(context, "Couldn't make the PDF. Check your connection and try again.");
    }
  }

  Future<void> _reminder() async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: context.c.card,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(26))),
      builder: (_) => _ReminderSheet(party: _p!, linksAvailable: _links),
    );
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.c;
    final p = _p;
    final cur = (p?['currency'] ?? 'INR') as String;
    return Scaffold(
      appBar: innerBar('Customer', actions: [
        if (p != null) IconButton(onPressed: _editCustomer, icon: Icon(Icons.edit_outlined, color: c.ink), tooltip: 'Edit customer'),
        const SizedBox(width: 6),
      ]),
      body: _error != null
          ? ErrorView(message: _error!, onRetry: _load)
          : p == null
              ? const Working('Opening history…')
              : RefreshIndicator(
                  color: c.accent,
                  onRefresh: _load,
                  child: ListView(padding: const EdgeInsets.fromLTRB(20, 8, 20, 24), children: [
                    Text(p['name'] as String, style: display(context, 32)),
                    if ((p['phone'] ?? '').toString().isNotEmpty || (p['language'] ?? '').toString().isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text([p['phone'], p['language']].where((v) => (v ?? '').toString().isNotEmpty).join(' · '),
                            style: TextStyle(color: c.inkFaint)),
                      ),
                    const SizedBox(height: 12),
                    Text(_label(p['balance_minor'] as int), style: TextStyle(color: c.inkSoft, fontSize: 15)),
                    const SizedBox(height: 2),
                    Text(formatMoney((p['balance_minor'] as int).abs(), cur),
                        style: display(context, 42, color: _tone(p['balance_minor'] as int))),
                    const SizedBox(height: 16),
                    Row(children: [
                      Expanded(
                        child: FilledButton.icon(
                          onPressed: (p['balance_minor'] as int) > 0 ? _reminder : null,
                          icon: const Icon(Icons.message_outlined, size: 19),
                          label: const Text('Reminder'),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: _statement,
                          icon: const Icon(Icons.description_outlined, size: 19),
                          label: const Text('Statement'),
                        ),
                      ),
                    ]),
                    if (_requests.isNotEmpty) ...[
                      const SizedBox(height: 24),
                      Text('PayPal payment links', style: display(context, 22)),
                      const SizedBox(height: 8),
                      for (final r in _requests) _requestTile(r),
                    ],
                    const SizedBox(height: 24),
                    Text('History', style: display(context, 22)),
                    const SizedBox(height: 8),
                    if ((p['entries'] as List).isEmpty) Text('No entries yet.', style: TextStyle(color: c.inkFaint)),
                    for (final e in (p['entries'] as List).cast<Map<String, dynamic>>())
                      EntryTile(entry: e, currency: cur, onChanged: _load, showParty: false),
                    if ((p['entries'] as List).isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text('Tap an entry to edit or cancel it.', style: TextStyle(color: c.inkFaint)),
                      ),
                  ]),
                ),
    );
  }

  Widget _requestTile(Map<String, dynamic> r) {
    final c = context.c;
    final cur = r['currency'] as String;
    final amount = r['amount_minor'] as int;
    final paid = r['paid_seen'] as int;
    final (String label, Color tone) = switch (r['status'] as String) {
      'PAID' || 'MARKED_AS_PAID' => ('Paid', c.advance),
      'PARTIALLY_PAID' || 'MARKED_AS_PARTIALLY_PAID' => ('Part paid', c.doubt),
      'CANCELLED' => ('Cancelled', c.inkFaint),
      'REFUNDED' || 'PARTIALLY_REFUNDED' => ('Refunded', c.doubt),
      _ => ('Sent', c.accent),
    };
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Panel(
        padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
        child: Row(children: [
          Icon(Icons.account_balance_wallet_outlined, color: c.inkSoft, size: 20),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(formatMoney(amount, cur), style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
              Text([if (paid > 0) '${formatMoney(paid, cur)} received', whenLabel(r['created_at'] as String)].join(' · '),
                  style: TextStyle(color: c.inkFaint, fontSize: 13)),
            ]),
          ),
          TagChip(label, bg: c.paper, fg: tone),
          IconButton(
            tooltip: 'Share link again',
            icon: Icon(Icons.share_outlined, color: c.inkSoft, size: 20),
            onPressed: () => SharePlus.instance.share(ShareParams(text: r['link_url'] as String)),
          ),
        ]),
      ),
    );
  }

  Color _tone(int b) => b > 0 ? context.c.owes : b < 0 ? context.c.advance : context.c.inkSoft;
  String _label(int b) => b > 0 ? 'Owes you' : b < 0 ? 'Paid in advance' : 'All settled';
}

class _ReminderSheet extends StatefulWidget {
  const _ReminderSheet({required this.party, this.linksAvailable = false});
  final Map<String, dynamic> party;
  final bool linksAvailable;

  @override
  State<_ReminderSheet> createState() => _ReminderSheetState();
}

class _ReminderSheetState extends State<_ReminderSheet> {
  static const _languages = ['English', 'Hindi', 'Hinglish', 'Spanish', 'Portuguese', 'French', 'Bahasa Indonesia', 'Swahili'];
  late String _lang = (widget.party['language'] as String?)?.isNotEmpty == true ? widget.party['language'] as String : 'English';
  final _text = TextEditingController();
  bool _busy = false;
  bool? _aiWritten;
  late bool _withLink = widget.linksAvailable;
  late final _amount = TextEditingController(text: majorText(widget.party['balance_minor'] as int, widget.party['currency'] as String));
  String? _linkUrl;

  int get _balance => widget.party['balance_minor'] as int;

  /// Amount typed by the shopkeeper, in minor units; null if invalid. Can only go down from the balance.
  int? _amountMinor() {
    final minor = parseMinor(_amount.text, widget.party['currency'] as String);
    return minor == null || minor > _balance ? null : minor;
  }

  Future<void> _draft() async {
    int? amount;
    if (_withLink) {
      amount = _amountMinor();
      if (amount == null) {
        showSnack(context, 'Enter an amount up to ${formatMoney(_balance, widget.party['currency'] as String)}.');
        return;
      }
    }
    setState(() => _busy = true);
    try {
      if (_withLink) {
        final s = (await Api.instance.collectSend([
          {'party_id': widget.party['id'], 'amount_minor': amount, 'language': _lang},
        ]))
            .first;
        _text.text = s['message'] as String;
        if (mounted) setState(() => (_aiWritten = s['used_model'] as bool?, _linkUrl = (s['request'] as Map)['link_url'] as String));
        return;
      }
      final r = await Api.instance.reminder(widget.party['id'] as int, _lang);
      _text.text = r['text'] as String;
      if (mounted) setState(() => _aiWritten = r['ai_written'] as bool?);
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = context.c;
    final langs = {..._languages, _lang}.toList();
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 22, 20, 20 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text('Payment reminder', style: display(context, 22)),
          const SizedBox(height: 4),
          Text('The app writes a draft. You check it and send it yourself.', style: TextStyle(color: c.inkFaint)),
          const SizedBox(height: 14),
          DropdownButtonFormField<String>(
            initialValue: _lang,
            decoration: const InputDecoration(labelText: 'Language'),
            items: [for (final l in langs) DropdownMenuItem(value: l, child: Text(l))],
            onChanged: (v) => setState(() => _lang = v ?? _lang),
          ),
          if (widget.linksAvailable) ...[
            const SizedBox(height: 4),
            if (_linkUrl != null)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(Icons.check_circle_outline_rounded, color: c.advance),
                title: const Text('PayPal payment link added', style: TextStyle(fontWeight: FontWeight.w600)),
                subtitle: Text('When ${widget.party['name']} pays, it shows up in your Payment inbox.',
                    style: TextStyle(color: c.inkFaint)),
              )
            else
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              value: _withLink,
              activeThumbColor: c.accent,
              title: const Text('Add PayPal payment link', style: TextStyle(fontWeight: FontWeight.w600)),
              subtitle: Text('Customer pays from the link. You confirm it in your Payment inbox.', style: TextStyle(color: c.inkFaint)),
              onChanged: _linkUrl != null ? null : (v) => setState(() => _withLink = v),
            ),
            if (_withLink && _linkUrl == null)
              TextField(
                controller: _amount,
                enabled: _linkUrl == null,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(labelText: 'Link amount (${widget.party['currency']}) — up to the balance'),
              ),
          ],
          const SizedBox(height: 12),
          if (_linkUrl == null)
          FilledButton.icon(
            onPressed: _busy ? null : _draft,
            icon: _busy
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2.4, color: Colors.white))
                : const Icon(Icons.auto_awesome_outlined),
            label: Text(_withLink ? 'Create link & write message' : _text.text.isEmpty ? 'Write draft' : 'Write again'),
          ),
          if (_text.text.isNotEmpty) ...[
            const SizedBox(height: 12),
            TextField(controller: _text, minLines: 3, maxLines: 7, decoration: const InputDecoration(labelText: 'Message')),
            const SizedBox(height: 6),
            Text(
              _aiWritten == false
                  ? 'Standard message used. The amount is taken from your book.'
                  : 'Written by AI. The amount${_linkUrl != null ? ' and PayPal link are' : ' is'} inserted from your book, never typed by AI.',
              style: TextStyle(color: c.inkFaint, fontSize: 12.5),
            ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: () => SharePlus.instance.share(ShareParams(text: _text.text)),
              icon: const Icon(Icons.share_outlined),
              label: const Text('Share'),
            ),
          ],
        ]),
      ),
    );
  }
}
