import 'package:flutter/material.dart';

import '../api.dart';
import '../money.dart';
import '../theme.dart';
import '../widgets.dart';

/// Payments PayPal reported for our payment links. Nothing reaches the book
/// until the shopkeeper taps Confirm. Amounts come from PayPal (re-fetched on the
/// server), never from AI text.
class InboxScreen extends StatefulWidget {
  const InboxScreen({super.key});

  @override
  State<InboxScreen> createState() => _InboxScreenState();
}

class _InboxScreenState extends State<InboxScreen> {
  List<Map<String, dynamic>>? _open;
  List<Map<String, dynamic>> _done = [];
  String? _error;
  bool _syncing = false;
  final Set<int> _busy = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final open = await Api.instance.inbox();
      final done = [...await Api.instance.inbox(status: 'confirmed'), ...await Api.instance.inbox(status: 'dismissed')]
        ..sort((a, b) => (b['id'] as int).compareTo(a['id'] as int));
      if (mounted) setState(() => (_open = open, _done = done.take(15).toList()));
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  /// Ask PayPal directly — the fallback when a webhook hasn't arrived.
  Future<void> _sync() async {
    setState(() => _syncing = true);
    try {
      final n = await Api.instance.collectSync();
      if (mounted) showSnack(context, n == 0 ? 'Checked PayPal — nothing new.' : 'PayPal reported $n new update${n == 1 ? '' : 's'}.');
      await _load();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message);
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  Future<void> _confirm(Map<String, dynamic> item, {int? partyId}) async {
    final id = item['id'] as int;
    setState(() => _busy.add(id));
    try {
      final r = await Api.instance.inboxConfirm(id, partyId: partyId);
      if (mounted) {
        final bal = r['balance_minor'] as int;
        final cur = item['currency'] as String;
        showSnack(
          context,
          bal > 0
              ? 'Saved. Still owes ${formatMoney(bal, cur)}.'
              : bal < 0
              ? 'Saved. ${formatMoney(-bal, cur)} kept as advance.'
              : 'Saved. All settled.',
        );
      }
      await _load();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message);
    } finally {
      if (mounted) setState(() => _busy.remove(id));
    }
  }

  Future<void> _dismiss(Map<String, dynamic> item) async {
    final id = item['id'] as int;
    setState(() => _busy.add(id));
    try {
      await Api.instance.inboxDismiss(id);
      await _load();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message);
    } finally {
      if (mounted) setState(() => _busy.remove(id));
    }
  }

  Future<void> _changeCustomer(Map<String, dynamic> item) async {
    List<Map<String, dynamic>> people;
    try {
      people = ((await Api.instance.parties())['parties'] as List).cast<Map<String, dynamic>>();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message);
      return;
    }
    if (!mounted) return;
    final picked = await showModalBottomSheet<int>(
      context: context,
      isScrollControlled: true,
      backgroundColor: context.c.card,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(26))),
      builder: (ctx) => SafeArea(
        child: ConstrainedBox(
          constraints: BoxConstraints(maxHeight: MediaQuery.of(ctx).size.height * 0.7),
          child: ListView(
            shrinkWrap: true,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 20, 20, 8),
                child: Text('Whose payment is this?', style: display(ctx, 22)),
              ),
              for (final p in people)
                ListTile(
                  title: Text(p['name'] as String, style: const TextStyle(fontWeight: FontWeight.w600)),
                  trailing: Text(
                    formatMoney(p['balance_minor'] as int, item['currency'] as String),
                    style: TextStyle(color: ctx.c.inkSoft),
                  ),
                  selected: p['id'] == item['party_id'],
                  onTap: () => Navigator.pop(ctx, p['id'] as int),
                ),
            ],
          ),
        ),
      ),
    );
    if (picked != null && picked != item['party_id']) await _confirm(item, partyId: picked);
  }

  @override
  Widget build(BuildContext context) {
    final c = context.c;
    return Scaffold(
      appBar: innerBar(
        'Payment inbox',
        actions: [
          IconButton(
            tooltip: 'Check PayPal now',
            onPressed: _syncing ? null : _sync,
            icon: _syncing
                ? SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2.4, color: c.accent))
                : Icon(Icons.sync_rounded, color: c.ink),
          ),
          const SizedBox(width: 6),
        ],
      ),
      body: _error != null
          ? ErrorView(message: _error!, onRetry: _load)
          : _open == null
          ? const Working('Checking your inbox…')
          : RefreshIndicator(
              color: c.accent,
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
                children: [
                  Text(
                    'PayPal tells us when a customer pays. You confirm before anything is saved.',
                    style: TextStyle(color: c.inkSoft, fontSize: 15),
                  ),
                  const SizedBox(height: 14),
                  if (_open!.isEmpty)
                    Panel(
                      child: Column(
                        children: [
                          Icon(Icons.inbox_outlined, size: 40, color: c.inkFaint),
                          const SizedBox(height: 10),
                          Text('No new payments.', style: TextStyle(color: c.inkSoft, fontSize: 16)),
                          const SizedBox(height: 6),
                          TextButton.icon(
                            onPressed: _syncing ? null : _sync,
                            icon: const Icon(Icons.sync_rounded),
                            label: const Text('Check PayPal now'),
                          ),
                        ],
                      ),
                    ),
                  for (final item in _open!) _card(item),
                  if (_done.isNotEmpty) ...[
                    const SizedBox(height: 18),
                    Text('Recently handled', style: display(context, 20)),
                    const SizedBox(height: 8),
                    for (final item in _done) _doneTile(item),
                  ],
                ],
              ),
            ),
    );
  }

  Widget _card(Map<String, dynamic> item) {
    final c = context.c;
    final kind = item['kind'] as String;
    final cur = item['currency'] as String;
    final amount = item['amount_minor'] as int;
    final p = Map<String, dynamic>.from(item['proposal'] as Map);
    final d = Map<String, dynamic>.from(item['detail'] as Map);
    final who = (p['party'] as String?) ?? 'Unknown customer';
    final busy = _busy.contains(item['id']);
    final before = (p['balance_before_minor'] ?? 0) as int;
    final after = (p['balance_after_minor'] ?? 0) as int;
    final advance = (p['advance_minor'] ?? 0) as int;
    final requested = d['requested_minor'] as int?;

    final (IconData icon, Color tone, String title) = switch (kind) {
      'payment' => (Icons.south_west_rounded, c.advance, '$who paid ${formatMoney(amount, cur)}'),
      'refund' => (Icons.undo_rounded, c.doubt, 'PayPal refunded ${formatMoney(amount, cur)} to $who'),
      _ => (Icons.block_rounded, c.inkSoft, '$who\'s payment link was cancelled'),
    };
    final lines = <String>[
      if (kind == 'payment' && requested != null && amount < requested) 'Part payment — the link was for ${formatMoney(requested, cur)}.',
      if (kind == 'payment' && advance > 0) '${formatMoney(advance, cur)} more than owed — kept as advance.',
      if (kind == 'payment')
        after > 0
            ? 'Applied to the oldest credit first. Still owes ${formatMoney(after, cur)}.'
            : after == 0
            ? 'Clears the balance.'
            : '',
      if (kind == 'refund') 'Confirm to add it back to what $who owes (${formatMoney(before, cur)} → ${formatMoney(after, cur)}).',
      if (kind == 'cancelled') 'Nothing to save. Dismiss when you\'ve seen it.',
    ].where((s) => s.isNotEmpty).toList();

    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Panel(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                CircleAvatar(
                  radius: 18,
                  backgroundColor: c.paper,
                  child: Icon(icon, color: tone, size: 20),
                ),
                const SizedBox(width: 12),
                Expanded(child: Text(title, style: display(context, 19))),
              ],
            ),
            const SizedBox(height: 10),
            for (final l in lines)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text(l, style: TextStyle(color: c.inkSoft, fontSize: 14.5)),
              ),
            if (kind == 'payment')
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'Balance ${formatMoney(before, cur)} → ${formatMoney(after, cur)}',
                  style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15),
                ),
              ),
            const SizedBox(height: 8),
            Text(
              [
                'Reported by PayPal',
                if (d['invoice_id'] != null) d['invoice_id'],
                if (d['paypal_status'] != null) d['paypal_status'],
                whenLabel(item['created_at'] as String),
              ].join(' · '),
              style: TextStyle(color: c.inkFaint, fontSize: 12.5),
            ),
            const SizedBox(height: 12),
            if (busy)
              Center(
                child: SizedBox(width: 26, height: 26, child: CircularProgressIndicator(strokeWidth: 2.4, color: c.accent)),
              )
            else if (kind == 'cancelled')
              OutlinedButton(onPressed: () => _dismiss(item), child: const Text('Dismiss'))
            else
              Row(
                children: [
                  Expanded(
                    flex: 3,
                    child: FilledButton(onPressed: () => _confirm(item), child: const Text('Confirm')),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    flex: 2,
                    child: OutlinedButton(
                      onPressed: () => _changeCustomer(item),
                      child: const Text('Change', maxLines: 1),
                    ),
                  ),
                  IconButton(
                    tooltip: 'Dismiss',
                    onPressed: () => _dismiss(item),
                    icon: Icon(Icons.close_rounded, color: c.inkFaint),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }

  Widget _doneTile(Map<String, dynamic> item) {
    final c = context.c;
    final p = Map<String, dynamic>.from(item['proposal'] as Map);
    final confirmed = item['status'] == 'confirmed';
    final label = switch (item['kind']) {
      'payment' => 'Payment',
      'refund' => 'Refund',
      _ => 'Cancelled link',
    };
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: Icon(
        confirmed ? Icons.check_circle_outline_rounded : Icons.remove_circle_outline_rounded,
        color: confirmed ? c.advance : c.inkFaint,
      ),
      title: Text('$label · ${p['party'] ?? 'Unknown'}', style: const TextStyle(fontWeight: FontWeight.w600)),
      subtitle: Text(
        '${confirmed ? 'Saved to the book' : 'Dismissed'} · ${whenLabel((item['decided_at'] ?? item['created_at']) as String)}',
        style: TextStyle(color: c.inkFaint),
      ),
      trailing: (item['amount_minor'] as int) > 0 ? Text(formatMoney(item['amount_minor'] as int, item['currency'] as String)) : null,
    );
  }
}
