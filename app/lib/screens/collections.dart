import 'package:flutter/material.dart';

import '../api.dart';
import '../money.dart';
import '../theme.dart';
import '../widgets.dart';
import 'collect_agent.dart';
import 'customer_detail.dart';

/// Who owes what, and for how long. Ageing is calculated on the server
/// (payments clear the oldest credit first).
class CollectionsScreen extends StatefulWidget {
  const CollectionsScreen({super.key});

  @override
  State<CollectionsScreen> createState() => _CollectionsScreenState();
}

class _CollectionsScreenState extends State<CollectionsScreen> {
  Map<String, dynamic>? _data;
  String? _error;
  bool _onlyRemind = false;
  bool _links = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final d = await Api.instance.collections();
      if (mounted) setState(() => _data = d);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
      return;
    }
    try {
      final st = await Api.instance.collectStatus();
      if (mounted) setState(() => _links = st['links_available'] == true && st['paypal_configured'] == true);
    } on ApiException {
      // Collect is optional.
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = context.c;
    final d = _data;
    return Scaffold(
      appBar: innerBar('Collections'),
      body: _error != null
          ? ErrorView(message: _error!, onRetry: _load)
          : d == null
              ? const Working('Checking who owes what…')
              : RefreshIndicator(color: c.accent, onRefresh: _load, child: _body(d)),
    );
  }

  Widget _body(Map<String, dynamic> d) {
    final c = context.c;
    final cur = d['currency'] as String;
    final total = d['outstanding_total_minor'] as int;
    final b = Map<String, dynamic>.from(d['buckets'] as Map);
    final people = (d['customers'] as List).cast<Map<String, dynamic>>();
    final shown = _onlyRemind ? people.where((p) => p['remind'] == true).toList() : people;
    const labels = {'0_30': '0–30 days', '31_60': '31–60 days', '61_90': '61–90 days', '90_plus': '90+ days'};
    final tones = [c.advance, c.doubt, c.owes, c.accent];

    return ListView(padding: const EdgeInsets.fromLTRB(20, 4, 20, 24), children: [
      Text('Still to collect', style: TextStyle(color: c.inkSoft, fontSize: 15)),
      Text(formatMoney(total, cur), style: display(context, 44)),
      const SizedBox(height: 14),
      if (total > 0) ...[
        ClipRRect(
          borderRadius: BorderRadius.circular(8),
          child: SizedBox(
            height: 14,
            child: Row(children: [
              for (final (i, k) in labels.keys.indexed)
                if ((b[k] as int) > 0) Expanded(flex: b[k] as int, child: Container(color: tones[i])),
            ]),
          ),
        ),
        const SizedBox(height: 12),
        Wrap(spacing: 14, runSpacing: 8, children: [
          for (final (i, k) in labels.keys.indexed)
            Row(mainAxisSize: MainAxisSize.min, children: [
              Container(width: 10, height: 10, decoration: BoxDecoration(color: tones[i], shape: BoxShape.circle)),
              const SizedBox(width: 6),
              Text('${labels[k]}  ', style: TextStyle(color: c.inkSoft, fontSize: 13.5)),
              Text(formatMoney(b[k] as int, cur), style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5)),
            ]),
        ]),
        const SizedBox(height: 18),
      ],
      if ((d['remind_count'] as int) > 0)
        Panel(
          color: c.accentSoft,
          padding: const EdgeInsets.all(14),
          onTap: () => setState(() => _onlyRemind = !_onlyRemind),
          child: Row(children: [
            Icon(Icons.notifications_active_outlined, color: c.accent),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                '${d['remind_count']} customer${d['remind_count'] == 1 ? '' : 's'} could use a friendly reminder — unpaid over 2 weeks, no payment lately.',
                style: TextStyle(color: c.ink),
              ),
            ),
            Text(_onlyRemind ? 'Show all' : 'Show', style: TextStyle(color: c.accent, fontWeight: FontWeight.w700)),
          ]),
        ),
      if (_links && total > 0) ...[
        const SizedBox(height: 12),
        FilledButton.icon(
          onPressed: () async {
            await Navigator.push(context,
                MaterialPageRoute(builder: (_) => const CollectAgentScreen(initialText: 'Who should I chase today?')));
            _load();
          },
          icon: const Icon(Icons.auto_awesome_outlined),
          label: const Text('Who to chase today'),
        ),
      ],
      const SizedBox(height: 12),
      if (people.isEmpty)
        Padding(
          padding: const EdgeInsets.all(32),
          child: Text('Nobody owes you anything right now. 🎉', textAlign: TextAlign.center, style: TextStyle(color: c.inkSoft, fontSize: 16)),
        ),
      for (final p in shown)
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: Panel(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
            onTap: () async {
              await Navigator.push(context, MaterialPageRoute(builder: (_) => CustomerDetailScreen(partyId: p['id'] as int)));
              _load();
            },
            child: Row(children: [
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    Flexible(child: Text(p['name'] as String, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16.5))),
                    if (p['remind'] == true) ...[
                      const SizedBox(width: 8),
                      TagChip('Remind', bg: c.accentSoft, fg: c.accent),
                    ],
                  ]),
                  const SizedBox(height: 4),
                  Text(
                    [
                      'Oldest unpaid ${p['oldest_unpaid_days']} day${p['oldest_unpaid_days'] == 1 ? '' : 's'}',
                      p['days_since_payment'] == null ? 'no payments yet' : 'last paid ${p['days_since_payment']}d ago',
                    ].join(' · '),
                    style: TextStyle(color: c.inkFaint, fontSize: 13.5),
                  ),
                ]),
              ),
              Text(formatMoney(p['balance_minor'] as int, cur), style: display(context, 20, color: c.owes)),
            ]),
          ),
        ),
    ]);
  }
}
